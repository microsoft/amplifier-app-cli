import pytest

from amplifier_app_cli.ui.interaction_state import NeedsYouQueue
from amplifier_app_cli.ui.interaction_state import PermissionDecision
from amplifier_app_cli.ui.interaction_state import PermissionSlot
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.interaction_state import TRUST_POLICY_VERSION
from amplifier_app_cli.ui.interaction_state import TrustState


def test_default_trust_presets_encode_mode_boundaries() -> None:
    trust = TrustState(initial="build")

    assert trust.active.decision_for(PermissionSlot.READ) == PermissionDecision.AUTO
    assert trust.active.decision_for(PermissionSlot.TEST) == PermissionDecision.AUTO
    assert trust.active.decision_for(PermissionSlot.WRITE) == PermissionDecision.ASK
    assert trust.active.summary() == (
        "auto read,test · ask net,outside-project,spend,subagent,write"
    )
    assert trust.activate("auto").summary() == "classifier-gated"


def test_plan_and_brainstorm_presets_block_mutating_tools() -> None:
    trust = TrustState(initial="plan")
    assert trust.active.decision_for(PermissionSlot.READ) == PermissionDecision.AUTO
    assert trust.active.decision_for(PermissionSlot.WRITE) == PermissionDecision.BLOCK

    trust.activate("brainstorm")
    assert all(
        trust.active.decision_for(slot) == PermissionDecision.BLOCK
        for slot in PermissionSlot
    )


def test_bypass_preset_is_explicitly_unrestricted_and_high_risk() -> None:
    trust = TrustState(initial="bypass")

    assert all(
        trust.active.decision_for(slot) == PermissionDecision.AUTO
        for slot in PermissionSlot
    )
    assert trust.active.requires_risk_treatment is True


def test_permission_cycle_is_independent_and_skips_brainstorm() -> None:
    trust = TrustState()

    assert trust.cycle().name == "build"
    assert trust.cycle().name == "plan"
    assert trust.cycle().name == "auto"
    assert trust.cycle().name == "bypass"
    assert trust.cycle().name == "chat"

    trust.activate("brainstorm")
    assert trust.cycle().name == "chat"


def test_trust_state_notifies_only_on_change() -> None:
    trust = TrustState()
    changes = []
    trust.add_listener(lambda: changes.append(trust.active.name))

    trust.activate("chat")
    trust.activate("build")

    assert changes == ["build"]


def test_trust_slot_edit_creates_a_disjoint_custom_preset() -> None:
    state = TrustState(initial="build")

    custom = state.set_slot(PermissionSlot.WRITE, PermissionDecision.AUTO)

    assert custom.name == "custom"
    assert custom.decision_for(PermissionSlot.WRITE) == PermissionDecision.AUTO
    assert PermissionSlot.WRITE not in custom.ask
    assert state.active is custom


def test_custom_trust_posture_round_trips_complete_slot_state() -> None:
    original = TrustState(initial="build")
    original.set_slot(PermissionSlot.WRITE, PermissionDecision.AUTO)
    original.set_slot(PermissionSlot.NETWORK, PermissionDecision.BLOCK)

    restored = TrustState(initial="bypass")
    restored.restore(original.snapshot())

    assert restored.active.name == "custom"
    assert restored.active.decision_for(PermissionSlot.WRITE) == PermissionDecision.AUTO
    assert (
        restored.active.decision_for(PermissionSlot.NETWORK) == PermissionDecision.BLOCK
    )
    assert restored.snapshot() == original.snapshot()


def test_missing_persisted_posture_keeps_safe_chat_default() -> None:
    state = TrustState()

    assert state.restore_persisted(None, None) is False
    assert state.active.name == "chat"
    assert state.bypass_permissions is False


def test_explicit_persisted_bypass_is_restored() -> None:
    state = TrustState()

    assert (
        state.restore_persisted(
            None,
            "bypass",
            policy_version=TRUST_POLICY_VERSION,
        )
        is True
    )
    assert state.active.name == "bypass"
    assert state.bypass_permissions is True


