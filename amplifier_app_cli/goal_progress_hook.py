"""Console rendering for the /goal auto-continue loop's progress events.

The auto-continue loop itself lives in the orchestrator (loop-streaming's
StreamingOrchestrator.execute()) rather than the app layer -- see
docs/GOAL_COMMAND.md. The orchestrator emits an ``orchestrator:goal_progress``
event instead of writing to stdout directly, because bare print() from an
orchestrator corrupts protocol channels for other hosts (amplifier-agent's
stdout JSON-RPC, amplifierd/amplifier-chat's journald-only stdout). This hook
is the CLI-specific renderer: it subscribes to that event and prints
formatted progress lines to the CLI's rich ``console``.

Registered once per session (both interactive_chat and execute_single),
mirroring the incremental_save.py pattern.

Rendering is width-agnostic by design: NO Rule, Table, or Padding, and no
calls to console width / terminal size anywhere in this module. A prior
version used Rich's Rule + Table.grid + Padding, which resolves layout
against ``console.width`` at print time -- a number that's frequently wrong
(SSH from a phone, tmux panes, piped output) and meaningless when scrollback
is reread at a different width. Baked-in line breaks and column padding
cannot reflow once printed.

The fix, applied consistently across every ``console.print`` call here, is
"floor-size the structure, full-bleed the prose":
  - Structural lines (the status header) are short by construction -- they
    never need to wrap at any realistic width.
  - Prose lines (the one narrative sentence a state is allowed) carry no
    width policy at all: printed with ``soft_wrap=True`` so Rich never
    inserts its own line breaks, leaving the terminal free to reflow the
    line however it likes, at whatever width it currently has.
This mirrors the precedent in amplifier-module-hooks-streaming-ui, which
deleted its own narrow-rail renderable (``_rail_renderable``, hardcoded to a
fixed width with a gutter) in favor of full-width markdown: "no rail, no
narrow column."
"""

from __future__ import annotations

import re
from typing import Any

from .console import console

# Terminal (loop-ending) states. Every other recognized state ("continuing")
# gets the lightweight per-turn progress line instead of the end-of-run
# header below.
_TERMINAL_STATES = frozenset({"achieved", "cap_hit", "cancelled", "error", "stalled"})

# Grammar for every terminal header: "<glyph> <status> -- <cause>". Status is
# always one of exactly these three phrases, and the glyph always matches --
# a skimmer reading only the first three words gets the answer, and nothing
# after the dash can invert that verdict.
#
# "cap_hit" is NOT a failure: it means we stopped, not that the goal was
# unmet, so it reads as unconfirmed (warning glyph) rather than failed (red
# X) -- same as "cancelled" (the user stopped it) and "error" (the evaluator
# itself broke, telling us nothing about the goal). Only "stalled" -- the
# agent visibly failed to make progress -- earns "Goal not met".
_STATUS_PHRASE: dict[str, str] = {
    "achieved": "Goal met",
    "stalled": "Goal not met",
    "cap_hit": "Goal unconfirmed",
    "cancelled": "Goal unconfirmed",
    "error": "Goal unconfirmed",
}

_GLYPH: dict[str, str] = {
    "achieved": "\u2713",  # check
    "stalled": "\u2717",  # cross
    "cap_hit": "\u26a0",  # warning
    "cancelled": "\u26a0",
    "error": "\u26a0",
}

_STYLE: dict[str, str] = {
    "achieved": "green",
    "stalled": "red",
    "cap_hit": "yellow",
    "cancelled": "yellow",
    "error": "yellow",
}

# Sane character budget for any single rendered prose line. The orchestrator
# is being changed in parallel to constrain generated reason/summary text to
# one sentence, but this hook doesn't trust that -- it truncates defensively
# at render, on a word boundary, so a misbehaving upstream can't dump a
# paragraph into the terminal.
_MAX_PROSE_CHARS = 180

# Matches a trailing "(\u00d7N)" repeat-count annotation the orchestrator's
# own dedupe may already have attached to a reason string (e.g.
# "blocked on X (\u00d73)"). Parsed defensively so this hook's own collapsing
# doesn't double-count or fail to collapse entries that arrive pre-annotated.
_REPEAT_SUFFIX = re.compile(r"\s*\(\u00d7(\d+)\)\s*$")


