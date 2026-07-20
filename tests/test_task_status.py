"""Tests for live task and todo state used by the layered REPL."""

from __future__ import annotations

from amplifier_app_cli.ui.task_status import TaskStatus
from amplifier_app_cli.ui.task_status import TaskStatusTracker
from amplifier_app_cli.ui.task_hooks import attach_task_status_hooks
from amplifier_app_cli.session_spawner import _propagate_task_status_tracker


def test_delegate_lifecycle_builds_parent_child_tree_and_counts():
    tracker = TaskStatusTracker("root-session")
    notifications = []
    tracker.add_listener(lambda: notifications.append(True))

    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "planner",
            "sub_session_id": "child_planner",
            "parent_session_id": "root-session",
        },
    )
    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "tester",
            "sub_session_id": "grandchild_tester",
            "parent_session_id": "child_planner",
        },
    )
    tracker.consume(
        "delegate:agent_completed",
        {
            "agent": "planner",
            "sub_session_id": "child_planner",
            "parent_session_id": "root-session",
            "success": True,
        },
    )

    rows = tracker.tree_rows()
    assert [row.node.agent for row in rows] == ["planner", "tester"]
    assert rows[0].prefix == "`- "
    assert rows[1].prefix == "   `- "
    assert tracker.counts().running == 1
    assert tracker.counts().completed == 1
    assert len(notifications) == 3


def test_terminal_state_ignores_late_spawn_but_resume_reopens_task():
    tracker = TaskStatusTracker("root")
    payload = {"agent": "coder", "sub_session_id": "child_coder"}

    tracker.consume("delegate:agent_completed", payload)
    tracker.consume("delegate:agent_spawned", payload)
    assert tracker.nodes()[0].status == TaskStatus.COMPLETED

    tracker.consume("delegate:agent_resumed", payload)
    assert tracker.nodes()[0].status == TaskStatus.RUNNING


def test_session_events_normalize_failure_and_cancelled_statuses():
    tracker = TaskStatusTracker("root")
    tracker.consume(
        "session:fork",
        {
            "child_session_id": "child-reviewer",
            "parent_session_id": "root",
            "agent_name": "reviewer",
        },
    )
    tracker.consume(
        "session:end", {"session_id": "child-reviewer", "status": "incomplete"}
    )
    tracker.consume(
        "delegate:agent_cancelled",
        {"sub_session_id": "child-cancelled", "agent": "researcher"},
    )

    statuses = {node.agent: node.status for node in tracker.nodes()}
    assert statuses == {
        "reviewer": TaskStatus.INCOMPLETE,
        "researcher": TaskStatus.CANCELLED,
    }


def test_todo_snapshot_stays_separate_and_footer_reports_both_feeds():
    todo_state = [
        {"content": "Inspect", "activeForm": "Inspecting", "status": "completed"},
        {"content": "Test", "activeForm": "Testing", "status": "in_progress"},
    ]
    tracker = TaskStatusTracker("root", todo_source=lambda: todo_state)
    tracker.consume(
        "delegate:agent_spawned",
        {"agent": "tester", "sub_session_id": "child_tester"},
    )

    assert [todo.display_text for todo in tracker.todo_snapshot()] == [
        "Inspect",
        "Testing",
    ]
    assert len(tracker.nodes()) == 1
    assert tracker.footer_summary() == "todo 1/2 | agents 1 running"


def test_plan_snapshot_exposes_active_step_and_progress_immutably():
    tracker = TaskStatusTracker("root")
    tracker.set_todos(
        [
            {
                "content": "Inspect paths",
                "activeForm": "Inspecting paths",
                "status": "completed",
            },
            {
                "content": "Migrate history",
                "activeForm": "Migrating history",
                "status": "in_progress",
            },
            {
                "content": "Verify",
                "activeForm": "Verifying",
                "status": "pending",
            },
        ]
    )

    snapshot = tracker.plan_snapshot()

    assert snapshot.completed_count == 1
    assert snapshot.active_text == "Migrating history"
    assert tracker.active_step_text() == "Migrating history"
    assert isinstance(snapshot.items, tuple)


def test_tool_events_attach_delegate_instruction_and_commit_todo_update():
    tracker = TaskStatusTracker("root")
    tracker.consume(
        "tool:pre",
        {
            "tool_name": "delegate",
            "tool_call_id": "call-1",
            "tool_input": {"instruction": "Check the parser"},
        },
    )
    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "reviewer",
            "sub_session_id": "child_reviewer",
            "tool_call_id": "call-1",
        },
    )
    tracker.consume(
        "tool:pre",
        {
            "tool_name": "todo",
            "tool_input": {
                "todos": [
                    {
                        "content": "Run tests",
                        "activeForm": "Running tests",
                        "status": "in_progress",
                    }
                ]
            },
        },
    )
    tracker.consume("tool:post", {"tool_name": "todo", "result": {"output": {}}})

    assert tracker.nodes()[0].summary == "Check the parser"
    assert tracker.todo_snapshot()[0].display_text == "Running tests"


def test_nested_delegate_tool_post_preserves_emitting_session_as_parent():
    tracker = TaskStatusTracker("root")
    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "planner",
            "sub_session_id": "child_planner",
            "parent_session_id": "root",
        },
    )
    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "tester",
            "sub_session_id": "grandchild_tester",
            "parent_session_id": "child_planner",
        },
    )

    tracker.consume(
        "tool:post",
        {
            "session_id": "child_planner",
            "tool_name": "delegate",
            "result": {
                "output": {
                    "session_id": "grandchild_tester",
                    "agent": "tester",
                    "status": "success",
                }
            },
        },
    )

    grandchild = next(
        node for node in tracker.nodes() if node.session_id == "grandchild_tester"
    )
    assert grandchild.parent_id == "child_planner"
    assert [row.prefix for row in tracker.tree_rows()] == ["`- ", "   `- "]


