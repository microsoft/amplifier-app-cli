"""Typed lifecycle owner for layered and legacy interactive REPL loops."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import click
from amplifier_core import ModuleValidationError  # pyright: ignore[reportAttributeAccessIssue]
from rich.console import Console

from amplifier_app_cli.stdout_offload import patch_stdout_offloaded as patch_stdout
from amplifier_app_cli.ui.clipboard import ChatSubmission, ImageAttachment
from amplifier_app_cli.ui.layered_repl_config import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl_config import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl_config import LayeredReplServices
from amplifier_app_cli.ui.ui_events import UiEvent, UiEventDispatcher


Message = dict[str, Any]
ApprovalDefault = Literal["allow", "deny"]
ApprovalHandler = Callable[
    [str, tuple[str, ...], float, ApprovalDefault], Awaitable[str]
]


class InteractiveInputHandler(Protocol):
    async def __call__(
        self,
        user_input: str,
        attachments: tuple[ImageAttachment, ...] = (),
        *,
        display_text: str | None = None,
    ) -> bool: ...


class PromptSessionHandle(Protocol):
    async def prompt_async(self) -> str: ...


class LayeredReplHandle(Protocol):
    def mark_backgrounded(self) -> bool: ...

    def request_exit(self) -> None: ...

    async def request_approval(
        self,
        prompt: str,
        options: tuple[str, ...],
        timeout: float,
        default: ApprovalDefault,
    ) -> str: ...

    def capture_output(self, console: Console) -> AbstractContextManager[object]: ...

    def batch_transcript_output(self) -> AbstractContextManager[object]: ...

    def mark_exit_flush_boundary(self) -> None: ...

    async def run_async(self) -> None: ...


class LayeredReplAppFactory(Protocol):
    def __call__(
        self,
        *,
        config: LayeredReplConfig,
        bindings: LayeredReplBindings,
        services: LayeredReplServices,
    ) -> LayeredReplHandle: ...


class RenderMessage(Protocol):
    def __call__(
        self,
        message: Message,
        console: Console | None = None,
        *,
        show_thinking: bool = False,
        show_label: bool = True,
        dispatcher: UiEventDispatcher | None = None,
    ) -> None: ...


class ValidationErrorDisplay(Protocol):
    def __call__(
        self,
        console: Console,
        error: ModuleValidationError,
        verbose: bool = False,
    ) -> bool: ...


@runtime_checkable
class ApprovalBindingProvider(Protocol):
    def bind_handler(self, handler: ApprovalHandler) -> object: ...


@dataclass(frozen=True, slots=True)
class InteractiveReplCallbacks:
    """Session-owned actions invoked by either REPL surface."""

    handle_input: InteractiveInputHandler
    submit_initial_prompt: Callable[[], Awaitable[None]]
    request_exit: Callable[[], None]
    runner_active: Callable[[], bool]
    set_terminal_title: Callable[[], None]
    publish_layered_app: Callable[[LayeredReplHandle], None]
    register_capability: Callable[[str, object], None]
    display_execution_error: Callable[[Exception], None]


@dataclass(frozen=True, slots=True)
class InteractiveReplDependencies:
    """Patchable terminal and rendering dependencies for the REPL lifecycle."""

    console: Console
    prompt_session: PromptSessionHandle
    events: UiEventDispatcher
    display_validation_error: ValidationErrorDisplay
    escape_markup: Callable[[object], str]
    verbose: bool = False
    app_factory: LayeredReplAppFactory | None = None
    render_message: RenderMessage | None = None
    approval_system: object | None = None
    confirm_exit: Callable[[], bool] = lambda: click.confirm(
        "Exit Amplifier?", default=False
    )


@dataclass(frozen=True, slots=True)
class InteractiveReplRequest:
    """One layered or legacy REPL execution request."""

    layered: bool
    config: LayeredReplConfig | None = None
    bindings: LayeredReplBindings | None = None
    services: LayeredReplServices | None = None
    session_banner: str | None = None
    session_header: UiEvent | None = None
    initial_transcript: Sequence[Message] | None = None
    initial_display_transcript: Sequence[Message] | None = None
    initial_show_thinking: bool = False

    def __post_init__(self) -> None:
        if self.layered and (
            self.config is None or self.bindings is None or self.services is None
        ):
            raise ValueError(
                "layered REPL requests require config, bindings, and services"
            )


@dataclass(frozen=True, slots=True)
class InteractiveReplResult:
    """Lifecycle values main needs for cleanup and in-process resume."""

    app: LayeredReplHandle | None = None
    unregister_approval: Callable[[], None] | None = None
    requested_session_id: str | None = None


class InteractiveReplRunner:
    """Run one interactive surface and own its terminal error boundary."""

    def __init__(
        self,
        callbacks: InteractiveReplCallbacks,
        dependencies: InteractiveReplDependencies,
    ) -> None:
        self._callbacks = callbacks
        self._dependencies = dependencies
        self._requested_session_id: str | None = None

    async def submit_layered(self, submission: ChatSubmission) -> None:
        """Route a layered submission through the shared input error boundary."""
        try:
            should_continue = await self._callbacks.handle_input(
                submission.text,
                submission.attachments,
                display_text=submission.display_text,
            )
            if not should_continue:
                self._callbacks.request_exit()
        except Exception as error:
            self._report_error(error)

    async def run(self, request: InteractiveReplRequest) -> InteractiveReplResult:
        """Run the configured layered or legacy terminal surface."""
        self._requested_session_id = None
        if request.layered:
            return await self._run_layered(request)
        return await self._run_legacy(request)

    async def _run_layered(
        self, request: InteractiveReplRequest
    ) -> InteractiveReplResult:
        config = request.config
        bindings = request.bindings
        services = request.services
        factory = self._dependencies.app_factory
        render_message = self._dependencies.render_message
        if config is None or bindings is None or services is None:
            raise RuntimeError("layered REPL request was not fully configured")
        if factory is None or render_message is None:
            raise RuntimeError("layered REPL dependencies are unavailable")

        app = factory(config=config, bindings=bindings, services=services)
        self._callbacks.publish_layered_app(app)
        self._callbacks.register_capability("ui.background", app.mark_backgrounded)

        def request_resume(session_id: str) -> None:
            self._requested_session_id = session_id
            app.request_exit()

        self._callbacks.register_capability("ui.resume", request_resume)
        unregister_approval = self._bind_approval(app)
        try:
            self._callbacks.set_terminal_title()
            with app.capture_output(self._dependencies.console):
                display_transcript = (
                    request.initial_transcript
                    if request.initial_display_transcript is None
                    else request.initial_display_transcript
                )
                if display_transcript:
                    with app.batch_transcript_output():
                        for message in display_transcript:
                            if isinstance(message, dict):
                                render_message(
                                    message,
                                    show_thinking=request.initial_show_thinking,
                                    show_label=False,
                                    dispatcher=self._dependencies.events,
                                )
                    app.mark_exit_flush_boundary()
                if request.session_header is not None:
                    self._dependencies.events.emit(request.session_header)
                await self._callbacks.submit_initial_prompt()
                await app.run_async()
        except BaseException:
            if unregister_approval is not None:
                unregister_approval()
            raise
        return InteractiveReplResult(
            app=app,
            unregister_approval=unregister_approval,
            requested_session_id=self._requested_session_id,
        )

    async def _run_legacy(
        self, request: InteractiveReplRequest
    ) -> InteractiveReplResult:
        console = self._dependencies.console
        if request.session_banner is not None:
            console.print(request.session_banner)
        await self._callbacks.submit_initial_prompt()

        while True:
            try:
                with patch_stdout(raw=True):
                    user_input = await self._dependencies.prompt_session.prompt_async()
                if not await self._callbacks.handle_input(user_input):
                    break
            except EOFError:
                message = (
                    "\n[dim]Exiting after current queued work...[/dim]"
                    if self._callbacks.runner_active()
                    else "\n[dim]Exiting...[/dim]"
                )
                console.print(message)
                break
            except KeyboardInterrupt:
                console.print()
                if await asyncio.to_thread(self._dependencies.confirm_exit):
                    console.print("[dim]Exiting...[/dim]")
                    break
            except Exception as error:
                self._report_error(error)
        return InteractiveReplResult()

    def _bind_approval(self, app: LayeredReplHandle) -> Callable[[], None] | None:
        provider = self._dependencies.approval_system
        if not isinstance(provider, ApprovalBindingProvider):
            return None
        unregister = provider.bind_handler(app.request_approval)
        if not callable(unregister):
            return None

        def unregister_approval() -> None:
            unregister()

        return unregister_approval

    def _report_error(self, error: Exception) -> None:
        if isinstance(error, ModuleValidationError):
            if not self._dependencies.display_validation_error(
                self._dependencies.console,
                error,
                verbose=self._dependencies.verbose,
            ):
                self._dependencies.console.print(
                    f"[red]Error:[/red] {self._dependencies.escape_markup(error)}"
                )
                if self._dependencies.verbose:
                    self._dependencies.console.print_exception()
            return
        self._callbacks.display_execution_error(error)


__all__ = [
    "InteractiveReplCallbacks",
    "InteractiveReplDependencies",
    "InteractiveReplRequest",
    "InteractiveReplResult",
    "InteractiveReplRunner",
    "LayeredReplHandle",
]
