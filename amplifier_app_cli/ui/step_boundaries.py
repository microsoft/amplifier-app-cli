"""Agent-loop bridge for visible steering at safe provider boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from amplifier_core import HookResult

from .interaction_state import DeferredDecision, NeedsYouQueue
from .interaction_state import QueuedSteer, SteeringQueue
from .task_status import HookRegistry


class StepBoundaryBridge:
    """Consume one user steer immediately before the next root model request."""

    EVENTS = ("provider:request",)

    def __init__(
        self,
        root_session_id: str,
        steering: SteeringQueue,
        *,
        needs_you: NeedsYouQueue | None = None,
        on_applied: Callable[[QueuedSteer], None] | None = None,
        on_answers: Callable[[tuple[DeferredDecision, ...]], None] | None = None,
    ) -> None:
        self._root_session_id = root_session_id
        self._steering = steering
        self._needs_you = needs_you
        self._on_applied = on_applied
        self._on_answers = on_answers

    async def handle_event(self, event: str, data: dict[str, Any]) -> HookResult:
        if event != "provider:request":
            return HookResult(action="continue")
        session_id = str(data.get("session_id") or self._root_session_id)
        if session_id != self._root_session_id:
            return HookResult(action="continue")
        steer = self._steering.consume_next()
        answers = self._needs_you.consume_answered() if self._needs_you else ()
        if steer is None and not answers:
            return HookResult(action="continue")
        if steer is not None and self._on_applied is not None:
            self._on_applied(steer)
        if answers and self._on_answers is not None:
            self._on_answers(answers)
        injections: list[str] = []
        if steer is not None:
            injections.append(
                "User steering received during this turn. Apply it at this safe "
                f"step boundary:\n{steer.text}"
            )
        if answers:
            answer_lines = [
                f"{item.decision_id}: {item.question}\nAnswer: {item.answer}"
                for item in answers
            ]
            injections.append(
                "The user answered deferred decisions. Apply these answers to "
                "dependent work:\n" + "\n".join(answer_lines)
            )
        return HookResult(
            action="inject_context",
            context_injection="\n\n".join(injections),
            context_injection_role="user",
            ephemeral=False,
            suppress_output=True,
        )

    def register_hooks(
        self, hooks: HookRegistry, *, priority: int = 950
    ) -> Callable[[], None]:
        unregister = hooks.register(
            "provider:request",
            self.handle_event,
            priority=priority,
            name="cli-step-boundary-steering",
        )
        return unregister if callable(unregister) else lambda: None


__all__ = ["StepBoundaryBridge"]
