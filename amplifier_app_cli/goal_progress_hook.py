"""Console rendering for the /goal auto-continue loop's progress events.

The auto-continue loop itself lives in the orchestrator (loop-streaming's
StreamingOrchestrator.execute()) rather than the app layer -- see
docs/designs/goal-command.md. The orchestrator emits an
``orchestrator:goal_progress`` event instead of writing to stdout directly,
because bare print() from an orchestrator corrupts protocol channels for
other hosts (amplifier-agent's stdout JSON-RPC, amplifierd/amplifier-chat's
journald-only stdout). This hook is the CLI-specific renderer: it subscribes
to that event and prints the exact strings the CLI previously printed
itself, to the CLI's rich ``console``.

Registered once per session (both interactive_chat and execute_single),
mirroring the incremental_save.py pattern.
"""

from __future__ import annotations

from typing import Any

from .console import console


class GoalProgressHook:
    """Renders ``orchestrator:goal_progress`` events to the CLI console.

    Contract:
    - Listens for: orchestrator:goal_progress events
    - Side effects: writes formatted progress lines to the CLI's rich console
    - Never raises: an unrecognized/malformed payload is rendered as-is
      rather than crashing the session (hook failures must not block
      execution)

    Usage:
        hook = GoalProgressHook()
        hooks.register("orchestrator:goal_progress", hook.on_goal_progress,
                        name="goal_progress")
    """

    async def on_goal_progress(self, event: str, data: dict[str, Any]) -> None:
        """Render one goal-progress event using the pre-existing strings.

        Mirrors exactly what the orchestrator used to print() itself
        (docs/designs/goal-command.md) before that was moved to an event
        emission for protocol-channel safety.
        """
        state = data.get("state")
        reason = data.get("reason")
        turn = data.get("turn")
        cap = data.get("cap")

        if state == "continuing":
            turn_label = f"{turn}/{cap}" if cap else f"{turn}"
            console.print(f"\u27f3 goal: turn {turn_label} \u2014 {reason}")
        elif state == "achieved":
            console.print(f"\u2713 goal achieved: {reason}")
        elif state == "cap_hit":
            console.print(f"\u26a0 goal: hit turn cap ({cap}) \u2014 stopping.")
        elif state == "cancelled":
            console.print(f"\u2717 goal: cancelled \u2014 {reason}")
        elif state == "error":
            console.print(f"\u2717 goal: evaluator failed \u2014 {reason}")
        else:
            # Unrecognized state: don't silently drop it, but don't guess at
            # formatting either -- surface the raw payload.
            console.print(f"[dim]goal progress: {data}[/dim]")


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