def test_root_resume_downgrades_legacy_implicit_bypass_to_chat() -> None:
    state = TrustState()
    legacy_profile = TrustState(initial="bypass").snapshot()

    assert state.restore_persisted(legacy_profile, "bypass") is False
    assert state.active.name == "chat"
    assert state.bypass_permissions is False


def test_risk_treatment_only_tracks_auto_network_or_spend() -> None:
    state = TrustState(initial="build")
    assert state.active.requires_risk_treatment is False

    custom = state.set_slot(PermissionSlot.NETWORK, PermissionDecision.AUTO)
    assert custom.requires_risk_treatment is True


def test_editing_classifier_gated_preset_keeps_conservative_boundaries() -> None:
    state = TrustState(initial="auto")

    custom = state.set_slot(PermissionSlot.NETWORK, PermissionDecision.BLOCK)

    assert custom.classifier_gated is False
    assert custom.decision_for(PermissionSlot.READ) == PermissionDecision.AUTO
    assert custom.decision_for(PermissionSlot.NETWORK) == PermissionDecision.BLOCK
    assert custom.decision_for(PermissionSlot.SPEND) == PermissionDecision.ASK


def test_needs_you_queue_defers_and_batch_answers_without_blocking() -> None:
    queue = NeedsYouQueue(clock=lambda: 42.0)
    first = queue.defer("Push to your fork?", "origin is outside trust boundary")
    second = queue.defer("Publish release?", "spend approval required")

    assert queue.pending_count == 2
    answered = queue.answer_many(
        {first.decision_id: "yes", second.decision_id: "not yet"}
    )

    assert [decision.answer for decision in answered] == ["yes", "not yet"]
    assert queue.pending_count == 0
    assert queue.answered == answered
    consumed = queue.consume_answered()
    assert [decision.answer for decision in consumed] == ["yes", "not yet"]
    assert queue.answered == ()


def test_needs_you_blocks_only_declared_dependent_work() -> None:
    queue = NeedsYouQueue()
    decision = queue.defer(
        "Publish now?",
        "release timing needs judgment",
        dependencies=("publish-release",),
    )

    assert queue.dependency_blocked("publish-release") is True
    assert queue.dependency_blocked("run-tests") is False

    queue.answer(decision.decision_id, "yes")
    assert queue.dependency_blocked("publish-release") is True

    queue.consume_answered()
    assert queue.dependency_blocked("publish-release") is False


def test_needs_you_queue_rejects_duplicate_or_unknown_answers() -> None:
    queue = NeedsYouQueue()
    decision = queue.defer("Continue?", "permission")
    queue.answer(decision.decision_id, "yes")

    with pytest.raises(ValueError, match="already answered"):
        queue.answer(decision.decision_id, "again")
    with pytest.raises(KeyError):
        queue.answer("missing", "yes")


def test_batch_answers_are_atomic_when_any_decision_is_invalid() -> None:
    queue = NeedsYouQueue()
    first = queue.defer("Continue?", "permission")

    with pytest.raises(KeyError):
        queue.answer_many({first.decision_id: "yes", "missing": "no"})

    assert queue.pending == (first,)
    assert queue.answered == ()


def test_steering_queue_consumes_fifo_at_step_boundaries() -> None:
    queue = SteeringQueue(clock=lambda: 12.0)
    first = queue.enqueue("keep the public API")
    second = queue.enqueue("also run integration tests")

    assert queue.pending == (first, second)
    assert queue.consume_next() == first
    assert queue.consume_next() == second
    assert queue.consume_next() is None


def test_steering_strips_terminal_controls_but_preserves_multiline_text() -> None:
    queue = SteeringQueue()

    steer = queue.enqueue("first\nsecond\x1b")

    assert steer.text == "first\nsecond"


def test_steering_preserves_separate_compact_display_text() -> None:
    queue = SteeringQueue()
    payload = "line\n" * 20

    steer = queue.enqueue(
        payload,
        display_text="[Pasted #1 · 20 lines]",
    )

    assert steer.text == payload
    assert steer.display_text == "[Pasted #1 · 20 lines]"
