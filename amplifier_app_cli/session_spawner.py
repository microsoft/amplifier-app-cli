"""Session spawning for agent delegation.

Implements sub-session creation with configuration inheritance and overlays.
"""

import logging

from amplifier_core import AmplifierSession
from amplifier_foundation import generate_sub_session_id

from .agent_config import merge_configs

logger = logging.getLogger(__name__)


def _extract_bundle_context(session: "AmplifierSession") -> dict | None:
    """Extract serializable bundle context from session.

    Extracts both module resolution paths and mention mappings needed to
    reconstruct bundle context on resume.

    Args:
        session: The session to extract bundle context from.

    Returns:
        Dict with module_paths and mention_mappings, or None if not bundle mode.
    """
    # Get module resolver
    resolver = session.coordinator.get("module-source-resolver")
    if resolver is None:
        return None

    # Extract module paths from resolver
    # Handle both AppModuleResolver (wraps _bundle) and BundleModuleResolver directly
    module_paths: dict[str, str] = {}

    if hasattr(resolver, "_bundle") and hasattr(resolver._bundle, "_paths"):
        # AppModuleResolver wrapping BundleModuleResolver
        module_paths = {k: str(v) for k, v in resolver._bundle._paths.items()}
    elif hasattr(resolver, "_paths"):
        # Direct BundleModuleResolver
        module_paths = {k: str(v) for k, v in resolver._paths.items()}

    if not module_paths:
        # Not bundle mode - no paths to preserve
        return None

    # Extract mention mappings from mention resolver (for @namespace:path resolution)
    mention_mappings: dict[str, str] = {}
    mention_resolver = session.coordinator.get_capability("mention_resolver")
    if mention_resolver and hasattr(mention_resolver, "_bundle_mappings"):
        mention_mappings = {
            k: str(v) for k, v in mention_resolver._bundle_mappings.items()
        }

    return {
        "module_paths": module_paths,
        "mention_mappings": mention_mappings,
    }


def _filter_tools(config: dict, tool_inheritance: dict[str, list[str]]) -> dict:
    """Filter tools in config based on tool inheritance policy.

    Args:
        config: Session config containing "tools" list
        tool_inheritance: Policy dict with either:
            - "exclude_tools": list of tool module names to exclude
            - "inherit_tools": list of tool module names to include (allowlist)

    Returns:
        New config dict with filtered tools list
    """
    tools = config.get("tools", [])
    if not tools:
        return config

    exclude_tools = tool_inheritance.get("exclude_tools", [])
    inherit_tools = tool_inheritance.get("inherit_tools")

    if inherit_tools is not None:
        # Allowlist mode: only include specified tools
        filtered_tools = [t for t in tools if t.get("module") in inherit_tools]
    elif exclude_tools:
        # Blocklist mode: exclude specified tools
        filtered_tools = [t for t in tools if t.get("module") not in exclude_tools]
    else:
        # No filtering
        return config

    # Return new config with filtered tools
    new_config = dict(config)
    new_config["tools"] = filtered_tools

    logger.debug(
        "Filtered tools: %d -> %d (exclude=%s, inherit=%s)",
        len(tools),
        len(filtered_tools),
        exclude_tools,
        inherit_tools,
    )

    return new_config


def _apply_provider_override(
    config: dict,
    provider_id: str | None,
    model: str | None,
) -> dict:
    """Apply provider/model override to config.

    If provider_id is specified and exists in configured providers,
    promotes it to priority 0 (highest precedence).
    If provider not found, logs warning and returns config unchanged.

    Args:
        config: Session config containing "providers" list
        provider_id: Provider to promote (e.g., "anthropic")
        model: Model to use with the provider

    Returns:
        New config with provider priority adjusted
    """
    if not provider_id and not model:
        return config

    providers = config.get("providers", [])
    if not providers:
        logger.warning(
            "Provider override '%s' specified but no providers in config",
            provider_id,
        )
        return config

    # Find target provider (flexible matching)
    target_idx = None
    for i, p in enumerate(providers):
        module_id = p.get("module", "")
        # Match: "anthropic", "provider-anthropic", or full module ID
        if provider_id and provider_id in (
            module_id,
            module_id.replace("provider-", ""),
            f"provider-{provider_id}",
        ):
            target_idx = i
            break

    # If only model specified (no provider), apply to first/priority provider
    if provider_id is None and model:
        # Find lowest priority provider (current default)
        min_priority = float("inf")
        for i, p in enumerate(providers):
            p_config = p.get("config", {})
            priority = p_config.get("priority", 100)
            if priority < min_priority:
                min_priority = priority
                target_idx = i

    if target_idx is None:
        logger.warning(
            "Provider '%s' not found in config. Available: %s",
            provider_id,
            ", ".join(p.get("module", "?") for p in providers),
        )
        return config

    # Clone providers list
    new_providers = []
    for i, p in enumerate(providers):
        p_copy = dict(p)
        p_copy["config"] = dict(p.get("config", {}))

        if i == target_idx:
            # Promote to priority 0 (highest)
            p_copy["config"]["priority"] = 0
            if model:
                p_copy["config"]["model"] = model
            logger.info(
                "Provider override applied: %s (priority=0, model=%s)",
                p_copy.get("module"),
                model or "default",
            )

        new_providers.append(p_copy)

    return {**config, "providers": new_providers}


