"""Construction and restoration of one interactive session resource graph."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_core import AmplifierSession
from rich.console import Console

from amplifier_app_cli.runtime.interactive_resource_setup import (
    InteractiveCleanupCallbacks,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    attach_trackers as _attach_trackers,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    authorization_classifier as _authorization_classifier,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    bind_approval_trust as _bind_approval_trust,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    create_improve_workflow as _create_improve_workflow,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    register_base_capabilities as _register_base_capabilities,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    restore_resume_state as _restore_resume_state,
)
from amplifier_app_cli.runtime.interactive_resource_setup import (
    restore_trust as _restore_trust,
)
from amplifier_app_cli.runtime.session_state import coordinator_session_state
from amplifier_app_cli.session_runner import InitializedSession, SessionConfig
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.ui.clipboard import ClipboardImageInjector
from amplifier_app_cli.ui.command_processor import CommandProcessor
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.governance import ActionGovernor
from amplifier_app_cli.ui.governance_hooks import GovernanceHook
from amplifier_app_cli.ui.improve_workflow import ImproveWorkflow
from amplifier_app_cli.ui.interaction_controller import InteractionController
from amplifier_app_cli.ui.interaction_state import NeedsYouQueue
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.interaction_runtime_state import InteractionRuntimeState
from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry
from amplifier_app_cli.ui.mode_profiles import ModeRuntimeBinding
from amplifier_app_cli.ui.notices import NoticeKind, TransientNoticeState
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui.session_commands import SessionCommandService
from amplifier_app_cli.ui.step_boundaries import StepBoundaryBridge
from amplifier_app_cli.ui.stream_status import StreamStatusTracker
from amplifier_app_cli.ui.task_status import TaskStatusTracker
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock
from amplifier_app_cli.ui.ui_events import UiEventDispatcher

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle


@dataclass(frozen=True, slots=True)
class InteractiveResourceRequest:
    config: dict[str, Any]
    search_paths: list[Path]
    verbose: bool
    session_id: str | None = None
    bundle_name: str = "unknown"
    prepared_bundle: PreparedBundle | None = None
    initial_transcript: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class InteractiveResourceDependencies:
    console: Console
    input_stream: Any
    create_initialized_session: Callable[
        [SessionConfig, Console], Awaitable[InitializedSession]
    ]
    session_store_factory: Callable[[], SessionStore]
    command_processor_factory: Callable[..., CommandProcessor]
    supports_layered_ui: Callable[[Any, Any], bool]
    get_layered_app: Callable[[], object | None]


@dataclass(slots=True)
class UiRefreshRelay:
    """Allow setup-time UI policy to call a title renderer bound by the host."""

    _callback: Callable[[], None] | None = None

    def bind(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def __call__(self) -> None:
        if self._callback is not None:
            self._callback()


@dataclass(slots=True)
class InteractiveSessionResources:
    request: InteractiveResourceRequest
    session_config: SessionConfig
    initialized: InitializedSession
    session: AmplifierSession
    session_id: str
    layered_ui_enabled: bool
    task_tracker: TaskStatusTracker | None
    stream_status: StreamStatusTracker | None
    runtime_status: RuntimeStatusTracker | None
    image_injector: ClipboardImageInjector | None
    notice_state: TransientNoticeState
    trust_state: TrustState
    interaction_state: InteractionRuntimeState
    outcome_ledger: OutcomeLedger
    evidence_model: EvidenceLinkModel
    needs_you: NeedsYouQueue
    steering_queue: SteeringQueue
    mode_profiles: ModeProfileRegistry
    mode_binding: ModeRuntimeBinding
    governor: ActionGovernor
    improve_workflow: ImproveWorkflow
    session_commands: SessionCommandService
    command_processor: CommandProcessor
    store: SessionStore
    ui_events: UiEventDispatcher
    interaction: InteractionController
    approval_system: object | None
    step_boundary: StepBoundaryBridge
    governance_hook: GovernanceHook
    refresh: UiRefreshRelay
    cleanup: InteractiveCleanupCallbacks
    _get_layered_app: Callable[[], object | None] = field(repr=False)

    def active_mode(self) -> str:
        return self.interaction.active_mode()

    async def cycle_mode(self) -> None:
        await self.interaction.cycle()

    async def cycle_permission(self) -> None:
        await self.interaction.cycle_permission()

    def notify(self, text: str, *, kind: NoticeKind = NoticeKind.INFO) -> None:
        if self._get_layered_app() is not None:
            self.notice_state.show(text, kind=kind)
            return
        self.ui_events.emit(NarrationBlock(text))


async def create_interactive_session_resources(
    request: InteractiveResourceRequest,
    dependencies: InteractiveResourceDependencies,
) -> InteractiveSessionResources:
    """Create, register, and restore the app-owned interactive resource graph."""
    session_config = SessionConfig(
        config=request.config,
        search_paths=request.search_paths,
        verbose=request.verbose,
        session_id=request.session_id,
        bundle_name=request.bundle_name,
        initial_transcript=request.initial_transcript,
        prepared_bundle=request.prepared_bundle,
    )
    initialized = await dependencies.create_initialized_session(
        session_config, dependencies.console
    )
    session = initialized.session
    session_id = initialized.session_id
    approval_system = getattr(session.coordinator, "approval_system", None)
    layered = dependencies.supports_layered_ui(
        dependencies.input_stream, dependencies.console.file
    )
    cleanup = InteractiveCleanupCallbacks()

    notice_state = TransientNoticeState()
    trust_state = TrustState()
    outcome_ledger = OutcomeLedger()
    evidence_model = EvidenceLinkModel()
    needs_you = NeedsYouQueue()
    steering_queue = SteeringQueue()
    mode_profiles = ModeProfileRegistry()
    interaction_state = InteractionRuntimeState(
        coordinator_session_state(session.coordinator),
        trust_state,
        ui_modes=mode_profiles.names,
    )
    cleanup.interaction_state = interaction_state.close
    mode_binding = ModeRuntimeBinding(
        session.coordinator,
        mode_profiles,
    )
    governor = ActionGovernor(
        classifier=_authorization_classifier(session),
        needs_you=needs_you,
    )
    _register_base_capabilities(
        session,
        notice_state=notice_state,
        trust_state=trust_state,
        interaction_state=interaction_state,
        outcome_ledger=outcome_ledger,
        evidence_model=evidence_model,
        needs_you=needs_you,
        steering_queue=steering_queue,
        governor=governor,
    )
    task_tracker, stream_status, runtime_status, image_injector = _attach_trackers(
        session,
        request.config,
        session_id,
        layered,
        cleanup,
    )
    improve_workflow = _create_improve_workflow(
        initialized,
        session,
        request.config,
        trust_state,
        outcome_ledger,
        governor,
        runtime_status,
    )
    session_commands = SessionCommandService(
        session_id=session_id,
        bundle_name=request.bundle_name,
        trust_state=trust_state,
        outcome_ledger=outcome_ledger,
        needs_you=needs_you,
        runtime_status=runtime_status,
        task_tracker=task_tracker,
        denial_log=governor.denial_log,
        improve_workflow=improve_workflow,
        cwd=Path.cwd(),
        session=session,
        coordinator=session.coordinator,
    )
    session.coordinator.register_capability("ui.session_commands", session_commands)
    command_processor = dependencies.command_processor_factory(
        session,
        request.bundle_name,
        mcp_prompts=session_commands.mcp_palette_prompts,
    )
    if initialized.configurator is not None:
        command_processor.configurator = initialized.configurator

    store = dependencies.session_store_factory()
    restored = await _restore_resume_state(
        session_config,
        session,
        session_id,
        store,
        command_processor,
        mode_profiles,
        runtime_status,
        outcome_ledger,
    )
    refresh = UiRefreshRelay()
    ui_events = UiEventDispatcher(
        dependencies.console,
        render_profile=lambda: (
            mode_binding.snapshot.render_profile.value
            if mode_binding.snapshot is not None
            else "conversational"
        ),
        show_debug=lambda: bool(
            coordinator_session_state(session.coordinator).get("ui.show_debug")
        ),
    )

    def notify(text: str) -> None:
        if dependencies.get_layered_app() is not None:
            notice_state.show(text)
            return
        ui_events.emit(NarrationBlock(text))

    async def clear_legacy_mode() -> object:
        return await command_processor._handle_mode("off")

    interaction = InteractionController(
        state=interaction_state,
        profiles=mode_profiles,
        binding=mode_binding,
        clear_legacy_mode=clear_legacy_mode,
        notify=notify,
        refresh=refresh,
    )
    await interaction.initialize()
    _restore_trust(trust_state, restored)
    cleanup.approval_trust = _bind_approval_trust(approval_system, trust_state)

    def steer_applied(steer: Any) -> None:
        from amplifier_app_cli.ui.repl import summarize_text

        ui_events.emit(
            NarrationBlock(
                f"Applying steer: {summarize_text(steer.text, max_chars=96)}"
            )
        )

    step_boundary = StepBoundaryBridge(
        session_id,
        steering_queue,
        needs_you=needs_you,
        on_applied=steer_applied,
        on_answers=lambda answers: ui_events.emit(
            NarrationBlock(f"Applying {len(answers)} deferred answers")
        ),
    )
    session.coordinator.register_capability("ui.step_boundary", step_boundary)
    hooks = session.coordinator.get("hooks")
    if hooks:
        cleanup.step_boundary = step_boundary.register_hooks(hooks)

    def governance_denied(result: Any) -> None:
        ui_events.emit(result.to_blocked_block())
        if result.deferred_decision_id:
            notice_state.show(
                f"decision waiting · {result.deferred_decision_id}",
                kind=NoticeKind.WARNING,
            )

    governance_hook = GovernanceHook(
        session_id,
        trust_state,
        governor,
        project_root=Path.cwd(),
        on_denied=governance_denied,
    )
    session.coordinator.register_capability("ui.governance_hook", governance_hook)
    if hooks:
        cleanup.governance = governance_hook.register_hooks(hooks)

    from amplifier_app_cli import incremental_save

    incremental_save.register_incremental_save(
        session, store, session_id, request.bundle_name, request.config
    )
    return InteractiveSessionResources(
        request=request,
        session_config=session_config,
        initialized=initialized,
        session=session,
        session_id=session_id,
        layered_ui_enabled=layered,
        task_tracker=task_tracker,
        stream_status=stream_status,
        runtime_status=runtime_status,
        image_injector=image_injector,
        notice_state=notice_state,
        trust_state=trust_state,
        interaction_state=interaction_state,
        outcome_ledger=outcome_ledger,
        evidence_model=evidence_model,
        needs_you=needs_you,
        steering_queue=steering_queue,
        mode_profiles=mode_profiles,
        mode_binding=mode_binding,
        governor=governor,
        improve_workflow=improve_workflow,
        session_commands=session_commands,
        command_processor=command_processor,
        store=store,
        ui_events=ui_events,
        interaction=interaction,
        approval_system=approval_system,
        step_boundary=step_boundary,
        governance_hook=governance_hook,
        refresh=refresh,
        cleanup=cleanup,
        _get_layered_app=dependencies.get_layered_app,
    )


__all__ = [
    "InteractiveCleanupCallbacks",
    "InteractiveResourceDependencies",
    "InteractiveResourceRequest",
    "InteractiveSessionResources",
    "UiRefreshRelay",
    "create_interactive_session_resources",
]
