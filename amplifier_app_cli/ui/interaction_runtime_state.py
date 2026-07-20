"""Typed owner for interactive mode and permission state."""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass

from amplifier_app_cli.runtime.session_state import coordinator_session_state

from .interaction_state import TrustState

INTERACTION_STATE_CAPABILITY = "ui.interaction_state"
DEFAULT_UI_MODES = ("chat", "plan", "brainstorm", "build", "auto")


@dataclass(frozen=True, slots=True)
class InteractionSnapshot:
    """Current app-owned interaction state."""

    ui_mode: str
    bundle_mode: str | None
    permission_posture: str


class InteractionRuntimeState:
    """Own coordinator persistence keys for modes and trust posture."""

    def __init__(
        self,
        backing: MutableMapping[str, object],
        trust: TrustState,
        *,
        ui_modes: Iterable[str] = DEFAULT_UI_MODES,
    ) -> None:
        self._backing = backing
        self._trust = trust
        self._ui_modes = frozenset(ui_modes)
        if "chat" not in self._ui_modes:
            raise ValueError("interaction modes must include chat")
        self._remove_trust_listener = trust.add_listener(self._sync_trust)
        self._sync_trust()
        self._backing.setdefault("active_mode", None)
        self.ui_mode  # Repair invalid persisted state at the boundary.

    @property
    def trust(self) -> TrustState:
        return self._trust

    @property
    def ui_mode(self) -> str:
        value = self._backing.get("ui.active_mode")
        if not isinstance(value, str) or value not in self._ui_modes:
            value = "chat"
            self._backing["ui.active_mode"] = value
        return value

    @property
    def bundle_mode(self) -> str | None:
        value = self._backing.get("active_mode")
        return value if isinstance(value, str) and value else None

    @property
    def permission_posture(self) -> str:
        return self._trust.active.name

    @property
    def snapshot(self) -> InteractionSnapshot:
        return InteractionSnapshot(
            ui_mode=self.ui_mode,
            bundle_mode=self.bundle_mode,
            permission_posture=self.permission_posture,
        )

    def select_ui_mode(self, name: str | None) -> str:
        selected = name if name in self._ui_modes else "chat"
        self._backing["ui.active_mode"] = selected
        return selected

    def select_bundle_mode(self, name: str | None) -> str | None:
        selected = name.strip() if isinstance(name, str) else ""
        value = selected or None
        self._backing["active_mode"] = value
        return value

    def select_trust(self, name: str) -> str:
        self._trust.activate(name)
        self._sync_trust()
        return self._trust.active.name

    def close(self) -> None:
        self._remove_trust_listener()

    def _sync_trust(self) -> None:
        self._backing["ui.permission_posture"] = self._trust.active.name


def interaction_state_for(
    coordinator: object,
    *,
    ui_modes: Iterable[str] = DEFAULT_UI_MODES,
) -> InteractionRuntimeState:
    """Return the registered state owner, creating one at the app boundary."""
    get_capability = getattr(coordinator, "get_capability", None)
    existing = (
        get_capability(INTERACTION_STATE_CAPABILITY)
        if callable(get_capability)
        else None
    )
    if isinstance(existing, InteractionRuntimeState):
        return existing
    cached = getattr(coordinator, "__dict__", {}).get("_cli_interaction_state")
    if isinstance(cached, InteractionRuntimeState):
        return cached

    trust = get_capability("ui.trust_state") if callable(get_capability) else None
    created_trust = not isinstance(trust, TrustState)
    if created_trust:
        trust = TrustState()
    state = InteractionRuntimeState(
        coordinator_session_state(coordinator),
        trust,
        ui_modes=ui_modes,
    )
    register = getattr(coordinator, "register_capability", None)
    if callable(register):
        if created_trust:
            register("ui.trust_state", trust)
        register(INTERACTION_STATE_CAPABILITY, state)
    try:
        setattr(coordinator, "_cli_interaction_state", state)
    except (AttributeError, TypeError):
        pass
    return state


__all__ = [
    "DEFAULT_UI_MODES",
    "INTERACTION_STATE_CAPABILITY",
    "InteractionRuntimeState",
    "InteractionSnapshot",
    "interaction_state_for",
]
