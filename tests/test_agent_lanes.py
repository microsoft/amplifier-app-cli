from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from prompt_toolkit.utils import get_cwidth

from amplifier_app_cli.ui.agent_lanes import AgentLaneViewModel
from amplifier_app_cli.ui.agent_lanes import AgentTestOutcome
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui.task_status import TaskStatus
from amplifier_app_cli.ui.task_status import TaskStatusTracker


def _spawn(
    tracker: TaskStatusTracker,
    session_id: str,
    agent: str,
    summary: str,
    *,
    parent: str = "root",
) -> None:
    tracker.consume(
        "delegate:agent_spawned",
        {
            "sub_session_id": session_id,
            "parent_session_id": parent,
            "agent": agent,
            "instruction": summary,
        },
    )


def _usage(runtime: RuntimeStatusTracker, session_id: str, cost: str) -> None:
    runtime.consume(
        "llm:response",
        {
            "session_id": session_id,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cost_usd": cost,
            },
        },
    )


def test_lane_snapshot_combines_status_current_tool_cost_and_test_outcome():
    base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    tasks = TaskStatusTracker("root")
    runtime = RuntimeStatusTracker("root", wall_clock=lambda: base)
    _spawn(tasks, "child-research", "researcher", "Review sources")
    _spawn(tasks, "child-code", "coder", "Migrating store")
    _spawn(tasks, "child-test", "tester", "Run tests")

    runtime.consume(
        "tool:pre",
        {
            "session_id": "child-research",
            "tool_call_id": "docs",
            "tool_name": "web",
            "tool_input": {"description": "Scanning provider docs"},
        },
    )
    runtime.consume(
        "tool:pre",
        {
            "session_id": "child-test",
            "tool_call_id": "tests",
            "tool_name": "bash",
            "tool_input": {"command": "uv run pytest -q"},
        },
    )
    runtime.consume(
        "tool:post",
        {
            "session_id": "child-test",
            "tool_call_id": "tests",
            "tool_name": "bash",
            "result": {
                "success": True,
                "output": {"stdout": "32 passed", "returncode": 0},
            },
        },
    )
    tasks.consume(
        "delegate:agent_completed",
        {"sub_session_id": "child-test", "agent": "tester", "success": True},
    )
    _usage(runtime, "child-research", "0.09")
    _usage(runtime, "child-code", "0.31")
    _usage(runtime, "child-test", "0.07")

    for index, node in enumerate(tasks.nodes()):
        node.started_at = base - timedelta(seconds=(41, 120, 55)[index])
        if node.status != TaskStatus.RUNNING:
            node.updated_at = base

    model = AgentLaneViewModel(tasks, runtime, clock=lambda: base)
    snapshot = model.snapshot()
    researcher, coder, tester = snapshot.lanes

    assert researcher.glyph == "◐"
    assert researcher.summary == "Scanning provider docs"
    assert researcher.elapsed_seconds == 41
    assert researcher.cost_usd == Decimal("0.09")
    assert coder.glyph == "■"
    assert coder.summary == "Migrating store"
    assert coder.elapsed_seconds == 120
    assert tester.glyph == "✔"
    assert tester.summary == "done"
    assert tester.test_outcome == AgentTestOutcome.PASSED
    assert tester.cost_usd == Decimal("0.07")

    lines = snapshot.render_lines(max_columns=96)
    assert "◐ researcher · Scanning provider docs · 41s · $0.09" in lines[0]
    assert "■ coder" in lines[1] and "Migrating store · 2m · $0.31" in lines[1]
    assert "✔ tester" in lines[2] and "done · tests ✔ · 55s · $0.07" in lines[2]
    assert all("\n" not in line and get_cwidth(line) <= 96 for line in lines)

    with pytest.raises(FrozenInstanceError):
        researcher.agent = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        runtime.snapshot().session_usage[0].session_id = "changed"  # type: ignore[misc]


def test_lane_selection_wraps_and_enter_esc_follow_parent_chain():
    tasks = TaskStatusTracker("root")
    _spawn(tasks, "child-parent", "planner", "Plan")
    _spawn(
        tasks,
        "child-leaf",
        "tester",
        "Verify",
        parent="child-parent",
    )
    model = AgentLaneViewModel(tasks)
    changes = []
    model.add_listener(lambda: changes.append(True))

    assert model.snapshot().selected_session_id == "child-parent"
    assert model.select_next().selected_session_id == "child-leaf"
    assert model.select_next().selected_session_id == "child-parent"
    assert model.select_previous().selected_session_id == "child-leaf"

    assert model.focus_selected() == "child-leaf"
    focused = model.snapshot()
    assert focused.focused_session_id == "child-leaf"
    assert focused.focused_parent_session_id == "child-parent"
    assert (
        next(lane for lane in focused.lanes if lane.focused).session_id == "child-leaf"
    )
    assert model.focus_parent() == "child-parent"
    assert model.focus_parent() == "root"
    assert len(changes) == 6


def test_lane_board_is_bounded_sanitized_and_keeps_explicit_selection():
    tasks = TaskStatusTracker("root")
    for index in range(7):
        _spawn(
            tasks,
            f"child-{index}",
            f"agent-{index}\x1b[31m",
            "work " + "x" * 500,
        )

    model = AgentLaneViewModel(tasks, max_lanes=3)
    initial = model.snapshot()
    assert len(initial.lanes) == 3
    assert all("\x1b" not in lane.agent for lane in initial.lanes)
    selected = model.select("child-0")
    assert len(selected.lanes) == 3
    assert selected.selected_session_id == "child-0"
    assert "child-0" in {lane.session_id for lane in selected.lanes}
    assert all(len(lane.summary) <= 192 for lane in selected.lanes)
    assert all(get_cwidth(line) <= 48 for line in selected.render_lines(max_columns=48))
    assert all(get_cwidth(line) <= 8 for line in selected.render_lines(max_columns=8))


def test_lane_test_failure_and_terminal_elapsed_are_explicit():
    base = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    tasks = TaskStatusTracker("root")
    runtime = RuntimeStatusTracker("root", wall_clock=lambda: base)
    _spawn(tasks, "child-test", "tester", "Run checks")
    runtime.consume(
        "tool:post",
        {
            "session_id": "child-test",
            "tool_call_id": "test-call",
            "tool_name": "bash",
            "tool_input": {"command": "npm test"},
            "result": {"success": False, "error": "failed"},
        },
    )
    tasks.consume(
        "session:end",
        {
            "session_id": "child-test",
            "parent_session_id": "root",
            "agent_name": "tester",
            "status": "failed",
        },
    )
    node = tasks.nodes()[0]
    node.started_at = base - timedelta(seconds=55)
    node.updated_at = base

    lane = (
        AgentLaneViewModel(
            tasks,
            runtime,
            clock=lambda: base + timedelta(hours=1),
        )
        .snapshot()
        .lanes[0]
    )

    assert lane.status == TaskStatus.FAILED
    assert lane.glyph == "✘"
    assert lane.elapsed_seconds == 55
    assert lane.test_outcome == AgentTestOutcome.FAILED
    assert "tests ✘ · 55s · $—" in lane.render()


def test_runtime_per_session_usage_is_bounded_and_preserves_root():
    runtime = RuntimeStatusTracker("root")
    _usage(runtime, "root", "0.01")
    for index in range(300):
        _usage(runtime, f"child-{index}", "0.01")

    session_usage = runtime.snapshot().session_usage

    assert len(session_usage) == 256
    assert session_usage[0].session_id == "root"
    assert session_usage[-1].session_id == "child-299"
