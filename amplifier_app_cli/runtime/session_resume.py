"""Reconstruction and execution of persisted child sessions."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_core.hooks import HookResult
from amplifier_foundation.bundle import BundleModuleResolver
from amplifier_app_cli.approval_provider import CLIApprovalProvider
from amplifier_app_cli.lib.bundle_loader import AppModuleResolver
from amplifier_app_cli.lib.settings import AppSettings
from amplifier_app_cli.runtime.amplifier_compat import (
    install_hook_serialization_compatibility,
)
from amplifier_app_cli.runtime.bundle_context import BUNDLE_CONTEXT_CAPABILITY
from amplifier_app_cli.runtime.bundle_context import build_bundle_context
from amplifier_app_cli.runtime.bundle_context import normalize_bundle_context
from amplifier_app_cli.runtime.config_merge import deep_merge
from amplifier_app_cli.runtime.config_merge import expand_env_vars
from .config_policies import _apply_hook_overrides
from amplifier_app_cli.runtime.config_providers import apply_provider_overrides
from amplifier_app_cli.runtime.config_providers import map_provider_ids_to_instance_ids
from amplifier_app_cli.runtime.session_spawn_models import ResumeRequest
from amplifier_app_cli.runtime.session_spawn_models import SessionLifecycleServices
from amplifier_app_cli.ui.interaction_state import TRUST_POLICY_VERSION
from amplifier_app_cli.ui.interaction_state import TrustState

logger = logging.getLogger(__name__)

_REDACTION_SENTINEL = "[REDACTED]"


def _find_redacted_values(value: object, path: str = "") -> list[str]:
    """Return paths whose persisted value still contains the redaction sentinel."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_find_redacted_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_redacted_values(child, f"{path}[{index}]"))
    elif value == _REDACTION_SENTINEL:
        found.append(path or "<root>")
    return found


def _refresh_resume_credentials(
    merged_config: dict[str, Any],
    *,
    session_id: str,
) -> dict[str, Any]:
    """Rehydrate persisted provider and hook credentials from live settings."""
    settings = AppSettings()
    refreshed_config = merged_config

    providers = refreshed_config.get("providers")
    if providers:
        live_provider_overrides = settings.get_provider_overrides()
        if live_provider_overrides:
            refreshed_providers = apply_provider_overrides(
                providers, live_provider_overrides
            )
            refreshed_providers = map_provider_ids_to_instance_ids(refreshed_providers)
            refreshed_config = {
                **refreshed_config,
                "providers": refreshed_providers,
            }
            logger.debug(
                "Refreshed credentials for %d provider(s) at resume time",
                len(refreshed_providers),
            )

    hooks = refreshed_config.get("hooks")
    if hooks:
        config_overrides = settings.get_config_overrides()
        refreshed_hooks = [
            {
                **hook,
                "config": deep_merge(
                    hook.get("config", {}) or {},
                    config_overrides[hook["module"]],
                ),
            }
            if isinstance(hook, dict) and hook.get("module") in config_overrides
            else hook
            for hook in hooks
        ]
        notification_overrides = settings.get_notification_hook_overrides()
        if notification_overrides:
            refreshed_hooks = _apply_hook_overrides(
                refreshed_hooks, notification_overrides
            )
        refreshed_config = {**refreshed_config, "hooks": refreshed_hooks}
        logger.debug(
            "Refreshed credentials for %d hook(s) at resume time",
            len(refreshed_hooks),
        )

    refreshed_config = expand_env_vars(refreshed_config)
    redacted_paths = _find_redacted_values(refreshed_config)
    if redacted_paths:
        logger.warning(
            "Sub-session %s: %d config field(s) still hold the redaction "
            "sentinel '%s' after credential refresh (no live override found "
            "to restore them): %s. These fields are mounted as-is; the "
            "destination/consumer is expected to reject them rather than "
            "receive a fake credential.",
            session_id,
            len(redacted_paths),
            _REDACTION_SENTINEL,
            redacted_paths,
        )
    return refreshed_config


