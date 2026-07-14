"""Public sub-session spawn and resume facade.

The facade intentionally retains the historical patch points used by tests and
integrations. Focused runtime modules receive those live dependencies on each
call, keeping behavior replaceable without centralizing lifecycle logic here.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import replace

from amplifier_core import AmplifierSession
from amplifier_foundation import bridge_child_cost
from amplifier_foundation import generate_sub_session_id

from .agent_config import merge_configs
from .runtime.bundle_context import BUNDLE_CONTEXT_CAPABILITY
from .runtime.bundle_context import SerializedBundleContext
from .runtime.bundle_context import normalize_bundle_context
from .runtime.session_resume import _REDACTION_SENTINEL
from .runtime.session_resume import _find_redacted_values
from .runtime.session_resume import resume_child_session
from .runtime.session_spawn_config import filter_hooks
from .runtime.session_spawn_config import filter_tools
from .runtime.session_spawn_config import prepare_spawn
from .runtime.session_spawn_inprocess import run_inprocess_spawn
from .runtime.session_spawn_models import ResumeRequest
from .runtime.session_spawn_models import SessionLifecycleServices
from .runtime.session_spawn_models import SpawnRequest
from .runtime.session_spawn_subprocess import run_subprocess_spawn
from .ui.interaction_state import TrustState
from .ui.runtime_status import RUNTIME_STATUS_CAPABILITY
from .ui.runtime_status import RuntimeStatusTracker
from .ui.runtime_status import attach_runtime_status_hooks
from .ui.task_hooks import TASK_STATUS_CAPABILITY
from .ui.task_hooks import attach_task_status_hooks
from .ui.task_status import TaskStatusTracker

logger = logging.getLogger(__name__)

# Filter out ambient interpreter paths when forwarding bundle additions to a
# subprocess. This is captured once, before bundle activation mutates sys.path.
_DEFAULT_SYS_PATHS: frozenset[str] = frozenset(sys.path)

# Historical helper names remain importable from this public module.
_filter_tools = filter_tools
_filter_hooks = filter_hooks


def _session_trust_state(session: object) -> TrustState | None:
    """Return the app-owned trust state exposed by a live session."""
    coordinator = getattr(session, "coordinator", None)
    get_capability = getattr(coordinator, "get_capability", None)
    if not callable(get_capability):
        return None
    trust_state = get_capability("ui.trust_state")
    return trust_state if isinstance(trust_state, TrustState) else None


def _session_bypass_permissions(session: object) -> bool:
    """Read only an explicit bypass selection from a live session."""
    trust_state = _session_trust_state(session)
    return trust_state.bypass_permissions if trust_state is not None else False


def _propagate_task_status_tracker(
    parent_session: object,
    child_session: object,
) -> None:
    """Share layered task state with an in-process child session."""
    parent_coordinator = getattr(parent_session, "coordinator", None)
    child_coordinator = getattr(child_session, "coordinator", None)
    if parent_coordinator is None or child_coordinator is None:
        return
    tracker = parent_coordinator.get_capability(TASK_STATUS_CAPABILITY)
    if isinstance(tracker, TaskStatusTracker):
        attach_task_status_hooks(child_coordinator, tracker)


def _propagate_runtime_status_tracker(
    parent_session: object,
    child_session: object,
) -> None:
    """Share layered runtime state with an in-process child session."""
    parent_coordinator = getattr(parent_session, "coordinator", None)
    child_coordinator = getattr(child_session, "coordinator", None)
    if parent_coordinator is None or child_coordinator is None:
        return
    tracker = parent_coordinator.get_capability(RUNTIME_STATUS_CAPABILITY)
    if isinstance(tracker, RuntimeStatusTracker):
        attach_runtime_status_hooks(child_coordinator, tracker)


def _extract_bundle_context(
    session: AmplifierSession,
) -> SerializedBundleContext | None:
    """Read the public serialized bundle context owned by the root session."""
    value = session.coordinator.get_capability(BUNDLE_CONTEXT_CAPABILITY)
    return normalize_bundle_context(value)


def _lifecycle_services() -> SessionLifecycleServices:
    """Capture the facade's current patchable dependencies for one operation."""
    return SessionLifecycleServices(
        session_factory=AmplifierSession,
        merge_configs=merge_configs,
        generate_sub_session_id=generate_sub_session_id,
        bridge_child_cost=bridge_child_cost,
        extract_bundle_context=_extract_bundle_context,
        session_trust_state=_session_trust_state,
        session_bypass_permissions=_session_bypass_permissions,
        propagate_task_status_tracker=_propagate_task_status_tracker,
        propagate_runtime_status_tracker=_propagate_runtime_status_tracker,
        spawn_sub_session=spawn_sub_session,
        resume_sub_session=resume_sub_session,
        default_sys_paths=_DEFAULT_SYS_PATHS,
    )


