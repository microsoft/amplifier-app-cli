"""Cell-aware rendering for the persistent two-zone REPL footer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from functools import partial

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.utils import get_cwidth

from .key_bindings_table import hint_label

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_CAPABILITY_ORDER = (
    "read",
    "test",
    "write",
    "net",
    "spend",
    "outside-project",
    "subagent",
)
_CAPABILITY_INDEX = {name: index for index, name in enumerate(_CAPABILITY_ORDER)}
_COMPACT_CAPABILITIES = {
    "read": "r",
    "test": "t",
    "write": "w",
    "net": "n",
    "spend": "$",
    "outside-project": "out",
    "subagent": "sub",
}
# At/above this width `mode <id>` survives by abbreviating the trust dial (spec 6).
_MODE_PREFIX_MIN_WIDTH = 100


def format_bottom_toolbar_text(
    *,
    bundle_name: str,
    session_id: str | None,
    active_mode: str | None,
    is_running: bool = False,
    queued_count: int = 0,
    activity_label: str | None = None,
    tasks_available: bool = False,
    image_paste_available: bool = False,
    task_summary: str | None = None,
    session_cost: Decimal | float | str | None = None,
    trust_summary: str | None = None,
    permission_mode: str | None = None,
    last_yield: str | None = None,
    needs_attention_count: int = 0,
    approval_pending: bool = False,
    palette_open: bool = False,
    lane_focused: bool = False,
    max_width: int | None = None,
    hint_overrides: Mapping[str, str] | None = None,
) -> str:
    """Render persistent state left and at most three contextual hints right."""
    # These belong in the live/notice rows, not the footer.
    del activity_label, task_summary, image_paste_available
    mode = _identifier(active_mode or "chat", 12)
    posture = _posture_variants(
        mode,
        _identifier(permission_mode or mode, 12),
        trust_summary,
    )
    bundle = _clean(bundle_name).removeprefix("bundle:") or "unknown"
    session = _clean(session_id or "new")[:4] or "new"
    cost = _format_session_cost(session_cost)
    yield_glyph = _clean(last_yield or "")
    if yield_glyph:
        cost = f"{cost} {_first_token(yield_glyph, 2)}"

    needs_wide = (
        f"{needs_attention_count} decision"
        f"{'s' if needs_attention_count != 1 else ''} waiting · "
        f"{hint_label('show_needs_you', hint_overrides)}"
        if needs_attention_count > 0
        else ""
    )
    needs_compact = (
        f"needs-you {needs_attention_count}" if needs_attention_count > 0 else ""
    )
    queued = f"q{queued_count}" if queued_count > 0 else ""

    def tier(posture_text: str, bundle_cells: int, tier_cost: str, needs: str) -> str:
        return _join_state(
            posture_text,
            _identifier(bundle, bundle_cells),
            session,
            tier_cost,
            needs,
            queued,
        )

    full_tier = tier(posture.full, 24, cost, needs_wide)
    compact_tier = tier(posture.compact, 14, cost, needs_compact)
    tight_tier = tier(posture.tight, 10, cost.replace(" ", ""), needs_compact)
    tiers = _unique((full_tier, compact_tier, tight_tier))
    wide_compact_tier = tier(posture.wide_compact, 14, cost, needs_compact)
    wide_tight_tier = tier(posture.wide_tight, 14, cost, needs_compact)
    wide_tiers = _unique(
        (full_tier, wide_compact_tier, wide_tight_tier, compact_tier, tight_tier)
    )
    essential_tier = _join_state(
        posture.tight, cost.replace(" ", ""), needs_compact, queued
    )
    hints = _hint_levels(
        is_running=is_running,
        tasks_available=tasks_available,
        approval_pending=approval_pending,
        palette_open=palette_open,
        lane_focused=lane_focused,
        hint_overrides=hint_overrides,
    )
    if max_width is None:
        return _render_two_zones(tiers[0], hints[0], None)

    width = max(1, max_width)
    state_tiers = wide_tiers if width >= _MODE_PREFIX_MIN_WIDTH else tiers
    candidate_states = state_tiers + ((essential_tier,) if approval_pending else ())
    multi_hints = tuple(level for level in hints if len(level) >= 2)
    single_hints = tuple(level for level in hints if len(level) == 1)
    for hint_level in multi_hints:
        for state in candidate_states:
            if _zones_width(state, hint_level) <= width:
                return _render_two_zones(state, hint_level, width)
    for hint_level in single_hints:
        for state in candidate_states:
            if _zones_width(state, hint_level) <= width:
                return _render_two_zones(state, hint_level, width)
    for state in state_tiers:
        if get_cwidth(state) <= width:
            return _render_two_zones(state, (), width)
    return _fit_essential_state(
        mode=posture.tight,
        trust="",
        bundle=_slice_cells(bundle, 5),
        session=session,
        cost=cost.replace(" ", ""),
        needs=needs_compact,
        max_width=width,
    )


def format_bottom_toolbar_html(
    *,
    bundle_name: str,
    session_id: str | None,
    active_mode: str | None,
    is_running: bool = False,
    queued_count: int = 0,
    tasks_available: bool = False,
    image_paste_available: bool = False,
    task_summary: str | None = None,
    session_cost: Decimal | float | str | None = None,
    trust_summary: str | None = None,
    permission_mode: str | None = None,
    last_yield: str | None = None,
    needs_attention_count: int = 0,
    approval_pending: bool = False,
    palette_open: bool = False,
    lane_focused: bool = False,
    hint_overrides: Mapping[str, str] | None = None,
) -> FormattedText:
    """Return prompt-toolkit fragments for the compatibility prompt session."""
    text = format_bottom_toolbar_text(
        bundle_name=bundle_name,
        session_id=session_id,
        active_mode=active_mode,
        is_running=is_running,
        queued_count=queued_count,
        tasks_available=tasks_available,
        image_paste_available=image_paste_available,
        task_summary=task_summary,
        session_cost=session_cost,
        trust_summary=trust_summary,
        permission_mode=permission_mode,
        last_yield=last_yield,
        needs_attention_count=needs_attention_count,
        approval_pending=approval_pending,
        palette_open=palette_open,
        lane_focused=lane_focused,
        hint_overrides=hint_overrides,
    )
    return FormattedText([("class:bottom-toolbar", f" {text} ")])


class _TrustVariants:
    """Responsive text variants; `wide_*` keep the `mode <id>` prefix (spec 6)."""

    __slots__ = ("full", "compact", "tight", "wide_compact", "wide_tight")

    def __init__(
        self,
        full: str = "",
        compact: str = "",
        tight: str = "",
        wide_compact: str = "",
        wide_tight: str = "",
    ) -> None:
        self.full = full
        self.compact = compact or full
        self.tight = tight or compact or full
        self.wide_compact = wide_compact or self.compact
        self.wide_tight = wide_tight or self.tight


def _trust_variants(summary: str | None) -> _TrustVariants:
    cleaned = _clean(summary or "")
    if not cleaned:
        return _TrustVariants()
    if cleaned == "classifier-gated":
        groups = (
            ("auto", ("read", "write")),
            ("check", ("test", "net", "spend", "outside-project", "subagent")),
        )
    else:
        parsed: list[tuple[str, tuple[str, ...]]] = []
        for segment in cleaned.split("·"):
            label, separator, values = segment.strip().partition(" ")
            capabilities = tuple(
                sorted(
                    (item.strip() for item in values.split(",") if item.strip()),
                    key=lambda item: (_CAPABILITY_INDEX.get(item, 99), item),
                )
            )
            if separator and capabilities:
                parsed.append((label, capabilities))
        if not parsed:
            safe = _identifier(cleaned, 28)
            return _TrustVariants(safe, safe, safe)
        groups = tuple(parsed)
    return _TrustVariants(
        _format_trust(groups, compact=False, limit=3),
        _format_trust(groups, compact=True, limit=3),
        _format_tight_trust(groups),
    )


def _format_trust(
    groups: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    compact: bool,
    limit: int,
) -> str:
    rendered: list[str] = []
    for label, capabilities in groups:
        shown = capabilities[:limit]
        labels = [
            _COMPACT_CAPABILITIES.get(item, _identifier(item, 5)) if compact else item
            for item in shown
        ]
        hidden = len(capabilities) - len(shown)
        if hidden:
            labels.append(f"+{hidden}")
        rendered.append(f"{label} {','.join(labels)}")
    return " · ".join(rendered)


def _format_tight_trust(groups: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    labels = {"auto": "a", "ask": "?", "block": "x", "check": "?"}
    rendered: list[str] = []
    for label, capabilities in groups:
        shown = capabilities[:2]
        values = [
            _COMPACT_CAPABILITIES.get(item, _identifier(item, 4)) for item in shown
        ]
        hidden = len(capabilities) - len(shown)
        if hidden:
            values.append(f"+{hidden}")
        rendered.append(f"{labels.get(label, label[:1])}:{','.join(values)}")
    return " ".join(rendered)


def _hint_levels(
    *,
    is_running: bool,
    tasks_available: bool,
    approval_pending: bool,
    palette_open: bool = False,
    lane_focused: bool = False,
    hint_overrides: Mapping[str, str] | None = None,
) -> tuple[tuple[str, ...], ...]:
    label = partial(hint_label, overrides=hint_overrides)
    enter = label("submit")
    if approval_pending or palette_open:
        select_key = label("approval_move" if approval_pending else "palette_move")
        esc = label("deny_approval" if approval_pending else "close_palette")
        accept = f"{enter} confirm" if approval_pending else f"{enter} run"
        close = f"{esc} deny" if approval_pending else f"{esc} close"
        return (
            (f"{select_key} select", accept, close),
            (accept, close),
            (select_key, enter, esc),
            (enter, esc),
            (enter,),
            (),
        )
    if lane_focused:
        esc = label("close_tasks")
        return (
            (f"{esc} back to parent", "transcript is the subagent's own"),
            (f"{esc} back to parent",),
            (f"{esc} back",),
            (),
        )
    if is_running:
        esc = label("interrupt_running")
        full = [f"{esc} interrupt", f"{enter} steer", f"{label('queue_message')} queue"]
        compact = [esc, "steer", "queue"]
        cap = 3
    else:
        # Mode (Shift-Tab) and permission posture (Ctrl-P) are independent
        # controls (ADR-0005 amendment), so the permission hint now rides
        # alongside the mode hint wherever it's shown. Tasks keeps its
        # existing narrow-width priority (it was already protected at tight
        # widths); permission posture is additive at the 4th, widest slot.
        slash, mode, perm = (
            label("open_palette"),
            label("cycle_mode"),
            label("cycle_permission"),
        )
        compact = [slash, mode]
        full = [f"{slash} commands", f"{mode} mode"]
        if tasks_available:
            tasks = label("toggle_tasks")
            compact.append(tasks)
            full.append(f"{tasks} tasks")
        compact.append(perm)
        full.append(f"{perm} perms")
        cap = 4
    full = full[:cap]
    levels: list[tuple[str, ...]] = [tuple(full), tuple(compact[:cap])]
    if len(full) > 3:
        levels.append(tuple(full[:3]))
    if len(compact) > 3:
        levels.append(tuple(compact[:3]))
    if len(full) > 2:
        levels.append(tuple(full[:2]))
    if len(compact) > 2:
        levels.append(tuple(compact[:2]))
    if len(full) > 1:
        levels.append((full[0],))
    levels.append(())
    return tuple(dict.fromkeys(levels))


def _mode_state_label(mode: str, trust_summary: str | None) -> str:
    labels = {
        "chat": "manual mode on",
        "build": "build mode on",
        "plan": "plan mode on",
        "auto": "auto mode on",
        "bypass": "bypass permissions on",
        "brainstorm": "brainstorm mode on",
    }
    if mode in labels:
        return labels[mode]
    if mode == "custom" or (trust_summary or "").startswith("custom"):
        return "custom permissions"
    return f"{mode} mode on"


def _posture_variants(
    mode: str,
    permission_mode: str,
    trust_summary: str | None,
) -> _TrustVariants:
    """Return the effective permission posture before secondary session state."""
    if permission_mode == "bypass":
        if mode == "bypass":
            return _TrustVariants(
                "bypass permissions on", "bypass permissions", "bypass"
            )
        return _TrustVariants(
            f"mode {mode} · bypass permissions on",
            f"{mode} · bypass",
            f"{mode}/bypass",
            wide_compact=f"mode {mode} · bypass",
        )
    trust = _trust_variants(trust_summary)
    if trust.full:
        mode_name = _identifier(mode, 12)
        return _TrustVariants(
            f"mode {mode_name} · {trust.full}",
            f"{mode_name} · {trust.compact}",
            f"{mode_name} · {trust.tight}",
            wide_compact=f"mode {mode_name} · {trust.compact}",
            wide_tight=f"mode {mode_name} · {trust.tight}",
        )
    label = _mode_state_label(permission_mode, trust_summary)
    if permission_mode != mode:
        label = f"{mode} · {label}"
    return _TrustVariants(
        label,
        _compact_mode_state(label),
        _tight_mode_state(label),
    )


def _compact_mode_state(label: str) -> str:
    return label.removesuffix(" on")


def _tight_mode_state(label: str) -> str:
    return {
        "manual mode on": "manual",
        "build mode on": "build",
        "plan mode on": "plan",
        "auto mode on": "auto",
        "bypass permissions on": "bypass",
        "brainstorm mode on": "brainstorm",
    }.get(label, _compact_mode_state(label))


def _render_two_zones(state: str, hints: tuple[str, ...], max_width: int | None) -> str:
    hint_text = " · ".join(hints)
    if not hint_text:
        return state
    if max_width is None:
        return f"{state}  {hint_text}"
    gap = max_width - get_cwidth(state) - get_cwidth(hint_text)
    return f"{state}{' ' * max(2, gap)}{hint_text}"


def _zones_width(state: str, hints: tuple[str, ...]) -> int:
    hint_text = " · ".join(hints)
    return get_cwidth(state) + get_cwidth(hint_text) + (2 if hint_text else 0)


def _fit_essential_state(
    *,
    mode: str,
    trust: str,
    bundle: str,
    session: str,
    cost: str,
    needs: str,
    max_width: int,
) -> str:
    # Mode/risk and spend are non-negotiable. Add bundle/session in their normal
    # order only when the complete state (including cost) still fits.
    minimum_width = get_cwidth(cost) + 3
    fitted_mode = _slice_cells(mode, max(1, max_width - minimum_width))
    fields = [fitted_mode]
    for field in (bundle, session):
        candidate = _join_state(*fields, field, cost)
        if get_cwidth(candidate) <= max_width:
            fields.append(field)
    fields.append(cost)
    for field in (needs, trust):
        candidate = _join_state(*fields, field)
        if get_cwidth(candidate) <= max_width:
            fields.append(field)
    result = _join_state(*fields)
    if get_cwidth(result) <= max_width:
        return result
    return _slice_cells(mode, max_width) if max_width < get_cwidth(mode) else mode


def _format_session_cost(value: Decimal | float | str | None) -> str:
    if value is None:
        return "$0.00"
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "$0.00"
    if not cost.is_finite() or cost < 0:
        return "$0.00"
    return f"${cost:.2f}"


def _identifier(value: str, max_cells: int) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return "unknown"
    if get_cwidth(cleaned) <= max_cells:
        return cleaned
    tokens = [token for token in re.split(r"[/_:-]+", cleaned) if token]
    if tokens and get_cwidth(tokens[0]) <= max_cells:
        return tokens[0]
    if max_cells < 4:
        return _slice_cells(cleaned, max_cells)
    head = _slice_cells(cleaned, max_cells - 3)
    tail = _slice_cells(cleaned[::-1], 2)[::-1]
    return f"{head}~{tail}"


def _first_token(value: str, max_cells: int) -> str:
    return _slice_cells(value.split(maxsplit=1)[0], max_cells)


def _slice_cells(value: str, max_cells: int) -> str:
    result = ""
    for character in value:
        if get_cwidth(result + character) > max_cells:
            break
        result += character
    return result


def _clean(value: object) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", str(value)).split())


def _join_state(*parts: str) -> str:
    return " · ".join(part for part in parts if part)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = ["format_bottom_toolbar_html", "format_bottom_toolbar_text"]
