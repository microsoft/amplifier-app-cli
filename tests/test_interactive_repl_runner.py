from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from amplifier_app_cli.runtime.interactive_repl_runner import (
    InteractiveReplCallbacks,
)
from amplifier_app_cli.runtime.interactive_repl_runner import (
    InteractiveReplDependencies,
)
from amplifier_app_cli.runtime.interactive_repl_runner import InteractiveReplRequest
from amplifier_app_cli.runtime.interactive_repl_runner import InteractiveReplRunner
from amplifier_app_cli.ui.clipboard import ChatSubmission
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock
from amplifier_app_cli.ui.ui_events import UiEventDispatcher


class _PromptSession:
    def __init__(self, values: list[str | BaseException] | None = None) -> None:
        self.values = list(values or [])

    async def prompt_async(self) -> str:
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _LayeredApp:
    def __init__(self, on_run: Callable[[], None] | None = None) -> None:
        self.on_run = on_run
        self.exited = False
        self.capture_entered = False
        self.batch_entered = False
        self.flush_boundary = False

    def mark_backgrounded(self) -> bool:
        return True

    def request_exit(self) -> None:
        self.exited = True

    async def request_approval(
        self,
        prompt: str,
        options: tuple[str, ...],
        timeout: float,
        default: str,
    ) -> str:
        return options[0]

    @contextmanager
    def capture_output(self, console: Console):
        self.capture_entered = True
        yield self

    @contextmanager
    def batch_transcript_output(self):
        self.batch_entered = True
        yield self

    def mark_exit_flush_boundary(self) -> None:
        self.flush_boundary = True

    async def run_async(self) -> None:
        if self.on_run is not None:
            self.on_run()


class _ApprovalSystem:
    def __init__(self) -> None:
        self.handler = None
        self.unbound = False

    def bind_handler(self, handler):
        self.handler = handler

        def unbind() -> None:
            self.unbound = True

        return unbind


def _terminal() -> tuple[Console, UiEventDispatcher, StringIO]:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=100)
    return console, UiEventDispatcher(console), output


def _config(tmp_path) -> LayeredReplConfig:
    return LayeredReplConfig(
        history_path=tmp_path / "history",
        completion=LayeredReplCompletion(CommandRegistry(())),
        bundle_name="foundation",
        session_id="session",
    )


@pytest.mark.asyncio
async def test_layered_runner_owns_resume_render_and_approval_lifecycle(
    tmp_path,
) -> None:
    console, events, _ = _terminal()
    registered: dict[str, object] = {}
    published = []
    rendered = []
    initial_submits = []
    approval = _ApprovalSystem()
    app = _LayeredApp(lambda: registered["ui.resume"]("child-session"))

    async def handle_input(*args, **kwargs) -> bool:
        return True

    callbacks = InteractiveReplCallbacks(
        handle_input=handle_input,
        submit_initial_prompt=lambda: _record_async(initial_submits, "initial"),
        request_exit=lambda: None,
        runner_active=lambda: False,
        set_terminal_title=lambda: None,
        publish_layered_app=published.append,
        register_capability=registered.__setitem__,
        display_execution_error=lambda error: None,
    )
    dependencies = InteractiveReplDependencies(
        console=console,
        prompt_session=_PromptSession(),
        events=events,
        display_validation_error=lambda *args, **kwargs: True,
        escape_markup=str,
        app_factory=lambda **kwargs: app,
        render_message=lambda message, **kwargs: rendered.append((message, kwargs)),
        approval_system=approval,
    )
    runner = InteractiveReplRunner(callbacks, dependencies)
    request = InteractiveReplRequest(
        layered=True,
        config=_config(tmp_path),
        bindings=LayeredReplBindings(on_submit=runner.submit_layered),
        services=LayeredReplServices(),
        session_header=NarrationBlock("header"),
        initial_transcript=({"role": "assistant", "content": "prior"},),
        initial_show_thinking=True,
    )

    result = await runner.run(request)

    assert result.app is app
    assert result.requested_session_id == "child-session"
    assert published == [app]
    assert callable(registered["ui.background"])
    assert app.exited and app.capture_entered and app.batch_entered
    assert app.flush_boundary
    assert initial_submits == ["initial"]
    assert rendered[0][0]["content"] == "prior"
    assert rendered[0][1]["show_thinking"] is True
    assert approval.handler == app.request_approval
    assert result.unregister_approval is not None
    result.unregister_approval()
    assert approval.unbound