async def spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
    tool_inheritance: dict[str, list[str]] | None = None,
    hook_inheritance: dict[str, list[str]] | None = None,
    orchestrator_config: dict | None = None,
    parent_messages: list[dict] | None = None,
    provider_preferences: list | None = None,
    self_delegation_depth: int = 0,
    session_metadata: dict | None = None,
    use_subprocess: bool = False,
) -> dict:
    """Spawn a child with parent config plus the selected agent overlay.

    Precedence is app policy documented in ``docs/SPAWN_PRECEDENCE.md``.
    ``use_subprocess`` or ``spawn_mode: subprocess`` selects the isolated
    Foundation adapter; otherwise the child runs in-process.
    """
    services = _lifecycle_services()
    request = SpawnRequest(
        agent_name=agent_name,
        instruction=instruction,
        parent_session=parent_session,
        agent_configs=agent_configs,
        sub_session_id=sub_session_id,
        tool_inheritance=tool_inheritance,
        hook_inheritance=hook_inheritance,
        orchestrator_config=orchestrator_config,
        parent_messages=parent_messages,
        provider_preferences=provider_preferences,
        self_delegation_depth=self_delegation_depth,
        session_metadata=session_metadata,
        use_subprocess=use_subprocess,
    )
    prepared = await prepare_spawn(request, services)
    if use_subprocess or prepared.merged_config.get("spawn_mode") == "subprocess":
        return await run_subprocess_spawn(prepared, services)
    return await run_inprocess_spawn(prepared, services)


async def resume_sub_session(
    sub_session_id: str,
    instruction: str,
    parent_session: AmplifierSession | None = None,
) -> dict:
    """Resume a persisted child session for multi-turn engagement."""

    async def child_spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: AmplifierSession,
        agent_configs: dict[str, dict],
        sub_session_id: str | None = None,
        tool_inheritance: dict[str, list[str]] | None = None,
        hook_inheritance: dict[str, list[str]] | None = None,
        orchestrator_config: dict | None = None,
        parent_messages: list[dict] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        session_metadata: dict | None = None,
        use_subprocess: bool = False,
    ) -> dict:
        return await spawn_sub_session(
            agent_name=agent_name,
            instruction=instruction,
            parent_session=parent_session,
            agent_configs=agent_configs,
            sub_session_id=sub_session_id,
            tool_inheritance=tool_inheritance,
            hook_inheritance=hook_inheritance,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
            session_metadata=session_metadata,
            use_subprocess=use_subprocess,
        )

    services = replace(
        _lifecycle_services(),
        spawn_sub_session=child_spawn_capability,
    )
    return await resume_child_session(
        ResumeRequest(
            sub_session_id=sub_session_id,
            instruction=instruction,
            parent_session=parent_session,
        ),
        services,
    )


__all__ = [
    "_REDACTION_SENTINEL",
    "_extract_bundle_context",
    "_filter_hooks",
    "_filter_tools",
    "_find_redacted_values",
    "_propagate_runtime_status_tracker",
    "_propagate_task_status_tracker",
    "_session_bypass_permissions",
    "_session_trust_state",
    "resume_sub_session",
    "spawn_sub_session",
]