def _filter_hooks(config: dict, hook_inheritance: dict[str, list[str]]) -> dict:
    """Filter hooks in config based on hook inheritance policy.

    Args:
        config: Session config containing "hooks" list
        hook_inheritance: Policy dict with either:
            - "exclude_hooks": list of hook module names to exclude
            - "inherit_hooks": list of hook module names to include (allowlist)

    Returns:
        New config dict with filtered hooks list
    """
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    exclude_hooks = hook_inheritance.get("exclude_hooks", [])
    inherit_hooks = hook_inheritance.get("inherit_hooks")

    if inherit_hooks is not None:
        # Allowlist mode: only include specified hooks
        filtered_hooks = [h for h in hooks if h.get("module") in inherit_hooks]
    elif exclude_hooks:
        # Blocklist mode: exclude specified hooks
        filtered_hooks = [h for h in hooks if h.get("module") not in exclude_hooks]
    else:
        # No filtering
        return config

    # Return new config with filtered hooks
    new_config = dict(config)
    new_config["hooks"] = filtered_hooks

    logger.debug(
        "Filtered hooks: %d -> %d (exclude=%s, inherit=%s)",
        len(hooks),
        len(filtered_hooks),
        exclude_hooks,
        inherit_hooks,
    )

    return new_config


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
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """
    Spawn sub-session with agent configuration overlay.

    Args:
        agent_name: Name of agent from configuration
        instruction: Task for agent to execute
        parent_session: Parent session for inheritance
        agent_configs: Dict of agent configurations
        sub_session_id: Optional explicit ID (generates if None)
        tool_inheritance: Optional tool filtering policy:
            - {"exclude_tools": ["tool-task"]} - inherit all EXCEPT these
            - {"inherit_tools": ["tool-filesystem"]} - inherit ONLY these
        hook_inheritance: Optional hook filtering policy:
            - {"exclude_hooks": ["hooks-logging"]} - inherit all EXCEPT these
            - {"inherit_hooks": ["hooks-approval"]} - inherit ONLY these
        orchestrator_config: Optional orchestrator config to merge into session
            (e.g., {"min_delay_between_calls_ms": 500} for rate limiting)
        parent_messages: Optional list of messages from parent session to inject
            into child's context. Enables context inheritance where child can
            reference parent's conversation history.
        provider_override: Optional provider ID to use for this session
            (e.g., "anthropic", "openai"). Promotes the provider to priority 0.
        model_override: Optional model name to use with the provider
            (e.g., "claude-sonnet-4-5-20250514", "gpt-4o").

    Returns:
        Dict with "output" (response) and "session_id" (for multi-turn)

    Raises:
        ValueError: If agent not found or config invalid
    """
    # Get agent configuration
    if agent_name not in agent_configs:
        raise ValueError(f"Agent '{agent_name}' not found in configuration")

    agent_config = agent_configs[agent_name]

    # Merge parent config with agent overlay
    merged_config = merge_configs(parent_session.config, agent_config)

    # Apply tool inheritance filtering if specified
    if tool_inheritance and "tools" in merged_config:
        merged_config = _filter_tools(merged_config, tool_inheritance)

    # Apply hook inheritance filtering if specified
    if hook_inheritance and "hooks" in merged_config:
        merged_config = _filter_hooks(merged_config, hook_inheritance)

    # Apply provider override if specified (recipe-level provider selection)
    if provider_override or model_override:
        merged_config = _apply_provider_override(
            merged_config, provider_override, model_override
        )

    # Apply orchestrator config override if specified (recipe-level rate limiting)
    # Session reads orchestrator config from: config["session"]["orchestrator"]["config"]
    if orchestrator_config:
        if "session" not in merged_config:
            merged_config["session"] = {}
        if "orchestrator" not in merged_config["session"]:
            merged_config["session"]["orchestrator"] = {}
        if "config" not in merged_config["session"]["orchestrator"]:
            merged_config["session"]["orchestrator"]["config"] = {}
        # Merge orchestrator config (caller's config takes precedence)
        merged_config["session"]["orchestrator"]["config"].update(orchestrator_config)
        logger.debug(
            "Applied orchestrator config override to session.orchestrator.config: %s",
            orchestrator_config,
        )

    # Generate child session ID using W3C Trace Context span_id pattern
    # Use 16 hex chars (8 bytes) for fixed-length, filesystem-safe IDs
    if not sub_session_id:
        sub_session_id = generate_sub_session_id(
            agent_name=agent_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )

    # Create child session with parent_id and inherited UX systems (kernel mechanism)
    # NOTE: We intentionally do NOT share parent's loader here.
    # The loader caches modules with their config, so sharing would cause child sessions
    # to get the parent's cached orchestrator config instead of their own.
    # Each session needs its own loader to respect session-specific config (e.g., rate limiting).
    display_system = parent_session.coordinator.display_system
    child_session = AmplifierSession(
        config=merged_config,
        loader=None,  # Let child create its own loader to respect its config
        session_id=sub_session_id,
        parent_id=parent_session.session_id,  # Links to parent
        approval_system=parent_session.coordinator.approval_system,  # Inherit from parent
        display_system=display_system,  # Inherit from parent
    )

    # Notify display system we're entering a nested session (for indentation)
    if hasattr(display_system, "push_nesting"):
        display_system.push_nesting()

    # NOTE: Parent message injection moved to AFTER initialize() because
    # the context module is only mounted during initialize().

    # Register app-layer capabilities for child session BEFORE initialization
    # These must be mounted before initialize() because module loading needs the resolver
    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Module source resolver - inherit from parent to preserve BundleModuleResolver in bundle mode
    # CRITICAL: Must be mounted BEFORE initialize() so modules with source: directives can be resolved
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)
    else:
        # Fallback to fresh resolver if parent doesn't have one
        resolver = create_foundation_resolver()
        await child_session.coordinator.mount("module-source-resolver", resolver)

    # Share sys.path additions from parent BEFORE initialize()
    # This ensures bundle packages (like amplifier_bundle_python_dev) are importable
    # when child session loads modules that depend on them.
    #
    # Two sources of paths need to be shared:
    # 1. loader._added_paths - individual module paths added during loading
    # 2. bundle_package_paths capability - bundle src/ directories (e.g., python-dev)
    import sys

    paths_to_share: list[str] = []

    # Source 1: Module paths from parent loader
    if hasattr(parent_session, "loader") and parent_session.loader is not None:
        parent_added_paths = getattr(parent_session.loader, "_added_paths", [])
        paths_to_share.extend(parent_added_paths)

    # Source 2: Bundle package paths (src/ directories from bundles like python-dev)
    # These are registered as a capability during bundle preparation
    bundle_package_paths = parent_session.coordinator.get_capability(
        "bundle_package_paths"
    )
    if bundle_package_paths:
        paths_to_share.extend(bundle_package_paths)

    # Add all paths to sys.path
    if paths_to_share:
        for path in paths_to_share:
            if path not in sys.path:
                sys.path.insert(0, path)
        logger.debug(
            f"Shared {len(paths_to_share)} sys.path entries from parent to child session"
        )

    # Initialize child session (mounts modules per merged config)
    # Now the resolver is available for loading modules with source: directives
    await child_session.initialize()

    # Note: Parent context inheritance is now handled by tool-task formatting
    # the parent messages directly into the instruction text. This ensures the
    # child agent sees the context regardless of session/orchestrator behavior.
    # The parent_messages parameter is kept for potential future use.

    # Wire up cancellation propagation: parent cancellation should propagate to child
    # This enables graceful Ctrl+C handling for nested agent sessions
    parent_cancellation = parent_session.coordinator.cancellation
    child_cancellation = child_session.coordinator.cancellation
    parent_cancellation.register_child(child_cancellation)
    logger.debug(
        f"Registered child cancellation token for sub-session {sub_session_id}"
    )

    # Mention resolver - inherit from parent to preserve bundle_override context
    parent_mention_resolver = parent_session.coordinator.get_capability(
        "mention_resolver"
    )
    if parent_mention_resolver:
        child_session.coordinator.register_capability(
            "mention_resolver", parent_mention_resolver
        )
    else:
        # Fallback to fresh resolver if parent doesn't have one
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )

    # Mention deduplicator - inherit from parent to preserve session-wide deduplication state
    parent_deduplicator = parent_session.coordinator.get_capability(
        "mention_deduplicator"
    )
    if parent_deduplicator:
        child_session.coordinator.register_capability(
            "mention_deduplicator", parent_deduplicator
        )
    else:
        # Fallback to fresh deduplicator if parent doesn't have one
        child_session.coordinator.register_capability(
            "mention_deduplicator", ContentDeduplicator()
        )

    # Working directory - inherit from parent for consistent path resolution
    # This ensures child sessions use the same project directory as parent
    # (critical for server/web deployments where process cwd differs from user's project)
    parent_working_dir = parent_session.coordinator.get_capability(
        "session.working_dir"
    )
    if parent_working_dir:
        child_session.coordinator.register_capability(
            "session.working_dir", parent_working_dir
        )

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(f"Registered approval provider for child session {sub_session_id}")

    # Inject agent's system instruction
    # Check top-level instruction first (from agent .md file body), then nested system.instruction
    system_instruction = agent_config.get("instruction") or agent_config.get(
        "system", {}
    ).get("instruction")
    if system_instruction:
        context = child_session.coordinator.get("context")
        if context and hasattr(context, "add_message"):
            await context.add_message({"role": "system", "content": system_instruction})

    # Execute instruction in child session
    response = await child_session.execute(instruction)

    # Persist state for multi-turn resumption
    from datetime import UTC
    from datetime import datetime

    from .session_store import SessionStore

    context = child_session.coordinator.get("context")
    transcript = await context.get_messages() if context else []

    # Extract or generate trace_id for W3C Trace Context pattern
    # Root session ID is the trace_id, propagate it to all children
    parent_trace_id = getattr(parent_session, "trace_id", parent_session.session_id)

    metadata = {
        "session_id": sub_session_id,
        "parent_id": parent_session.session_id,
        "trace_id": parent_trace_id,  # W3C Trace Context: trace entire conversation
        "agent_name": agent_name,
        "created": datetime.now(UTC).isoformat(),
        "config": merged_config,
        "agent_overlay": agent_config,
        "turn_count": 1,
        "bundle_context": _extract_bundle_context(parent_session),
    }

    store = SessionStore()
    store.save(sub_session_id, transcript, metadata)
    logger.debug(f"Sub-session {sub_session_id} state persisted")

    # Unregister child cancellation token before cleanup
    parent_cancellation.unregister_child(child_cancellation)
    logger.debug(
        f"Unregistered child cancellation token for sub-session {sub_session_id}"
    )

    # Notify display system we're exiting the nested session (for indentation)
    if hasattr(display_system, "pop_nesting"):
        display_system.pop_nesting()

    # Cleanup child session
    await child_session.cleanup()

    # Return response and session ID for potential multi-turn
    return {"output": response, "session_id": sub_session_id}


