"""Application host for one interactive Amplifier session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_core import AmplifierSession
from prompt_toolkit import PromptSession
from rich.console import Console

from amplifier_app_cli.runtime.execution_interrupt import ExecutionInterruptController
from amplifier_app_cli.runtime.interactive_cleanup import InteractiveSessionCleanup
from amplifier_app_cli.runtime.interactive_input import InteractiveInputRouter
from amplifier_app_cli.runtime.interactive_repl_runner import InteractiveReplCallbacks
from amplifier_app_cli.runtime.interactive_repl_runner import (
    InteractiveReplDependencies,
)
from amplifier_app_cli.runtime.interactive_repl_runner import InteractiveReplRequest
from amplifier_app_cli.runtime.interactive_repl_runner import InteractiveReplResult
from amplifier_app_cli.runtime.interactive_repl_runner import InteractiveReplRunner
from amplifier_app_cli.runtime.interactive_repl_runner import LayeredReplHandle
from amplifier_app_cli.runtime.interactive_resources import (
    InteractiveResourceDependencies,
)
from amplifier_app_cli.runtime.interactive_resources import InteractiveResourceRequest
from amplifier_app_cli.runtime.interactive_resources import (
    create_interactive_session_resources,
)
from amplifier_app_cli.runtime.interactive_session import InteractiveSessionRuntime
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnBindings
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnConfig
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnRunner
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnServices
from amplifier_app_cli.runtime.session_persistence import InteractiveSessionPersistence
from amplifier_app_cli.runtime.transcript_repair import repair_interactive_transcript
from amplifier_app_cli.session_runner import InitializedSession, SessionConfig
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.ui.clipboard import ImageAttachment
from amplifier_app_cli.ui.command_processor import CommandProcessor
from amplifier_app_cli.ui.execution_errors import render_execution_error
from amplifier_app_cli.ui.git_yield import GitDiffSnapshot
from amplifier_app_cli.ui.notices import NoticeKind
from amplifier_app_cli.ui.outcome_ledger import TurnOutcome
from amplifier_app_cli.ui.plan_sync import PlanStepSynchronizer
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock
from amplifier_app_cli.ui.transcript_blocks import SessionHeaderBlock
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.turn_completion import TurnCompletionRenderer

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle


@dataclass(frozen=True, slots=True)
class InteractiveHostRequest:
    config: dict[str, Any]
    search_paths: list[Path]
    verbose: bool
    session_id: str | None = None
    bundle_name: str = "unknown"
    prepared_bundle: PreparedBundle | None = None
    initial_prompt: str | None = None
    initial_transcript: list[dict[str, Any]] | None = None
    initial_display_transcript: list[dict[str, Any]] | None = None
    initial_show_thinking: bool = False


@dataclass(frozen=True, slots=True)
class InteractiveHostDependencies:
    """Patchable app-layer seams retained by ``amplifier_app_cli.main``."""

    console: Console
    input_stream: Any
    create_initialized_session: Callable[
        [SessionConfig, Console], Awaitable[InitializedSession]
    ]
    session_store_factory: Callable[[], SessionStore]
    command_processor_factory: Callable[..., CommandProcessor]
    supports_layered_ui: Callable[[Any, Any], bool]
    effective_config_summary: Callable[[dict[str, Any], str], Any]
    get_version: Callable[[], str]
    get_core_version: Callable[[], str]
    create_prompt_session: Callable[..., PromptSession]
    process_runtime_mentions: Callable[[AmplifierSession, str], Awaitable[str]]
    capture_diff: Callable[[Path], Awaitable[GitDiffSnapshot]]
    display_validation_error: Callable[..., bool]
    escape_markup: Callable[[object], str]


async def run_interactive_host(
    request: InteractiveHostRequest,
    dependencies: InteractiveHostDependencies,
) -> str | None:
    """Assemble and run one interactive session using public app services."""
    layered_app_state: dict[str, Any] = {"app": None}
    resources = await create_interactive_session_resources(
        InteractiveResourceRequest(
            config=request.config,
            search_paths=request.search_paths,
            verbose=request.verbose,
            session_id=request.session_id,
            bundle_name=request.bundle_name,
            prepared_bundle=request.prepared_bundle,
            initial_transcript=request.initial_transcript,
        ),
        InteractiveResourceDependencies(
            console=dependencies.console,
            input_stream=dependencies.input_stream,
            create_initialized_session=dependencies.create_initialized_session,
            session_store_factory=dependencies.session_store_factory,
            command_processor_factory=dependencies.command_processor_factory,
            supports_layered_ui=dependencies.supports_layered_ui,
            get_layered_app=lambda: layered_app_state.get("app"),
        ),
    )
    session = resources.session
    actual_session_id = resources.session_id
    command_processor = resources.command_processor
    session_commands = resources.session_commands
    ui_events = resources.ui_events
    console = dependencies.console

    session_banner = None
    session_header = None
    if not resources.session_config.is_resume:
        summary = dependencies.effective_config_summary(
            request.config, request.bundle_name
        )
        headline = (
            f"Amplifier {dependencies.get_version()} · "
            f"core {dependencies.get_core_version()}"
        )
        detail = f"{summary.format_banner_line()} · session {actual_session_id[:6]}"
        session_banner = f"[bold]{headline}[/bold]\n[dim]{detail}[/dim]"
        session_header = SessionHeaderBlock(headline, detail)

    from amplifier_app_cli.ui.repl import build_terminal_title
    from amplifier_app_cli.ui.repl import emit_terminal_title
    from amplifier_app_cli.ui.repl import summarize_text

    execution_state = {"running": False}
    current_task: dict[str, str | None] = {"title": None}
    immediate_interrupt = asyncio.Event()
    prompt_runtime_state: dict[
        str, InteractiveSessionRuntime[ImageAttachment] | None
    ] = {"runtime": None}
    remove_title_listener: Callable[[], None] | None = None
    remove_needs_listener: Callable[[], None] | None = None

    def active_mode() -> str:
        return resources.active_mode()

    interrupt = ExecutionInterruptController(
        cancellation=session.coordinator.cancellation,
        is_running=lambda: execution_state["running"],
        immediate_event=immediate_interrupt,
        notify=lambda text, kind: resources.notify(text, kind=kind),
    )

    def queued_count() -> int:
        runtime = prompt_runtime_state["runtime"]
        return runtime.queued_count if runtime is not None else 0

    def runner_active() -> bool:
        runtime = prompt_runtime_state["runtime"]
        return runtime.active if runtime is not None else False

    def set_terminal_title(
        task_summary: str | None = None, *, is_running: bool = False
    ) -> None:
        active_step = (
            resources.task_tracker.active_step_text()
            if resources.task_tracker is not None
            else None
        )
        title = build_terminal_title(
            cwd=Path.cwd(),
            bundle_name=request.bundle_name,
            session_id=actual_session_id,
            active_mode=active_mode(),
            task_summary=task_summary or active_step or current_task["title"],
            is_running=is_running,
            agent_count=(
                resources.task_tracker.counts().running
                if resources.task_tracker is not None
                else 0
            ),
            needs_count=resources.needs_you.pending_count,
        )
        layered_app = layered_app_state.get("app")
        if layered_app is not None:
            layered_app.emit_terminal_title(title)
            layered_app.emit_ambient_state(
                is_running=is_running,
                needs_count=resources.needs_you.pending_count,
            )
        else:
            emit_terminal_title(console, title)

    resources.refresh.bind(
        lambda: set_terminal_title(is_running=execution_state["running"])
    )
    set_terminal_title()
    if resources.task_tracker is not None:
        plan_sync = PlanStepSynchronizer(
            resources.task_tracker,
            on_step=lambda step: ui_events.emit(NarrationBlock(step)),
            on_title=lambda _active: set_terminal_title(
                is_running=execution_state["running"]
            ),
        )
        remove_title_listener = plan_sync.close
    remove_needs_listener = resources.needs_you.add_listener(
        lambda: set_terminal_title(is_running=execution_state["running"])
    )

    async def rewind_to(outcome: TurnOutcome) -> None:
        try:
            turn_number = resources.outcome_ledger.entries.index(outcome) + 1
        except ValueError:
            resources.notify(
                "rewind checkpoint is no longer available", kind=NoticeKind.ERROR
            )
            return
        ui_events.emit(
            AnswerBlock(await command_processor._fork_session(str(turn_number)))
        )

    prompt_session = dependencies.create_prompt_session(
        get_active_mode=active_mode,
        get_is_running=lambda: execution_state["running"],
        get_queued_count=queued_count,
        on_interrupt=interrupt.request,
        commands=command_processor.COMMANDS,
        mode_shortcuts=command_processor.MODE_SHORTCUTS,
        skill_shortcuts=command_processor.SKILL_SHORTCUTS,
        mcp_prompts=session_commands.mcp_palette_prompts,
        mode_names=command_processor._get_mode_completion_names(),
        skill_names=command_processor._get_skill_completion_names(),
        model_names=lambda: session_commands.model_names,
        bundle_name=request.bundle_name,
        session_id=actual_session_id,
    )
    persistence = InteractiveSessionPersistence(
        session=session,
        store=resources.store,
        session_id=actual_session_id,
        bundle_name=request.bundle_name,
        config=request.config,
        interaction_state=resources.interaction_state,
        outcome_ledger=resources.outcome_ledger,
        runtime_status=resources.runtime_status,
    )
    completion = TurnCompletionRenderer(
        events=ui_events,
        interaction=resources.interaction,
        current_task=lambda: current_task["title"],
        get_layered_app=lambda: layered_app_state.get("app"),
    )

    from amplifier_app_cli.ui import render_message

    def enqueue_followup(prompt: str) -> None:
        runtime = prompt_runtime_state["runtime"]
        if runtime is not None:
            runtime.enqueue_next(prompt)

    turn_runner = InteractiveTurnRunner(
        config=InteractiveTurnConfig(actual_session_id, Path.cwd()),
        services=InteractiveTurnServices(
            execute=session.execute,
            cancellation=session.coordinator.cancellation,
            get_hooks=lambda: session.coordinator.get("hooks"),
            repair_transcript=lambda: repair_interactive_transcript(
                session, persist=persistence.save
            ),
            persist=persistence.save,
            render_message=render_message,
            capture_diff=dependencies.capture_diff,
            events=ui_events,
            outcome_ledger=resources.outcome_ledger,
            completion=completion,
            evidence=resources.evidence_model,
            runtime_status=resources.runtime_status,
            image_injector=resources.image_injector,
        ),
        bindings=InteractiveTurnBindings(
            immediate_interrupt=immediate_interrupt,
            request_interrupt=interrupt.request,
            summarize=summarize_text,
            set_running=lambda value: execution_state.__setitem__("running", value),
            set_task_title=lambda value: current_task.__setitem__("title", value),
            refresh_title=lambda title, running: set_terminal_title(
                title, is_running=running
            ),
            get_layered_app=lambda: layered_app_state.get("app"),
            active_mode=active_mode,
            enqueue_followup=enqueue_followup,
            notify=resources.notify,
            steering_queue=resources.steering_queue,
        ),
    )

    def display_execution_error(error: Exception) -> None:
        render_execution_error(error, events=ui_events, verbose=request.verbose)

    def exit_layered_app() -> None:
        layered_app = layered_app_state.get("app")
        if layered_app is not None:
            layered_app.exit()

    prompt_runtime = InteractiveSessionRuntime[ImageAttachment](
        execute_turn=turn_runner.execute,
        on_error=display_execution_error,
        on_idle_exit=exit_layered_app,
    )
    prompt_runtime_state["runtime"] = prompt_runtime

    async def enqueue_prompt(
        prompt_text: str,
        attachments: tuple[ImageAttachment, ...] = (),
    ) -> None:
        result = await prompt_runtime.enqueue(prompt_text, attachments)
        if result.queued_behind_active_turn:
            resources.notify(
                f"queued {result.queued_count} · {summarize_text(prompt_text)}"
            )

    initial_prompt = request.initial_prompt

    async def submit_initial_prompt() -> None:
        nonlocal initial_prompt
        if not initial_prompt:
            return
        ui_events.emit(UserBlock(initial_prompt, mode=active_mode()))
        initial_prompt = await dependencies.process_runtime_mentions(
            session, initial_prompt
        )
        await enqueue_prompt(initial_prompt)

    input_router = InteractiveInputRouter(
        command_processor=command_processor,
        session_commands=session_commands,
        interaction=resources.interaction,
        steering_queue=resources.steering_queue,
        events=ui_events,
        active_mode=active_mode,
        is_running=lambda: execution_state["running"],
        expand_prompt=lambda text: dependencies.process_runtime_mentions(session, text),
        enqueue_prompt=enqueue_prompt,
        notify=lambda text, kind: resources.notify(text, kind=kind),
        get_layered_app=lambda: layered_app_state.get("app"),
        summarize=summarize_text,
    )

    def request_repl_exit() -> None:
        if not prompt_runtime.request_exit():
            resources.notify("exiting after queued work")

    app_factory = None
    message_renderer = None
    layered_config = None
    layered_services = None
    if resources.layered_ui_enabled:
        from amplifier_app_cli.project_utils import get_project_slug
        from amplifier_app_cli.ui import render_message as message_renderer
        from amplifier_app_cli.ui.layered_repl import LayeredReplApp
        from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
        from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
        from amplifier_app_cli.ui.layered_repl import LayeredReplServices

        app_factory = LayeredReplApp
        layered_config = LayeredReplConfig(
            history_path=(
                Path.home()
                / ".amplifier"
                / "projects"
                / get_project_slug()
                / "repl_history"
            ),
            completion=LayeredReplCompletion(
                registry=command_processor.command_registry,
                mode_names=tuple(command_processor._get_mode_completion_names()),
                skill_names=tuple(command_processor._get_skill_completion_names()),
                model_names=lambda: session_commands.model_names,
            ),
            bundle_name=request.bundle_name,
            session_id=actual_session_id,
        )
        layered_services = LayeredReplServices(
            task_tracker=resources.task_tracker,
            stream_status=resources.stream_status,
            runtime_status=resources.runtime_status,
            notice_state=resources.notice_state,
            trust_state=resources.trust_state,
            outcome_ledger=resources.outcome_ledger,
            needs_you=resources.needs_you,
            steering_queue=resources.steering_queue,
            evidence_model=resources.evidence_model,
            event_dispatcher=ui_events,
        )

    def publish_layered_app(app: LayeredReplHandle) -> None:
        layered_app_state["app"] = app

    repl_callbacks = InteractiveReplCallbacks(
        handle_input=input_router.handle,
        submit_initial_prompt=submit_initial_prompt,
        request_exit=request_repl_exit,
        runner_active=runner_active,
        set_terminal_title=set_terminal_title,
        publish_layered_app=publish_layered_app,
        register_capability=session.coordinator.register_capability,
        display_execution_error=display_execution_error,
    )
    repl_runner = InteractiveReplRunner(
        repl_callbacks,
        InteractiveReplDependencies(
            console=console,
            prompt_session=prompt_session,
            events=ui_events,
            display_validation_error=dependencies.display_validation_error,
            escape_markup=dependencies.escape_markup,
            verbose=request.verbose,
            app_factory=app_factory,
            render_message=message_renderer,
            approval_system=resources.approval_system,
        ),
    )
    layered_bindings = None
    if resources.layered_ui_enabled:
        from amplifier_app_cli.ui.layered_repl import LayeredReplBindings

        layered_bindings = LayeredReplBindings(
            on_submit=repl_runner.submit_layered,
            on_interrupt=interrupt.request,
            on_exit=request_repl_exit,
            get_active_mode=active_mode,
            get_render_profile=lambda: (
                resources.mode_binding.snapshot.render_profile.value
                if resources.mode_binding.snapshot is not None
                else "conversational"
            ),
            get_is_running=lambda: execution_state["running"],
            get_queued_count=queued_count,
            get_task_title=lambda: current_task["title"],
            on_cycle_mode=resources.cycle_mode,
            on_rewind=rewind_to,
        )
    repl_request = InteractiveReplRequest(
        layered=resources.layered_ui_enabled,
        config=layered_config,
        bindings=layered_bindings,
        services=layered_services,
        session_banner=session_banner,
        session_header=session_header,
        initial_transcript=request.initial_transcript,
        initial_display_transcript=request.initial_display_transcript,
        initial_show_thinking=request.initial_show_thinking,
    )
    repl_result = InteractiveReplResult()
    try:
        repl_result = await repl_runner.run(repl_request)
    finally:
        unregister = resources.cleanup.collect(
            repl_result.unregister_approval,
            remove_title_listener,
            remove_needs_listener,
        )
        cleanup = InteractiveSessionCleanup(
            session=session,
            session_id=actual_session_id,
            wait_for_runner=prompt_runtime.wait,
            persist=persistence.save,
            cleanup_session=resources.initialized.cleanup,
            unregister=unregister,
            set_terminal_title=set_terminal_title,
            get_layered_app=lambda: layered_app_state.get("app"),
        )
        await cleanup.run()
    if repl_result.requested_session_id:
        return repl_result.requested_session_id
    console.print(
        "\n[yellow]Session exited - resume anytime with these commands:[/yellow]"
    )
    console.print("  [cyan]amplifier resume[/cyan]  # interactive list of sessions")
    console.print(
        f"  [cyan]amplifier session resume {actual_session_id[:8]}[/cyan]  "
        "# jump directly to this session"
    )
    console.print()
    return None