def test_delegate_tool_post_without_emitter_preserves_current_parent():
    tracker = TaskStatusTracker("root")
    tracker.consume(
        "delegate:agent_spawned",
        {
            "agent": "tester",
            "sub_session_id": "grandchild_tester",
            "parent_session_id": "child_planner",
        },
    )

    tracker.consume(
        "tool:post",
        {
            "tool_name": "delegate",
            "result": {
                "output": {
                    "session_id": "grandchild_tester",
                    "agent": "tester",
                    "status": "success",
                }
            },
        },
    )

    assert tracker.nodes()[0].parent_id == "child_planner"


def test_child_todo_events_do_not_replace_root_todos():
    tracker = TaskStatusTracker("root")
    root_todos = [
        {
            "content": "Inspect root",
            "activeForm": "Inspecting root",
            "status": "in_progress",
        }
    ]
    child_todos = [
        {
            "content": "Inspect child",
            "activeForm": "Inspecting child",
            "status": "in_progress",
        }
    ]
    tracker.consume(
        "tool:pre",
        {
            "session_id": "root",
            "tool_name": "todo",
            "tool_input": {"todos": root_todos},
        },
    )
    tracker.consume(
        "tool:post",
        {"session_id": "root", "tool_name": "todo", "result": {"output": {}}},
    )

    tracker.consume(
        "tool:pre",
        {
            "session_id": "child_planner",
            "tool_name": "todo",
            "tool_input": {"todos": child_todos},
        },
    )
    tracker.consume(
        "tool:post",
        {
            "session_id": "child_planner",
            "tool_name": "todo",
            "result": {"output": {"todos": child_todos}},
        },
    )

    assert [todo.content for todo in tracker.todo_snapshot()] == ["Inspect root"]


def test_task_tracker_propagates_to_child_and_replaces_todo_panels():
    class Hooks:
        def __init__(self):
            self.registered = []
            self.unregistered = []

        def register(self, event, handler, *, priority=0, name=None):
            self.registered.append((event, handler, priority, name))

        def unregister(self, name):
            self.unregistered.append(name)

    class Coordinator:
        def __init__(self, tracker=None):
            self.tracker = tracker
            self.hooks = Hooks()
            self.capabilities = {}

        def get_capability(self, name):
            return self.tracker if name == "ui.task_status_tracker" else None

        def register_capability(self, name, value):
            self.capabilities[name] = value

        def get(self, name):
            return self.hooks if name == "hooks" else None

    tracker = TaskStatusTracker("root")
    parent = type("Session", (), {"coordinator": Coordinator(tracker)})()
    child = type("Session", (), {"coordinator": Coordinator()})()

    _propagate_task_status_tracker(parent, child)

    assert child.coordinator.capabilities["ui.task_status_tracker"] is tracker
    assert {event for event, *_ in child.coordinator.hooks.registered} == set(
        tracker.EVENTS
    )
    removed = set(child.coordinator.hooks.unregistered)
    assert {"hooks-todo-display-pre", "hooks-todo-display-post"} <= removed
    assert {
        "streaming-ui-content-block-start",
        "streaming-ui-content-block-end",
        "streaming-ui-tool-pre",
        "streaming-ui-tool-post",
        "streaming-ui-overlay-start",
        "streaming-ui-overlay-delta",
        "streaming-ui-overlay-end",
    } <= removed


def test_task_hook_wiring_disables_legacy_streaming_tool_output():
    calls = []

    class StreamingUI:
        async def handle_tool_pre(self, event, data):
            calls.append((event, data["tool_name"]))
            return None

        async def handle_tool_post(self, event, data):
            calls.append((event, data["tool_name"]))
            return None

    class Hooks:
        def __init__(self):
            self.handlers = {}

        def register(self, event, handler, *, priority=0, name=None):
            self.handlers[name] = handler

        def unregister(self, name):
            self.handlers.pop(name, None)

    class Coordinator:
        def __init__(self):
            self.hooks = Hooks()
            self.streaming = StreamingUI()

        def register_capability(self, name, value):
            pass

        def get_capability(self, name):
            return self.streaming if name == "ui.streaming_hooks" else None

        def get(self, name):
            return self.hooks if name == "hooks" else None

    coordinator = Coordinator()
    attach_task_status_hooks(coordinator, TaskStatusTracker("root"))
    assert "streaming-ui-tool-pre" not in coordinator.hooks.handlers
    assert "streaming-ui-tool-post" not in coordinator.hooks.handlers
    assert calls == []


def test_incomplete_session_end_survives_later_delegate_cancellation():
    tracker = TaskStatusTracker("root")
    tracker.consume(
        "session:fork",
        {
            "child_session_id": "subprocess-child",
            "parent_session_id": "root",
            "agent_name": "worker",
        },
    )
    tracker.consume(
        "session:end",
        {
            "session_id": "subprocess-child",
            "status": "incomplete",
            "success": False,
        },
    )
    tracker.consume(
        "delegate:agent_cancelled",
        {"sub_session_id": "subprocess-child", "agent": "worker"},
    )

    node = next(
        node for node in tracker.nodes() if node.session_id == "subprocess-child"
    )
    assert node.status.value == "incomplete"


def test_task_tracker_bounds_deep_running_graphs():
    tracker = TaskStatusTracker("root")
    parent_id = "root"
    for index in range(1_100):
        child_id = f"child-{index}"
        tracker.consume(
            "session:fork",
            {
                "child_session_id": child_id,
                "parent_session_id": parent_id,
                "agent_name": "worker",
            },
        )
        parent_id = child_id

    assert len(tracker.nodes()) <= 512
    assert len(tracker.tree_rows()) <= 512
