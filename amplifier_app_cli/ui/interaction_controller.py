"""Single owner for interactive mode and trust posture transitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .interaction_state import TrustState
from .interaction_runtime_state import InteractionRuntimeState
from .mode_profiles import ModeProfileRegistry
from .mode_profiles import ModeRuntimeBinding

# Notice labels for the two independent controls (ADR-0005 amendment). These
# are deliberately separate maps -- mode and permission names collide on four
# of five values (chat/build/plan/auto) but diverge at the fifth
# (brainstorm vs bypass), which is exactly the coupling bug this splits apart.
_MODE_LABELS: dict[str, str] = {
    "chat": "manual mode on",
    "build": "build mode on",
    "plan": "plan mode on",
    "auto": "auto mode on",
    "brainstorm": "brainstorm mode on",
}
_PERMISSION_LABELS: dict[str, str] = {
    "chat": "chat permissions on",
    "build": "build permissions on",
    "plan": "plan permissions on",
    "auto": "auto permissions on",
    "bypass": "bypass permissions on",
}


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
    mode_profiles: ModeProfileRegistry,
) -> tuple[str, str]:
    """Return the next conversation mode and its default trust preset.

    Pure mode-only cycling: chat -> build -> plan -> auto -> brainstorm ->
    chat. Permission posture is a fully independent axis with its own
    dedicated control (``InteractionController.cycle_permission``, bound to
    ctrl-p) that cycles ``TrustState`` directly -- see the ADR-0005
    amendment. This function used to special-case ``permission_posture ==
    "bypass"``/``active_mode == "auto"``, which meant Shift-Tab could never
    reach `brainstorm` from `auto` (the two 5-state cycles share four members
    but diverge at the fifth). That coupling is gone: this is now exactly
    ``mode_profiles.cycle(current_mode)``.
    """
    current_mode = active_mode if active_mode in mode_profiles.names else "chat"
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
        # Per ADR-0005, mode changes must never silently mutate an explicit
        # trust choice. `_trust_explicitly_set` latches True the first time
        # trust changes for a reason other than this controller applying a
        # mode's default preset (a user /permissions command, an explicit
        # ctrl-p permission selection, or a restored persisted posture). Once
        # latched, mode transitions stop touching trust for the rest of the
        # session.
        self._trust_explicitly_set = False
        self._applying_default_trust = False
        state.trust.add_listener(self._on_trust_changed)

    def _on_trust_changed(self) -> None:
        if not self._applying_default_trust:
            self._trust_explicitly_set = True

    def mark_trust_explicit(self) -> None:
        """Record that trust reflects a deliberate choice, not a mode default.

        Callers use this when they know trust is about to change (or already
        changed) for a reason other than a mode-profile default -- e.g.
        restoring a persisted posture before the first mode reconciliation.
        """
        self._trust_explicitly_set = True

    def _apply_default_trust(self, preset_name: str) -> None:
        """Apply a mode's default trust preset unless the user chose trust.

        A no-op once `_trust_explicitly_set` latches True, so mode switches
        never silently override an explicit posture (e.g. `bypass`).
        """
        if self._trust_explicitly_set:
            return
        self._applying_default_trust = True
        try:
            self._state.select_trust(preset_name)
        finally:
            self._applying_default_trust = False

    def active_mode(self) -> str:
        mode = self._state.ui_mode
        if mode != self._last_mode:
            self._binding.apply_local(mode)
            self._last_mode = mode
        return mode

    async def initialize(self) -> None:
        mode = self.active_mode()
        profile = self._profiles.get(mode)
        self._apply_default_trust(profile.trust_preset)
        await self._binding.apply(mode)

    async def reconcile(self, previous_mode: str | None) -> str:
        previous = previous_mode if previous_mode in self._profiles.names else "chat"
        selected = self._state.ui_mode
        if selected == previous:
            return selected
        profile = self._profiles.get(selected)
        self._apply_default_trust(profile.trust_preset)
        await self._binding.apply(selected)
        self._last_mode = selected
        return selected

    async def cycle(self) -> None:
        """Advance the conversation mode (Shift-Tab). Pure mode-only cycling
        -- it never reads or writes permission posture. See
        ``cycle_permission`` for the independent permission control
        (ADR-0005 amendment)."""
        if self._state.bundle_mode:
            await self._clear_legacy_mode()
        next_mode, default_trust = next_shift_tab_state(
            self.active_mode(),
            self._profiles,
        )
        self._apply_default_trust(default_trust)
        self._state.select_ui_mode(next_mode)
        await self._binding.apply(next_mode)
        self._last_mode = next_mode
        self._notify(f"{_MODE_LABELS[next_mode]} · shift-tab to cycle")
        self._refresh()

    async def cycle_permission(self) -> None:
        """Advance the permission posture (ctrl-p), independent of mode.

        Reuses ``TrustState.cycle()`` (chat -> build -> plan -> auto ->
        bypass -> chat). Using this dedicated control is itself the explicit
        user action ADR-0005 requires -- landing on any posture (not just
        `bypass`) latches ``_trust_explicitly_set`` so later mode-only
        cycling never silently reverts it to a mode's default preset.
        """
        preset = self._state.trust.cycle()
        self.mark_trust_explicit()
        self._notify(f"{_PERMISSION_LABELS[preset.name]} · ctrl-p to cycle")
        self._refresh()

    def activate_local(self, mode: str) -> str:
        profile = self._profiles.get(mode)
        selected = profile.name.value
        self._state.select_ui_mode(selected)
        self._apply_default_trust(profile.trust_preset)
        self._binding.apply_local(selected)
        self._last_mode = selected
        return selected


__all__ = [
    "InteractionController",
    "apply_ui_mode_transition",
    "next_shift_tab_state",
]
