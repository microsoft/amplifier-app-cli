from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.ui.interaction_controller import InteractionController
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
async def test_cycle_reaches_explicit_bypass_then_brainstorm() -> None:
    controller, state, trust, notices, refreshes = _controller()
    await controller.initialize()

    await controller.cycle()  # chat -> build
    await controller.cycle()  # build -> plan
    await controller.cycle()  # plan -> auto
    await controller.cycle()  # auto -> bypass

    assert controller.active_mode() == "auto"
    assert trust.active.name == "bypass"
    assert state["ui.permission_posture"] == "bypass"
    assert notices[-1].startswith("bypass permissions on")

    await controller.cycle()  # bypass -> brainstorm
    assert controller.active_mode() == "brainstorm"
    assert trust.active.name == "brainstorm"
    assert len(refreshes) == 5


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
