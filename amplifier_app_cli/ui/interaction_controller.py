"""Single owner for interactive mode and trust posture transitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .interaction_state import TrustState
from .interaction_runtime_state import InteractionRuntimeState
from .mode_profiles import ModeProfileRegistry
from .mode_profiles import ModeRuntimeBinding


async def apply_ui_mode_transition(
    session_state: dict[str, object],
    previous_mode: str | None,
    mode_profiles: ModeProfileRegistry,
    mode_binding: ModeRuntimeBinding,
    active_mode_state: dict[str, str | None],
    trust_state: TrustState | None = None,
) -> str:
    """Apply runtime policy only when a command selected a different mode."""
    trust = trust_state or TrustState()
    interaction = InteractionRuntimeState(
        session_state,
        trust,
        ui_modes=mode_profiles.names,
    )
    try:
        previous = previous_mode if previous_mode in mode_profiles.names else "chat"
        selected = interaction.ui_mode
        if selected == previous:
            return selected
        profile = mode_profiles.get(selected)
        if trust_state is not None:
            interaction.select_trust(profile.trust_preset)
        await mode_binding.apply(selected)
        active_mode_state["last"] = selected
        return selected
    finally:
        interaction.close()


def next_shift_tab_state(
    active_mode: str | None,
    permission_posture: str,
    mode_profiles: ModeProfileRegistry,
) -> tuple[str, str]:
    """Return the next conversation mode and explicit permission posture."""
    current_mode = active_mode if active_mode in mode_profiles.names else "chat"
    if permission_posture == "bypass":
        profile = mode_profiles.cycle(current_mode)
        return profile.name.value, profile.trust_preset
    if current_mode == "auto":
        return current_mode, "bypass"
    profile = mode_profiles.cycle(current_mode)
    return profile.name.value, profile.trust_preset


class InteractionController:
    """Coordinate typed mode profiles with an independent trust state."""

    def __init__(
        self,
        *,
        state: InteractionRuntimeState,
        profiles: ModeProfileRegistry,
        binding: ModeRuntimeBinding,
        clear_legacy_mode: Callable[[], Awaitable[object]],
        notify: Callable[[str], None],
        refresh: Callable[[], None],
    ) -> None:
        self._state = state
        self._profiles = profiles
        self._binding = binding
        self._clear_legacy_mode = clear_legacy_mode
        self._notify = notify
        self._refresh = refresh
        self._last_mode: str | None = None

    def active_mode(self) -> str:
        mode = self._state.ui_mode
        if mode != self._last_mode:
            self._binding.apply_local(mode)
            self._last_mode = mode
        return mode

    async def initialize(self) -> None:
        mode = self.active_mode()
        profile = self._profiles.get(mode)
        self._state.select_trust(profile.trust_preset)
        await self._binding.apply(mode)

    async def reconcile(self, previous_mode: str | None) -> str:
        previous = previous_mode if previous_mode in self._profiles.names else "chat"
        selected = self._state.ui_mode
        if selected == previous:
            return selected
        profile = self._profiles.get(selected)
        self._state.select_trust(profile.trust_preset)
        await self._binding.apply(selected)
        self._last_mode = selected
        return selected

    async def cycle(self) -> None:
        if self._state.bundle_mode:
            await self._clear_legacy_mode()
        next_mode, next_permission = next_shift_tab_state(
            self.active_mode(),
            self._state.permission_posture,
            self._profiles,
        )
        if next_permission == "bypass":
            self._state.select_trust("bypass")
        else:
            self._state.select_trust(next_permission)
            self._state.select_ui_mode(next_mode)
            await self._binding.apply(next_mode)
            self._last_mode = next_mode
        label = {
            "chat": "manual mode on",
            "build": "build mode on",
            "plan": "plan mode on",
            "auto": "auto mode on",
            "bypass": "bypass permissions on",
            "brainstorm": "brainstorm mode on",
        }[next_permission]
        self._notify(f"{label} · shift-tab to cycle")
        self._refresh()

    def activate_local(self, mode: str) -> str:
        profile = self._profiles.get(mode)
        selected = profile.name.value
        self._state.select_ui_mode(selected)
        self._state.select_trust(profile.trust_preset)
        self._binding.apply_local(selected)
        self._last_mode = selected
        return selected


__all__ = [
    "InteractionController",
    "apply_ui_mode_transition",
    "next_shift_tab_state",
]
