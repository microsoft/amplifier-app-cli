"""Transient LLM stream state for the layered terminal UI."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from amplifier_core import HookResult

from .runtime_status import BoundedText
from .runtime_status import RequestTelemetrySnapshot
from .runtime_status import RuntimeStatusSnapshot
from .runtime_status import RuntimeStatusTracker
from .runtime_status import TelemetrySnapshot
from .runtime_status import ToolActivitySnapshot
from .runtime_status import ToolActivityStatus
from .runtime_status import UsageTotalsSnapshot
from .task_status import HookRegistry

logger = logging.getLogger(__name__)
_MAX_ACTIVE_BLOCKS = 8
_MAX_STREAM_CHARS = 16_384
_DELTA_REFRESH_SECONDS = 0.05

_LEGACY_STREAMING_UI_HANDLERS = (
    "streaming-ui-content-block-start",
    "streaming-ui-content-block-end",
    "streaming-ui-tool-pre",
    "streaming-ui-tool-post",
    "streaming-ui-llm-response",
    "streaming-ui-cost-summary",
    "streaming-ui-cost-seed",
    "streaming-ui-render-end",
    "streaming-ui-overlay-start",
    "streaming-ui-overlay-delta",
    "streaming-ui-overlay-end",
    "streaming-ui-overlay-aborted",
    "streaming-ui-overlay-retry",
    "streaming-ui-overlay-prompt-reset",
)


@dataclass(frozen=True)
class StreamPreview:
    kind: str
    text: str


class StreamStatusTracker:
    """Track the active root-session stream without printing terminal controls."""

    EVENTS = (
        "llm:stream_block_start",
        "llm:stream_block_delta",
        "llm:stream_block_end",
        "llm:stream_aborted",
        "provider:error",
        "provider:retry",
        "orchestrator:complete",
        "execution:end",
        "prompt:submit",
    )

    def __init__(self, root_session_id: str, *, show_thinking: bool = False) -> None:
        self.root_session_id = root_session_id
        self.show_thinking = show_thinking
        self._blocks: dict[tuple[str, str, int], tuple[str, str, int]] = {}
        self._hidden_blocks: set[tuple[str, str, int]] = set()
        self._listeners: list[Callable[[], None]] = []
        self._sequence = 0
        self._last_delta_notification = 0.0

    @property
    def preview(self) -> StreamPreview | None:
        if not self._blocks:
            return None
        kind, text, _ = max(self._blocks.values(), key=lambda block: block[2])
        return StreamPreview(kind, text)

    @property
    def estimated_tokens(self) -> int:
        """Estimate currently streamed text tokens before provider usage arrives."""
        characters = sum(
            len(text) for kind, text, _ in self._blocks.values() if kind == "text"
        )
        return max(0, (characters + 3) // 4)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def register_hooks(
        self, hooks: HookRegistry, *, priority: int = 60
    ) -> Callable[[], None]:
        unregister_callbacks = []
        for event in self.EVENTS:
            unregister = hooks.register(
                event,
                self.handle_event,
                priority=priority,
                name=f"cli-layered-stream-{event.replace(':', '-')}",
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

    def consume(self, event: str, data: dict[str, Any]) -> None:
        session_id = str(data.get("session_id") or self.root_session_id)
        if session_id != self.root_session_id:
            return
        if event in {
            "llm:stream_aborted",
            "provider:error",
            "provider:retry",
            "orchestrator:complete",
            "execution:end",
            "prompt:submit",
        }:
            self._blocks.clear()
            self._hidden_blocks.clear()
            self._notify()
            return

        raw_index = data.get("block_index", 0)
        block_index = raw_index if isinstance(raw_index, int) else 0
        request_id = str(data.get("request_id") or "")[:256]
        key = (session_id, request_id, block_index)
        if event == "llm:stream_block_end":
            self._blocks.pop(key, None)
            self._hidden_blocks.discard(key)
            self._notify()
            return

        current_kind, current_text, _ = self._blocks.get(key, ("text", "", 0))
        kind = str(data.get("block_type") or current_kind)
        if kind not in {"text", "thinking", "reasoning"}:
            self._blocks.pop(key, None)
            if len(self._hidden_blocks) >= _MAX_ACTIVE_BLOCKS:
                self._hidden_blocks.pop()
            self._hidden_blocks.add(key)
            return
        if kind in {"thinking", "reasoning"} and not self.show_thinking:
            self._blocks.pop(key, None)
            if len(self._hidden_blocks) >= _MAX_ACTIVE_BLOCKS:
                self._hidden_blocks.pop()
            self._hidden_blocks.add(key)
            return
        if event == "llm:stream_block_start":
            self._hidden_blocks.discard(key)
        if key in self._hidden_blocks:
            return
        text = "" if event == "llm:stream_block_start" else current_text
        if event == "llm:stream_block_delta":
            addition = str(data.get("text") or "")
            text = (current_text + addition)[-_MAX_STREAM_CHARS:]
        if key not in self._blocks and len(self._blocks) >= _MAX_ACTIVE_BLOCKS:
            oldest = min(self._blocks, key=lambda item: self._blocks[item][2])
            self._blocks.pop(oldest)
        self._sequence += 1
        self._blocks[key] = (kind, text, self._sequence)
        if event == "llm:stream_block_delta":
            now = monotonic()
            if now - self._last_delta_notification < _DELTA_REFRESH_SECONDS:
                return
            self._last_delta_notification = now
        self._notify()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                logger.debug("Stream status listener failed", exc_info=True)


def attach_layered_stream_hooks(
    coordinator: Any, tracker: StreamStatusTracker
) -> Callable[[], None]:
    """Replace legacy transcript painters with the in-layout stream preview."""
    hooks = coordinator.get("hooks")
    if not hooks:
        return lambda: None
    suppress_legacy_streaming_ui(hooks)
    return tracker.register_hooks(hooks)


def suppress_legacy_streaming_ui(hooks: HookRegistry) -> None:
    """Remove terminal painters superseded by the layered transcript and footer."""
    for name in _LEGACY_STREAMING_UI_HANDLERS:
        hooks.unregister(name)


__all__ = [
    "BoundedText",
    "RequestTelemetrySnapshot",
    "RuntimeStatusSnapshot",
    "RuntimeStatusTracker",
    "StreamPreview",
    "StreamStatusTracker",
    "TelemetrySnapshot",
    "ToolActivitySnapshot",
    "ToolActivityStatus",
    "UsageTotalsSnapshot",
    "attach_layered_stream_hooks",
    "suppress_legacy_streaming_ui",
]
