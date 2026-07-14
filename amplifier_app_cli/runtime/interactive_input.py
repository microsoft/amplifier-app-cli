"""Route one interactive composer submission into session behavior."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

from amplifier_app_cli.ui.clipboard import ImageAttachment
from amplifier_app_cli.ui.interaction_controller import InteractionController
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.notices import NoticeKind
from amplifier_app_cli.ui.session_commands import SessionCommandResult
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.ui_events import UiEvent


class _CommandProcessor(Protocol):
    def process_input(self, user_input: str) -> tuple[str, dict[str, Any]]: ...

    async def handle_command(
        self, action: str, data: dict[str, Any]
    ) -> str | SessionCommandResult: ...


class _SessionCommands(Protocol):
    async def execute(self, command: str, args: str = "") -> SessionCommandResult: ...


class _Events(Protocol):
    def emit(self, event: UiEvent) -> None: ...

    def emit_many(self, events: Iterable[UiEvent]) -> None: ...


class InteractiveInputRouter:
    """Single dispatch path for prompts and slash-command outcomes."""

    def __init__(
        self,
        *,
        command_processor: _CommandProcessor,
        session_commands: _SessionCommands,
        interaction: InteractionController,
        steering_queue: SteeringQueue,
        events: _Events,
        active_mode: Callable[[], str],
        is_running: Callable[[], bool],
        expand_prompt: Callable[[str], Awaitable[str]],
        enqueue_prompt: Callable[[str, tuple[ImageAttachment, ...]], Awaitable[None]],
        notify: Callable[[str, NoticeKind], None],
        get_layered_app: Callable[[], Any | None],
        summarize: Callable[..., str],
    ) -> None:
        self._commands = command_processor
        self._session_commands = session_commands
        self._interaction = interaction
        self._steering = steering_queue
        self._events = events
        self._active_mode = active_mode
        self._is_running = is_running
        self._expand_prompt = expand_prompt
        self._enqueue_prompt = enqueue_prompt
        self._notify = notify
        self._get_layered_app = get_layered_app
        self._summarize = summarize

    async def handle(
        self,
        user_input: str,
        attachments: tuple[ImageAttachment, ...] = (),
        *,
        display_text: str | None = None,
    ) -> bool:
        if user_input.strip().lower() in {"exit", "quit"}:
            return False
        if not user_input.strip():
            return True

        action, data = self._commands.process_input(user_input)
        if action == "prompt":
            expanded = await self._expand_prompt(str(data["text"]))
            if self._is_running() and not attachments:
                steer = self._steering.enqueue(expanded, display_text=display_text)
                self._notify(
                    f"steer queued · {self._summarize(steer.text, max_chars=72)}",
                    NoticeKind.INFO,
                )
                return True
            self._emit_user(display_text or user_input)
            await self._enqueue_prompt(expanded, attachments)
            return True

        self._emit_user(display_text or user_input)
        if attachments:
            self._notify(
                "images can only be sent with a chat prompt",
                NoticeKind.WARNING,
            )
            return True

        if action == "handle_mode":
            previous_mode = self._interaction.active_mode()
            result = await self._commands.handle_command(action, data)
            await self._interaction.reconcile(previous_mode)
            await self._render_command_result(result)
        elif action == "session_ui":
            await self._handle_session_command(data)
        else:
            result = await self._commands.handle_command(action, data)
            await self._render_command_result(result)

        trailing_prompt = data.get("trailing_prompt")
        if trailing_prompt:
            expanded = await self._expand_prompt(str(trailing_prompt))
            await self._enqueue_prompt(expanded, ())
        return True

    async def _render_command_result(self, result: str | SessionCommandResult) -> None:
        if isinstance(result, str):
            self._events.emit(AnswerBlock(result))
            return
        if result.prompt:
            await self._enqueue_prompt(await self._expand_prompt(result.prompt), ())
        elif result.blocks:
            self._events.emit_many(result.blocks)
        elif result.transient:
            self._notify(result.text, NoticeKind.INFO)
        else:
            self._events.emit(AnswerBlock(result.text))

    async def _handle_session_command(self, data: dict[str, Any]) -> None:
        command = str(data.get("command", ""))
        result = await self._session_commands.execute(
            command,
            str(data.get("args", "")),
        )
        if result.prompt:
            await self._enqueue_prompt(await self._expand_prompt(result.prompt), ())
            return
        app = self._get_layered_app()
        if command == "/tasks":
            if app is not None:
                app.toggle_task_pane()
            self._notify(result.text, NoticeKind.INFO)
        elif command == "/rewind":
            if app is not None and app.open_rewind_picker():
                self._notify("select a turn checkpoint to fork", NoticeKind.INFO)
            else:
                self._events.emit(AnswerBlock(result.text))
        elif result.blocks:
            self._events.emit_many(tuple(result.blocks))
        elif result.transient:
            self._notify(result.text, NoticeKind.INFO)
        else:
            self._events.emit(AnswerBlock(result.text))

    def _emit_user(self, text: str) -> None:
        self._events.emit(UserBlock(text, mode=self._active_mode()))


__all__ = ["InteractiveInputRouter"]