class GoalProgressHook:
    """Renders ``orchestrator:goal_progress`` events to the CLI console.

    Contract:
    - Listens for: orchestrator:goal_progress events
    - Side effects: writes plain, width-agnostic text lines to the CLI's
      rich console (no Rule/Table/Padding/Panel, no width computation)
    - Never raises: an unrecognized/malformed payload is rendered as-is
      rather than crashing the session (hook failures must not block
      execution)
    - Reads all payload fields defensively via ``.get()`` so an older
      orchestrator that only emits the original fields (state/turn/cap/
      reason) still renders something sane here.

    Usage:
        hook = GoalProgressHook()
        hooks.register("orchestrator:goal_progress", hook.on_goal_progress,
                        name="goal_progress")
    """

    async def on_goal_progress(self, event: str, data: dict[str, Any]) -> None:
        """Render one goal-progress event.

        ``continuing`` gets the lightweight per-turn line (unchanged in
        shape from before). Every terminal state (achieved/cap_hit/
        cancelled/error/stalled) gets the width-agnostic header (plus, for
        every state but ``achieved``, at most a couple of plain prose
        lines) via ``_render_terminal``.
        """
        state = data.get("state")

        if state == "continuing":
            self._render_continuing(data)
        elif state in _TERMINAL_STATES:
            self._render_terminal(state, data)
        else:
            # Unrecognized state: don't silently drop it, but don't guess at
            # formatting either -- surface the raw payload.
            _print(f"[dim]goal progress: {data}[/dim]")

    def _render_continuing(self, data: dict[str, Any]) -> None:
        turn = data.get("turn")
        cap = data.get("cap")
        reason = data.get("reason")
        turn_label = f"{turn}/{cap}" if cap else f"{turn}"
        line = f"\u27f3 goal: turn {turn_label}"
        if reason:
            line += f" \u2014 {_truncate(reason)}"
        _print(line)

    def _render_terminal(self, state: str, data: dict[str, Any]) -> None:
        """Render the end-of-run header for a terminal goal state.

        Every state gets exactly one header line: "<glyph> <status>" plus,
        for every state but ``achieved``, a "-- <cause>" suffix that is
        never long enough to need wrapping. ``achieved`` instead gets an
        optional "* sent back N times" suffix -- the only place a
        continuation count is ever shown, since every other state already
        folds its relevant count into the cause phrase (e.g. "hit the
        8-turn cap").

        Below the header, at most one prose slot is rendered, chosen by
        state -- never both a `reason` and a `summary`, since they're two
        renderings of the same judgment. ``achieved`` gets no prose at all;
        the user just watched it succeed.
        """
        glyph = _GLYPH[state]
        style = _STYLE[state]
        status = _STATUS_PHRASE[state]
        header = f"[{style}]{glyph} {status}[/{style}]"

        if state == "achieved":
            header += _count_suffix(data.get("continuations"))
            _print(header)
            return

        cause = _cause_phrase(state, data)
        if cause:
            header += f" \u2014 {cause}"

        # Blank line before the block on every non-success (unconfirmed /
        # not-met) state; none on the achieved one-liner above.
        console.print()
        _print(header)
        for line in _body_lines(state, data):
            _print(f"  {line}")


def _cause_phrase(state: str, data: dict[str, Any]) -> str:
    """The short, fixed "-- <cause>" suffix for a terminal state's header.

    Every phrase here is bounded and known in advance (never derived from
    unbounded upstream text), so the header line stays short by
    construction and never needs to wrap.
    """
    if state == "stalled":
        return "stalled"
    if state == "cancelled":
        return "cancelled"
    if state == "error":
        return "evaluator failed"
    if state == "cap_hit":
        cap = data.get("cap")
        return f"hit the {cap}-turn cap" if cap else "hit the turn cap"
    return ""


def _body_lines(state: str, data: dict[str, Any]) -> list[str]:
    """The (at most two) prose lines under a terminal header, by state.

    - stalled: the collapsed blocker phrase (never the raw blocker list).
    - cap_hit: the summary (preferred) or reason, prefixed "still open:",
      plus a static hint -- correct in exactly this state, because this is
      the one state where more turns might actually finish the job.
    - cancelled / error: an optional single line from reason (preferred) or
      summary, verbatim -- no label, since the header already carries the
      cause.
    - achieved: never called; see ``_render_terminal``.
    """
    if state == "stalled":
        line = _stalled_line(data)
        return [line] if line else []

    if state == "cap_hit":
        lines: list[str] = []
        narrative = data.get("summary") or data.get("reason")
        if narrative:
            lines.append(_truncate(f"still open: {narrative}"))
        lines.append("rerun with a higher cap to finish")
        return lines

    if state in ("cancelled", "error"):
        narrative = data.get("reason") or data.get("summary")
        return [_truncate(narrative)] if narrative else []

    return []


