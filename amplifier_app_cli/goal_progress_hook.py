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
"""

from __future__ import annotations

from typing import Any

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

_BLOCK_RULE = "\u2500" * 3


class GoalProgressHook:
    """Renders ``orchestrator:goal_progress`` events to the CLI console.

    Contract:
    - Listens for: orchestrator:goal_progress events
    - Side effects: writes formatted progress lines to the CLI's rich console
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

        Always includes: the outcome, how many times the turn was sent back
        to the assistant (continuations), and the fast-model summary when
        present. Never suppressed when summary is None -- the structured
        facts (count, outcome, last reason) still render.
        """
        label = _TERMINAL_LABELS[state]
        style = _TERMINAL_STYLES[state]
        cap = data.get("cap")
        reason = data.get("reason")
        reasons = data.get("reasons") or []
        summary = data.get("summary")
        stall_detail = data.get("stall_detail")
        continuations = data.get("continuations")

        if continuations is None:
            continuations_label = "unknown number of continuations"
        else:
            plural = "" if continuations == 1 else "s"
            continuations_label = f"{continuations} continuation{plural}"
        if cap:
            continuations_label += f" (cap: {cap})"

        console.print(f"[{style}]{_BLOCK_RULE} {label} {_BLOCK_RULE}[/{style}]")
        console.print(f"  sent back to assistant: {continuations_label}")

        if reason:
            console.print(f"  last reason: {reason}")

        if state == "stalled" and stall_detail:
            console.print(f"[bold red]  stalled on:[/bold red] {stall_detail}")

        if len(reasons) > 1:
            console.print("  reason history:")
            for r in reasons:
                console.print(f"    - {r}")

        if summary:
            console.print(f"  summary: {summary}")
        else:
            console.print("  [dim](no summary available)[/dim]")


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
