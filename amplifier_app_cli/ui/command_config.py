"""Configuration command routing and summary rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from amplifier_app_cli.runtime.session_state import coordinator_session_state
from amplifier_app_cli.ui.interaction_runtime_state import interaction_state_for

from .command_config_flags import parse_config_flags as _parse_config_flags
from .dashboard_renderer import DashboardRenderer
from .item_renderer import ItemRenderer
from .view_policy import resolve_view


class CommandConfigMixin:
    """Implement configuration routing for CommandProcessor."""

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
        ) -> str: ...

        async def _render_config_item(self, category: str, name: str) -> str: ...

        async def _handle_config_toggle(
            self, category: str, action: str, name: str
        ) -> str: ...

        async def _handle_config_diff(self) -> str: ...
        async def _handle_config_save(self, scope: str = "global") -> str: ...
        async def _handle_config_set(self, path: str, value: str) -> str: ...
        async def _render_legacy_config(self) -> str: ...

    def _render_simple_section(
        self,
        console: Any,
        title: str,
        items: list,
        *,
        trailing_newline: bool = True,
        show_config: bool = False,
    ) -> None:
        """Render a simple enabled/disabled section list (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_simple_section(
            title, items, trailing_newline=trailing_newline, show_config=show_config
        )

    def _render_hooks_section_v2(
        self,
        console: Any,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render hooks section listing ALL hooks individually (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_hooks_section(
            items, trailing_newline=trailing_newline
        )

    _CAT_LABELS: dict[str, str] = {
        "context": "context",
        "tools": "tools",
        "hooks": "hooks",
        "providers": "providers",
        "agents": "agents",
    }

    def _render_behaviors_section_v2(
        self,
        console: Any,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render behaviors section showing non-zero categories (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_behaviors_section(
            items, trailing_newline=trailing_newline
        )

    def _render_items_with_behavior_attribution(
        self,
        console: Any,
        items: list,
        section_name: str,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render a section with behavior attribution (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_attributed_section(
            items, section_name, trailing_newline=trailing_newline
        )

    def _render_context_section(
        self,
        console: Any,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render context section (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_attributed_section(
            items, "context", trailing_newline=trailing_newline
        )

    def _render_agents_section(
        self,
        console: Any,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render agents section (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_attributed_section(
            items, "agents", trailing_newline=trailing_newline
        )

    async def _get_config_display(self, args: str = "") -> str:
        """Display current configuration or handle subcommands.

        Parses args and dispatches to subcommand handlers:
        - No args → _render_config_help()
        - 'show' [--compact|--detailed|--format json] → ItemRenderer dashboard
        - 'show' <category> <name> → ItemRenderer single-item detail
        - 'diff' → _handle_config_diff()
        - 'save' [--scope <scope>] → _handle_config_save(scope)
        - 'set' <path> <value> → _handle_config_set(path, value)
        - <category> [--compact|--detailed|--format json] → ItemRenderer category list
        - <category> disable/enable <name> → _handle_config_toggle(...)
        - <category> <name> → ItemRenderer single-item detail
        """
        raw_parts = args.strip().split() if args.strip() else []
        if raw_parts and raw_parts[0].lower() == "debug":
            state = coordinator_session_state(self.session.coordinator)
            current = bool(state.get("ui.show_debug"))
            if len(raw_parts) == 1:
                return f"Debug transcript details: {'on' if current else 'off'}"
            requested = raw_parts[1].lower()
            if requested not in {"on", "off"} or len(raw_parts) != 2:
                return "Usage: `/config debug <on|off>`"
            enabled = requested == "on"
            state["ui.show_debug"] = enabled
            return f"Debug transcript details: {'on' if enabled else 'off'}"

        configurator = getattr(self, "configurator", None)
        if configurator is None:
            return await self._render_legacy_config()

        if not raw_parts:
            return self._render_config_help()

        # Strip global flags from the parts list
        remaining_parts, compact_flag, detailed_flag, trees_flag, fmt = (
            _parse_config_flags(raw_parts)
        )

        if not remaining_parts:
            # Only flags, no subcommand — show dashboard with flags applied
            return await self._render_config_dashboard_v2(
                compact=compact_flag, detailed=detailed_flag, trees=trees_flag, fmt=fmt
            )

        subcmd = remaining_parts[0].lower()

        # ── show ──────────────────────────────────────────────────────────────
        if subcmd == "show":
            show_parts = remaining_parts[1:]

            _VALID_CATEGORIES = {
                "context",
                "tools",
                "hooks",
                "providers",
                "agents",
                "behaviors",
            }

            if len(show_parts) >= 2 and show_parts[0].lower() in _VALID_CATEGORIES:
                # /config show <category> <name>
                category = show_parts[0].lower()
                name = show_parts[1]
                return await self._render_config_item(category, name)

            if len(show_parts) == 1 and show_parts[0].lower() in _VALID_CATEGORIES:
                # /config show <category>  — treat as category list
                return await self._render_config_category(
                    show_parts[0].lower(),
                    compact=compact_flag,
                    detailed=detailed_flag,
                    trees=trees_flag,
                    fmt=fmt,
                )

            # /config show  (with optional flags)
            return await self._render_config_dashboard_v2(
                compact=compact_flag, detailed=detailed_flag, trees=trees_flag, fmt=fmt
            )

        # ── diff ──────────────────────────────────────────────────────────────
        if subcmd == "diff":
            return await self._handle_config_diff()

        # ── save ──────────────────────────────────────────────────────────────
        if subcmd == "save":
            scope = "global"
            save_remaining = remaining_parts[1:]
            for i, p in enumerate(save_remaining):
                if p == "--scope" and i + 1 < len(save_remaining):
                    scope = save_remaining[i + 1]
            return await self._handle_config_save(scope)

        # ── set ───────────────────────────────────────────────────────────────
        if subcmd == "set":
            if len(remaining_parts) < 3:
                return "Usage: `/config set <path> <value>`"
            path = remaining_parts[1]
            value = remaining_parts[2]
            return await self._handle_config_set(path, value)

        # ── <category> ────────────────────────────────────────────────────────
        _VALID_CATEGORIES = {
            "context",
            "tools",
            "hooks",
            "providers",
            "agents",
            "behaviors",
        }

        if subcmd in _VALID_CATEGORIES:
            category = subcmd
            cat_remaining = remaining_parts[1:]

            if not cat_remaining:
                # /config <category>  [--flags]
                return await self._render_config_category(
                    category,
                    compact=compact_flag,
                    detailed=detailed_flag,
                    trees=trees_flag,
                    fmt=fmt,
                )

            if len(cat_remaining) >= 2 and cat_remaining[0].lower() in (
                "disable",
                "enable",
            ):
                action = cat_remaining[0].lower()
                name = cat_remaining[1]
                return await self._handle_config_toggle(category, action, name)

            # /config <category> <name>  → single-item detail
            name = cat_remaining[0]
            return await self._render_config_item(category, name)

        # Unknown subcommand — show dashboard
        return await self._render_config_dashboard_v2(
            compact=compact_flag, detailed=detailed_flag, trees=trees_flag, fmt=fmt
        )

    def _render_config_help(self) -> str:
        """Render a concise help listing of /config subcommands."""
        from ..console import console

        console.print()
        console.print("[bold]/config[/bold] — Session Configuration")
        console.print()
        console.print(
            "  [bold]/config show[/bold]                       Show full live config tree"
        )
        console.print(
            "  [bold]/config show --detailed[/bold]            Multi-line attributed view"
        )
        console.print(
            "  [bold]/config show --trees[/bold]               Per-item tree drilldown view"
        )
        console.print(
            "  [bold]/config <category>[/bold]                 List items in a category"
        )
        console.print(
            "  [bold]/config <category> <name>[/bold]          Show detailed config for one item"
        )
        console.print(
            "  [bold]/config <category> disable <n>[/bold]     Disable an item"
        )
        console.print(
            "  [bold]/config <category> enable <n>[/bold]      Re-enable an item"
        )
        console.print(
            "  [bold]/config set <path> <value>[/bold]         Set a config value"
        )
        console.print(
            "  [bold]/config diff[/bold]                       Show changes since session start"
        )
        console.print(
            "  [bold]/config save[/bold] [--scope project|global]  Persist to settings.yaml"
        )
        console.print()
        console.print(
            "  Categories: context, tools, hooks, providers, agents, behaviors"
        )
        console.print("  Hooks are read-only (visible but not toggleable)")
        console.print()
        return ""

    def _render_providers_section_v2(
        self,
        console: Any,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render providers section with source URI + full config tree (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_providers_section(
            items, trailing_newline=trailing_newline
        )

    def _render_tools_section(
        self,
        console: Any,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render tools section with module ID + attribution (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_tools_section(
            items, trailing_newline=trailing_newline
        )

    async def _render_config_dashboard(self) -> str:
        """Render the full configuration dashboard using SessionConfigurator."""
        from ..console import console

        configurator = self.configurator

        # Collect all list data from the configurator
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

        renderer = DashboardRenderer(console)

        # Render header
        renderer.render_header(self._display_bundle_name, active_mode, change_count)

        # Render session section (orchestrator info from coordinator.config)
        raw_config = self.session.coordinator.config
        session_config = (
            raw_config.get("session", {}) if isinstance(raw_config, dict) else {}
        )
        if session_config and isinstance(session_config, dict):
            console.print("── session ──")
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
                                renderer.render_config_tree({k: v}, "      ", dim=True)
                    else:
                        console.print(f"  {field}: {value}")
            console.print()

        # Render all sections via DashboardRenderer
        renderer.render_providers_section(providers_items)
        renderer.render_tools_section(tools_items)
        renderer.render_hooks_section(hooks_items)
        renderer.render_attributed_section(context_items, "context")
        renderer.render_attributed_section(agents_items, "agents")
        renderer.render_behaviors_section(behaviors_items)

        return ""  # Output already printed via console

    def _render_category_summary(
        self, console: Any, category: str, items: list
    ) -> None:
        """Render one category section using the appropriate specialized renderer."""
        renderer = DashboardRenderer(console)
        if category == "tools":
            renderer.render_tools_section(items)
        elif category == "hooks":
            renderer.render_hooks_section(items)
        elif category == "providers":
            renderer.render_providers_section(items)
        elif category in ("context", "agents"):
            renderer.render_attributed_section(items, category)
        elif category == "behaviors":
            renderer.render_behaviors_section(items)
        else:
            self._render_simple_section(console, category.capitalize(), items)

    async def _render_config_category(
        self,
        category: str,
        *,
        compact: bool = False,
        detailed: bool = False,
        trees: bool = False,
        fmt: str = "text",
    ) -> str:
        """Render a per-category list view using ItemRenderer.

        Args:
            category: One of context / tools / hooks / providers / agents / behaviors.
            compact:  Force compact (one-line) view.
            detailed: Force detailed (multi-line) view.  For lists this renders
                      as the "regular" multi-line DashboardRenderer output.
            trees:    Force tree-style per-item drilldown.  Takes precedence over
                      ``detailed`` (last flag wins in the flag parser).
            fmt:      ``"json"`` to emit JSON; anything else → text.
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

        if fmt == "json":
            ItemRenderer(console).render_json(items)
            return ""

        view = resolve_view(
            ("config", "category"),
            compact_flag=compact,
            detailed_flag=detailed,
        )
        # --trees overrides; for non-trees list contexts, "detailed" → "regular"
        if trees:
            view = "trees"
        elif view == "detailed":
            view = "regular"

        ItemRenderer(console).render(items, view=view, category=category)  # type: ignore[arg-type]
        return ""  # Output already printed via console


__all__ = ["CommandConfigMixin"]
