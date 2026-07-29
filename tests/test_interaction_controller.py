from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.ui.interaction_controller import InteractionController
from amplifier_app_cli.ui.interaction_state import PermissionDecision
from amplifier_app_cli.ui.interaction_state import PermissionSlot
from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.interaction_runtime_state import InteractionRuntimeState
from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry
from amplifier_app_cli.ui.mode_profiles import ModeRuntimeBinding


def _controller() -> tuple[
    InteractionController,
    dict[str, object],
    TrustState,
    list[str],
    list[bool],
]:
    state: dict[str, object] = {"ui.active_mode": "chat"}
    coordinator = MagicMock()
    coordinator.session_state = state
    coordinator.get.return_value = None
    coordinator.get_capability.return_value = None
    trust = TrustState()
    profiles = ModeProfileRegistry()
    binding = ModeRuntimeBinding(coordinator, profiles)
    interaction_state = InteractionRuntimeState(state, trust, ui_modes=profiles.names)
    notices: list[str] = []
    refreshes: list[bool] = []
    controller = InteractionController(
        state=interaction_state,
        profiles=profiles,
        binding=binding,
        clear_legacy_mode=AsyncMock(),
        notify=notices.append,
        refresh=lambda: refreshes.append(True),
    )
    return controller, state, trust, notices, refreshes


@pytest.mark.asyncio
async def test_pure_mode_cycle_wraps_five_modes_and_never_touches_permission() -> None:
    """Shift-Tab (``cycle()``) is now a PURE mode cycle: chat -> build -> plan
    -> auto -> brainstorm -> chat. It must never read or branch on
    permission_posture -- that coupling was the bug (ADR-0005 amendment):
    from `auto`, Shift-Tab could never reach `brainstorm` because the shared
    control forced `bypass` instead. Permission is now a fully independent
    axis (see the ``cycle_permission`` tests below)."""
    controller, state, trust, notices, refreshes = _controller()
    await controller.initialize()
    trust.activate("bypass")
    controller.mark_trust_explicit()  # explicit permission choice, independent of mode

    expected_modes = ["build", "plan", "auto", "brainstorm", "chat", "build"]
    for expected in expected_modes:
        await controller.cycle()
        assert controller.active_mode() == expected
        assert trust.active.name == "bypass"  # untouched by every mode cycle step

    assert state["ui.permission_posture"] == "bypass"
    assert notices[-1].startswith("build mode on")
    assert len(refreshes) == len(expected_modes)


@pytest.mark.asyncio
async def test_pure_permission_cycle_wraps_five_postures_and_never_touches_mode() -> (
    None
):
    """The dedicated permission control (``cycle_permission()``, bound to
    ctrl-p) cycles chat -> build -> plan -> auto -> bypass -> chat,
    independent of the conversation mode."""
    controller, state, trust, notices, refreshes = _controller()
    await controller.initialize()
    controller.mark_trust_explicit()  # freeze trust so activate_local can't touch it
    controller.activate_local("plan")  # arbitrary mode, must stay fixed below

    expected_postures = ["build", "plan", "auto", "bypass", "chat", "build"]
    for expected in expected_postures:
        await controller.cycle_permission()
        assert trust.active.name == expected
        assert controller.active_mode() == "plan"  # untouched by every permission step

    assert state["ui.active_mode"] == "plan"
    assert notices[-1].startswith("build permissions on")
    assert len(refreshes) == len(expected_postures)


@pytest.mark.asyncio
async def test_selecting_bypass_via_permission_control_latches_and_survives_mode_cycling() -> (
    None
):
    """Regression test for the ADR-0005 explicit-bypass-selection guarantee
    under the new two-control design: landing on ``bypass`` via ctrl-p is
    itself the deliberate user action, so a subsequent Shift-Tab (pure mode
    cycle) must not silently revert it to a mode's default trust preset."""
    controller, state, trust, notices, refreshes = _controller()
    await controller.initialize()

    for _ in range(4):  # chat -> build -> plan -> auto -> bypass
        await controller.cycle_permission()
    assert trust.active.name == "bypass"

    await controller.cycle()  # mode-only; must not revert the explicit bypass
    assert trust.active.name == "bypass"
    assert controller.active_mode() == "build"  # chat -> build; mode unaffected


def test_invalid_mode_is_repaired_to_chat() -> None:
    controller, state, trust, notices, refreshes = _controller()
    state["ui.active_mode"] = "unknown"

    assert controller.active_mode() == "chat"
    assert state["ui.active_mode"] == "chat"


@pytest.mark.asyncio
async def test_initialize_sets_safe_mode_trust_but_queries_preserve_explicit_bypass() -> (
    None
):
    controller, state, trust, _, _ = _controller()

    await controller.initialize()
    assert trust.active.name == "chat"

    trust.activate("bypass")
    assert controller.active_mode() == "chat"
    assert trust.active.name == "bypass"
    assert state["ui.permission_posture"] == "bypass"


def test_local_mode_transition_is_owned_by_controller() -> None:
    controller, state, trust, _, _ = _controller()

    assert controller.activate_local("plan") == "plan"
    assert trust.active.name == "plan"
    assert state["ui.permission_posture"] == "plan"


def test_explicit_permissions_command_blocks_future_mode_trust_defaults() -> None:
    """`/permissions preset <name>` (ui/session_commands.py) mutates TrustState
    directly, without routing through the controller. The controller must still
    detect that as an explicit choice and stop applying mode-driven defaults."""
    controller, state, trust, _, _ = _controller()

    trust.activate("build")  # simulates SessionCommandService._permissions_result

    assert controller.activate_local("brainstorm") == "brainstorm"
    assert trust.active.name == "build"
    assert state["ui.permission_posture"] == "build"


@pytest.mark.asyncio
async def test_explicit_trust_survives_reconcile_after_mode_command() -> None:
    controller, state, trust, _, _ = _controller()
    await controller.initialize()

    trust.set_slot(PermissionSlot.NETWORK, PermissionDecision.AUTO)
    assert trust.active.name == "custom"

    state["ui.active_mode"] = "plan"
    selected = await controller.reconcile("chat")

    assert selected == "plan"
    assert trust.active.name == "custom"


@pytest.mark.asyncio
async def test_mark_trust_explicit_before_initialize_prevents_resume_override() -> None:
    """Regression test for the resume/name-collision path: a persisted trust
    posture restored before `initialize()` must not be clobbered even if a
    resumed mode name happens to collide with a builtin mode (e.g. a bundle
    mode literally named "brainstorm"), whose profile default blocks everything.
    """
    controller, state, trust, _, _ = _controller()

    trust.activate("bypass")  # simulates a persisted posture restored on resume
    controller.mark_trust_explicit()
    state["ui.active_mode"] = "brainstorm"  # simulates the name-collision

    await controller.initialize()

    assert trust.active.name == "bypass"


@pytest.mark.asyncio
async def test_fresh_session_still_gets_sensible_per_mode_trust_defaults() -> None:
    """With no explicit choice made, mode changes should still apply sensible
    per-mode trust defaults (the non-regression half of the ADR-0005 contract)."""
    controller, state, trust, _, _ = _controller()

    await controller.initialize()
    assert trust.active.name == "chat"

    state["ui.active_mode"] = "build"
    selected = await controller.reconcile("chat")

    assert selected == "build"
    assert trust.active.name == "build"
