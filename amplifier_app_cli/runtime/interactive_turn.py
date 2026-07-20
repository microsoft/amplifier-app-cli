"""One interactive provider turn with deterministic render and cleanup ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import signal
from time import monotonic
from typing import Any, Protocol

from amplifier_app_cli.ui.clipboard import ClipboardImageInjector
from amplifier_app_cli.ui.clipboard import ImageAttachment
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.git_yield import GitDiffSnapshot
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.turn_completion import TurnCompletionRenderer
from amplifier_app_cli.ui.turn_outcomes import build_turn_outcome
from amplifier_app_cli.ui.ui_events import UiEventDispatcher

from .cleanup_events import CLEANUP_RENDER_BEGIN
from .cleanup_events import CLEANUP_RENDER_END
from .cleanup_events import CLEANUP_STORE_BEGIN
from .cleanup_events import CLEANUP_STORE_END
from .session_events import PROMPT_COMPLETE
from .turn_execution import await_turn_or_interrupt


class _Cancellation(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    @property
    def is_immediate(self) -> bool: ...

    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class InteractiveTurnConfig:
    session_id: str
    cwd: Path


@dataclass(frozen=True, slots=True)
class InteractiveTurnServices:
    execute: Callable[[str], Awaitable[str]]
    cancellation: _Cancellation
    get_hooks: Callable[[], Any | None]
    repair_transcript: Callable[[], Awaitable[bool]]
    persist: Callable[[], Awaitable[None]]
    render_message: Callable[..., None]
    capture_diff: Callable[[Path], Awaitable[GitDiffSnapshot]]
    events: UiEventDispatcher
    outcome_ledger: OutcomeLedger
    completion: TurnCompletionRenderer
    evidence: EvidenceLinkModel
    runtime_status: RuntimeStatusTracker | None = None
    image_injector: ClipboardImageInjector | None = None


@dataclass(frozen=True, slots=True)
class InteractiveTurnBindings:
    immediate_interrupt: asyncio.Event
    request_interrupt: Callable[[], bool]
    summarize: Callable[..., str]
    set_running: Callable[[bool], None]
    set_task_title: Callable[[str | None], None]
    refresh_title: Callable[[str | None, bool], None]
    get_layered_app: Callable[[], Any | None]
    active_mode: Callable[[], str]
    enqueue_followup: Callable[[str], None]
    notify: Callable[[str], None]
    steering_queue: SteeringQueue


class InteractiveTurnRunner:
    """Run one turn and leave the session ready for the next input."""

    def __init__(
        self,
        *,
        config: InteractiveTurnConfig,
        services: InteractiveTurnServices,
        bindings: InteractiveTurnBindings,
    ) -> None:
        self._config = config
        self._services = services
        self._bindings = bindings

    async def execute(
        self,
        prompt: str,
        attachments: tuple[ImageAttachment, ...] = (),
    ) -> bool:
        await self._services.repair_transcript()
        injector = self._services.image_injector
        if attachments:
            if injector is None:
                raise RuntimeError("Session hooks cannot accept image attachments")
            injector.prepare(prompt, attachments)

        cancellation = self._services.cancellation
        cancellation.reset()
        self._bindings.immediate_interrupt.clear()
        started_at = monotonic()
        starting_diff = await self._services.capture_diff(self._config.cwd)
        title = self._bindings.summarize(prompt, max_chars=72)
        self._bindings.set_task_title(title)
        starting_tool_keys = self._starting_tool_keys()
        runtime = self._services.runtime_status
        if runtime is not None:
            runtime.consume("prompt:submit", {"session_id": self._config.session_id})
        self._bindings.set_running(True)
        self._bindings.refresh_title(title, True)

        def handle_sigint(signum: int, frame: object) -> None:
            self._bindings.request_interrupt()

        original_handler = signal.signal(signal.SIGINT, handle_sigint)
        try:

            async def invoke() -> str:
                return await self._services.execute(prompt)

            execute_task = asyncio.create_task(invoke())
            try:
                response = await await_turn_or_interrupt(
                    execute_task,
                    self._bindings.immediate_interrupt,
                    is_immediate=lambda: cancellation.is_immediate,
                )
                return await self._complete_success(
                    prompt=prompt,
                    response=response,
                    started_at=started_at,
                    starting_tool_keys=starting_tool_keys,
                    starting_diff=starting_diff,
                )
            except asyncio.CancelledError:
                await self._complete_cancelled(
                    started_at=started_at,
                    starting_tool_keys=starting_tool_keys,
                    starting_diff=starting_diff,
                )
                return False
        except Exception:
            app = self._bindings.get_layered_app()
            if app is not None:
                app.notify_turn_failed()
            raise
        finally:
            signal.signal(signal.SIGINT, original_handler)
            if injector is not None:
                injector.clear()
            self._bindings.set_running(False)
            self._bindings.set_task_title(None)
            self._bindings.refresh_title(None, False)
            self._roll_steers_forward()

    async def _complete_success(
        self,
        *,
        prompt: str,
        response: str,
        started_at: float,
        starting_tool_keys: set[tuple[str, str]],
        starting_diff: GitDiffSnapshot,
    ) -> bool:
        ending_diff = await self._services.capture_diff(self._config.cwd)
        self._record_evidence(response, starting_tool_keys)
        hooks = self._services.get_hooks()
        await self._emit(hooks, CLEANUP_RENDER_BEGIN)
        self._services.render_message(
            {"role": "assistant", "content": response},
            show_label=False,
            dispatcher=self._services.events,
        )
        await self._emit(hooks, CLEANUP_RENDER_END)

        cancelled = self._services.cancellation.is_cancelled
        self._record_outcome(
            started_at=started_at,
            response=response,
            cancelled=cancelled,
            starting_tool_keys=starting_tool_keys,
            starting_diff=starting_diff,
            ending_diff=ending_diff,
        )
        await self._flush_layered_output()
        if hooks:
            await hooks.emit(
                PROMPT_COMPLETE,
                {
                    "prompt": prompt,
                    "response": response,
                    "session_id": self._config.session_id,
                },
            )
        await self._emit(hooks, CLEANUP_STORE_BEGIN)
        await self._services.persist()
        await self._emit(hooks, CLEANUP_STORE_END)
        return not cancelled

    async def _complete_cancelled(
        self,
        *,
        started_at: float,
        starting_tool_keys: set[tuple[str, str]],
        starting_diff: GitDiffSnapshot,
    ) -> None:
        ending_diff = await self._services.capture_diff(self._config.cwd)
        self._record_outcome(
            started_at=started_at,
            response="",
            cancelled=True,
            starting_tool_keys=starting_tool_keys,
            starting_diff=starting_diff,
            ending_diff=ending_diff,
        )
        await self._flush_layered_output()
        await self._services.persist()

    def _record_outcome(
        self,
        *,
        started_at: float,
        response: str,
        cancelled: bool,
        starting_tool_keys: set[tuple[str, str]],
        starting_diff: GitDiffSnapshot,
        ending_diff: GitDiffSnapshot,
    ) -> None:
        outcome = build_turn_outcome(
            session_id=self._config.session_id,
            outcome_ledger=self._services.outcome_ledger,
            runtime_status=self._services.runtime_status,
            started_at=started_at,
            response=response,
            cancelled=cancelled,
            starting_tool_keys=starting_tool_keys,
            starting_diff=starting_diff,
            ending_diff=ending_diff,
            active_mode=self._bindings.active_mode(),
        )
        self._services.outcome_ledger.record(outcome)
        self._services.completion.render(outcome)

    def _record_evidence(
        self,
        response: str,
        starting_tool_keys: set[tuple[str, str]],
    ) -> None:
        runtime = self._services.runtime_status
        answer_id = (
            f"{self._config.session_id}:answer:"
            f"{len(self._services.evidence.answer_ids) + 1}"
        )
        tools = (
            (
                tool
                for tool in runtime.tool_snapshot()
                if tool.terminal
                and (tool.session_id, tool.tool_call_id) not in starting_tool_keys
            )
            if runtime is not None
            else ()
        )
        self._services.evidence.record(answer_id, response, tools)

    def _starting_tool_keys(self) -> set[tuple[str, str]]:
        runtime = self._services.runtime_status
        if runtime is None:
            return set()
        return {
            (tool.session_id, tool.tool_call_id) for tool in runtime.tool_snapshot()
        }

    async def _flush_layered_output(self) -> None:
        app = self._bindings.get_layered_app()
        if app is not None:
            await app.flush_output()

    async def _emit(self, hooks: Any | None, event: str) -> None:
        if hooks:
            await hooks.emit(event, {"session_id": self._config.session_id})

    def _roll_steers_forward(self) -> None:
        steering = self._bindings.steering_queue
        while steering.pending:
            steer = steering.consume_next()
            if steer is None:
                break
            self._services.events.emit(
                UserBlock(
                    steer.display_text or steer.text,
                    mode=self._bindings.active_mode(),
                )
            )
            self._bindings.enqueue_followup(steer.text)
            self._bindings.notify("steer moved to the next turn")


__all__ = [
    "InteractiveTurnBindings",
    "InteractiveTurnConfig",
    "InteractiveTurnRunner",
    "InteractiveTurnServices",
]
