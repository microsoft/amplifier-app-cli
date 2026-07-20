"""Mode inspection and transition commands for the interactive CLI."""

from __future__ import annotations

from typing import Any

from amplifier_app_cli.runtime.session_state import coordinator_session_state
from amplifier_app_cli.ui.interaction_runtime_state import interaction_state_for


class CommandModeMixin:
    """Implement mode lifecycle commands for CommandProcessor."""

    session: Any
    BUILTIN_MODE_NAMES: tuple[str, ...]
    BUILTIN_MODE_PROFILES: Any

    async def _handle_mode(self, args: str) -> str:
        """Handle /mode command for setting, toggling, or clearing modes."""
        args = args.strip()
        args_lower = args.lower()
        session_state = coordinator_session_state(self.session.coordinator)
        interaction = interaction_state_for(
            self.session.coordinator,
            ui_modes=self.BUILTIN_MODE_NAMES,
        )
        current_mode = interaction.bundle_mode
        current_ui_mode = interaction.ui_mode

        # /mode info <name> — full details for a specific mode
        if args_lower.startswith("info ") or args_lower == "info":
            mode_name = (
                args[5:].strip().lower() if args_lower.startswith("info ") else ""
            )
            return await self._mode_info(mode_name)

        # Continue with lower-case args for remaining /mode subcommands
        args = args_lower

        # /mode off - clear any active mode
        if args == "off":
            if current_mode:
                # Emit mode:cleared BEFORE state mutation so hooks see the old state
                await self.session.coordinator.hooks.emit(
                    "mode:cleared",
                    {"name": current_mode, "previous_mode": current_mode},
                )
                interaction.select_bundle_mode(None)
                # Reset warnings in mode hooks if present
                mode_hooks = session_state.get("mode_hooks")
                if mode_hooks and hasattr(mode_hooks, "reset_warnings"):
                    mode_hooks.reset_warnings()
                interaction.select_ui_mode("chat")
                return f"Mode off: {current_mode}"
            if current_ui_mode != "chat":
                interaction.select_ui_mode("chat")
                return "Mode: chat"
            return "Already in chat mode"

        # /mode (no args) - show current mode
        if not args:
            return f"Active mode: {current_ui_mode}"

        # /mode <name> [on|off] - set or toggle a mode
        parts = args.split()
        mode_name = parts[0]
        explicit_state = parts[1] if len(parts) > 1 else None

        # Built-in interaction modes are always available, even when the active
        # bundle does not mount the legacy modes discovery capability.
        if mode_name in self.BUILTIN_MODE_NAMES:
            if explicit_state == "off":
                if current_ui_mode != mode_name:
                    return f"Not in {mode_name} mode"
                interaction.select_ui_mode("chat")
                return "Mode: chat"
            if current_ui_mode == mode_name:
                return f"Already in {mode_name} mode"
            interaction.select_ui_mode(mode_name)
            profile = self.BUILTIN_MODE_PROFILES.get(mode_name)
            return f"Mode: {mode_name} — {profile.autonomy}"

        # Check if mode exists via discovery
        discovery = session_state.get("mode_discovery")
        mode_def = None
        if discovery:
            mode_def = discovery.find(mode_name)
            if not mode_def:
                return f"Unknown mode: {mode_name}. Use /modes to list available modes."
            description = mode_def.description
        else:
            # No discovery available - just set the mode name
            description = ""

        # Handle explicit on/off
        if explicit_state == "on":
            if current_mode == mode_name:
                return f"Already in {mode_name} mode"
            _prev = current_mode
            # Emit lifecycle event BEFORE state mutation so hooks see the old state.
            # Build full payload from mode_def when discovery is available.
            if _prev and _prev != mode_name:
                _payload: dict = {
                    "old": _prev,
                    "new": mode_name,
                    "from_mode": _prev,
                    "to_mode": mode_name,
                }
                if mode_def is not None:
                    _payload.update(
                        {
                            "description": mode_def.description,
                            "default_action": mode_def.default_action,
                            "safe_tools": mode_def.safe_tools,
                            "warn_tools": mode_def.warn_tools,
                            "confirm_tools": mode_def.confirm_tools,
                            "block_tools": mode_def.block_tools,
                        }
                    )
                await self.session.coordinator.hooks.emit("mode:changed", _payload)
            else:
                _payload = {"name": mode_name, "mode": mode_name}
                if mode_def is not None:
                    _payload.update(
                        {
                            "description": mode_def.description,
                            "default_action": mode_def.default_action,
                            "safe_tools": mode_def.safe_tools,
                            "warn_tools": mode_def.warn_tools,
                            "confirm_tools": mode_def.confirm_tools,
                            "block_tools": mode_def.block_tools,
                        }
                    )
                await self.session.coordinator.hooks.emit("mode:activated", _payload)
            interaction.select_bundle_mode(mode_name)
            mode_hooks = session_state.get("mode_hooks")
            if mode_hooks and hasattr(mode_hooks, "reset_warnings"):
                mode_hooks.reset_warnings()
            return f"Mode: {mode_name}" + (f" — {description}" if description else "")

        if explicit_state == "off":
            if current_mode != mode_name:
                return f"Not in {mode_name} mode"
            # Emit mode:cleared BEFORE state mutation so hooks see the old state
            await self.session.coordinator.hooks.emit(
                "mode:cleared", {"name": mode_name, "previous_mode": mode_name}
            )
            interaction.select_bundle_mode(None)
            mode_hooks = session_state.get("mode_hooks")
            if mode_hooks and hasattr(mode_hooks, "reset_warnings"):
                mode_hooks.reset_warnings()
            return f"Mode off: {mode_name}"

        # Toggle behavior (no explicit on/off)
        if current_mode == mode_name:
            # Emit mode:cleared BEFORE state mutation so hooks see the old state
            await self.session.coordinator.hooks.emit(
                "mode:cleared", {"name": mode_name, "previous_mode": mode_name}
            )
            interaction.select_bundle_mode(None)
            mode_hooks = session_state.get("mode_hooks")
            if mode_hooks and hasattr(mode_hooks, "reset_warnings"):
                mode_hooks.reset_warnings()
            return f"Mode off: {mode_name}"
        else:
            _prev_toggle = current_mode
            # Emit lifecycle event BEFORE state mutation so hooks see the old state.
            # Build full payload from mode_def when discovery is available.
            if _prev_toggle:
                _payload = {
                    "old": _prev_toggle,
                    "new": mode_name,
                    "from_mode": _prev_toggle,
                    "to_mode": mode_name,
                }
                if mode_def is not None:
                    _payload.update(
                        {
                            "description": mode_def.description,
                            "default_action": mode_def.default_action,
                            "safe_tools": mode_def.safe_tools,
                            "warn_tools": mode_def.warn_tools,
                            "confirm_tools": mode_def.confirm_tools,
                            "block_tools": mode_def.block_tools,
                        }
                    )
                await self.session.coordinator.hooks.emit("mode:changed", _payload)
            else:
                _payload = {"name": mode_name, "mode": mode_name}
                if mode_def is not None:
                    _payload.update(
                        {
                            "description": mode_def.description,
                            "default_action": mode_def.default_action,
                            "safe_tools": mode_def.safe_tools,
                            "warn_tools": mode_def.warn_tools,
                            "confirm_tools": mode_def.confirm_tools,
                            "block_tools": mode_def.block_tools,
                        }
                    )
                await self.session.coordinator.hooks.emit("mode:activated", _payload)
            interaction.select_bundle_mode(mode_name)
            mode_hooks = session_state.get("mode_hooks")
            if mode_hooks and hasattr(mode_hooks, "reset_warnings"):
                mode_hooks.reset_warnings()
            return f"Mode: {mode_name}" + (f" — {description}" if description else "")

    async def _list_modes(self) -> str:
        """List available modes, grouped by source bundle.

        Shows ALL modes — advertised and unadvertised. Unadvertised modes are
        marked with ``(hidden)`` to signal that they are available via slash
        command but are not surfaced to agents via the mode(list) tool.

        Layout: one line per mode, terminal-width-aware truncation, aligned
        columns within each source group. No line wrapping.
        """
        import shutil
        from collections import defaultdict

        session_state = coordinator_session_state(self.session.coordinator)
        interaction = interaction_state_for(
            self.session.coordinator,
            ui_modes=self.BUILTIN_MODE_NAMES,
        )
        discovery = session_state.get("mode_discovery")
        modes = discovery.list_modes() if discovery else ()
        current_ui_mode = interaction.ui_mode
        current_mode = (
            current_ui_mode
            if current_ui_mode in self.BUILTIN_MODE_NAMES
            else interaction.bundle_mode
        )
        terminal_cols = shutil.get_terminal_size((100, 24)).columns

        # Parse each entry — supports ModeListing NamedTuple (name/desc/source/advertised)
        # and legacy tuple formats (2-tuple or 3-tuple) for backward compat.
        # Group: source → list of (name, description, advertised)
        groups: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
        for name in self.BUILTIN_MODE_NAMES:
            profile = self.BUILTIN_MODE_PROFILES.get(name)
            groups["interaction"].append((profile.name.value, profile.autonomy, True))
        builtin_names = set(self.BUILTIN_MODE_NAMES)
        for item in modes:
            name = item[0]
            if name in builtin_names:
                groups["interaction"] = [
                    entry for entry in groups["interaction"] if entry[0] != name
                ]
            description = item[1] if len(item) > 1 else ""
            source = item[2] if len(item) > 2 else ""
            # ModeListing has 4 elements; old tuples have 2 or 3 — advertised defaults to True
            advertised = item[3] if len(item) > 3 else getattr(item, "advertised", True)
            groups[source or "other"].append((name, description, bool(advertised)))

        if not groups["interaction"]:
            del groups["interaction"]

        has_hidden = any(
            not advertised
            for source_modes in groups.values()
            for _, _, advertised in source_modes
        )

        lines = ["Available modes:"]

        for source in sorted(groups.keys()):
            source_modes = sorted(groups[source], key=lambda x: x[0])
            lines.append(f"\n  {source}:")

            # Name column width: widest (name + optional " (hidden)" suffix) in this group
            name_col = max(
                len(name) + (len(" (hidden)") if not adv else 0)
                for name, _, adv in source_modes
            )

            # Description gets the remaining space: total - indent(4) - name - gap(3)
            desc_max = terminal_cols - 4 - name_col - 3
            if desc_max < 10:
                desc_max = 10  # minimum visible width

            for name, description, advertised in source_modes:
                hidden_sfx = " (hidden)" if not advertised else ""
                active_sfx = " *" if name == current_mode else ""
                name_field = f"{name}{hidden_sfx}{active_sfx}"

                if description:
                    truncated = (
                        description
                        if len(description) <= desc_max
                        else description[: desc_max - 3] + "..."
                    )
                    lines.append(f"    {name_field:<{name_col}}   {truncated}")
                else:
                    lines.append(f"    {name_field}")

        if current_mode:
            lines.append(f"\nActive: {current_mode}")

        if has_hidden:
            lines.append(
                "\n(hidden) = available only via slash command, not advertised to agents."
            )

        lines.append("Use `/mode <name>` to switch modes; `/mode off` returns to chat.")
        return "\n".join(lines)

    async def _mode_info(self, mode_name: str) -> str:
        """Show full details for a specific mode.

        Usage: /mode info <name>
        """
        if not mode_name:
            return "Usage: `/mode info <name>` - show full details for a mode"

        session_state = coordinator_session_state(self.session.coordinator)
        discovery = session_state.get("mode_discovery")
        mode_def = discovery.find(mode_name) if discovery else None
        if not mode_def:
            if mode_name in self.BUILTIN_MODE_NAMES:
                profile = self.BUILTIN_MODE_PROFILES.get(mode_name)
                return "\n".join(
                    (
                        profile.name.value,
                        "  Source:      interaction",
                        f"  Description: {profile.autonomy}",
                        f"  Rendering:   {profile.render_profile.value}",
                        f"  Model role:  {profile.model_role}",
                        f"  Effort:      {profile.reasoning_effort.value}",
                        f"  Trust:       {profile.trust_preset}",
                        f"  Shortcut:    /{profile.name.value}",
                    )
                )
            if not discovery:
                return "Mode system not available. Include the modes bundle to enable modes."
            return f"Mode '{mode_name}' not found. Use /modes to see available modes."

        advertised_label = (
            "yes"
            if getattr(mode_def, "advertised", True)
            else "no (hidden — not advertised to agents)"
        )

        lines = [
            f"{mode_def.name}"
            + (" (hidden)" if not getattr(mode_def, "advertised", True) else ""),
            f"  Source:      {getattr(mode_def, 'source', 'unknown')}",
            f"  Advertised:  {advertised_label}",
        ]

        if mode_def.description:
            lines.append(f"  Description: {mode_def.description}")

        shortcut = getattr(mode_def, "shortcut", None)
        if shortcut:
            lines.append(f"  Shortcut:    /{shortcut}")

        default_action = getattr(mode_def, "default_action", None)
        if default_action:
            lines.append(f"  Default:     {default_action}")

        # Tool policies
        has_tools = any(
            getattr(mode_def, attr, [])
            for attr in ("safe_tools", "warn_tools", "confirm_tools", "block_tools")
        )
        if has_tools:
            lines.append("  Tools:")
            for label, attr in (
                ("safe", "safe_tools"),
                ("warn", "warn_tools"),
                ("confirm", "confirm_tools"),
                ("block", "block_tools"),
            ):
                tools = getattr(mode_def, attr, [])
                if tools:
                    lines.append(f"    {label}: {', '.join(tools)}")

        # Contributions (mode-design style)
        contributes = getattr(mode_def, "contributes", {})
        if contributes:
            lines.append("  Contributes:")
            for kind, items in contributes.items():
                if isinstance(items, list):
                    for item in items:
                        lines.append(f"    {kind}: {item}")
                else:
                    lines.append(f"    {kind}: {items}")

        return "\n".join(lines)


__all__ = ["CommandModeMixin"]
