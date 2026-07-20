from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

from amplifier_app_cli.ui.interaction_runtime_state import InteractionRuntimeState
from amplifier_app_cli.ui.interaction_runtime_state import interaction_state_for
from amplifier_app_cli.ui.interaction_state import TrustState


def test_interaction_state_repairs_and_snapshots_all_owned_dimensions() -> None:
    backing: dict[str, object] = {
        "ui.active_mode": "invalid",
        "active_mode": "bundle-mode",
    }
    trust = TrustState()
    state = InteractionRuntimeState(backing, trust)

    assert state.snapshot.ui_mode == "chat"
    assert state.snapshot.bundle_mode == "bundle-mode"
    assert state.snapshot.permission_posture == "chat"

    state.select_ui_mode("plan")
    state.select_bundle_mode(None)
    state.select_trust("bypass")

    assert state.snapshot.ui_mode == "plan"
    assert state.snapshot.bundle_mode is None
    assert state.snapshot.permission_posture == "bypass"
    assert backing["ui.permission_posture"] == "bypass"


def test_external_trust_transition_is_reflected_in_typed_snapshot() -> None:
    backing: dict[str, object] = {}
    trust = TrustState()
    state = InteractionRuntimeState(backing, trust)

    trust.activate("build")

    assert state.permission_posture == "build"
    assert backing["ui.permission_posture"] == "build"


def test_coordinator_returns_one_registered_interaction_state() -> None:
    coordinator = MagicMock()
    coordinator.session_state = {}
    capabilities: dict[str, object] = {"ui.trust_state": TrustState()}
    coordinator.get_capability.side_effect = capabilities.get
    coordinator.register_capability.side_effect = capabilities.__setitem__

    first = interaction_state_for(coordinator)
    second = interaction_state_for(coordinator)

    assert first is second


def test_interaction_persistence_keys_have_one_source_owner() -> None:
    source_root = Path(__file__).parents[1] / "amplifier_app_cli"
    owner = Path("ui/interaction_runtime_state.py")
    protected = {"active_mode", "ui.active_mode", "ui.permission_posture"}
    violations: list[str] = []

    for source_path in source_root.rglob("*.py"):
        relative = source_path.relative_to(source_root)
        if relative == owner:
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(
                node.ctx, ast.Store
            ):
                continue
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in protected:
                violations.append(f"{relative}:{node.lineno} writes {key.value}")

    assert violations == [], "\n".join(violations)
