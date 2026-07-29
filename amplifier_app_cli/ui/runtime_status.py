"""Bounded tool activity and LLM telemetry state for terminal renderers."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Any

from amplifier_core import HookResult

from .runtime_values import MAX_COST_USD
from .runtime_values import MAX_DURATION_SECONDS
from .runtime_values import MAX_INPUT_CHARS
from .runtime_values import MAX_NAME_CHARS
from .runtime_values import MAX_RESULT_CHARS
from .runtime_values import MAX_TOKENS
from .runtime_values import MAX_TOOLS
from .runtime_values import BoundedText
from .runtime_values import RequestTelemetrySnapshot
from .runtime_values import RuntimeStatusSnapshot
from .runtime_values import SessionUsageSnapshot
from .runtime_values import TelemetrySnapshot
from .runtime_values import ToolActivitySnapshot
from .runtime_values import ToolActivityStatus
from .runtime_values import UsageTotalsSnapshot
from .runtime_values import as_mapping
from .runtime_values import bounded_text
from .runtime_values import clean_line
from .runtime_values import decimal_value
from .runtime_values import identifier
from .runtime_values import integer
from .runtime_values import request_telemetry
from .runtime_values import result_value
from .runtime_values import session_id
from .runtime_values import tool_command
from .runtime_values import tool_succeeded
from .runtime_values import tool_summary
from .runtime_values import usage_signature
from .task_status import HookRegistry

logger = logging.getLogger(__name__)
_MAX_SESSION_USAGE = 256
RUNTIME_STATUS_CAPABILITY = "ui.runtime_status_tracker"


@dataclass
class _ToolRecord:
    snapshot: ToolActivitySnapshot
    started_monotonic: float
    completed_monotonic: float | None = None


@dataclass
class _UsageTotals:
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    known_cost_usd: Decimal = Decimal("0")
    costed_requests: int = 0
    duration_seconds: float = 0.0

    def add(self, request: RequestTelemetrySnapshot) -> None:
        self.request_count += 1
        token_fields = (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
        for field in token_fields:
            total = getattr(self, field) + getattr(request, field)
            setattr(self, field, min(MAX_TOKENS, total))
        if request.cost_usd is not None:
            self.known_cost_usd = min(
                MAX_COST_USD, self.known_cost_usd + request.cost_usd
            )
            self.costed_requests += 1
        self.duration_seconds = min(
            MAX_DURATION_SECONDS,
            self.duration_seconds + request.duration_seconds,
        )

    def snapshot(self, baseline: Decimal | None = None) -> UsageTotalsSnapshot:
        has_cost = baseline is not None or self.costed_requests > 0
        cost = (baseline or Decimal("0")) + self.known_cost_usd if has_cost else None
        return UsageTotalsSnapshot(
            request_count=self.request_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost_usd=min(MAX_COST_USD, cost) if cost is not None else None,
            cost_complete=has_cost and self.costed_requests == self.request_count,
            duration_seconds=self.duration_seconds,
        )


class RuntimeStatusTracker:
    """Consume hook events without retaining unbounded or terminal-active data."""

    EVENTS = (
        "tool:pre",
        "tool:post",
        "tool:error",
        "llm:response",
        "content_block:end",
        "prompt:submit",
        "prompt:complete",
    )

    def __init__(
        self,
        root_session_id: str,
        *,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        max_tools: int = MAX_TOOLS,
    ) -> None:
        self.root_session_id = identifier(root_session_id, "session")
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic_clock
        self._max_tools = max(1, min(MAX_TOOLS, int(max_tools)))
        self._tools: dict[tuple[str, str], _ToolRecord] = {}
        self._listeners: list[Callable[[], None]] = []
        self._turn = _UsageTotals()
        self._session = _UsageTotals()
        self._usage_by_session: dict[str, _UsageTotals] = {}
        self._session_cost_baseline: Decimal | None = None
        self._last_request: RequestTelemetrySnapshot | None = None
        self._updated_at: datetime | None = None
        self._pending_response_usage: dict[str, tuple[Any, ...]] = {}

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def register_hooks(
        self, hooks: HookRegistry, *, priority: int = 55
    ) -> Callable[[], None]:
        unregister_callbacks: list[Callable[[], None]] = []
        for event in self.EVENTS:
            unregister = hooks.register(
                event,
                self.handle_event,
                priority=priority,
                name=f"cli-runtime-status-{event.replace(':', '-')}",
            )
            if callable(unregister):
                unregister_callbacks.append(unregister)

        def unregister_all() -> None:
            for unregister in reversed(unregister_callbacks):
                unregister()

        return unregister_all

    async def handle_event(self, event: str, data: dict[str, Any]) -> HookResult:
        self.consume(event, data)
        return HookResult(action="continue")

    def consume(self, event: str, data: Mapping[str, Any]) -> None:
        if event in {"tool:pre", "tool:post", "tool:error"}:
            self._consume_tool(event, data)
        elif event == "llm:response":
            self._consume_llm_response(data)
        elif event == "content_block:end":
            self._consume_content_end(data)
        elif event == "prompt:submit":
            source_session = session_id(data, self.root_session_id)
            self._discard_running_tools(
                None if source_session == self.root_session_id else source_session
            )
            if source_session == self.root_session_id:
                self._turn = _UsageTotals()
                self._last_request = None
                self._pending_response_usage.clear()
            self._touch()
        elif event == "prompt:complete":
            source_session = session_id(data, self.root_session_id)
            self._discard_running_tools(
                None if source_session == self.root_session_id else source_session
            )
            self._touch()

    def seed_session_cost(self, prior_cost_usd: object) -> None:
        """Set restored spend that predates usage events observed by this tracker."""
        cost = decimal_value(prior_cost_usd)
        if cost is None or cost == self._session_cost_baseline:
            return
        self._session_cost_baseline = cost
        self._touch()

    def tool_snapshot(self) -> tuple[ToolActivitySnapshot, ...]:
        now = self._monotonic()
        snapshots = []
        for record in self._tools.values():
            end = record.completed_monotonic
            duration = (end if end is not None else now) - record.started_monotonic
            duration = max(0.0, min(MAX_DURATION_SECONDS, duration))
            snapshots.append(replace(record.snapshot, duration_seconds=duration))
        return tuple(snapshots)

    def telemetry_snapshot(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            turn=self._turn.snapshot(),
            session=self._session.snapshot(self._session_cost_baseline),
            last_request=self._last_request,
            updated_at=self._updated_at,
        )

    def usage_by_session_snapshot(self) -> tuple[SessionUsageSnapshot, ...]:
        """Return immutable usage totals attributed to each observed session."""
        return tuple(
            SessionUsageSnapshot(source_session, totals.snapshot())
            for source_session, totals in self._usage_by_session.items()
        )

    def snapshot(self) -> RuntimeStatusSnapshot:
        return RuntimeStatusSnapshot(
            self.tool_snapshot(),
            self.telemetry_snapshot(),
            self.usage_by_session_snapshot(),
        )

    def _consume_tool(self, event: str, data: Mapping[str, Any]) -> None:
        call_id = identifier(data.get("tool_call_id"), "")
        if not call_id:
            return
        source_session = session_id(data, self.root_session_id)
        key = (source_session, call_id)
        if event != "tool:pre" and key not in self._tools:
            matches = [item for item in self._tools if item[1] == call_id]
            if len(matches) == 1:
                key = matches[0]
                source_session = key[0]
        now, tick = self._now(), self._monotonic()
        tool_input = as_mapping(data.get("tool_input") or data.get("input"))
        if event == "tool:pre":
            self._start_tool(key, source_session, call_id, data, tool_input, now, tick)
            return
        self._finish_tool(
            key, source_session, call_id, event, data, tool_input, now, tick
        )

    def _start_tool(
        self,
        key: tuple[str, str],
        source_session: str,
        call_id: str,
        data: Mapping[str, Any],
        tool_input: Mapping[str, Any],
        now: datetime,
        tick: float,
    ) -> None:
        existing = self._tools.get(key)
        if existing is not None:
            return
        command = tool_command(tool_input)
        tool_name = self._tool_name(data)
        snapshot = ToolActivitySnapshot(
            tool_call_id=call_id,
            session_id=source_session,
            tool_name=tool_name,
            status=ToolActivityStatus.RUNNING,
            command=command,
            summary=tool_summary(tool_input, command, tool_name),
            input=bounded_text(tool_input, MAX_INPUT_CHARS),
            result=None,
            parallel_group_id=identifier(data.get("parallel_group_id"), ""),
            started_at=now,
            completed_at=None,
            duration_seconds=0.0,
        )
        if existing is None:
            self._evict_for_insert()
            self._tools[key] = _ToolRecord(snapshot, tick)
        else:
            existing.snapshot = snapshot
            existing.started_monotonic = tick
            existing.completed_monotonic = None
        self._touch(now)

    def _finish_tool(
        self,
        key: tuple[str, str],
        source_session: str,
        call_id: str,
        event: str,
        data: Mapping[str, Any],
        tool_input: Mapping[str, Any],
        now: datetime,
        tick: float,
    ) -> None:
        record = self._tools.get(key)
        if record is not None and record.snapshot.terminal:
            return
        if record is None:
            command = tool_command(tool_input)
            tool_name = self._tool_name(data)
            self._evict_for_insert()
            record = _ToolRecord(
                ToolActivitySnapshot(
                    call_id,
                    source_session,
                    tool_name,
                    ToolActivityStatus.RUNNING,
                    command,
                    tool_summary(tool_input, command, tool_name),
                    bounded_text(tool_input, MAX_INPUT_CHARS),
                    None,
                    identifier(data.get("parallel_group_id"), ""),
                    now,
                    None,
                    0.0,
                ),
                tick,
            )
            self._tools[key] = record
        raw_result = (
            data.get("error")
            if event == "tool:error"
            else data.get("tool_response", data.get("result"))
        )
        failed = event == "tool:error" or not tool_succeeded(raw_result)
        old = record.snapshot
        record.snapshot = replace(
            old,
            status=(
                ToolActivityStatus.FAILED if failed else ToolActivityStatus.SUCCEEDED
            ),
            result=bounded_text(result_value(raw_result), MAX_RESULT_CHARS),
            completed_at=now,
            duration_seconds=max(0.0, tick - record.started_monotonic),
        )
        record.completed_monotonic = tick
        self._touch(now)

    def _consume_llm_response(self, data: Mapping[str, Any]) -> None:
        request, has_usage = request_telemetry(data, self.root_session_id)
        self._last_request = request
        if has_usage:
            self._add_usage(request)
            self._pending_response_usage[request.session_id] = usage_signature(request)
            if len(self._pending_response_usage) > MAX_TOOLS:
                self._pending_response_usage.pop(
                    next(iter(self._pending_response_usage))
                )
        self._touch()

    def _consume_content_end(self, data: Mapping[str, Any]) -> None:
        total_blocks = integer(data.get("total_blocks"))
        block_index = integer(data.get("block_index"))
        if total_blocks and block_index != total_blocks - 1:
            return
        request, has_usage = request_telemetry(data, self.root_session_id)
        if not has_usage:
            return
        signature = usage_signature(request)
        if self._pending_response_usage.pop(request.session_id, None) == signature:
            return
        self._last_request = request
        self._add_usage(request)
        self._touch()

    def _add_usage(self, request: RequestTelemetrySnapshot) -> None:
        self._turn.add(request)
        self._session.add(request)
        totals = self._usage_by_session.get(request.session_id)
        if totals is None:
            if len(self._usage_by_session) >= _MAX_SESSION_USAGE:
                evictable = next(
                    (
                        item
                        for item in self._usage_by_session
                        if item != self.root_session_id
                    ),
                    next(iter(self._usage_by_session)),
                )
                self._usage_by_session.pop(evictable, None)
            totals = _UsageTotals()
            self._usage_by_session[request.session_id] = totals
        totals.add(request)

    def _evict_for_insert(self) -> None:
        if len(self._tools) < self._max_tools:
            return
        key = next(
            (item for item, record in self._tools.items() if record.snapshot.terminal),
            next(iter(self._tools)),
        )
        self._tools.pop(key, None)

    def _discard_running_tools(self, source_session: str | None = None) -> None:
        self._tools = {
            key: record
            for key, record in self._tools.items()
            if record.snapshot.terminal
            or (source_session is not None and key[0] != source_session)
        }

    def _tool_name(self, data: Mapping[str, Any]) -> str:
        raw_name = data.get("tool_name") or data.get("tool")
        return clean_line(raw_name, MAX_NAME_CHARS) or "unknown"

    def _now(self) -> datetime:
        value = self._wall_clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    def _touch(self, now: datetime | None = None) -> None:
        self._updated_at = now or self._now()
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                logger.debug("Runtime status listener failed", exc_info=True)


def attach_runtime_status_hooks(
    coordinator: Any,
    tracker: RuntimeStatusTracker,
) -> Callable[[], None]:
    """Expose shared runtime telemetry and attach it to one session's hooks."""
    existing = coordinator.get_capability(RUNTIME_STATUS_CAPABILITY)
    if existing is tracker:
        return lambda: None
    if existing is not None:
        return lambda: None
    coordinator.register_capability(RUNTIME_STATUS_CAPABILITY, tracker)
    hooks = coordinator.get("hooks")
    if not hooks:
        return lambda: None
    return tracker.register_hooks(hooks)


__all__ = [
    "attach_runtime_status_hooks",
    "BoundedText",
    "RequestTelemetrySnapshot",
    "RuntimeStatusSnapshot",
    "RuntimeStatusTracker",
    "RUNTIME_STATUS_CAPABILITY",
    "SessionUsageSnapshot",
    "TelemetrySnapshot",
    "ToolActivitySnapshot",
    "ToolActivityStatus",
    "UsageTotalsSnapshot",
]
