"""ItemRenderer — unified view renderer for ItemRecord objects.

Three view modes:
- compact  : one line per item — ``[on]  <name>  <root-bundle>``
- regular  : multi-line attributed output delegating to DashboardRenderer
- detailed : full single-item drilldown with origin chain + include_path

JSON output is handled by ``render_json`` (separate method).

Usage::

    from amplifier_app_cli.ui.item_renderer import ItemRenderer

    renderer = ItemRenderer(console)
    renderer.render(tools_items, view="compact", category="tools")
    renderer.render_one(item, view="detailed")
    renderer.render_json(items)
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

from amplifier_app_cli.utils.error_format import escape_markup

from .dashboard_renderer import (
    DashboardRenderer,
    _item_get,
    _item_get_behaviors,
    _item_get_config,
    _redact_value,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CATEGORY_TITLE: dict[str, str] = {
    "providers": "providers",
    "provider": "providers",
    "tools": "tools",
    "tool": "tools",
    "hooks": "hooks",
    "hook": "hooks",
    "context": "context",
    "agents": "agents",
    "agent": "agents",
    "behaviors": "behaviors",
    "behavior": "behaviors",
    "session": "session",
    "session.orchestrator": "session",
    "session.context": "session",
}


def _canonical_title(category: str, section_title: str | None) -> str:
    """Return a human-readable section title for a category."""
    if section_title:
        return section_title
    return _CATEGORY_TITLE.get(category, category)


def _item_direct_claimant(item: Any) -> str | None:
    """Return the first direct claimant bundle name for compact view.

    The direct claimant is the first ``Origin`` entry where
    ``via_behavior is None`` (self-introduced, not propagated).  Falls back
    to the first origin entry if none are direct.

    For dict-based items (test mocks), falls back to the first element of the
    'behaviors' or 'source' field.
    """
    if hasattr(item, "origins"):
        origins = item.origins or []
        # Prefer the first direct claimant (via_behavior=None)
        for o in origins:
            if getattr(o, "via_behavior", None) is None:
                return o.bundle
        # Fallback: first origin regardless
        return origins[0].bundle if origins else None
    # Dict-based (legacy / test mock)
    behaviors = _item_get_behaviors(item)
    return behaviors[0] if behaviors else None


def _item_all_bundle_names(item: Any) -> list[str]:
    """Return all distinct bundle names from origins, for regular view.

    Order: direct claimants first (via_behavior is None), then propagated
    entries by insertion order.
    """
    if hasattr(item, "origins"):
        origins = item.origins or []
        seen: set[str] = set()
        result: list[str] = []
        # Direct claimants first
        for o in origins:
            if getattr(o, "via_behavior", None) is None and o.bundle not in seen:
                seen.add(o.bundle)
                result.append(o.bundle)
        # Propagated entries
        for o in origins:
            if o.bundle not in seen:
                seen.add(o.bundle)
                result.append(o.bundle)
        return result
    # Dict-based fallback
    return _item_get_behaviors(item) or []


# Keep backward-compat alias (used by tests and DashboardRenderer internally)
def _item_root_bundle(item: Any) -> str | None:
    """Return the first direct claimant bundle for compact view (compat alias)."""
    return _item_direct_claimant(item)


def _serialize_item(item: Any) -> Any:
    """Serialize an item to a JSON-safe dict.

    Works with ``dataclasses.ItemRecord`` (uses ``dataclasses.asdict``) and
    plain ``dict`` items (returned as-is).
    """
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return dataclasses.asdict(item)
    if isinstance(item, dict):
        return item
    # Fallback: try __dict__
    return vars(item) if hasattr(item, "__dict__") else str(item)


# ---------------------------------------------------------------------------
# ItemRenderer
# ---------------------------------------------------------------------------


class ItemRenderer:
    """Renders item lists and single items in compact, regular, or detailed view.

    All rendering writes directly to ``console``; methods return ``None``.
    Use ``render_json`` for JSON output.

    The renderer handles both ``ItemRecord`` dataclass instances (from the
    foundation layer) and plain ``dict`` items (from legacy code and test
    fixtures) so that it works in both production and test contexts.
    """

    def __init__(self, console: Any) -> None:
        self._console = console
        self._dr = DashboardRenderer(console)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(
        self,
        items: list[Any],
        *,
        view: str,
        category: str,
        section_title: str | None = None,
        trailing_newline: bool = True,
    ) -> None:
        """Render a list of items in the requested view mode.

        Args:
            items:          List of ``ItemRecord`` or dict items.
            view:           ``"compact"``, ``"regular"``, or ``"detailed"``.
            category:       Category key (``"tools"``, ``"providers"``, etc.).
            section_title:  Override the section header; derived from *category*
                            when ``None``.
            trailing_newline: Emit a blank line after the section.

        Note:
            ``view="detailed"`` applied to a list renders as ``"regular"``
            (the multi-line DashboardRenderer output) rather than per-item
            full drilldown.  Use ``render_one`` for single-item detailed view.
        """
        if view == "compact":
            self._render_compact(
                items,
                section_title=_canonical_title(category, section_title),
                trailing_newline=trailing_newline,
            )
        elif view in ("regular", "detailed"):
            # "detailed" for a list falls back to regular multi-line output
            self._render_regular(
                items,
                category=category,
                section_title=section_title,
                trailing_newline=trailing_newline,
            )

    def render_one(
        self,
        item: Any,
        *,
        view: str = "detailed",
    ) -> None:
        """Render a single item.

        Args:
            item: ``ItemRecord`` or dict.
            view: Rendering mode; defaults to ``"detailed"`` (full drilldown).
        """
        if view == "compact":
            self._render_compact([item], section_title=None, trailing_newline=True)
        elif view == "regular":
            category = _item_get(item, "category", "tool") or "tool"
            self._render_regular(
                [item],
                category=str(category),
                section_title=None,
                trailing_newline=True,
            )
        else:
            self._render_detailed_one(item)

    def render_json(self, items: list[Any] | Any) -> None:
        """Print items serialized as JSON to stdout.

        Accepts a single item or a list of items.  ``ItemRecord`` dataclasses
        are serialized via ``dataclasses.asdict``; plain dicts are passed
        through unchanged.

        Uses ``sys.stdout.write`` rather than Rich console to avoid line-wrap
        corruption of JSON string values.

        The JSON shape is the public schema (experimental — subject to change
        until a future version tag).  See ``docs/OUTPUT_FORMATS.md``.
        """
        import sys

        if isinstance(items, list):
            payload = [_serialize_item(i) for i in items]
        else:
            payload = _serialize_item(items)
        sys.stdout.write(json.dumps(payload, indent=2, default=str))
        sys.stdout.write("\n")

    # ------------------------------------------------------------------
    # compact view
    # ------------------------------------------------------------------

    def _render_compact(
        self,
        items: list[Any],
        *,
        section_title: str | None,
        trailing_newline: bool,
    ) -> None:
        """One line per item: ``  [on]  <name>  <direct-claimant>``."""
        if not items:
            return

        enabled = sum(1 for x in items if _item_get(x, "enabled", True))
        disabled = len(items) - enabled
        count = f"{enabled} active" + (f", {disabled} disabled" if disabled else "")

        if section_title:
            self._console.print(f"── {section_title} ({count}) ──")

        for item in items:
            is_on = _item_get(item, "enabled", True)
            name = escape_markup(str(_item_get(item, "name", "unknown")))
            # Compact view: show the first direct claimant (via_behavior=None)
            claimant = _item_direct_claimant(item)

            status = "\\[on]" if is_on else "\\[off]"
            line = f"  {status}  {name}"
            if claimant:
                line += f"  {escape_markup(claimant)}"
            if not is_on:
                line += "  ← disabled"
            self._console.print(line)

        if trailing_newline:
            self._console.print()

    # ------------------------------------------------------------------
    # regular view (delegates to DashboardRenderer)
    # ------------------------------------------------------------------

    def _render_regular(
        self,
        items: list[Any],
        *,
        category: str,
        section_title: str | None,
        trailing_newline: bool,
    ) -> None:
        """Multi-line attributed output — delegates to DashboardRenderer."""
        cat = category.lower()

        if cat in ("tools", "tool"):
            self._dr.render_tools_section(items, trailing_newline=trailing_newline)
        elif cat in ("hooks", "hook"):
            self._dr.render_hooks_section(items, trailing_newline=trailing_newline)
        elif cat in ("providers", "provider"):
            self._dr.render_providers_section(items, trailing_newline=trailing_newline)
        elif cat in ("context",):
            self._dr.render_attributed_section(
                items,
                section_title or "context",
                trailing_newline=trailing_newline,
            )
        elif cat in ("agents", "agent"):
            self._dr.render_attributed_section(
                items,
                section_title or "agents",
                trailing_newline=trailing_newline,
            )
        elif cat in ("behaviors", "behavior"):
            self._dr.render_behaviors_section(items, trailing_newline=trailing_newline)
        else:
            # Generic fallback — simple section
            self._dr.render_simple_section(
                section_title or cat,
                items,
                trailing_newline=trailing_newline,
            )

    # ------------------------------------------------------------------
    # detailed view (single item)
    # ------------------------------------------------------------------

    def _render_detailed_one(self, item: Any) -> None:
        """Full drilldown: origin chain, include_paths, config, runtime_injection."""
        is_on = _item_get(item, "enabled", True)
        name = str(_item_get(item, "name", "unknown"))
        module_id = _item_get(item, "module_id", None)
        source_uri = _item_get(item, "source_uri", None)
        cfg = _item_get_config(item)
        runtime_injection: str | None = None

        # Prefer ItemRecord attributes where available
        origins: list[Any] = []
        # Support both plural include_paths (new) and singular include_path (legacy)
        include_paths: list[list[Any]] = []
        if hasattr(item, "origins"):
            origins = item.origins or []
        if hasattr(item, "include_paths"):
            raw = item.include_paths or []
            # Normalise: flat list → wrap as single path, nested list → use as-is
            if raw and not isinstance(raw[0], list):
                include_paths = [raw]
            else:
                include_paths = raw
        elif hasattr(item, "include_path"):
            # Legacy singular field
            raw_path = item.include_path or []
            if raw_path:
                include_paths = [raw_path]
        if hasattr(item, "runtime_injection"):
            runtime_injection = item.runtime_injection

        on_str = "[on]" if is_on else "[off]"
        self._console.print()
        safe_name = escape_markup(name)
        if is_on:
            self._console.print(f"  [green]\\{on_str}[/green]  {safe_name}")
        else:
            self._console.print(f"  [dim][red]\\{on_str}[/red]  {safe_name}[/dim]")

        indent = "        "

        # Module ID
        if module_id:
            self._console.print(
                f"[dim]{indent}module: {escape_markup(str(module_id))}[/dim]"
            )

        # Source URI
        if source_uri:
            self._console.print(
                f"[dim]{indent}source: {escape_markup(str(source_uri))}[/dim]"
            )

        # Origin chain (behavior-merge graph)
        if origins:
            self._console.print(f"[dim]{indent}chain:[/dim]")
            direct = [o for o in origins if getattr(o, "via_behavior", None) is None]
            propagated = [
                o for o in origins if getattr(o, "via_behavior", None) is not None
            ]
            for o in direct:
                bundle = escape_markup(getattr(o, "bundle", str(o)))
                self._console.print(f"[dim]{indent}  {bundle}  ← direct claimant[/dim]")
            for o in propagated:
                via = escape_markup(str(getattr(o, "via_behavior", "?")))
                bundle = escape_markup(getattr(o, "bundle", str(o)))
                self._console.print(f"[dim]{indent}  └─ {bundle}  (via {via})[/dim]")
        else:
            # Fallback for dict-based items: show behaviors
            behavior_names = _item_get_behaviors(item)
            if behavior_names:
                self._console.print(f"[dim]{indent}behaviors:[/dim]")
                for b in behavior_names:
                    self._console.print(f"[dim]{indent}  {escape_markup(b)}[/dim]")

        # Include paths (bundle-on-disk graph) — singular or plural
        if include_paths:
            if len(include_paths) == 1:
                # Single path: compact single-line format
                self._console.print(f"[dim]{indent}include_path:[/dim]")
                path_str = " → ".join(
                    escape_markup(getattr(s, "bundle", str(s)))
                    for s in include_paths[0]
                )
                self._console.print(f"[dim]{indent}  {path_str}[/dim]")
            else:
                # Multiple paths: one per line under include_paths:
                self._console.print(f"[dim]{indent}include_paths:[/dim]")
                for path in include_paths:
                    path_str = " → ".join(
                        escape_markup(getattr(s, "bundle", str(s))) for s in path
                    )
                    self._console.print(f"[dim]{indent}  {path_str}[/dim]")

        # Config
        if cfg and isinstance(cfg, dict):
            self._console.print(f"[dim]{indent}config:[/dim]")
            self._render_config_tree_detail(cfg, indent + "  ")

        # Runtime injection
        ri_label = (
            escape_markup(str(runtime_injection)) if runtime_injection else "none"
        )
        self._console.print(f"[dim]{indent}runtime_injection: {ri_label}[/dim]")
        self._console.print()

    def _render_config_tree_detail(self, cfg: dict, indent: str) -> None:
        """Render a config dict as a dim YAML-like tree (for detailed view)."""
        for k, v in cfg.items():
            redacted = _redact_value(k, v)
            if isinstance(v, dict) and v and redacted is v:
                self._console.print(f"[dim]{indent}{escape_markup(k)}:[/dim]")
                self._render_config_tree_detail(v, indent + "  ")
            elif isinstance(v, list) and v and redacted is v:
                self._console.print(f"[dim]{indent}{escape_markup(k)}:[/dim]")
                for list_item in v:
                    if isinstance(list_item, dict):
                        self._console.print(f"[dim]{indent}  -[/dim]")
                        self._render_config_tree_detail(list_item, indent + "    ")
                    else:
                        self._console.print(
                            f"[dim]{indent}  - {escape_markup(str(list_item))}[/dim]"
                        )
            else:
                self._console.print(
                    f"[dim]{indent}{escape_markup(k)}: {escape_markup(str(redacted))}[/dim]"
                )
