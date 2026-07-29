"""Immutable bounded values shared by runtime status trackers and renderers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

MAX_TOOLS = 256
MAX_ID_CHARS = 256
MAX_NAME_CHARS = 128
MAX_COMMAND_CHARS = 2_048
MAX_SUMMARY_CHARS = 512
MAX_INPUT_CHARS = 2_048
MAX_RESULT_CHARS = 4_096
MAX_SOURCE_SCAN_CHARS = 65_536
MAX_TOKENS = 1_000_000_000_000
MAX_COST_USD = Decimal("1000000000")
MAX_DURATION_SECONDS = 31 * 24 * 60 * 60

_MAX_VALUE_ITEMS = 8
_MAX_VALUE_DEPTH = 2
_MAX_SCALAR_CHARS = 1_024
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])"
)
# Trojan-Source bidi controls plus invisible formatting codepoints stripped
# from every sanitized surface. Deliberately emoji-safe: ZWNJ/ZWJ (U+200C,
# U+200D) and variation selectors (U+FE00-FE0F) are KEPT because transcript
# and tool-preview text legitimately contains emoji sequences and complex
# scripts. The aggressive titles-only set lives in ui/repl.py
# (_TITLE_DISALLOWED_CODEPOINTS), mirroring codex terminal_title.rs.
_INVISIBLE_FORMAT_CODEPOINTS = frozenset(
    {
        0x061C,  # Arabic letter mark
        0x200B,  # zero-width space
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0xFEFF,  # BOM / zero-width no-break space
        *range(0x202A, 0x202F),  # bidi embeddings/overrides (Trojan Source)
        *range(0x2060, 0x2065),  # word joiner + invisible operators
        *range(0x2066, 0x2070),  # bidi isolates + deprecated formatting
        *range(0xFFF9, 0xFFFC),  # interlinear annotation controls
        *range(0xE0000, 0xE0080),  # astral tag characters
    }
)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}


class ToolActivityStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class BoundedText:
    """Sanitized preview plus enough metadata to render a collapsed stub."""

    preview: str
    source_chars: int | None
    source_lines: int | None
    truncated: bool


@dataclass(frozen=True)
class ToolActivitySnapshot:
    tool_call_id: str
    session_id: str
    tool_name: str
    status: ToolActivityStatus
    command: str
    summary: str
    input: BoundedText
    result: BoundedText | None
    parallel_group_id: str
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float

    @property
    def terminal(self) -> bool:
        return self.status != ToolActivityStatus.RUNNING


@dataclass(frozen=True)
class RequestTelemetrySnapshot:
    session_id: str
    provider: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    cost_usd: Decimal | None
    duration_seconds: float

    @property
    def cache_percent(self) -> int | None:
        if self.input_tokens <= 0 or self.cache_read_tokens <= 0:
            return None
        return min(100, round(100 * self.cache_read_tokens / self.input_tokens))


@dataclass(frozen=True)
class UsageTotalsSnapshot:
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    cost_usd: Decimal | None
    cost_complete: bool
    duration_seconds: float

    @property
    def cache_percent(self) -> int | None:
        if self.input_tokens <= 0 or self.cache_read_tokens <= 0:
            return None
        return min(100, round(100 * self.cache_read_tokens / self.input_tokens))


@dataclass(frozen=True, slots=True)
class SessionUsageSnapshot:
    """Usage attributed to one root or delegated session."""

    session_id: str
    usage: UsageTotalsSnapshot


@dataclass(frozen=True)
class TelemetrySnapshot:
    turn: UsageTotalsSnapshot
    session: UsageTotalsSnapshot
    last_request: RequestTelemetrySnapshot | None
    updated_at: datetime | None


@dataclass(frozen=True)
class RuntimeStatusSnapshot:
    tools: tuple[ToolActivitySnapshot, ...]
    telemetry: TelemetrySnapshot
    session_usage: tuple[SessionUsageSnapshot, ...] = ()


def request_telemetry(
    data: Mapping[str, Any], root_session_id: str
) -> tuple[RequestTelemetrySnapshot, bool]:
    usage = as_mapping(data.get("usage"))
    recognized_keys = {
        "input_tokens",
        "input",
        "prompt_tokens",
        "output_tokens",
        "output",
        "completion_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_read_input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "cache_creation_input_tokens",
        "reasoning_tokens",
        "cost_usd",
    }
    input_tokens = first_integer(usage, "input_tokens", "input", "prompt_tokens")
    output_tokens = first_integer(usage, "output_tokens", "output", "completion_tokens")
    total_tokens = first_integer(usage, "total_tokens") or min(
        MAX_TOKENS, input_tokens + output_tokens
    )
    duration_ms = number(data.get("duration_ms"), MAX_DURATION_SECONDS * 1_000)
    return (
        RequestTelemetrySnapshot(
            session_id=session_id(data, root_session_id),
            provider=clean_line(data.get("provider"), MAX_NAME_CHARS),
            model=clean_line(data.get("model"), MAX_NAME_CHARS),
            status=clean_line(data.get("status"), 32) or "ok",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=first_integer(
                usage,
                "cache_read_tokens",
                "cache_read_input_tokens",
                "cached_tokens",
            ),
            cache_write_tokens=first_integer(
                usage, "cache_write_tokens", "cache_creation_input_tokens"
            ),
            reasoning_tokens=first_integer(usage, "reasoning_tokens"),
            cost_usd=decimal_value(usage.get("cost_usd")),
            duration_seconds=duration_ms / 1_000,
        ),
        bool(recognized_keys.intersection(usage)),
    )


def usage_signature(request: RequestTelemetrySnapshot) -> tuple[Any, ...]:
    return (
        request.input_tokens,
        request.output_tokens,
        request.total_tokens,
        request.cache_read_tokens,
        request.cache_write_tokens,
        request.reasoning_tokens,
        request.cost_usd,
    )


def tool_command(tool_input: Mapping[str, Any]) -> str:
    for key in ("command", "cmd", "script"):
        if key in tool_input:
            return bounded_text(tool_input[key], MAX_COMMAND_CHARS).preview.strip()
    return ""


def tool_summary(
    tool_input: Mapping[str, Any], command: str, tool_name: str = ""
) -> str:
    normalized_name = clean_line(tool_name, MAX_NAME_CHARS).lower()
    if normalized_name in {"delegate", "task"}:
        agent = clean_line(tool_input.get("agent") or tool_input.get("agent_name"), 80)
        return f"Delegated to {agent}" if agent else "Started delegated task"
    if normalized_name == "todo":
        return "Updated task plan"
    if normalized_name in {"load_skill", "skill"}:
        skill = clean_line(tool_input.get("skill_name") or tool_input.get("name"), 80)
        return f"Loaded {skill}" if skill else "Loaded skill"
    keys = (
        "summary",
        "description",
        "instruction",
        "task",
        "query",
        "path",
        "file_path",
    )
    for key in keys:
        if key in tool_input:
            value = clean_line(tool_input[key], 160)
            if value:
                return value
    return clean_line(command, MAX_SUMMARY_CHARS)


def tool_succeeded(value: Any) -> bool:
    result = as_mapping(value)
    status = clean_line(result.get("status"), 32).lower()
    if status in {"error", "failed", "failure", "cancelled", "canceled", "denied"}:
        return False
    success = result.get("success")
    if success is False or (isinstance(success, str) and success.lower() == "false"):
        return False
    output = as_mapping(result.get("output")) or result
    return_code = output.get("returncode", output.get("exit_code"))
    if return_code is not None:
        try:
            return int(return_code) == 0
        except (TypeError, ValueError, OverflowError):
            return False
    return not (result.get("error") and success is not True)


def result_value(value: Any) -> Any:
    result = as_mapping(value)
    if not result:
        return value
    output = result.get("output")
    output_map = as_mapping(output)
    if output_map and ("stdout" in output_map or "stderr" in output_map):
        raw_stdout = output_map.get("stdout")
        raw_stderr = output_map.get("stderr")
        if isinstance(raw_stdout, str) and not raw_stderr:
            return raw_stdout
        if isinstance(raw_stderr, str) and not raw_stdout:
            return raw_stderr
        stdout = safe_string(raw_stdout, MAX_SOURCE_SCAN_CHARS)
        stderr = safe_string(raw_stderr, MAX_SOURCE_SCAN_CHARS)
        if stdout and stderr:
            return f"{stdout}\n[stderr]\n{stderr}"
        return stdout or stderr
    if output is not None:
        return output
    return result.get("error", result)


def bounded_text(value: Any, limit: int) -> BoundedText:
    normalized_truncated = False
    if isinstance(value, bytes):
        source = value[:MAX_SOURCE_SCAN_CHARS].decode("utf-8", errors="replace")
        normalized_truncated = len(value) > MAX_SOURCE_SCAN_CHARS
        source_chars: int | None = len(value)
    elif isinstance(value, str):
        source = value
        source_chars = len(value)
    else:
        normalized, normalized_truncated = _bounded_value(value)
        source = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        source_chars = len(source)
    source_lines = (
        source.count("\n") + 1
        if source and len(source) <= MAX_SOURCE_SCAN_CHARS
        else (0 if not source else None)
    )
    scanned = source[:MAX_SOURCE_SCAN_CHARS]
    cleaned = sanitize(scanned)
    truncated = (
        normalized_truncated or len(source) > len(scanned) or len(cleaned) > limit
    )
    return BoundedText(cleaned[:limit], source_chars, source_lines, truncated)


def _bounded_value(
    value: Any, depth: int = 0, seen: set[int] | None = None
) -> tuple[Any, bool]:
    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else (None, True)
    if isinstance(value, Decimal):
        return str(value), False
    if isinstance(value, bytes):
        value = value[:_MAX_SCALAR_CHARS].decode("utf-8", errors="replace")
    if isinstance(value, str):
        cleaned = sanitize(value[:MAX_SOURCE_SCAN_CHARS])
        return cleaned[:_MAX_SCALAR_CHARS], len(value) > _MAX_SCALAR_CHARS
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump()
        except Exception:
            return f"<{type(value).__name__}>", True
    if depth >= _MAX_VALUE_DEPTH:
        return "<...>", True
    seen = seen or set()
    marker = id(value)
    if marker in seen:
        return "<cycle>", True
    seen.add(marker)
    try:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            truncated = False
            for index, (raw_key, item) in enumerate(value.items()):
                if index >= _MAX_VALUE_ITEMS:
                    truncated = True
                    break
                key = clean_line(raw_key, 128) or "?"
                if sensitive_key(key):
                    result[key] = "[redacted]"
                    continue
                result[key], child_truncated = _bounded_value(item, depth + 1, seen)
                truncated = truncated or child_truncated
            return result, truncated
        if isinstance(value, Sequence):
            items = []
            truncated = len(value) > _MAX_VALUE_ITEMS
            for item in value[:_MAX_VALUE_ITEMS]:
                normalized, child_truncated = _bounded_value(item, depth + 1, seen)
                items.append(normalized)
                truncated = truncated or child_truncated
            return items, truncated
        return f"<{type(value).__name__}>", True
    finally:
        seen.discard(marker)


def sanitize(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _ANSI_RE.sub("", value)
    return "".join(
        char
        for char in value
        if ord(char) not in _INVISIBLE_FORMAT_CODEPOINTS
        and (char in {"\n", "\t"} or ord(char) >= 0x20)
        and not 0x7F <= ord(char) <= 0x9F
    )


def clean_line(value: Any, limit: int) -> str:
    return " ".join(safe_string(value, MAX_SOURCE_SCAN_CHARS).split())[:limit]


def safe_string(value: Any, limit: int) -> str:
    if isinstance(value, str):
        return sanitize(value[:limit])
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)[:limit]
    return ""


def identifier(value: Any, fallback: str) -> str:
    return clean_line(value, MAX_ID_CHARS) or fallback


def session_id(data: Mapping[str, Any], fallback: str) -> str:
    return identifier(data.get("session_id"), fallback)


def as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            return dumped if isinstance(dumped, Mapping) else {}
        except Exception:
            return {}
    return {}


def first_integer(data: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        if key in data and data[key] is not None:
            return integer(data[key])
    return 0


def integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(MAX_TOKENS, max(0, parsed))


def number(value: Any, maximum: float) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(parsed) or parsed < 0:
        return 0.0
    return min(maximum, parsed)


def decimal_value(value: Any) -> Decimal | None:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value)[:128])
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return min(MAX_COST_USD, parsed)


def sensitive_key(key: str) -> bool:
    compact = key.lower().replace("-", "_")
    suffixes = ("_key", "_token", "_secret", "_password")
    return compact in _SENSITIVE_KEYS or compact.endswith(suffixes)


__all__ = [
    "BoundedText",
    "RequestTelemetrySnapshot",
    "RuntimeStatusSnapshot",
    "SessionUsageSnapshot",
    "TelemetrySnapshot",
    "ToolActivitySnapshot",
    "ToolActivityStatus",
    "UsageTotalsSnapshot",
]