def _stalled_line(data: dict[str, Any]) -> str | None:
    """Build the single stalled-state prose line.

    Never prints the blocker list. Instead: if every (collapsed) blocker is
    the same, says so with a turn count ("same blocker 4 turns running:
    <blocker>") -- a wall. If the blockers genuinely differ, says that
    instead ("4 turns, 4 different blockers, none resolved") -- flailing is
    a different diagnosis from a wall, and the phrase should say which.

    Falls back to `stall_detail`, then bare `reason`, for an older
    orchestrator that doesn't emit `reasons` at all.
    """
    reasons = data.get("reasons") or []
    if reasons:
        collapsed = _collapse_consecutive(reasons)
        continuations = data.get("continuations")
        total_turns = (
            continuations
            if isinstance(continuations, int)
            else sum(count for _, count in collapsed)
        )
        if len(collapsed) == 1:
            blocker, _count = collapsed[0]
            return _truncate(f"same blocker {total_turns} turns running: {blocker}")
        return _truncate(
            f"{total_turns} turns, {len(collapsed)} different blockers, none resolved"
        )

    stall_detail = data.get("stall_detail")
    if stall_detail:
        return _truncate(stall_detail)

    reason = data.get("reason")
    if reason:
        return _truncate(reason)

    return None


def _parse_repeat(reason: str) -> tuple[str, int]:
    """Strip a trailing "(\u00d7N)" annotation, returning (text, count)."""
    match = _REPEAT_SUFFIX.search(reason)
    if match:
        return reason[: match.start()].rstrip(), int(match.group(1))
    return reason, 1


def _collapse_consecutive(reasons: list[str]) -> list[tuple[str, int]]:
    """Collapse consecutive duplicate reasons into (text, count) pairs.

    Reads the orchestrator's own "(\u00d7N)" repeat-count convention
    defensively (see ``_parse_repeat``) so pre-collapsed entries are
    counted correctly rather than treated as N separate distinct blockers.
    """
    collapsed: list[tuple[str, int]] = []
    for raw in reasons:
        text, count = _parse_repeat(raw)
        if collapsed and collapsed[-1][0] == text:
            prev_text, prev_count = collapsed[-1]
            collapsed[-1] = (prev_text, prev_count + count)
        else:
            collapsed.append((text, count))
    return collapsed


def _count_suffix(continuations: int | None) -> str:
    """The optional "* sent back N times" header suffix (achieved only).

    Omitted entirely when 0/None (house convention: never print a count of
    zero) or unknown.
    """
    if not continuations:
        return ""
    return f" \u00b7 sent back {_count_word(continuations)}"


def _count_word(n: int) -> str:
    if n == 1:
        return "once"
    if n == 2:
        return "twice"
    return f"{n} times"


def _truncate(text: str, limit: int = _MAX_PROSE_CHARS) -> str:
    """Truncate prose to ``limit`` chars on a word boundary, with an ellipsis."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return f"{head.rstrip()}\u2026"


def _print(text: str) -> None:
    """Print one line with no Rich-imposed wrapping.

    ``soft_wrap=True`` stops Rich from inserting its own line breaks based
    on ``console.width`` -- the line is emitted exactly as built, and the
    terminal (not this hook) decides how to reflow it at whatever width it
    currently has.
    """
    console.print(text, soft_wrap=True)


def register_goal_progress_hook(session: Any) -> GoalProgressHook | None:
    """Register the goal-progress console renderer on ``session``.

    Convenience function mirroring incremental_save.py's
    register_incremental_save -- creates the hook and registers it with the
    session's hooks registry. Safe to call unconditionally: a no-op when
    hooks aren't available.

    Args:
        session: The AmplifierSession to register on

    Returns:
        The created hook instance, or None if hooks not available
    """
    hooks = session.coordinator.get("hooks")
    if not hooks or not hasattr(hooks, "register"):
        return None

    hook = GoalProgressHook()
    hooks.register(
        "orchestrator:goal_progress", hook.on_goal_progress, name="goal_progress"
    )
    return hook
