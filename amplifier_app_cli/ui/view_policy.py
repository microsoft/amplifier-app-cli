"""view_policy — single source of truth for default view modes per command context.

Usage::

    from amplifier_app_cli.ui.view_policy import resolve_view, DEFAULT_VIEW

    view = resolve_view(
        ("config", "show"),
        compact_flag=args.compact,
        detailed_flag=args.detailed,
    )
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Default view table
# ---------------------------------------------------------------------------

#: Maps command context tuples to their default view mode string.
#: The ``--compact`` / ``--detailed`` flags override these per-call.
DEFAULT_VIEW: dict[tuple[str, ...], str] = {
    # /config show  — multi-category dashboard; tight one-liner list
    ("config", "show"): "compact",
    # /config <category>  — single-category list; multi-line attributed output
    ("config", "category"): "regular",
    # /config show <category> <name>  — single-item full drilldown
    ("config", "item"): "detailed",
    # --- Commit-3 sites (not yet migrated; declared here for completeness) ---
    ("bundle", "list"): "compact",
    ("bundle", "show"): "detailed",
    ("module", "list"): "compact",
    ("module", "show"): "detailed",
    ("provider", "list"): "regular",
    ("tool", "list"): "compact",
    ("tool", "info"): "detailed",
    ("source", "list"): "compact",
    ("session", "list"): "compact",
    ("session", "show"): "detailed",
    ("routing", "list"): "regular",
    ("routing", "show"): "detailed",
    ("agents", "list"): "compact",
    ("agents", "show"): "detailed",
    ("module", "override", "list"): "compact",
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_view(
    context: tuple[str, ...],
    *,
    compact_flag: bool = False,
    detailed_flag: bool = False,
) -> str:
    """Return the effective view mode for a command context.

    Flag precedence (highest wins):
        1. ``compact_flag=True``  → ``"compact"``
        2. ``detailed_flag=True`` → ``"detailed"``
        3. ``DEFAULT_VIEW[context]`` if present
        4. ``"regular"`` fallback

    Args:
        context:       Command context key, e.g. ``("config", "show")``.
        compact_flag:  True when the user passed ``--compact``.
        detailed_flag: True when the user passed ``--detailed``.

    Returns:
        One of ``"compact"``, ``"regular"``, or ``"detailed"``.

    Example::

        >>> resolve_view(("config", "show"))
        'compact'
        >>> resolve_view(("config", "show"), detailed_flag=True)
        'detailed'
        >>> resolve_view(("config", "category"), compact_flag=True)
        'compact'
    """
    if compact_flag:
        return "compact"
    if detailed_flag:
        return "detailed"
    return DEFAULT_VIEW.get(context, "regular")
