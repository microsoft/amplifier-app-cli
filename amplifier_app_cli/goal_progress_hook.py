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

Rendering uses real Rich renderables (Rule / Table.grid / Padding) rather
than hand-assembled strings with manual dividers and indentation -- Rich
knows the terminal width and wraps long lines while preserving hanging
indents; a plain f-string with a leading "  " loses that indent the moment
a line wraps back to column 0.
"""

from __future__ import annotations

from typing import Any

from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table

from .console import console

# Terminal (loop-ending) states. Every other recognized state ("continuing")
# gets the lightweight per-turn progress line instead of the end-of-run
# block below.
#
# "stalled" is reported by the orchestrator when it detects the agent making
# zero progress -- repeated turns with no tool calls and an unchanging
# blocker -- and gives up. It is a FAILURE outcome, not success.
_TERMINAL_LABELS: dict[str, str] = {
    "achieved": "GOAL ACHIEVED",
    "cap_hit": "GOAL STOPPED \u2014 turn cap hit (NOT confirmed complete)",
    "cancelled": "GOAL CANCELLED",
    "error": "GOAL FAILED \u2014 evaluator error",
    "stalled": "GOAL FAILED \u2014 stalled (no progress detected)",
}

# Rich style applied to each terminal state's headline. Only "achieved" reads
# as success; every other terminal state must be visually and textually
# unmistakable as "did not confirm completion."
_TERMINAL_STYLES: dict[str, str] = {
    "achieved": "bold green",
    "cap_hit": "bold yellow",
    "cancelled": "yellow",
    "error": "bold red",
    "stalled": "bold red",
}

# States where the run actually struggled -- collapsed reason history earns
# its space here because it shows *whether the evaluator kept repeating
# itself*. "achieved" doesn't need it (it succeeded); "cancelled" doesn't
# need it (the user stopped it, not the loop).
_STRUGGLE_STATES = frozenset({"stalled", "cap_hit"})

# Which field wins when both `reason` (the final turn's evaluator verdict)
# and `summary` (a fast-model narrative of the whole run) are present. They
# overlap heavily -- never show both. For a successful run, the narrative of
# *what got done* is more useful than the terse final "yes it's done."
# For every other terminal state the run did NOT succeed, so the concrete,
# current blocker (reason) is more actionable than a broad narrative.
_PREFER_SUMMARY_STATES = frozenset({"achieved"})

# Cap on rendered reason-history entries (after collapsing consecutive
# duplicates) so a long-stalled run doesn't dump a wall of near-identical
# lines.
_MAX_HISTORY_ENTRIES = 5

_INDENT_1 = (0, 0, 0, 2)
_INDENT_2 = (0, 0, 0, 4)


class GoalProgressHook:
    """Renders ``orchestrator:goal_progress`` events to the CLI console.

    Contract:
    - Listens for: orchestrator:goal_progress events
    - Side effects: writes formatted progress lines/renderables to the CLI's
      rich console
    - Never raises: an unrecognized/malformed payload is rendered as-is
      rather than crashing the session (hook failures must not block
      execution)
    - Reads new payload fields (continuations, reasons, stall_detail,
      summary, etc.) defensively via ``.get()`` so an older orchestrator
      that only emits the original fields (state/turn/cap/reason) still
      renders something sane here.

    Usage:
        hook = GoalProgressHook()
        hooks.register("orchestrator:goal_progress", hook.on_goal_progress,
                        name="goal_progress")
    """

    async def on_goal_progress(self, event: str, data: dict[str, Any]) -> None:
        """Render one goal-progress event.

        ``continuing`` gets the lightweight per-turn line (unchanged from
        before). Every terminal state (achieved/cap_hit/cancelled/error/
        stalled) gets a distinct end-of-run block via ``_render_terminal``.
        """
        state = data.get("state")

        if state == "continuing":
            turn = data.get("turn")
            cap = data.get("cap")
            reason = data.get("reason")
            turn_label = f"{turn}/{cap}" if cap else f"{turn}"
            console.print(f"\u27f3 goal: turn {turn_label} \u2014 {reason}")
        elif state in _TERMINAL_LABELS:
            self._render_terminal(state, data)
        else:
            # Unrecognized state: don't silently drop it, but don't guess at
            # formatting either -- surface the raw payload.
            console.print(f"[dim]goal progress: {data}[/dim]")

    def _render_terminal(self, state: str, data: dict[str, Any]) -> None:
        """Render the end-of-run block for a terminal goal state.

        The amount of detail scales with how interesting the outcome is:
        an immediate, uncontested success gets one line; a run that
        struggled (stalled, hit its cap) gets the full picture, including
        collapsed reason history. `reason` and `summary` are never both
        shown -- see ``_PREFER_SUMMARY_STATES``.
        """
        label = _TERMINAL_LABELS[state]
        style = _TERMINAL_STYLES[state]
        cap = data.get("cap")
        reason = data.get("reason")
        reasons = data.get("reasons") or []
        summary = data.get("summary")
        stall_detail = data.get("stall_detail")
        continuations = data.get("continuations")

        # Trivial success: the user watched it happen in one pass. There is
        # nothing else worth saying -- one line, done.
        if state == "achieved" and continuations == 0:
            console.print(
                f"[{style}]\u2713 {label}[/{style}] \u2014 no continuations needed"
            )
            return

        console.print(
            Rule(title=f"[{style}]{label}[/{style}]", style=style, align="left")
        )

        facts = Table.grid(padding=(0, 1))
        facts.add_column(no_wrap=True, style="dim")
        facts.add_column(overflow="fold")
        facts.add_row(
            "sent back to assistant:", _continuations_label(continuations, cap)
        )
        console.print(facts)

        if state == "stalled" and stall_detail:
            console.print(
                Padding(f"[bold red]stalled on:[/bold red] {stall_detail}", _INDENT_1)
            )

        narrative_label, narrative_text = _pick_narrative(
            reason, summary, prefer_summary=state in _PREFER_SUMMARY_STATES
        )
        if narrative_text:
            console.print(
                Padding(f"[dim]{narrative_label}:[/dim] {narrative_text}", _INDENT_1)
            )

        if state in _STRUGGLE_STATES:
            # The most recent reason is already shown above (as `reason` or
            # `summary`) -- history covers everything before it, so nothing
            # is repeated.
            history = _collapse_consecutive(reasons[:-1])
            if history:
                console.print(Padding("[dim]reason history:[/dim]", _INDENT_1))
                shown = history[-_MAX_HISTORY_ENTRIES:]
                omitted = len(history) - len(shown)
                if omitted > 0:
                    console.print(
                        Padding(
                            f"[dim]\u2026 ({omitted} earlier, omitted)[/dim]", _INDENT_2
                        )
                    )
                for text, count in shown:
                    suffix = f" (\u00d7{count})" if count > 1 else ""
                    console.print(Padding(f"- {text}{suffix}", _INDENT_2))


def _continuations_label(continuations: int | None, cap: int | None) -> str:
    """Format the 'sent back to assistant' fact line's value."""
    if continuations is None:
        label = "unknown number of continuations"
    else:
        plural = "" if continuations == 1 else "s"
        label = f"{continuations} continuation{plural}"
    if cap:
        label += f" (cap: {cap})"
    return label


def _pick_narrative(
    reason: str | None, summary: str | None, *, prefer_summary: bool
) -> tuple[str, str]:
    """Choose exactly one of `reason` / `summary` to render, never both.

    They overlap heavily in practice (the same outcome described twice, at
    length); showing one is the fix. Returns (label, text), or ("", "") if
    neither is present.
    """
    order = (
        (("summary", summary), ("reason", reason))
        if prefer_summary
        else (("reason", reason), ("summary", summary))
    )
    for label, text in order:
        if text:
            return label, text
    return "", ""


def _collapse_consecutive(reasons: list[str]) -> list[tuple[str, int]]:
    """Collapse consecutive duplicate reasons into (text, count) pairs.

    The orchestrator is being updated to collapse consecutive duplicates
    itself before emitting `reasons`, but this hook collapses defensively
    too -- an older orchestrator (or any future one) may still send a run of
    5-8 near-identical entries, and that's noise, not signal.
    """
    collapsed: list[tuple[str, int]] = []
    for text in reasons:
        if collapsed and collapsed[-1][0] == text:
            prev_text, prev_count = collapsed[-1]
            collapsed[-1] = (prev_text, prev_count + 1)
        else:
            collapsed.append((text, 1))
    return collapsed


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
