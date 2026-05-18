"""Attribution chain helpers — deduplication and truncation.

Public API consumed by both ``DashboardRenderer`` and ``ItemRenderer``.
These helpers operate on plain ``list[str]`` so they are easy to unit-test
in isolation without any mock items or console objects.
"""

from __future__ import annotations


def dedupe_behavior_chain(chain: list[str]) -> list[str]:
    """Remove unsuffixed bundle names when their ``-behavior`` sibling is present.

    When a chain contains both ``X`` and ``X-behavior``, the unsuffixed ``X``
    is dropped; the more-specific ``X-behavior`` is kept in its original
    position.  Multiple such pairs in one chain are each handled independently.

    This deduplication should be applied **before** :func:`truncate_attribution_chain`
    so the truncation threshold uses the deduplicated count.

    Args:
        chain: Ordered list of bundle name strings.

    Returns:
        New list with unsuffixed duplicates removed; original order preserved.

    Examples:
        >>> dedupe_behavior_chain(["X", "X-behavior", "foundation"])
        ['X-behavior', 'foundation']
        >>> dedupe_behavior_chain(["X-behavior", "X", "foundation"])
        ['X-behavior', 'foundation']
        >>> dedupe_behavior_chain(["X", "Y"])
        ['X', 'Y']
        >>> dedupe_behavior_chain(["X-behavior", "Y"])
        ['X-behavior', 'Y']
    """
    chain_set = set(chain)
    return [entry for entry in chain if entry + "-behavior" not in chain_set]


def truncate_attribution_chain(chain: list[str]) -> str:
    """Format chain as a display string, eliding long middle sections.

    - 1–3 entries: rendered verbatim as comma-separated text.
    - 4+ entries: ``first, second, …, last`` — direct claimant and root
      are always shown; intermediate entries are elided with the Unicode
      horizontal ellipsis character (U+2026).

    The motivation: chains longer than three entries push attribution labels
    past comfortable reading width.  The direct claimant (first) and the
    root (last) are the most informative; intermediate propagation hops are
    noise in compact and regular views (they remain visible in the detailed
    single-item view).

    Args:
        chain: Ordered list of bundle name strings (should already be deduped).

    Returns:
        Display string, e.g. ``"foundation"`` or ``"a, b, …, e"``.

    Examples:
        >>> truncate_attribution_chain(["a"])
        'a'
        >>> truncate_attribution_chain(["a", "b", "c"])
        'a, b, c'
        >>> truncate_attribution_chain(["a", "b", "c", "d", "e"])
        'a, b, \u2026, e'
    """
    if len(chain) <= 3:
        return ", ".join(chain)
    return f"{chain[0]}, {chain[1]}, \u2026, {chain[-1]}"
