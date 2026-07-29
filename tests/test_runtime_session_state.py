from __future__ import annotations

from types import SimpleNamespace

import pytest

from amplifier_app_cli.runtime.session_state import coordinator_session_state


def test_coordinator_state_is_created_once() -> None:
    coordinator = SimpleNamespace()

    state = coordinator_session_state(coordinator)
    state["mode"] = "chat"

    assert coordinator_session_state(coordinator) is state
    assert coordinator.session_state == {"mode": "chat"}


def test_coordinator_state_rejects_invalid_boundary_value() -> None:
    coordinator = SimpleNamespace(session_state=[])

    with pytest.raises(TypeError, match="must be a dictionary"):
        coordinator_session_state(coordinator)
