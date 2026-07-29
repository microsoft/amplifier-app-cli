"""Validated access to app-owned coordinator session state."""

from __future__ import annotations

from typing import Any, cast


def coordinator_session_state(coordinator: object) -> dict[str, Any]:
    """Return mutable app state, creating it at the coordinator boundary."""
    state = getattr(coordinator, "session_state", None)
    if state is None:
        state = {}
        setattr(coordinator, "session_state", state)
    if not isinstance(state, dict):
        raise TypeError("coordinator session_state must be a dictionary")
    return cast(dict[str, Any], state)


__all__ = ["coordinator_session_state"]
