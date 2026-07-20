"""Tests for layered LLM stream state."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.ui.stream_status import RuntimeStatusTracker
from amplifier_app_cli.ui.stream_status import StreamPreview
from amplifier_app_cli.ui.stream_status import StreamStatusTracker
from amplifier_app_cli.ui.stream_status import ToolActivityStatus
from amplifier_app_cli.ui.stream_status import attach_layered_stream_hooks
from amplifier_app_cli.session_spawner import _propagate_runtime_status_tracker
from amplifier_app_cli.ui.runtime_status import RUNTIME_STATUS_CAPABILITY


def test_stream_preview_accumulates_root_deltas_and_clears_at_end():
    tracker = StreamStatusTracker("root")
    tracker.consume(
        "llm:stream_block_start",
        {"session_id": "root", "block_index": 2, "block_type": "text"},
    )
    tracker.consume(
        "llm:stream_block_delta",
        {"session_id": "root", "block_index": 2, "text": "hello "},
    )
    tracker.consume(
        "llm:stream_block_delta",
        {"session_id": "root", "block_index": 2, "text": "world"},
    )

    assert tracker.preview == StreamPreview("text", "hello world")
    assert tracker.estimated_tokens == 3

    tracker.consume(
        "llm:stream_block_end",
        {"session_id": "root", "block_index": 2},
    )
    assert tracker.preview is None
    assert tracker.estimated_tokens == 0


def test_stream_preview_ignores_child_sessions_and_notifies_listeners():
    tracker = StreamStatusTracker("root", show_thinking=True)
    notifications = []
    remove = tracker.add_listener(lambda: notifications.append(True))

    tracker.consume(
        "llm:stream_block_delta",
        {"session_id": "child", "block_index": 0, "text": "hidden"},
    )
    tracker.consume(
        "llm:stream_block_delta",
        {
            "session_id": "root",
            "block_index": 0,
            "block_type": "thinking",
            "text": "visible",
        },
    )

    assert tracker.preview == StreamPreview("thinking", "visible")
    assert notifications == [True]
    remove()


def test_stream_preview_separates_requests_and_resets_on_retry():
    tracker = StreamStatusTracker("root")
    tracker.consume(
        "llm:stream_block_start",
        {"session_id": "root", "request_id": "old", "block_index": 0},
    )
    tracker.consume(
        "llm:stream_block_delta",
        {
            "session_id": "root",
            "request_id": "old",
            "block_index": 0,
            "text": "old text",
        },
    )
    tracker.consume(
        "llm:stream_block_start",
        {"session_id": "root", "request_id": "new", "block_index": 0},
    )
    tracker.consume(
        "llm:stream_block_delta",
        {
            "session_id": "root",
            "request_id": "new",
            "block_index": 0,
            "text": "new text",
        },
    )
    tracker.consume(
        "llm:stream_block_end",
        {"session_id": "root", "request_id": "old", "block_index": 0},
    )

    assert tracker.preview == StreamPreview("text", "new text")

    tracker.consume("provider:retry", {"session_id": "root"})
    assert tracker.preview is None


def test_thinking_preview_is_hidden_by_default_and_can_be_enabled():
    hidden = StreamStatusTracker("root")
    visible = StreamStatusTracker("root", show_thinking=True)
    events = (
        (
            "llm:stream_block_start",
            {"session_id": "root", "block_index": 0, "block_type": "thinking"},
        ),
        (
            "llm:stream_block_delta",
            {"session_id": "root", "block_index": 0, "text": "private"},
        ),
    )
    for event, data in events:
        hidden.consume(event, data)
        visible.consume(event, data)

    assert hidden.preview is None
    assert visible.preview == StreamPreview("thinking", "private")


def test_provider_error_clears_stream_that_never_received_a_delta():
    tracker = StreamStatusTracker("root")
    tracker.consume(
        "llm:stream_block_start",
        {"session_id": "root", "request_id": "request", "block_index": 0},
    )
    assert tracker.preview == StreamPreview("text", "")

    tracker.consume("provider:error", {"session_id": "root"})

    assert tracker.preview is None


def test_tool_stream_blocks_never_replace_text_preview():
    tracker = StreamStatusTracker("root")
    tracker.consume(
        "llm:stream_block_delta",
        {
            "session_id": "root",
            "request_id": "request",
            "block_index": 0,
            "block_type": "text",
            "text": "answer",
        },
    )
    tracker.consume(
        "llm:stream_block_start",
        {
            "session_id": "root",
            "request_id": "request",
            "block_index": 1,
            "block_type": "tool_use",
        },
    )
    tracker.consume(
        "llm:stream_block_delta",
        {
            "session_id": "root",
            "request_id": "request",
            "block_index": 1,
            "text": '[{"path":"secret"}]',
        },
    )

    assert tracker.preview == StreamPreview("text", "answer")


def test_stream_state_bounds_blocks_and_preview_text():
    tracker = StreamStatusTracker("root")
    for index in range(20):
        tracker.consume(
            "llm:stream_block_delta",
            {
                "session_id": "root",
                "request_id": f"request-{index}",
                "block_index": index,
                "text": "x" * 50_000,
            },
        )

    assert len(tracker._blocks) <= 8
    assert tracker.preview is not None
    assert len(tracker.preview.text) <= 16_384


def test_layered_stream_hooks_disable_existing_legacy_painters():
    hooks = MagicMock()
    hooks.register.return_value = lambda: None
    coordinator = MagicMock()
    coordinator.get.return_value = hooks

    attach_layered_stream_hooks(coordinator, StreamStatusTracker("root"))

    removed = {call.args[0] for call in hooks.unregister.call_args_list}
    assert removed == {
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
    }
    registered_events = {call.args[0] for call in hooks.register.call_args_list}
    assert registered_events == set(StreamStatusTracker.EVENTS)


def test_runtime_tool_lifecycle_preserves_command_and_collapsed_result_metadata():
    tick = [10.0]
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    tracker = RuntimeStatusTracker(
        "root",
        wall_clock=lambda: now,
        monotonic_clock=lambda: tick[0],
    )
    notifications = []
    tracker.add_listener(lambda: notifications.append(True))

    tracker.consume(
        "tool:pre",
        {
            "session_id": "root",
            "tool_name": "bash\x1b[31m",
            "tool_call_id": "call-1",
            "parallel_group_id": "group-1",
            "tool_input": {
                "command": "printf '\x1b[31mhello\x1b[0m'",
                "description": "Run the command\u202e",
                "api_key": "must-not-render",
            },
        },
    )
    tick[0] = 12.5
    running = tracker.tool_snapshot()[0]

    assert running.status == ToolActivityStatus.RUNNING
    assert running.duration_seconds == 2.5
    assert running.tool_name == "bash"
    assert "\x1b" not in running.command
    assert "\u202e" not in running.summary
    assert "must-not-render" not in running.input.preview
    assert "[redacted]" in running.input.preview

    tick[0] = 15.0
    output = "line\n" * 2_000
    tracker.consume(
        "tool:post",
        {
            "tool_name": "bash",
            "tool_call_id": "call-1",
            "result": {
                "success": True,
                "output": {"stdout": output, "stderr": "", "returncode": 0},
            },
        },
    )
    completed = tracker.tool_snapshot()[0]

    assert completed.status == ToolActivityStatus.SUCCEEDED
    assert completed.terminal is True
    assert completed.duration_seconds == 5.0
    assert completed.result is not None
    assert completed.result.truncated is True
    assert completed.result.source_lines == 2_001
    assert len(completed.result.preview) == 4_096
    assert notifications == [True, True]
    with pytest.raises(FrozenInstanceError):
        completed.status = ToolActivityStatus.FAILED  # type: ignore[misc]


def test_runtime_tool_failures_recover_without_pre_and_do_not_reopen():
    tracker = RuntimeStatusTracker("root")
    tracker.consume(
        "tool:post",
        {
            "session_id": "child_worker",
            "tool_name": "filesystem",
            "tool_call_id": "missing-pre",
            "tool_input": {"path": "/tmp/item"},
            "result": {"success": False, "error": {"message": "denied"}},
        },
    )
    tracker.consume(
        "tool:pre",
        {
            "session_id": "child_worker",
            "tool_name": "filesystem",
            "tool_call_id": "missing-pre",
        },
    )
    tracker.consume("tool:pre", {"tool_name": "ignored-without-id"})
    tracker.consume(
        "tool:post",
        {
            "session_id": "child_worker",
            "tool_name": "filesystem",
            "tool_call_id": "missing-pre",
            "result": {"success": True, "output": "late duplicate"},
        },
    )

    tool = tracker.tool_snapshot()[0]
    assert tool.status == ToolActivityStatus.FAILED
    assert tool.session_id == "child_worker"
    assert tool.summary == "/tmp/item"
    assert tool.result is not None and "denied" in tool.result.preview
    assert tool.completed_at is not None


def test_runtime_tool_state_is_strictly_bounded():
    tracker = RuntimeStatusTracker("root", max_tools=3)
    for index in range(8):
        payload = {
            "tool_name": "read",
            "tool_call_id": f"call-{index}",
            "tool_input": {"path": f"/tmp/{index}"},
        }
        tracker.consume("tool:pre", payload)
        tracker.consume("tool:post", {**payload, "result": {"success": True}})

    assert [tool.tool_call_id for tool in tracker.tool_snapshot()] == [
        "call-5",
        "call-6",
        "call-7",
    ]


def test_prompt_completion_discards_denied_tools_without_terminal_events():
    tracker = RuntimeStatusTracker("root")
    tracker.consume(
        "tool:pre",
        {
            "session_id": "root",
            "tool_name": "load_skill",
            "tool_call_id": "denied-call",
        },
    )
    tracker.consume(
        "tool:pre",
        {
            "session_id": "child",
            "tool_name": "todo",
            "tool_call_id": "orphaned-child-call",
        },
    )

    assert len(tracker.tool_snapshot()) == 2

    tracker.consume("prompt:complete", {"session_id": "root"})

    assert tracker.tool_snapshot() == ()


def test_child_prompt_completion_discards_only_that_child_running_tools():
    tracker = RuntimeStatusTracker("root")
    for session_id in ("root", "child", "sibling"):
        tracker.consume(
            "tool:pre",
            {
                "session_id": session_id,
                "tool_name": "read",
                "tool_call_id": f"{session_id}-call",
            },
        )

    tracker.consume("prompt:complete", {"session_id": "child"})

    assert {tool.session_id for tool in tracker.tool_snapshot()} == {
        "root",
        "sibling",
    }


def test_runtime_tracker_propagates_idempotently_to_children_and_grandchildren():
    class Hooks:
        def __init__(self):
            self.registered = []

        def register(self, event, handler, *, priority=0, name=None):
            self.registered.append((event, handler, priority, name))
            return lambda: None

    class Coordinator:
        def __init__(self, tracker=None):
            self.capabilities = {}
            if tracker is not None:
                self.capabilities[RUNTIME_STATUS_CAPABILITY] = tracker
            self.hooks = Hooks()

        def get_capability(self, name):
            return self.capabilities.get(name)

        def register_capability(self, name, value):
            self.capabilities[name] = value

        def get(self, name):
            return self.hooks if name == "hooks" else None

    tracker = RuntimeStatusTracker("root")
    root = type("Session", (), {"coordinator": Coordinator(tracker)})()
    child = type("Session", (), {"coordinator": Coordinator()})()
    grandchild = type("Session", (), {"coordinator": Coordinator()})()

    _propagate_runtime_status_tracker(root, child)
    _propagate_runtime_status_tracker(root, child)
    _propagate_runtime_status_tracker(child, grandchild)

    assert child.coordinator.get_capability(RUNTIME_STATUS_CAPABILITY) is tracker
    assert grandchild.coordinator.get_capability(RUNTIME_STATUS_CAPABILITY) is tracker
    assert len(child.coordinator.hooks.registered) == len(tracker.EVENTS)
    assert len(grandchild.coordinator.hooks.registered) == len(tracker.EVENTS)


def test_runtime_telemetry_tracks_turn_session_cache_and_resumed_cost():
    tracker = RuntimeStatusTracker("root")
    tracker.seed_session_cost("1.00")
    tracker.consume("prompt:submit", {"session_id": "root"})
    tracker.consume(
        "llm:response",
        {
            "session_id": "root",
            "provider": "openai",
            "model": "gpt-test",
            "status": "ok",
            "duration_ms": 1_250,
            "usage": {
                "input_tokens": 1_000,
                "output_tokens": 200,
                "cache_read_tokens": 900,
                "reasoning_tokens": 25,
                "cost_usd": "0.12",
            },
        },
    )
    telemetry = tracker.telemetry_snapshot()

    assert telemetry.last_request is not None
    assert telemetry.last_request.cache_percent == 90
    assert telemetry.last_request.duration_seconds == 1.25
    assert telemetry.turn.request_count == 1
    assert telemetry.turn.total_tokens == 1_200
    assert telemetry.turn.cost_usd == Decimal("0.12")
    assert telemetry.session.cost_usd == Decimal("1.12")
    assert telemetry.session.cost_complete is True

    tracker.consume(
        "llm:response",
        {
            "session_id": "child_agent",
            "provider": "anthropic",
            "model": "claude-test",
            "usage": {"input_tokens": 50, "output_tokens": 10, "cost_usd": None},
        },
    )
    tracker.consume("prompt:submit", {"session_id": "child_agent"})
    assert tracker.telemetry_snapshot().turn.request_count == 2
    assert tracker.telemetry_snapshot().session.cost_complete is False

    tracker.consume("prompt:submit", {"session_id": "root"})
    telemetry = tracker.telemetry_snapshot()
    assert telemetry.turn.request_count == 0
    assert telemetry.last_request is None
    assert telemetry.session.request_count == 2
    assert telemetry.session.cost_usd == Decimal("1.12")


def test_runtime_telemetry_deduplicates_content_usage_and_accepts_fallback_shape():
    tracker = RuntimeStatusTracker("root")
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 80,
        "cache_write_tokens": 5,
        "cost_usd": "0.01",
    }
    tracker.consume("llm:response", {"session_id": "root", "usage": usage})
    tracker.consume(
        "content_block:end",
        {
            "session_id": "root",
            "block_index": 1,
            "total_blocks": 2,
            "usage": usage,
        },
    )
    assert tracker.telemetry_snapshot().session.request_count == 1

    tracker.consume("prompt:submit", {"session_id": "root"})
    tracker.consume(
        "content_block:end",
        {
            "session_id": "root",
            "block_index": 0,
            "total_blocks": 1,
            "usage": {
                "input": "40",
                "output": "2",
                "cache_read_input_tokens": "20",
                "cost_usd": "not-a-cost",
            },
        },
    )
    telemetry = tracker.telemetry_snapshot()
    assert telemetry.turn.request_count == 1
    assert telemetry.turn.total_tokens == 42
    assert telemetry.turn.cache_read_tokens == 20
    assert telemetry.turn.cost_usd is None
    assert telemetry.session.request_count == 2


@pytest.mark.asyncio
async def test_runtime_tracker_registers_known_hooks_and_returns_continue():
    hooks = MagicMock()
    unregister = MagicMock()
    hooks.register.return_value = unregister
    tracker = RuntimeStatusTracker("root")

    unregister_all = tracker.register_hooks(hooks)
    result = await tracker.handle_event(
        "llm:response", {"usage": {"input_tokens": 1, "output_tokens": 1}}
    )
    unregister_all()

    assert {call.args[0] for call in hooks.register.call_args_list} == set(
        RuntimeStatusTracker.EVENTS
    )
    assert result.action == "continue"
    assert unregister.call_count == len(RuntimeStatusTracker.EVENTS)
