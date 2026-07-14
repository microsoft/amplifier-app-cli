"""In-process creation, execution, and persistence for child sessions."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from amplifier_core import AmplifierSession
from amplifier_core.hooks import HookResult
from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY
from amplifier_app_cli.approval_provider import CLIApprovalProvider
from amplifier_app_cli.runtime.amplifier_compat import (
    install_hook_serialization_compatibility,
)
from amplifier_app_cli.runtime.bundle_context import BUNDLE_CONTEXT_CAPABILITY
from amplifier_app_cli.runtime.bundle_context import build_bundle_context
from amplifier_app_cli.runtime.session_spawn_models import PreparedSpawn
from amplifier_app_cli.runtime.session_spawn_models import SessionLifecycleServices
from amplifier_app_cli.ui.interaction_state import TRUST_POLICY_VERSION

logger = logging.getLogger(__name__)


async def run_inprocess_spawn(
    prepared: PreparedSpawn,
    services: SessionLifecycleServices,
) -> dict:
    """Create and execute a prepared child in the current process."""
    from amplifier_foundation.mentions import ContentDeduplicator
    from amplifier_foundation.mentions import expand_mentions_in_instruction

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver
    from amplifier_app_cli.session_store import SessionStore

    request = prepared.request
    parent = request.parent_session
    display_system = parent.coordinator.display_system
    child_session = services.session_factory(
        config=prepared.merged_config,
        loader=None,
        session_id=prepared.sub_session_id,
        parent_id=parent.session_id,
        approval_system=parent.coordinator.approval_system,
        display_system=display_system,
    )
    if prepared.parent_trust_state is not None:
        child_session.coordinator.register_capability(
            "ui.trust_state", prepared.parent_trust_state
        )
    if hasattr(display_system, "push_nesting"):
        display_system.push_nesting()

    parent_resolver = parent.coordinator.get("module-source-resolver")
    child_resolver = parent_resolver or create_foundation_resolver()
    await child_session.coordinator.mount("module-source-resolver", child_resolver)

    # Modules may consume this capability while mounting or from
    # on_session_ready, both of which run during initialize(). Register it
    # before initialization and always provide a usable fallback.
    child_working_dir = parent.coordinator.get_capability("session.working_dir") or str(
        Path.cwd().resolve()
    )
    child_session.coordinator.register_capability(
        "session.working_dir", child_working_dir
    )

    parent_bundle_context = services.extract_bundle_context(parent)
    shared_paths = list(
        dict.fromkeys(
            [
                *(parent_bundle_context or {}).get("module_paths", {}).values(),
                *(parent_bundle_context or {}).get("bundle_package_paths", []),
            ]
        )
    )
    for path in shared_paths:
        if path not in sys.path:
            sys.path.insert(0, path)
    if shared_paths:
        logger.debug(
            "Shared %d sys.path entries from parent to child session",
            len(shared_paths),
        )

    await child_session.initialize()
    child_bundle_context = build_bundle_context(
        prepared.merged_config,
        child_resolver,
        base_context=parent_bundle_context,
    )
    child_session.coordinator.register_capability(
        BUNDLE_CONTEXT_CAPABILITY,
        child_bundle_context,
    )
    install_hook_serialization_compatibility()
    services.propagate_task_status_tracker(parent, child_session)
    services.propagate_runtime_status_tracker(parent, child_session)

    child_coordinator = getattr(child_session, "coordinator", None)
    if prepared.parent_coordinator is not None and child_coordinator is not None:
        try:
            overlay_skills = prepared.parent_coordinator.get_capability(  # type: ignore[attr-defined]
                RUNTIME_SKILL_OVERLAY_CAPABILITY
            )
        except (AttributeError, KeyError):
            overlay_skills = None
        if overlay_skills:
            try:
                child_coordinator.register_capability(
                    RUNTIME_SKILL_OVERLAY_CAPABILITY,
                    list(overlay_skills),
                )
            except AttributeError:
                pass

    parent_cancellation = parent.coordinator.cancellation
    child_cancellation = child_session.coordinator.cancellation
    parent_cancellation.register_child(child_cancellation)
    logger.debug(
        "Registered child cancellation token for sub-session %s",
        prepared.sub_session_id,
    )

    parent_mention_resolver = parent.coordinator.get_capability("mention_resolver")
    child_session.coordinator.register_capability(
        "mention_resolver",
        parent_mention_resolver or AppMentionResolver(),
    )
    parent_deduplicator = parent.coordinator.get_capability("mention_deduplicator")
    child_session.coordinator.register_capability(
        "mention_deduplicator",
        parent_deduplicator or ContentDeduplicator(),
    )
    parent_routing = parent.coordinator.get_capability("session.routing")
    if parent_routing:
        child_session.coordinator.register_capability("session.routing", parent_routing)
    child_session.coordinator.register_capability(
        "self_delegation_depth", request.self_delegation_depth
    )

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
        return await services.spawn_sub_session(
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

    async def child_resume_capability(sub_session_id: str, instruction: str) -> dict:
        return await services.resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
            parent_session=parent,
        )

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )
    child_session.coordinator.register_capability(
        "session.resume", child_resume_capability
    )

    register_provider = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider:
        from rich.console import Console

        register_provider(
            CLIApprovalProvider(Console(), child_session.coordinator.approval_system)
        )
        logger.debug(
            "Registered approval provider for child session %s",
            prepared.sub_session_id,
        )

    system_instruction = prepared.agent_config.get(
        "instruction"
    ) or prepared.agent_config.get("system", {}).get("instruction")
    if system_instruction:
        context = child_session.coordinator.get("context")
        resolver = child_session.coordinator.get_capability("mention_resolver")
        if resolver is not None:
            deduplicator = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            working_dir = child_session.coordinator.get_capability(
                "session.working_dir"
            )
            system_instruction = await expand_mentions_in_instruction(
                system_instruction,
                resolver=resolver,
                deduplicator=deduplicator,
                relative_to=Path(working_dir) if working_dir else Path.cwd(),
            )
        if context and hasattr(context, "add_message"):
            await context.add_message({"role": "system", "content": system_instruction})

    completion_data: dict = {}
    hooks = child_session.coordinator.get("hooks")
    unregister_hook = None
    if hooks:

        async def capture_completion(event: str, data: dict) -> HookResult:
            completion_data.update(data)
            return HookResult()

        unregister_hook = hooks.register(
            "orchestrator:complete",
            capture_completion,
            priority=999,
            name="_spawn_capture",
        )

    instruction = request.instruction
    if instruction:
        resolver = child_session.coordinator.get_capability("mention_resolver")
        if resolver is not None:
            deduplicator = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            working_dir = child_session.coordinator.get_capability(
                "session.working_dir"
            )
            instruction = await expand_mentions_in_instruction(
                instruction,
                resolver=resolver,
                deduplicator=deduplicator,
                relative_to=Path(working_dir) if working_dir else Path.cwd(),
            )

    try:
        try:
            response = await child_session.execute(instruction)
        finally:
            if unregister_hook:
                unregister_hook()

        context = child_session.coordinator.get("context")
        transcript = await context.get_messages() if context else []
        parent_trace_id = getattr(parent, "trace_id", parent.session_id)
        child_span: str | None = None
        if "_" in prepared.sub_session_id and "-" in prepared.sub_session_id:
            child_span = prepared.sub_session_id.rsplit("_", 1)[0].rsplit("-", 1)[-1]
        metadata = {
            "session_id": prepared.sub_session_id,
            "parent_id": parent.session_id,
            "trace_id": parent_trace_id,
            "agent_name": request.agent_name,
            "child_span": child_span,
            "created": datetime.now(UTC).isoformat(),
            "config": prepared.merged_config,
            "agent_overlay": prepared.agent_config,
            "turn_count": 1,
            "bundle_context": services.extract_bundle_context(parent),
            "self_delegation_depth": request.self_delegation_depth,
            "working_dir": child_working_dir,
            "permission_posture": (
                prepared.parent_trust_state.active.name
                if prepared.parent_trust_state is not None
                else (
                    "bypass" if services.session_bypass_permissions(parent) else "chat"
                )
            ),
            "permission_policy_version": TRUST_POLICY_VERSION,
        }
        if prepared.parent_trust_state is not None:
            metadata["permission_profile"] = prepared.parent_trust_state.snapshot()
        SessionStore().save(prepared.sub_session_id, transcript, metadata)
        logger.debug("Sub-session %s state persisted", prepared.sub_session_id)
        await services.bridge_child_cost(
            child_coordinator=child_session.coordinator,
            parent_coordinator=parent.coordinator,
            child_session_id=prepared.sub_session_id,
        )
    finally:
        parent_cancellation.unregister_child(child_cancellation)
        logger.debug(
            "Unregistered child cancellation token for sub-session %s",
            prepared.sub_session_id,
        )
        if hasattr(display_system, "pop_nesting"):
            display_system.pop_nesting()
        await child_session.cleanup()

    return {
        "output": response,
        "session_id": prepared.sub_session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count", 1),
        "metadata": completion_data.get("metadata", {}),
    }


__all__ = ["run_inprocess_spawn"]
