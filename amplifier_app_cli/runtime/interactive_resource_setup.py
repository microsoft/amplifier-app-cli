"""Setup helpers for the interactive session resource graph."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from amplifier_core import AmplifierSession

from amplifier_app_cli.runtime.session_state import coordinator_session_state
from amplifier_app_cli.runtime.session_persistence import SessionRuntimeOverrides
from amplifier_app_cli.session_runner import InitializedSession, SessionConfig
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.ui.authorization_stage import CompletionProvider
from amplifier_app_cli.ui.authorization_stage import provider_backed_classifier
from amplifier_app_cli.ui.clipboard import ClipboardImageInjector
from amplifier_app_cli.ui.command_processor import CommandProcessor
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.governance import ActionGovernor
from amplifier_app_cli.ui.improve_evidence import RuntimeImproveEvidenceSource
from amplifier_app_cli.ui.improve_workflow import ConfiguratorImprovePersistence
from amplifier_app_cli.ui.improve_workflow import ImproveWorkflow
from amplifier_app_cli.ui.interaction_state import (
    DEFAULT_TRUST_PRESETS,
    NeedsYouQueue,
    SteeringQueue,
    TrustState,
)
from amplifier_app_cli.ui.interaction_runtime_state import InteractionRuntimeState
from amplifier_app_cli.ui.interaction_runtime_state import interaction_state_for
from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry
from amplifier_app_cli.ui.notices import TransientNoticeState
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui.runtime_status import attach_runtime_status_hooks
from amplifier_app_cli.ui.stream_status import StreamStatusTracker
from amplifier_app_cli.ui.stream_status import attach_layered_stream_hooks
from amplifier_app_cli.ui.task_hooks import attach_task_status_hooks
from amplifier_app_cli.ui.task_status import TaskStatusTracker
from amplifier_app_cli.ui.safety_classifier import TwoStageActionClassifier

CleanupCallback = Callable[[], None]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InteractiveCleanupCallbacks:
    """Named teardown slots in the original deterministic cleanup order."""

    task_tracker: CleanupCallback | None = None
    stream_status: CleanupCallback | None = None
    runtime_status: CleanupCallback | None = None
    step_boundary: CleanupCallback | None = None
    governance: CleanupCallback | None = None
    image_injector: CleanupCallback | None = None
    approval_trust: CleanupCallback | None = None
    interaction_state: CleanupCallback | None = None

    def collect(
        self, *repl_callbacks: CleanupCallback | None
    ) -> tuple[CleanupCallback, ...]:
        return tuple(
            callback
            for callback in (
                self.task_tracker,
                self.stream_status,
                self.runtime_status,
                self.step_boundary,
                self.governance,
                self.image_injector,
                self.approval_trust,
                self.interaction_state,
                *repl_callbacks,
            )
            if callback is not None
        )


def authorization_classifier(
    session: AmplifierSession,
) -> TwoStageActionClassifier | None:
    providers = session.coordinator.get("providers") or {}
    provider = next(
        (
            item
            for item in providers.values()
            if callable(getattr(item, "complete", None))
        ),
        None,
    )
    return (
        provider_backed_classifier(cast(CompletionProvider, provider))
        if provider is not None
        else None
    )


def register_base_capabilities(
    session: AmplifierSession,
    *,
    notice_state: TransientNoticeState,
    trust_state: TrustState,
    interaction_state: InteractionRuntimeState,
    outcome_ledger: OutcomeLedger,
    evidence_model: EvidenceLinkModel,
    needs_you: NeedsYouQueue,
    steering_queue: SteeringQueue,
    governor: ActionGovernor,
) -> None:
    coordinator = session.coordinator
    coordinator.register_capability("ui.notices", notice_state)
    coordinator.register_capability("ui.trust_state", trust_state)
    coordinator.register_capability("ui.interaction_state", interaction_state)
    coordinator.register_capability("ui.outcome_ledger", outcome_ledger)
    coordinator.register_capability("ui.evidence_links", evidence_model)
    coordinator.register_capability("ui.needs_you", needs_you)
    coordinator.register_capability("ui.steering_queue", steering_queue)
    coordinator.register_capability("ui.action_governor", governor)
    coordinator.register_capability("ui.defer_question", needs_you.defer)
    coordinator.register_capability(
        "ui.dependency_blocked", needs_you.dependency_blocked
    )
    coordinator.register_capability("ui.denial_log", governor.denial_log)


def attach_trackers(
    session: AmplifierSession,
    config: Mapping[str, Any],
    session_id: str,
    layered: bool,
    cleanup: InteractiveCleanupCallbacks,
) -> tuple[
    TaskStatusTracker | None,
    StreamStatusTracker | None,
    RuntimeStatusTracker | None,
    ClipboardImageInjector | None,
]:
    if not layered:
        return None, None, None, None
    task_tracker = TaskStatusTracker(
        session_id,
        todo_source=lambda: getattr(session.coordinator, "todo_state", None),
    )
    hook_configs = config.get("hooks", [])
    show_thinking = any(
        isinstance(hook, Mapping)
        and hook.get("module") == "hooks-streaming-ui"
        and bool(
            ((hook.get("config") or {}).get("ui", {})).get(
                "show_thinking_stream", False
            )
        )
        for hook in hook_configs
        if isinstance(hook_configs, Sequence)
    )
    stream_status = StreamStatusTracker(session_id, show_thinking=show_thinking)
    runtime_status = RuntimeStatusTracker(session_id)
    runtime_status.seed_session_cost("0")
    cleanup.task_tracker = attach_task_status_hooks(session.coordinator, task_tracker)
    cleanup.runtime_status = attach_runtime_status_hooks(
        session.coordinator, runtime_status
    )
    hooks = session.coordinator.get("hooks")
    image_injector = None
    if hooks:
        cleanup.stream_status = attach_layered_stream_hooks(
            session.coordinator, stream_status
        )
        image_injector = ClipboardImageInjector(session.coordinator.get("context"))
        cleanup.image_injector = _cleanup_callback(
            hooks.register(
                "provider:request",
                image_injector.handle_provider_request,
                priority=900,
                name="cli-clipboard-images",
            )
        )
    return task_tracker, stream_status, runtime_status, image_injector


def create_improve_workflow(
    initialized: InitializedSession,
    session: AmplifierSession,
    config: Mapping[str, Any],
    trust_state: TrustState,
    outcome_ledger: OutcomeLedger,
    governor: ActionGovernor,
    runtime_status: RuntimeStatusTracker | None,
) -> ImproveWorkflow:
    context = session.coordinator.get("context")
    get_messages = getattr(context, "get_messages", None)
    context_messages = (
        cast(
            Callable[[], Awaitable[Sequence[Mapping[str, Any]]]],
            get_messages,
        )
        if callable(get_messages)
        else None
    )
    approval_system = getattr(session.coordinator, "approval_system", None)
    evidence = RuntimeImproveEvidenceSource(
        context_messages=context_messages,
        approval_history=(
            (lambda: getattr(approval_system, "decision_history", ()))
            if approval_system is not None
            else None
        ),
        config=config,
        runtime_status=runtime_status,
    )
    persistence = None
    if initialized.configurator is not None:
        try:
            persistence = ConfiguratorImprovePersistence(initialized.configurator)
        except TypeError:
            logger.debug("Configurator cannot persist /improve edits")
    return ImproveWorkflow(
        outcome_ledger=outcome_ledger,
        denial_log=governor.denial_log,
        runtime_status=runtime_status,
        trust_state=trust_state,
        evidence_source=evidence,
        persistence=persistence,
    )


async def restore_resume_state(
    session_config: SessionConfig,
    session: AmplifierSession,
    session_id: str,
    store: SessionStore,
    command_processor: CommandProcessor,
    mode_profiles: ModeProfileRegistry,
    runtime_status: RuntimeStatusTracker | None,
    outcome_ledger: OutcomeLedger,
) -> tuple[object, object, object]:
    if not session_config.is_resume:
        return None, None, None
    try:
        metadata = store.get_metadata(session_id) or {}
    except FileNotFoundError:
        metadata = {}
    saved_mode = metadata.get("active_mode")
    if isinstance(saved_mode, str) and saved_mode:
        await command_processor._handle_mode(f"{saved_mode} on")
    saved_permission = metadata.get("permission_posture")
    restored_ui_mode = metadata.get("ui_mode")
    if (
        not isinstance(restored_ui_mode, str)
        or restored_ui_mode not in mode_profiles.names
    ):
        restored_ui_mode = saved_permission
    state = coordinator_session_state(session.coordinator)
    overrides = SessionRuntimeOverrides.from_metadata(metadata)
    if overrides.reasoning_effort is not None:
        state["ui.effort_override"] = overrides.reasoning_effort
    providers = session.coordinator.get("providers") or {}
    if (
        overrides.provider is not None
        and overrides.model is not None
        and isinstance(providers, Mapping)
        and providers.get(overrides.provider) is not None
    ):
        state["ui.model_override"] = {
            "provider": overrides.provider,
            "model": overrides.model,
        }
    if isinstance(metadata.get("show_debug"), bool):
        state["ui.show_debug"] = metadata["show_debug"]
    if isinstance(restored_ui_mode, str) and restored_ui_mode in mode_profiles.names:
        interaction_state_for(
            session.coordinator,
            ui_modes=mode_profiles.names,
        ).select_ui_mode(restored_ui_mode)
    if runtime_status is not None:
        runtime_status.seed_session_cost(metadata.get("session_cost_usd", "0"))
    outcome_ledger.restore_records(metadata.get("outcome_ledger"))
    return (
        metadata.get("permission_profile"),
        saved_permission,
        metadata.get("permission_policy_version"),
    )


def restore_runtime_overrides(session: AmplifierSession) -> None:
    """Replay explicit slash-command choices after mode profile initialization."""
    coordinator = session.coordinator
    state = coordinator_session_state(coordinator)
    overrides = SessionRuntimeOverrides.from_session_state(state)

    if overrides.reasoning_effort is not None:
        orchestrator = coordinator.get("orchestrator")
        orchestrator_config = getattr(orchestrator, "config", None)
        if isinstance(orchestrator_config, dict):
            orchestrator_config["reasoning_effort"] = overrides.reasoning_effort
            profile = state.get("ui.mode_profile")
            if isinstance(profile, dict):
                profile["reasoning_effort"] = overrides.reasoning_effort

    if overrides.provider is None or overrides.model is None:
        return
    providers = coordinator.get("providers") or {}
    if not isinstance(providers, Mapping):
        return
    provider = providers.get(overrides.provider)
    if provider is None:
        return
    setattr(provider, "default_model", overrides.model)
    provider_config = getattr(provider, "config", None)
    if isinstance(provider_config, dict):
        provider_config["default_model"] = overrides.model
    profile = state.get("ui.mode_profile")
    if isinstance(profile, dict):
        profile.update({"provider": overrides.provider, "model": overrides.model})


def restore_trust(
    trust_state: TrustState,
    restored: tuple[object, object, object],
) -> None:
    profile, posture, policy_version = restored
    try:
        trust_state.restore_persisted(
            profile,
            posture,
            policy_version=policy_version,
        )
    except ValueError:
        logger.debug("Ignoring invalid saved permission posture", exc_info=True)


@dataclass(frozen=True, slots=True)
class TuiStartupPreference:
    """Resolved config.tui.startup_mode / startup_permission for a fresh
    (non-resumed) interactive session.

    Only ever applied to brand-new sessions -- resuming a session is the
    user actively choosing to continue whatever mode and posture that
    session already had, which must win over this app-wide default. Per
    ADR-0005, a configured ``startup_permission`` (e.g. "choosing the
    bypass permissions preset") IS the explicit user action the ADR
    requires: the caller latches ``_trust_explicitly_set`` before
    ``initialize()`` so a later mode-only cycle never silently reverts it.
    """

    mode: str | None = None
    permission: str | None = None


_DEFAULT_VALID_PERMISSIONS: tuple[str, ...] = tuple(
    preset.name for preset in DEFAULT_TRUST_PRESETS
)


def resolve_tui_startup_preference(
    raw: Mapping[str, Any],
    *,
    valid_modes: Iterable[str],
    valid_permissions: Iterable[str] = _DEFAULT_VALID_PERMISSIONS,
) -> TuiStartupPreference:
    """Validate config.tui.startup_mode/startup_permission (AppSettings).

    Unknown, malformed, or non-string values are dropped -- never guessed
    or coerced -- and logged. The caller's fallback for a dropped value is
    the existing safe chat/chat default, never a broadened guess.
    """
    modes = frozenset(valid_modes)
    permissions = frozenset(valid_permissions)

    mode = raw.get("startup_mode")
    if mode is not None and (not isinstance(mode, str) or mode not in modes):
        logger.warning(
            "Ignoring invalid config.tui.startup_mode: %r (expected one of %s)",
            mode,
            sorted(modes),
        )
        mode = None

    permission = raw.get("startup_permission")
    if permission is not None and (
        not isinstance(permission, str) or permission not in permissions
    ):
        logger.warning(
            "Ignoring invalid config.tui.startup_permission: %r (expected one of %s)",
            permission,
            sorted(permissions),
        )
        permission = None

    return TuiStartupPreference(mode=mode, permission=permission)


def bind_approval_trust(
    approval_system: object,
    trust_state: TrustState,
) -> CleanupCallback:
    def sync() -> None:
        set_bypass = getattr(approval_system, "set_bypass_permissions", None)
        if callable(set_bypass):
            set_bypass(trust_state.bypass_permissions)

    sync()
    return trust_state.add_listener(sync)


def _cleanup_callback(value: object) -> CleanupCallback | None:
    if not callable(value):
        return None

    def cleanup() -> None:
        value()

    return cleanup


__all__ = [
    "InteractiveCleanupCallbacks",
    "TuiStartupPreference",
    "attach_trackers",
    "authorization_classifier",
    "bind_approval_trust",
    "create_improve_workflow",
    "register_base_capabilities",
    "resolve_tui_startup_preference",
    "restore_runtime_overrides",
    "restore_resume_state",
    "restore_trust",
]
