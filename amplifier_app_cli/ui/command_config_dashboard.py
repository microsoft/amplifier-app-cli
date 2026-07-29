"""Detailed configuration dashboard and mutation commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from amplifier_app_cli.ui.interaction_runtime_state import interaction_state_for

from .dashboard_renderer import DashboardRenderer
from .item_renderer import ItemRenderer
from .view_policy import resolve_view


class CommandConfigDashboardMixin:
    """Implement detailed configuration surfaces for CommandProcessor."""

    session: Any
    configurator: Any

    if TYPE_CHECKING:

        @property
        def _display_bundle_name(self) -> str: ...

    async def _render_config_dashboard_v2(
        self,
        *,
        compact: bool = False,
        detailed: bool = False,
        trees: bool = False,
        fmt: str = "text",
    ) -> str:
        """Render the full config dashboard using ItemRenderer (Commit 2 surface).

        - Default (no flags): compact one-liner per item across all sections.
        - ``--detailed``: regular multi-line DashboardRenderer output per section.
        - ``--trees``: per-item full drilldown (tree-style chain + include_paths).
        - ``--format json``: JSON dump of all ItemRecord lists (ignores --trees).
        - ``--compact``: explicit compact (same as default).

        ``--trees`` and ``--detailed`` are mutually exclusive; last flag wins.
        """
        from ..console import console

        configurator = self.configurator

        context_items = configurator.context_list()
        tools_items = configurator.tools_list()
        hooks_items = configurator.hooks_list()
        providers_items = configurator.providers_list()
        agents_items = configurator.agents_list()
        behaviors_items = configurator.behaviors_list()
        changes = configurator.diff_from_original()

        active_mode = (
            interaction_state_for(self.session.coordinator).bundle_mode or "none"
        )
        change_count = len(changes) if changes else 0

        # Header — always printed in text mode
        if fmt != "json":
            renderer_dr = DashboardRenderer(console)
            renderer_dr.render_header(
                self._display_bundle_name, active_mode, change_count
            )

        # JSON output — all categories as a single JSON object
        if fmt == "json":
            import dataclasses
            import json as _json

            def _ser(items: list) -> list:
                return [
                    dataclasses.asdict(i)
                    if dataclasses.is_dataclass(i) and not isinstance(i, type)
                    else i
                    for i in items
                ]

            payload = {
                "providers": _ser(providers_items),
                "tools": _ser(tools_items),
                "hooks": _ser(hooks_items),
                "context": _ser(context_items),
                "agents": _ser(agents_items),
                "behaviors": _ser(behaviors_items),
            }
            console.print(_json.dumps(payload, indent=2, default=str))
            return ""

        # Text output — resolve view mode
        view = resolve_view(
            ("config", "show"),
            compact_flag=compact,
            detailed_flag=detailed,
        )
        # Determine effective view:
        # --trees overrides everything (trees wins when both --detailed and --trees given,
        # because _parse_config_flags clears the losing flag — last flag wins).
        # For dashboard (multi-category), "detailed" falls back to "regular" multi-line.
        if trees:
            effective_view = "trees"
        elif view == "detailed":
            effective_view = "regular"
        else:
            effective_view = view

        ir = ItemRenderer(console)
        raw_config = self.session.coordinator.config
        session_config = (
            raw_config.get("session", {}) if isinstance(raw_config, dict) else {}
        )

        if effective_view == "compact":
            # Compact: show session block with simple key: value lines
            if session_config and isinstance(session_config, dict):
                console.print("\u2500\u2500 session \u2500\u2500")
                for field in ["orchestrator", "context"]:
                    if field in session_config:
                        value = session_config[field]
                        if isinstance(value, dict) and "module" in value:
                            mod_id = value.get("module", "unknown")
                            console.print(f"  {field}: {mod_id}")
                        else:
                            console.print(f"  {field}: {value}")
                console.print()

            ir.render(providers_items, view="compact", category="providers")
            ir.render(tools_items, view="compact", category="tools")
            ir.render(hooks_items, view="compact", category="hooks")
            ir.render(context_items, view="compact", category="context")
            ir.render(agents_items, view="compact", category="agents")
            ir.render(behaviors_items, view="compact", category="behaviors")

        elif effective_view == "trees":
            # Trees: per-item full drilldown for every item in every section
            renderer_dr = DashboardRenderer(console)
            if session_config and isinstance(session_config, dict):
                console.print("\u2500\u2500 session \u2500\u2500")
                for field in ["orchestrator", "context"]:
                    if field in session_config:
                        value = session_config[field]
                        if isinstance(value, dict) and "module" in value:
                            mod_id = value.get("module", "unknown")
                            cfg = value.get("config", {})
                            console.print(f"  {field}: {mod_id}")
                            if cfg and isinstance(cfg, dict):
                                console.print("[dim]    config:[/dim]")
                                for k, v in cfg.items():
                                    renderer_dr.render_config_tree(
                                        {k: v}, "      ", dim=True
                                    )
                        else:
                            console.print(f"  {field}: {value}")
                console.print()

            ir.render(providers_items, view="trees", category="providers")
            ir.render(tools_items, view="trees", category="tools")
            ir.render(hooks_items, view="trees", category="hooks")
            ir.render(context_items, view="trees", category="context")
            ir.render(agents_items, view="trees", category="agents")
            ir.render(behaviors_items, view="trees", category="behaviors")

        else:
            # Regular: full multi-line DashboardRenderer output (old dashboard look)
            renderer_dr = DashboardRenderer(console)
            if session_config and isinstance(session_config, dict):
                console.print("\u2500\u2500 session \u2500\u2500")
                for field in ["orchestrator", "context"]:
                    if field in session_config:
                        value = session_config[field]
                        if isinstance(value, dict) and "module" in value:
                            mod_id = value.get("module", "unknown")
                            cfg = value.get("config", {})
                            console.print(f"  {field}: {mod_id}")
                            if cfg and isinstance(cfg, dict):
                                console.print("[dim]    config:[/dim]")
                                for k, v in cfg.items():
                                    renderer_dr.render_config_tree(
                                        {k: v}, "      ", dim=True
                                    )
                        else:
                            console.print(f"  {field}: {value}")
                console.print()

            renderer_dr.render_providers_section(providers_items)
            renderer_dr.render_tools_section(tools_items)
            renderer_dr.render_hooks_section(hooks_items)
            renderer_dr.render_attributed_section(context_items, "context")
            renderer_dr.render_attributed_section(agents_items, "agents")
            renderer_dr.render_behaviors_section(behaviors_items)

        return ""

    async def _render_config_item(self, category: str, name: str) -> str:
        """Render a single named item in detailed view.

        Looks up the item by name within the category's ItemRecord list and
        renders it using ItemRenderer.render_one(view="detailed").

        Prints "Item not found" if no item matches *name* in *category*.
        """
        from ..console import console

        configurator = self.configurator

        list_methods = {
            "context": configurator.context_list,
            "tools": configurator.tools_list,
            "hooks": configurator.hooks_list,
            "providers": configurator.providers_list,
            "agents": configurator.agents_list,
            "behaviors": configurator.behaviors_list,
        }

        method = list_methods.get(category)
        if method is None:
            return f"Unknown category: {category}"

        items = method()

        # Find the matching item (ItemRecord or dict)
        matched = None
        for item in items:
            item_name = (
                item.name
                if hasattr(item, "name")
                else (item.get("name", "") if isinstance(item, dict) else "")
            )
            if item_name == name:
                matched = item
                break

        if matched is None:
            console.print(
                f"[yellow]Item not found: {name!r} in category {category!r}[/yellow]"
            )
            return ""

        ItemRenderer(console).render_one(matched, view="detailed")
        return ""

    async def _handle_config_toggle(self, category: str, action: str, name: str) -> str:
        """Map (category, action) to configurator method, handle async/sync, catch errors."""
        import inspect

        from ..console import console

        # Hooks are read-only: toggling requires a core suspend/resume API that doesn't
        # exist yet. Show a clear, actionable message rather than silently erroring.
        if category == "hooks":
            console.print(
                "[yellow]Hook toggle is not supported in this version. "
                "Hooks are visible in /config for inspection but cannot be "
                "disabled/re-enabled at runtime.\n"
                "A core suspend/resume API is needed for safe hook toggle.[/yellow]"
            )
            return ""

        configurator = self.configurator

        method_map = {
            ("context", "disable"): "context_disable",
            ("context", "enable"): "context_enable",
            ("tools", "disable"): "tool_disable",
            ("tools", "enable"): "tool_enable",
            ("providers", "disable"): "provider_disable",
            ("providers", "enable"): "provider_enable",
            ("agents", "disable"): "agent_disable",
            ("agents", "enable"): "agent_enable",
            ("behaviors", "disable"): "behavior_disable",
            ("behaviors", "enable"): "behavior_enable",
        }

        method_name = method_map.get((category, action))
        if method_name is None:
            return f"Unknown action: {action} for category: {category}"

        method = getattr(configurator, method_name, None)
        if method is None:
            return f"Method not available: {method_name}"

        try:
            result = method(name)
            if inspect.isawaitable(result):
                result = await result

            # Format success message
            if isinstance(result, dict):
                # behaviors return dict with enabled/disabled/warnings
                warnings = result.get("warnings", [])
                msg = f"\u2713 {action.capitalize()}d {name}"
                if warnings:
                    msg += f"\nWarnings: {', '.join(str(w) for w in warnings)}"
                return msg

            return f"\u2713 {action.capitalize()}d {name}"

        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"

    async def _handle_config_diff(self) -> str:
        """Show changes from original config."""
        from ..console import console

        configurator = self.configurator
        changes = configurator.diff_from_original()

        if not changes:
            return "No changes from original"

        console.print(f"[bold]Changes ({len(changes)}):[/bold]")
        for change in changes:
            cat = change.get("category", "?")
            change_name = change.get("name", "?")
            change_action = change.get("action", "?")
            console.print(f"  {cat} {change_name}: {change_action}")
        return ""  # Output already printed via console

    async def _handle_config_save(self, scope: str = "global") -> str:
        """Save config changes to disk."""
        configurator = self.configurator
        try:
            configurator.save(scope=scope)
            return f"\u2713 Config saved (scope: {scope})"
        except ValueError as e:
            return f"Error saving config: {e}"

    async def _handle_config_set(self, path: str, value: str) -> str:
        """Set a config value with automatic type inference (bool/int/float/string)."""
        configurator = self.configurator

        # Parse value type: bool → int → float → string
        parsed_value: Any
        if value.lower() == "true":
            parsed_value = True
        elif value.lower() == "false":
            parsed_value = False
        else:
            try:
                parsed_value = int(value)
            except ValueError:
                try:
                    parsed_value = float(value)
                except ValueError:
                    parsed_value = value  # Keep as string

        try:
            configurator.config_set(path, parsed_value)
            return f"\u2713 Set {path} = {parsed_value!r}"
        except (ValueError, RuntimeError) as e:
            return f"Error setting config: {e}"

    async def _render_legacy_config(self) -> str:
        """Render configuration using the legacy bundle display (fallback when no configurator)."""
        from ..console import console

        await self._render_bundle_config(self._display_bundle_name, console)

        # Also show loaded agents (available at runtime)
        # Note: agents can be a dict (resolved agents) or list/other format (config)
        loaded_agents = self.session.config.get("agents", {})
        if isinstance(loaded_agents, dict) and loaded_agents:
            # Filter out config keys (dirs, include, inline) - only show resolved agent names
            agent_names = [
                k for k in loaded_agents if k not in ("dirs", "include", "inline")
            ]
            if agent_names:
                console.print()  # Blank line after Agents: section
                console.print("[bold]Loaded Agents:[/bold]")
                for name in sorted(agent_names):
                    console.print(f"  {name}")

        return ""  # Output already printed

    async def _render_bundle_config(self, bundle_name: str, console: Any) -> None:
        """Render bundle configuration display."""
        config = self.session.config

        console.print(f"\n[bold]Bundle Configuration:[/bold] {bundle_name}\n")

        # Session section
        session_config = config.get("session", {})
        if session_config:
            console.print("[bold]Session:[/bold]")
            for field in ["orchestrator", "context"]:
                if field in session_config:
                    value = session_config[field]
                    if isinstance(value, dict) and "module" in value:
                        console.print(f"  {field}:")
                        console.print(f"    module: {value.get('module', 'unknown')}")
                        if value.get("source"):
                            source = value["source"]
                            if len(source) > 60:
                                source = source[:57] + "..."
                            console.print(f"    source: {source}")
                    else:
                        console.print(f"  {field}: {value}")

        # Providers section
        providers = config.get("providers", [])
        if providers:
            console.print("\n[bold]Providers:[/bold]")
            for provider in providers:
                if isinstance(provider, dict):
                    module = provider.get("module", "unknown")
                    console.print(f"  - {module}")
                    if provider.get("source"):
                        source = provider["source"]
                        if len(source) > 60:
                            source = source[:57] + "..."
                        console.print(f"    source: {source}")
                    if provider.get("config"):
                        console.print("    config:")
                        for key, val in provider["config"].items():
                            console.print(f"      {key}: {val}")

        # Tools section
        tools = config.get("tools", [])
        if tools:
            console.print("\n[bold]Tools:[/bold]")
            for tool in tools:
                if isinstance(tool, dict):
                    module = tool.get("module", "unknown")
                    console.print(f"  - {module}")
                elif isinstance(tool, str):
                    console.print(f"  - {tool}")

        # Hooks section
        hooks = config.get("hooks", [])
        if hooks:
            console.print("\n[bold]Hooks:[/bold]")
            for hook in hooks:
                if isinstance(hook, dict):
                    module = hook.get("module", "unknown")
                    console.print(f"  - {module}")
                elif isinstance(hook, str):
                    console.print(f"  - {hook}")


__all__ = ["CommandConfigDashboardMixin"]
