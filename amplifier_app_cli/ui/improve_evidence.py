"""Bounded runtime evidence adapter for the `/improve` workflow."""

from __future__ import annotations

import json
from pathlib import Path
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .mcp_commands import McpConfigError, McpConfigStore
from .runtime_status import RuntimeStatusTracker

_MAX_EVIDENCE_ITEMS = 512
_MAX_VALUE_CHARS = 2_048


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    prompt: str
    choice: str


@dataclass(frozen=True, slots=True)
class McpServerEvidence:
    name: str
    config_bytes: int
    calls: int = 0

    def __post_init__(self) -> None:
        name = _server_name(self.name)
        if not name or self.config_bytes < 0 or self.calls < 0:
            raise ValueError("invalid MCP server evidence")
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class ImproveEvidence:
    approvals: tuple[ApprovalEvidence, ...] = ()
    prompts: tuple[str, ...] = ()
    memory_entries: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerEvidence, ...] = ()


class RuntimeImproveEvidenceSource:
    """Take a bounded evidence snapshot from live session capabilities."""

    def __init__(
        self,
        *,
        context_messages: Callable[[], Awaitable[Sequence[Mapping[str, Any]]]]
        | None = None,
        approval_history: Callable[[], Sequence[object]] | None = None,
        config: Mapping[str, Any] | None = None,
        runtime_status: RuntimeStatusTracker | None = None,
        mcp_config_path: Path | None = None,
    ) -> None:
        self._context_messages = context_messages
        self._approval_history = approval_history
        self._config = config or {}
        self._runtime = runtime_status
        self._mcp_config_path = (
            mcp_config_path or Path.cwd() / ".amplifier" / "mcp.json"
        )

    async def __call__(self) -> ImproveEvidence:
        messages: Sequence[Mapping[str, Any]] = ()
        if self._context_messages is not None:
            try:
                messages = await self._context_messages()
            except (AttributeError, RuntimeError, TypeError):
                messages = ()
        prompts, memories = _message_evidence(messages)
        approvals = _approval_evidence(
            self._approval_history() if self._approval_history is not None else ()
        )
        return ImproveEvidence(
            approvals=approvals,
            prompts=prompts,
            memory_entries=memories,
            mcp_servers=_mcp_evidence(
                self._config, self._runtime, self._mcp_config_path
            ),
        )


def _approval_evidence(records: Sequence[object]) -> tuple[ApprovalEvidence, ...]:
    result = []
    for record in records[-_MAX_EVIDENCE_ITEMS:]:
        prompt = getattr(record, "prompt", "")
        choice = getattr(record, "choice", "")
        if isinstance(record, Mapping):
            prompt, choice = record.get("prompt", ""), record.get("choice", "")
        clean_prompt = _single_line(prompt, 512)
        clean_choice = _single_line(choice, 40)
        if clean_prompt and clean_choice:
            result.append(ApprovalEvidence(clean_prompt, clean_choice))
    return tuple(result)


def _message_evidence(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    prompts, memories = [], []
    for message in messages[-_MAX_EVIDENCE_ITEMS:]:
        role = _single_line(message.get("role", ""), 32).lower()
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            prompts.append(content[:_MAX_VALUE_CHARS])
        if role in {"system", "developer", "memory", "context"} or message.get(
            "memory_key"
        ):
            memories.append(content[:_MAX_VALUE_CHARS])
    return tuple(prompts), tuple(memories)


def _mcp_evidence(
    config: Mapping[str, Any],
    runtime: RuntimeStatusTracker | None,
    mcp_config_path: Path,
) -> tuple[McpServerEvidence, ...]:
    candidates: object = _project_mcp_servers(mcp_config_path)
    if candidates is None:
        candidates = config.get("mcpServers")
    if candidates is None:
        candidates = config.get("mcp_servers")
    mcp = config.get("mcp")
    if candidates is None and isinstance(mcp, Mapping):
        candidates = mcp.get("servers")
    nested = config.get("config")
    if candidates is None and isinstance(nested, Mapping):
        nested_mcp = nested.get("mcp")
        if isinstance(nested_mcp, Mapping):
            candidates = nested_mcp.get("servers")
    items: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(candidates, Mapping):
        items = [
            (str(name), value)
            for name, value in candidates.items()
            if isinstance(value, Mapping)
        ]
    elif isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        items = [
            (str(value.get("name", "")), value)
            for value in candidates
            if isinstance(value, Mapping)
        ]
    tool_names = (
        [item.tool_name.lower() for item in runtime.tool_snapshot()]
        if runtime is not None
        else []
    )
    result = []
    for raw_name, value in items[:_MAX_EVIDENCE_ITEMS]:
        name = _server_name(raw_name)
        if not name:
            continue
        config_bytes = len(
            json.dumps(
                {name: value}, ensure_ascii=False, sort_keys=True, default=str
            ).encode("utf-8")
        )
        match_name = name.lower()
        calls = sum(
            tool == match_name
            or tool.startswith(f"{match_name}__")
            or tool.startswith(f"mcp__{match_name}__")
            for tool in tool_names
        )
        result.append(McpServerEvidence(name, config_bytes, calls))
    return tuple(result)


def _project_mcp_servers(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    try:
        return McpConfigStore(path).servers()
    except McpConfigError:
        return {}


def _server_name(value: object) -> str:
    clean = _single_line(value, 80)
    return clean if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", clean) else ""


def _single_line(value: object, limit: int) -> str:
    text = "".join(character for character in str(value) if ord(character) >= 32)
    return " ".join(text.split())[:limit]


__all__ = [
    "ApprovalEvidence",
    "ImproveEvidence",
    "McpServerEvidence",
    "RuntimeImproveEvidenceSource",
]