async def resume_sub_session(sub_session_id: str, instruction: str) -> dict:
    """Resume existing sub-session for multi-turn engagement.

    Loads previously saved sub-session state, recreates the session with
    full context, executes new instruction, and saves updated state.

    Args:
        sub_session_id: ID of existing sub-session to resume
        instruction: Follow-up instruction to execute

    Returns:
        Dict with "output" (response) and "session_id" (same ID)

    Raises:
        FileNotFoundError: If session not found in storage
        RuntimeError: If session metadata corrupted or incomplete
        ValueError: If session_id is invalid
    """
    from datetime import UTC
    from datetime import datetime

    from .session_store import SessionStore

    # Load session state from storage
    store = SessionStore()

    if not store.exists(sub_session_id):
        raise FileNotFoundError(
            f"Sub-session '{sub_session_id}' not found. Session may have expired or was never created."
        )

    try:
        transcript, metadata = store.load(sub_session_id)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load sub-session '{sub_session_id}': {str(e)}"
        ) from e

    # Extract reconstruction data
    merged_config = metadata.get("config")
    if not merged_config:
        raise RuntimeError(
            f"Corrupted session metadata for '{sub_session_id}'. Cannot reconstruct session without config."
        )

    parent_id = metadata.get("parent_id")
    agent_name = metadata.get("agent_name", "unknown")
    trace_id = metadata.get("trace_id")

    # Sub-session resume creates fresh UX systems. Parent UX context (approval history,
    # display state) is not preserved across resume. This is acceptable because:
    # 1. Sub-sessions are typically short-lived agent delegations
    # 2. Serializing full UX state would add significant complexity
    # 3. The parent session may no longer be running when sub-session resumes
    # 4. Approval decisions are contextual to the current execution state
    from amplifier_app_cli.ui import CLIApprovalSystem
    from amplifier_app_cli.ui import CLIDisplaySystem

    logger.debug(
        "Resuming sub-session %s (agent=%s, parent=%s, trace=%s). "
        "UX context (approval history, display state) not preserved - using fresh UX systems.",
        sub_session_id,
        agent_name,
        parent_id,
        trace_id,
    )

    approval_system = CLIApprovalSystem()
    display_system = CLIDisplaySystem()

    child_session = AmplifierSession(
        config=merged_config,
        loader=None,  # Use default loader
        session_id=sub_session_id,  # REUSE same ID
        parent_id=parent_id,
        approval_system=approval_system,
        display_system=display_system,
    )

    # Register app-layer capabilities for resumed child session BEFORE initialization
    # Must be mounted before initialize() so modules with source: directives can be resolved
    from pathlib import Path

    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Extract bundle context from metadata (saved during spawn_sub_session)
    bundle_context = metadata.get("bundle_context")

    # Module source resolver - restore from bundle context if available
    # CRITICAL: Must be mounted BEFORE initialize() so modules with source: directives can be resolved
    if bundle_context and bundle_context.get("module_paths"):
        # Restore BundleModuleResolver with saved module paths
        from amplifier_foundation.bundle import BundleModuleResolver

        module_paths = {k: Path(v) for k, v in bundle_context["module_paths"].items()}
        resolver = BundleModuleResolver(module_paths=module_paths)
        logger.debug(
            f"Restored BundleModuleResolver with {len(module_paths)} module paths"
        )
    else:
        # Fallback to FoundationSettingsResolver
        resolver = create_foundation_resolver()
    await child_session.coordinator.mount("module-source-resolver", resolver)

    # Initialize session (mounts modules per config)
    # Now the resolver is available for loading modules with source: directives
    await child_session.initialize()

    # Mention resolver - restore bundle mappings if available
    if bundle_context and bundle_context.get("mention_mappings"):
        # Restore AppMentionResolver with saved bundle mappings for @namespace:path resolution
        mention_mappings = {
            k: Path(v) for k, v in bundle_context["mention_mappings"].items()
        }
        child_session.coordinator.register_capability(
            "mention_resolver",
            AppMentionResolver(bundle_mappings=mention_mappings),
        )
        logger.debug(
            f"Restored AppMentionResolver with {len(mention_mappings)} bundle mappings"
        )
    else:
        # Fallback to fresh resolver without bundle mappings
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )

    # Mention deduplicator - create fresh (deduplication state doesn't persist across resumes)
    child_session.coordinator.register_capability(
        "mention_deduplicator", ContentDeduplicator()
    )

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(
            f"Registered approval provider for resumed child session {sub_session_id}"
        )

    # Emit session:resume event for observability
    hooks = child_session.coordinator.get("hooks")
    if hooks:
        await hooks.emit(
            "session:resume",
            {
                "session_id": sub_session_id,
                "parent_id": parent_id,
                "agent_name": agent_name,
                "turn_count": len(transcript) + 1,
            },
        )

    # Restore transcript to context
    context = child_session.coordinator.get("context")
    if context and hasattr(context, "add_message"):
        for message in transcript:
            await context.add_message(message)
    else:
        logger.warning(
            f"Context module does not support add_message() - transcript not restored for session {sub_session_id}"
        )

    # Execute new instruction with full context
    response = await child_session.execute(instruction)

    # Update state for next resumption
    updated_transcript = await context.get_messages() if context else []
    metadata["turn_count"] = len(updated_transcript)
    metadata["last_updated"] = datetime.now(UTC).isoformat()

    store.save(sub_session_id, updated_transcript, metadata)
    logger.debug(
        f"Sub-session {sub_session_id} state updated (turn {metadata['turn_count']})"
    )

    # Cleanup child session
    await child_session.cleanup()

    # Return response and same session ID
    return {"output": response, "session_id": sub_session_id}
