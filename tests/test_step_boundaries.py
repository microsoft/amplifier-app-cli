import pytest

from amplifier_app_cli.ui.interaction_state import NeedsYouQueue, SteeringQueue
from amplifier_app_cli.ui.step_boundaries import StepBoundaryBridge


@pytest.mark.asyncio
async def test_steer_is_injected_once_at_next_root_provider_boundary() -> None:
    queue = SteeringQueue(clock=lambda: 1.0)
    steer = queue.enqueue("use sqlite, not json")
    applied = []
    bridge = StepBoundaryBridge("root", queue, on_applied=applied.append)

    child = await bridge.handle_event("provider:request", {"session_id": "child"})
    root = await bridge.handle_event("provider:request", {"session_id": "root"})
    later = await bridge.handle_event("provider:request", {"session_id": "root"})

    assert child.action == "continue"
    assert root.action == "inject_context"
    assert root.context_injection_role == "user"
    assert "use sqlite, not json" in root.context_injection
    assert root.suppress_output is True
    assert applied == [steer]
    assert later.action == "continue"


@pytest.mark.asyncio
async def test_steering_preserves_multiline_user_text() -> None:
    queue = SteeringQueue()
    queue.enqueue("first line\nsecond line")
    bridge = StepBoundaryBridge("root", queue)

    result = await bridge.handle_event("provider:request", {})

    assert result.context_injection.endswith("first line\nsecond line")


@pytest.mark.asyncio
async def test_answered_decisions_are_consumed_at_same_safe_boundary() -> None:
    queue = NeedsYouQueue()
    decision = queue.defer("Use Postgres?", "storage choice")
    queue.answer(decision.decision_id, "Use SQLite")
    applied = []
    bridge = StepBoundaryBridge(
        "root",
        SteeringQueue(),
        needs_you=queue,
        on_answers=applied.append,
    )

    result = await bridge.handle_event("provider:request", {"session_id": "root"})

    assert result.action == "inject_context"
    assert "Use Postgres?" in result.context_injection
    assert "Answer: Use SQLite" in result.context_injection
    assert applied[0][0].decision_id == decision.decision_id
    assert queue.answered == ()
