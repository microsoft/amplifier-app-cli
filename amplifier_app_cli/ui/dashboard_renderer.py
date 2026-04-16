"""DashboardRenderer — all /config dashboard rendering logic.

Extracted from CommandProcessor to keep the slash-command handler focused on
routing and coordination, not presentation details.
"""
from __future__ import annotations

from typing import Any

from amplifier_app_cli.utils.error_format import escape_markup

# ---------------------------------------------------------------------------
# Module-level sensitive-key handling (moved from CommandProcessor)
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_PATTERNS = ("key", "token", "secret", "password", "api_key")


def _redact_value(key: str, value: Any) -> Any:
    """Redact a config value if the key is sensitive and value is long enough.

    Returns the first 4 chars + '...redacted' for string values longer than
    20 characters whose key contains a sensitive keyword. Non-string values
    and short values are returned unchanged.
    """
    if not isinstance(value, str) or len(value) <= 20:
        return value
    key_lower = key.lower()
    for pattern in _SENSITIVE_KEY_PATTERNS:
        if pattern in key_lower:
            return f"{value[:4]}...redacted"
    return value


# ---------------------------------------------------------------------------
# DashboardRenderer
# ---------------------------------------------------------------------------


class DashboardRenderer:
    """Renders each section of the /config show dashboard.

    Constructed with a console object (Rich Console or compatible mock) and
    exposes one method per dashboard section.  All state is passed as
    arguments — the renderer itself is stateless beyond the console reference.
    """

    def __init__(self, console: Any) -> None:
        self._console = console

    # ------------------------------------------------------------------
    # Header / status
    # ------------------------------------------------------------------

    def render_header(
        self, bundle_name: str, active_mode: str, change_count: int
    ) -> None:
        """Render the dashboard header with bundle name and change status."""
        self._console.print()
        self._console.print(f"Active bundle: {bundle_name}")
        if change_count > 0:
            self._console.print(
                f"Mode: {active_mode} | Session changes: {change_count} items changed"
                " | /config save to persist"
            )
        else:
            self._console.print(f"Mode: {active_mode} | No changes from original")
        self._console.print()

    def format_status(self, active_mode: str, change_count: int) -> str:
        """Return the status line string (without printing)."""
        if change_count > 0:
            return (
                f"Mode: {active_mode} | Session changes: {change_count} items changed"
                " | /config save to persist"
            )
        return f"Mode: {active_mode} | No changes from original"

    def build_attribution(self, item: dict) -> str:
        """Build attribution string from a list of behaviors or source fallback."""
        behaviors = item.get("behaviors", [])
        if isinstance(behaviors, list) and behaviors:
            return ", ".join(str(b) for b in behaviors if b)
        return str(item.get("source", "") or "")

    # ------------------------------------------------------------------
    # Config tree
    # ------------------------------------------------------------------

    def render_config_tree(
        self, cfg: dict, indent: str, *, dim: bool = False
    ) -> None:
        """Render a config dict as an indented YAML-like tree."""
        _d = "[dim]" if dim else ""
        _e = "[/dim]" if dim else ""
        for k, v in cfg.items():
            redacted = _redact_value(k, v)
            if isinstance(v, dict) and v and redacted is v:
                self._console.print(f"{_d}{indent}{k}:{_e}")
                self.render_config_tree(v, indent + "  ", dim=dim)
            elif isinstance(v, list) and v and redacted is v:
                self._console.print(f"{_d}{indent}{k}:{_e}")
                for list_item in v:
                    if isinstance(list_item, dict):
                        self._console.print(f"{_d}{indent}  -{_e}")
                        self.render_config_tree(list_item, indent + "    ", dim=dim)
                    else:
                        self._console.print(f"{_d}{indent}  - {list_item}{_e}")
            else:
                self._console.print(f"{_d}{indent}{k}: {redacted}{_e}")

    # ------------------------------------------------------------------
    # Wrapped items
    # ------------------------------------------------------------------

    def print_wrapped_items(
        self,
        label: str,
        items: list,
        indent: str = "        ",
        max_width: int = 78,
        dim: bool = True,
    ) -> None:
        """Print ``label: item1, item2, ...`` with continuation-line indentation."""
        if not items:
            return
        prefix = f"{indent}{label}: "
        continuation = " " * len(prefix)
        start = "[dim]" if dim else ""
        end = "[/dim]" if dim else ""

        lines: list[str] = []
        current = prefix
        for i, item in enumerate(items):
            sep = "," if i < len(items) - 1 else ""
            piece = str(item) + sep
            if current != prefix and len(current) + 1 + len(piece) > max_width:
                lines.append(current)
                current = continuation + piece
            else:
                if current == prefix:
                    current += piece
                else:
                    current += " " + piece
        lines.append(current)

        for line in lines:
            self._console.print(f"{start}{line}{end}")

    # ------------------------------------------------------------------
    # Simple section (category list view)
    # ------------------------------------------------------------------

    def render_simple_section(
        self,
        title: str,
        items: list,
        *,
        trailing_newline: bool = True,
        show_config: bool = False,
    ) -> None:
        """Render a simple enabled/disabled section list with status indicators."""
        if not items:
            return
        enabled = sum(1 for x in items if x.get("enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} active" + (f", {disabled} disabled" if disabled else "")
        self._console.print(f"── {title.lower()} ({count}) ──")
        for item in items:
            is_on = item.get("enabled", True)
            status = "\\[on]" if is_on else "\\[off]"
            name = escape_markup(item.get("name", "unknown"))
            source = item.get("source", "")
            line = f"  {status}  {name}"
            if show_config:
                cfg = item.get("config", {})
                if cfg and isinstance(cfg, dict):
                    cfg_items = list(cfg.items())
                    truncated = len(cfg_items) > 3
                    pairs = [
                        f"{k}: {_redact_value(k, v)}" for k, v in cfg_items[:3]
                    ]
                    summary = "{" + ", ".join(pairs)
                    if truncated:
                        summary += ", ..."
                    summary += "}"
                    line += f"  {summary}"
            if source:
                line += f"  ({source})"
            if not is_on:
                line += "  ← disabled"
            self._console.print(line)
        if trailing_newline:
            self._console.print()

    # ------------------------------------------------------------------
    # Tools section
    # ------------------------------------------------------------------

    def render_tools_section(
        self,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render tools section with module ID + attribution on an indented second line."""
        if not items:
            return
        enabled = sum(1 for x in items if x.get("enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} active" + (f", {disabled} disabled" if disabled else "")
        self._console.print(f"── tools ({count}) ──")

        for item in items:
            is_on = item.get("enabled", True)
            name = item.get("name", "unknown")
            behaviors = item.get("behaviors", [])
            module_id = item.get("module_id", "") or ""

            if isinstance(behaviors, list) and behaviors:
                behavior_str = escape_markup(", ".join(str(b) for b in behaviors if b))
            else:
                behavior_str = ""

            safe_name = escape_markup(name)
            if is_on:
                self._console.print(f"  [green]\\[on][/green]  {safe_name}")
            else:
                self._console.print(f"  [dim][red]\\[off][/red]  {safe_name}[/dim]")

            safe_module_id = escape_markup(module_id) if module_id else ""
            module_str = (
                f"module: {safe_module_id}" if safe_module_id else "module: (unknown)"
            )
            if behavior_str:
                module_str += f"  ({behavior_str})"
            self._console.print(f"        [dim]{module_str}[/dim]")

            cfg = item.get("config", {})
            if cfg and isinstance(cfg, dict):
                self._console.print("[dim]        config:[/dim]")
                for k, v in cfg.items():
                    self.render_config_tree({k: v}, "          ", dim=True)

        if trailing_newline:
            self._console.print()

    # ------------------------------------------------------------------
    # Hooks section
    # ------------------------------------------------------------------

    def render_hooks_section(
        self,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render hooks section listing ALL hooks individually — no collapsing."""
        if not items:
            return
        enabled = sum(1 for x in items if x.get("enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} active" + (f", {disabled} disabled" if disabled else "")
        self._console.print(f"── hooks ({count}) ──")

        for item in items:
            is_on = item.get("enabled", True)
            name = item.get("name", "unknown")
            event = item.get("event", "")
            attribution = self.build_attribution(item)

            safe_name = escape_markup(name)
            if is_on:
                self._console.print(f"  [green]\\[on][/green]  {safe_name}")
            else:
                self._console.print(f"  [dim][red]\\[off][/red]  {safe_name}[/dim]")

            if event or attribution:
                safe_event = escape_markup(event)
                safe_attribution = escape_markup(attribution)
                detail = f"event: {safe_event}" if event else ""
                if attribution:
                    detail += (
                        f"  ({safe_attribution})" if detail else f"({safe_attribution})"
                    )
                self._console.print(f"        [dim]{detail}[/dim]")

        if trailing_newline:
            self._console.print()

    # ------------------------------------------------------------------
    # Providers section
    # ------------------------------------------------------------------

    def render_providers_section(
        self,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render the providers section with source URI and full config tree."""
        if not items:
            return
        enabled = sum(1 for x in items if x.get("enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} active" + (f", {disabled} disabled" if disabled else "")
        self._console.print(f"── providers ({count}) ──")

        for item in items:
            is_on = item.get("enabled", True)
            name = item.get("name", "unknown")
            attribution = escape_markup(self.build_attribution(item))

            name_padded = escape_markup(name).ljust(30)
            if is_on:
                line = f"  [green]\\[on][/green]  {name_padded}"
                if attribution:
                    line += f"  [dim]{attribution}[/dim]"
            else:
                line = f"  [dim][red]\\[off][/red]  {name_padded}"
                if attribution:
                    line += f"  {attribution}"
                line += "[/dim]"
            self._console.print(line)

            source_uri = item.get("source_uri", "")
            if source_uri:
                self._console.print(
                    f"        [dim]source: {escape_markup(source_uri)}[/dim]"
                )

            cfg = item.get("config", {})
            if cfg and isinstance(cfg, dict):
                self._console.print("[dim]        config:[/dim]")
                for k, v in cfg.items():
                    self._console.print(
                        f"[dim]          {k}: {_redact_value(k, v)}[/dim]"
                    )

        if trailing_newline:
            self._console.print()

    # ------------------------------------------------------------------
    # Attributed section (context + agents)
    # ------------------------------------------------------------------

    def render_attributed_section(
        self,
        items: list,
        section_name: str,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render a section where each item has attribution on a single line."""
        if not items:
            return
        enabled = sum(1 for x in items if x.get("enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} active" + (f", {disabled} disabled" if disabled else "")
        self._console.print(f"── {section_name} ({count}) ──")

        for item in items:
            is_on = item.get("enabled", True)
            name = escape_markup(item.get("name", "unknown"))

            behaviors = item.get("behaviors", [])
            if isinstance(behaviors, list) and behaviors:
                behavior_str = escape_markup(", ".join(behaviors))
            else:
                behavior_str = escape_markup(item.get("source", "") or "")

            if is_on:
                line = f"  [green]\\[on][/green]  {name}"
                if behavior_str:
                    line += f"  [dim]{behavior_str}[/dim]"
            else:
                line = f"  [dim][red]\\[off][/red]  {name}"
                if behavior_str:
                    line += f"  {behavior_str}"
                line += "[/dim]"
            self._console.print(line)

        if trailing_newline:
            self._console.print()

    # ------------------------------------------------------------------
    # Behaviors section
    # ------------------------------------------------------------------

    def render_behaviors_section(
        self,
        items: list,
        *,
        trailing_newline: bool = True,
    ) -> None:
        """Render behaviors section showing non-zero categories with item names."""
        if not items:
            return
        enabled = sum(1 for x in items if x.get("enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} composed" + (f", {disabled} disabled" if disabled else "")
        self._console.print(f"── behaviors ({count}) ──")

        _CAT_ORDER = ("context", "tools", "hooks", "providers", "agents")
        _CAT_LABELS: dict[str, str] = {
            "context": "context",
            "tools": "tools",
            "hooks": "hooks",
            "providers": "providers",
            "agents": "agents",
        }

        for item in items:
            is_on = item.get("enabled", True)
            name = item.get("name", "unknown")
            root_ns = item.get("root_namespace") or ""

            safe_name = escape_markup(name)
            if is_on:
                self._console.print(f"  [green]\\[on][/green]  {safe_name}")
            else:
                self._console.print(f"  [dim][red]\\[off][/red]  {safe_name}[/dim]")

            contributions = item.get("contributions", {})
            if isinstance(contributions, dict):
                for cat in _CAT_ORDER:
                    cat_items = contributions.get(cat, [])
                    if not isinstance(cat_items, list) or not cat_items:
                        continue
                    label = _CAT_LABELS.get(cat, cat)
                    raw_names = [
                        n.split(":", 1)[1] if ":" in n else n for n in cat_items
                    ]
                    if root_ns:
                        ns_prefix = root_ns + ":"
                        names = [
                            n[len(ns_prefix) :] if n.startswith(ns_prefix) else n
                            for n in raw_names
                        ]
                    else:
                        names = raw_names
                    self.print_wrapped_items(
                        label, names, indent="        ", max_width=78
                    )

        if trailing_newline:
            self._console.print()