async def resume_child_session(
    request: ResumeRequest,
    services: SessionLifecycleServices,
) -> dict:
    """Load, reconstruct, execute, and persist a child session."""
    from amplifier_foundation.mentions import ContentDeduplicator
    from amplifier_foundation.mentions import expand_mentions_in_instruction

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver
    from amplifier_app_cli.session_store import SessionStore
    from amplifier_app_cli.ui import CLIApprovalSystem
    from amplifier_app_cli.ui import CLIDisplaySystem

    store = SessionStore()
    if not store.exists(request.sub_session_id):
        raise FileNotFoundError(
            f"Sub-session '{request.sub_session_id}' not found. "
            "Session may have expired or was never created."
        )
    try:
        transcript, metadata = store.load(request.sub_session_id)
    except Exception as error:
        raise RuntimeError(
            f"Failed to load sub-session '{request.sub_session_id}': {error}"
        ) from error

    merged_config = metadata.get("config")
    if not merged_config:
        raise RuntimeError(
            f"Corrupted session metadata for '{request.sub_session_id}'. "
            "Cannot reconstruct session without config."
        )
    merged_config = _refresh_resume_credentials(
        merged_config,
        session_id=request.sub_session_id,
    )

    parent_id = metadata.get("parent_id")
    agent_name = metadata.get("agent_name", "unknown")
    trace_id = metadata.get("trace_id")
    resumed_trust_state: TrustState | None
    if request.parent_session is not None:
        resumed_trust_state = services.session_trust_state(request.parent_session)
        approval_system = request.parent_session.coordinator.approval_system
        display_system = request.parent_session.coordinator.display_system
        logger.debug(
            "Resuming sub-session %s (agent=%s, parent=%s, trace=%s) "
            "with parent UX systems",
            request.sub_session_id,
            agent_name,
            parent_id,
            trace_id,
        )
    else:
        resumed_trust_state = TrustState()
        try:
            resumed_trust_state.restore_persisted(
                metadata.get("permission_profile"),
                metadata.get("permission_posture"),
                policy_version=metadata.get("permission_policy_version"),
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid saved permission posture for sub-session %s",
                request.sub_session_id,
            )
        approval_system = CLIApprovalSystem(
            bypass_permissions=resumed_trust_state.bypass_permissions
        )
        display_system = CLIDisplaySystem()
        logger.debug(
            "Resuming standalone sub-session %s (agent=%s, parent=%s, trace=%s)",
            request.sub_session_id,
            agent_name,
            parent_id,
            trace_id,
        )

    child_session = services.session_factory(
        config=merged_config,
        loader=None,
        session_id=request.sub_session_id,
        parent_id=parent_id,
        approval_system=approval_system,
        display_system=display_system,
    )
    if resumed_trust_state is not None:
        child_session.coordinator.register_capability(
            "ui.trust_state", resumed_trust_state
        )

    bundle_context = normalize_bundle_context(metadata.get("bundle_context"))
    if bundle_context and bundle_context.get("module_paths"):
        module_paths = {
            name: Path(path) for name, path in bundle_context["module_paths"].items()
        }
        bundle_resolver = BundleModuleResolver(module_paths=module_paths)
        logger.debug(
            "Restored BundleModuleResolver with %d module paths",
            len(module_paths),
        )
        resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=create_foundation_resolver(),
        )
        logger.debug("Wrapped with AppModuleResolver for settings fallback")
    else:
        resolver = create_foundation_resolver()
    await child_session.coordinator.mount("module-source-resolver", resolver)

    saved_working_dir = metadata.get("working_dir")
    parent_working_dir = (
        request.parent_session.coordinator.get_capability("session.working_dir")
        if request.parent_session is not None
        else None
    )
    child_working_dir = (
        saved_working_dir or parent_working_dir or str(Path.cwd().resolve())
    )
    child_session.coordinator.register_capability(
        "session.working_dir", child_working_dir
    )

    if bundle_context:
        for path in bundle_context.get("bundle_package_paths", []):
            if path not in sys.path:
                sys.path.insert(0, path)
    await child_session.initialize()
    bundle_context = build_bundle_context(
        merged_config,
        resolver,
        base_context=bundle_context,
    )
    child_session.coordinator.register_capability(
        BUNDLE_CONTEXT_CAPABILITY,
        bundle_context,
    )
    install_hook_serialization_compatibility()
    if request.parent_session is not None:
        services.propagate_task_status_tracker(request.parent_session, child_session)
        services.propagate_runtime_status_tracker(request.parent_session, child_session)

    if bundle_context and bundle_context.get("mention_mappings"):
        mention_mappings = {
            name: Path(path)
            for name, path in bundle_context["mention_mappings"].items()
        }
        child_session.coordinator.register_capability(
            "mention_resolver",
            AppMentionResolver(bundle_mappings=mention_mappings),
        )
        logger.debug(
            "Restored AppMentionResolver with %d bundle mappings",
            len(mention_mappings),
        )
    else:
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )
    child_session.coordinator.register_capability(
        "mention_deduplicator", ContentDeduplicator()
    )
    child_session.coordinator.register_capability(
        "self_delegation_depth", metadata.get("self_delegation_depth", 0)
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
            parent_session=child_session,
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
            "Registered approval provider for resumed child session %s",
            request.sub_session_id,
        )

    hooks = child_session.coordinator.get("hooks")
    if hooks:
        await hooks.emit(
            "session:resume",
            {
                "session_id": request.sub_session_id,
                "parent_id": parent_id,
                "agent_name": agent_name,
                "turn_count": len(transcript) + 1,
            },
        )
    context = child_session.coordinator.get("context")
    if context and hasattr(context, "add_message"):
        for message in transcript:
            await context.add_message(message)
    else:
        logger.warning(
            "Context module does not support add_message() - transcript not restored "
            "for session %s",
            request.sub_session_id,
        )

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

    if request.parent_session is not None:
        parent_cancellation = request.parent_session.coordinator.cancellation
        child_cancellation = child_session.coordinator.cancellation
        parent_cancellation.register_child(child_cancellation)
        logger.debug(
            "Registered child cancellation token for resumed sub-session %s",
            request.sub_session_id,
        )
    else:
        parent_cancellation = None
        child_cancellation = None

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

        updated_transcript = await context.get_messages() if context else []
        metadata["turn_count"] = len(updated_transcript)
        metadata["last_updated"] = datetime.now(UTC).isoformat()
        if resumed_trust_state is not None:
            metadata["permission_posture"] = resumed_trust_state.active.name
            metadata["permission_profile"] = resumed_trust_state.snapshot()
            metadata["permission_policy_version"] = TRUST_POLICY_VERSION
        store.save(request.sub_session_id, updated_transcript, metadata)
        logger.debug(
            "Sub-session %s state updated (turn %s)",
            request.sub_session_id,
            metadata["turn_count"],
        )
        if request.parent_session is not None:
            await services.bridge_child_cost(
                child_coordinator=child_session.coordinator,
                parent_coordinator=request.parent_session.coordinator,
                child_session_id=request.sub_session_id,
            )
    finally:
        if parent_cancellation is not None and child_cancellation is not None:
            parent_cancellation.unregister_child(child_cancellation)
            logger.debug(
                "Unregistered child cancellation token for resumed sub-session %s",
                request.sub_session_id,
            )
        await child_session.cleanup()

    return {
        "output": response,
        "session_id": request.sub_session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count", 1),
        "metadata": completion_data.get("metadata", {}),
    }


__all__ = [
    "_REDACTION_SENTINEL",
    "_find_redacted_values",
    "resume_child_session",
]
