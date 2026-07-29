from amplifier_app_cli.ui.plan_sync import PlanStepSynchronizer
from amplifier_app_cli.ui.task_status import TaskStatusTracker


def test_plan_step_drives_narration_and_title_from_same_transition() -> None:
    tracker = TaskStatusTracker("root")
    narrated = []
    titles = []
    sync = PlanStepSynchronizer(
        tracker,
        on_step=narrated.append,
        on_title=titles.append,
    )

    tracker.set_todos(
        [
            {
                "content": "Migrate history",
                "activeForm": "Migrating history",
                "status": "in_progress",
            }
        ]
    )
    tracker.set_todos(
        [
            {
                "content": "Migrate history",
                "activeForm": "Migrating history",
                "status": "in_progress",
            }
        ]
    )
    tracker.set_todos([{"content": "Migrate history", "status": "completed"}])
    sync.close()

    assert narrated == ["Migrating history"]
    assert titles == ["Migrating history", "Migrating history", None]


def test_plan_step_announces_each_new_active_item_once() -> None:
    tracker = TaskStatusTracker("root")
    narrated = []
    sync = PlanStepSynchronizer(
        tracker,
        on_step=narrated.append,
        on_title=lambda active: None,
    )

    tracker.set_todos(
        [{"content": "Audit", "activeForm": "Auditing", "status": "in_progress"}]
    )
    tracker.set_todos(
        [
            {"content": "Audit", "status": "completed"},
            {"content": "Build", "activeForm": "Building", "status": "in_progress"},
        ]
    )

    assert narrated == ["Auditing", "Building"]
    sync.close()