@pytest.mark.asyncio
async def test_layered_failure_unbinds_approval_before_propagating(tmp_path) -> None:
    console, events, _ = _terminal()
    approval = _ApprovalSystem()

    class FailingApp(_LayeredApp):
        async def run_async(self) -> None:
            raise RuntimeError("terminal failed")

    app = FailingApp()

    async def handle_input(*args, **kwargs) -> bool:
        return True

    runner = InteractiveReplRunner(
        InteractiveReplCallbacks(
            handle_input=handle_input,
            submit_initial_prompt=_noop_async,
            request_exit=lambda: None,
            runner_active=lambda: False,
            set_terminal_title=lambda: None,
            publish_layered_app=lambda app: None,
            register_capability=lambda name, value: None,
            display_execution_error=lambda error: None,
        ),
        InteractiveReplDependencies(
            console=console,
            prompt_session=_PromptSession(),
            events=events,
            display_validation_error=lambda *args, **kwargs: True,
            escape_markup=str,
            app_factory=lambda **kwargs: app,
            render_message=lambda *args, **kwargs: None,
            approval_system=approval,
        ),
    )

    with pytest.raises(RuntimeError, match="terminal failed"):
        await runner.run(
            InteractiveReplRequest(
                layered=True,
                config=_config(tmp_path),
                bindings=LayeredReplBindings(on_submit=runner.submit_layered),
                services=LayeredReplServices(),
            )
        )

    assert approval.unbound


@pytest.mark.asyncio
async def test_legacy_runner_routes_input_then_reports_queued_eof() -> None:
    console, events, output = _terminal()
    handled = []

    async def handle_input(text: str, *args, **kwargs) -> bool:
        handled.append(text)
        return True

    runner = InteractiveReplRunner(
        InteractiveReplCallbacks(
            handle_input=handle_input,
            submit_initial_prompt=_noop_async,
            request_exit=lambda: None,
            runner_active=lambda: True,
            set_terminal_title=lambda: None,
            publish_layered_app=lambda app: None,
            register_capability=lambda name, value: None,
            display_execution_error=lambda error: None,
        ),
        InteractiveReplDependencies(
            console=console,
            prompt_session=_PromptSession(["hello", EOFError()]),
            events=events,
            display_validation_error=lambda *args, **kwargs: True,
            escape_markup=str,
        ),
    )

    result = await runner.run(
        InteractiveReplRequest(layered=False, session_banner="session banner")
    )

    assert result.app is None
    assert handled == ["hello"]
    assert "session banner" in output.getvalue()
    assert "Exiting after current queued work" in output.getvalue()


@pytest.mark.asyncio
async def test_layered_submission_uses_shared_error_boundary(monkeypatch) -> None:
    from amplifier_app_cli.runtime import interactive_repl_runner as runner_module

    console = MagicMock(spec=Console)
    events = MagicMock(spec=UiEventDispatcher)
    displayed = []

    class ValidationFailure(Exception):
        pass

    monkeypatch.setattr(runner_module, "ModuleValidationError", ValidationFailure)

    async def handle_input(*args, **kwargs) -> bool:
        raise ValidationFailure("invalid module")

    runner = InteractiveReplRunner(
        InteractiveReplCallbacks(
            handle_input=handle_input,
            submit_initial_prompt=_noop_async,
            request_exit=lambda: None,
            runner_active=lambda: False,
            set_terminal_title=lambda: None,
            publish_layered_app=lambda app: None,
            register_capability=lambda name, value: None,
            display_execution_error=displayed.append,
        ),
        InteractiveReplDependencies(
            console=console,
            prompt_session=_PromptSession(),
            events=events,
            display_validation_error=lambda *args, **kwargs: False,
            escape_markup=str,
            verbose=True,
        ),
    )

    await runner.submit_layered(ChatSubmission("hello"))

    assert displayed == []
    console.print.assert_called_once()
    console.print_exception.assert_called_once()


async def _noop_async() -> None:
    return None


async def _record_async(target: list[str], value: str) -> None:
    target.append(value)
