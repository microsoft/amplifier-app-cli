"""Validated adapters for dynamic Amplifier session surfaces."""

from __future__ import annotations

from typing import Any, Protocol, cast


class CoordinatorAccess(Protocol):
    def get(self, mount_point: str, name: str | None = None) -> Any: ...


def session_coordinator(session: object) -> CoordinatorAccess:
    """Validate and type the public coordinator surface at the app boundary."""
    coordinator = getattr(session, "coordinator", None)
    if coordinator is None or not callable(getattr(coordinator, "get", None)):
        raise TypeError("interactive session must expose a coordinator")
    return cast(CoordinatorAccess, coordinator)


__all__ = ["CoordinatorAccess", "session_coordinator"]
