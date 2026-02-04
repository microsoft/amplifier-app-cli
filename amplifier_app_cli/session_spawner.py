"""Session spawning for agent delegation.

Implements sub-session creation with configuration inheritance and overlays.
Uses spawn_bundle() as the core primitive for session lifecycle management.
"""

import logging
from pathlib import Path

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


def _filter_tools(
    config: dict,
    tool_inheritance: dict[str, list[str]],
    agent_explicit_tools: list[str] | None = None,
) -> dict:
    """Filter tools in config based on tool inheritance policy.

    Args:
        config: Session config containing "tools" list
        tool_inheritance: Policy dict with either:
            - "exclude_tools": list of tool module names to exclude
            - "inherit_tools": list of tool module names to include (allowlist)
        agent_explicit_tools: Optional list of tool module names explicitly declared
            by the agent. These are preserved even if they would be excluded.
            Formula: final_tools = (inherited - excluded) + explicit

    Returns:
        New config dict with filtered tools list
    """
    tools = config.get("tools", [])
    if not tools:
        return config

    exclude_tools = tool_inheritance.get("exclude_tools", [])
    inherit_tools = tool_inheritance.get("inherit_tools")

    # Get explicit tool module names (these are always preserved)
    explicit_modules = set(agent_explicit_tools or [])

    if inherit_tools is not None:
        # Allowlist mode: only include specified tools OR explicit
        filtered_tools = [
            t
            for t in tools
            if t.get("module") in inherit_tools or t.get("module") in explicit_modules
        ]
    elif exclude_tools:
        # Blocklist mode: exclude specified tools UNLESS explicit
        filtered_tools = [
            t
            for t in tools
            if t.get("module") not in exclude_tools
            or t.get("module") in explicit_modules
        ]
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


def _filter_hooks(
    config: dict,
    hook_inheritance: dict[str, list[str]],
    agent_explicit_hooks: list[str] | None = None,
) -> dict:
    """Filter hooks in config based on hook inheritance policy.

    Args:
        config: Session config containing "hooks" list
        hook_inheritance: Policy dict with either:
            - "exclude_hooks": list of hook module names to exclude
            - "inherit_hooks": list of hook module names to include (allowlist)
        agent_explicit_hooks: Optional list of hook module names explicitly declared
            by the agent. These are preserved even if they would be excluded.
            Formula: final_hooks = (inherited - excluded) + explicit

    Returns:
        New config dict with filtered hooks list
    """
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    exclude_hooks = hook_inheritance.get("exclude_hooks", [])
    inherit_hooks = hook_inheritance.get("inherit_hooks")

    # Get explicit hook module names (these are always preserved)
    explicit_modules = set(agent_explicit_hooks or [])

    if inherit_hooks is not None:
        # Allowlist mode: only include specified hooks OR explicit
        filtered_hooks = [
            h
            for h in hooks
            if h.get("module") in inherit_hooks or h.get("module") in explicit_modules
        ]
    elif exclude_hooks:
        # Blocklist mode: exclude specified hooks UNLESS explicit
        filtered_hooks = [
            h
            for h in hooks
            if h.get("module") not in exclude_hooks
            or h.get("module") in explicit_modules
        ]
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
    provider_preferences: list | None = None,
    self_delegation_depth: int = 0,
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
            LEGACY: Use provider_preferences instead for ordered fallback chains.
        model_override: Optional model name to use with the provider
            (e.g., "claude-sonnet-4-5-20250514", "gpt-4o").
            LEGACY: Use provider_preferences instead for ordered fallback chains.
        provider_preferences: Optional ordered list of ProviderPreference objects.
            Each preference has provider and model. System tries each in order
            until finding an available provider. Model names support glob patterns.
            Takes precedence over provider_override/model_override if both specified.
        self_delegation_depth: Current depth in the self-delegation chain (default: 0).
            Incremented for self-delegation, reset to 0 for named agents.
            Used to prevent infinite recursion.

    Returns:
        Dict with "output" (response) and "session_id" (for multi-turn)

    Raises:
        ValueError: If agent not found or config invalid
    """
    # Get agent configuration
    # Special handling for "self" - spawn with parent's config (no agent overlay)
    if agent_name == "self":
        agent_config = {}  # Empty overlay = inherit parent config as-is
        logger.debug("Self-delegation: using parent config without agent overlay")
    elif agent_name not in agent_configs:
        raise ValueError(f"Agent '{agent_name}' not found in configuration")
    else:
        agent_config = agent_configs[agent_name]

    # Merge parent config with agent overlay
    merged_config = merge_configs(parent_session.config, agent_config)

    # Apply tool inheritance filtering if specified
    if tool_inheritance and "tools" in merged_config:
        # Get agent's explicit tool modules to preserve them
        agent_tool_modules = [t.get("module") for t in agent_config.get("tools", [])]
        merged_config = _filter_tools(
            merged_config, tool_inheritance, agent_tool_modules
        )

    # Apply hook inheritance filtering if specified
    if hook_inheritance and "hooks" in merged_config:
        # Get agent's explicit hook modules to preserve them
        agent_hook_modules = [h.get("module") for h in agent_config.get("hooks", [])]
        merged_config = _filter_hooks(
            merged_config, hook_inheritance, agent_hook_modules
        )

    # Apply provider preferences if specified (ordered fallback chain)
    # Takes precedence over legacy provider_override/model_override
    if provider_preferences:
        from amplifier_foundation import apply_provider_preferences

        merged_config = apply_provider_preferences(merged_config, provider_preferences)
    elif provider_override or model_override:
        # Legacy: Apply single provider/model override
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
    assert sub_session_id is not None  # Always generated above if not provided

    # =========================================================================
    # PHASE 2: Create Inline Bundle from Merged Config
    # =========================================================================
    # spawn_bundle() requires a Bundle object. We create an inline bundle from
    # the merged config dict. This is equivalent to calling bundle.prepare()
    # with the config directly.

    from amplifier_foundation.bundle import Bundle

    inline_bundle = Bundle(
        name=agent_name,
        version="1.0.0",
        mount_plan=merged_config,
        agents={},  # Agents are resolved at CLI layer, not bundle layer
        context_files=[],
    )

    # =========================================================================
    # PHASE 3: Display System Nesting
    # =========================================================================

    display_system = parent_session.coordinator.display_system
    if hasattr(display_system, "push_nesting"):
        display_system.push_nesting()

    # =========================================================================
    # PHASE 4: Define CLI-Specific Setup Hook
    # =========================================================================
    # This hook runs after session init but before execution, allowing us to
    # register CLI-specific capabilities that spawn_bundle() doesn't know about.

    async def cli_pre_execute_hook(child_session: AmplifierSession) -> None:
        """Register CLI-specific capabilities on child session."""
        # Self-delegation depth tracking (for recursion limits)
        child_session.coordinator.register_capability(
            "self_delegation_depth", self_delegation_depth
        )

        # Override session.spawn with CLI's version (has more parameters)
        # spawn_bundle() registers its own version, but CLI needs agent resolution
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
            provider_override: str | None = None,
            model_override: str | None = None,
            provider_preferences: list | None = None,
            self_delegation_depth: int = 0,
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
                provider_override=provider_override,
                model_override=model_override,
                provider_preferences=provider_preferences,
                self_delegation_depth=self_delegation_depth,
            )

        async def child_resume_capability(
            sub_session_id: str, instruction: str
        ) -> dict:
            return await resume_sub_session(
                sub_session_id=sub_session_id,
                instruction=instruction,
            )

        # Override spawn_bundle's session.spawn with CLI's version
        child_session.coordinator.register_capability(
            "session.spawn", child_spawn_capability
        )
        child_session.coordinator.register_capability(
            "session.resume", child_resume_capability
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
                f"Registered approval provider for child session {sub_session_id}"
            )

    # =========================================================================
    # PHASE 5: Prepare CLI-Specific Metadata
    # =========================================================================
    # spawn_bundle() handles basic persistence, but CLI needs additional metadata
    # for session resumption, trace context, and working directory.

    parent_trace_id = getattr(parent_session, "trace_id", parent_session.session_id)

    # Extract child_span from sub_session_id for short_id resolution
    child_span: str | None = None
    if sub_session_id and "_" in sub_session_id and "-" in sub_session_id:
        base = sub_session_id.rsplit("_", 1)[0]  # Remove agent name
        child_span = base.rsplit("-", 1)[-1]  # Get child_span (16 hex chars)

    metadata_extra = {
        "trace_id": parent_trace_id,  # W3C Trace Context
        "agent_name": agent_name,
        "child_span": child_span,
        "config": merged_config,
        "agent_overlay": agent_config,
        "bundle_context": _extract_bundle_context(parent_session),
        "self_delegation_depth": self_delegation_depth,
        "working_dir": str(Path.cwd().resolve()),
    }

    # =========================================================================
    # PHASE 6: Extract System Instruction
    # =========================================================================

    system_instruction = agent_config.get("instruction") or agent_config.get(
        "system", {}
    ).get("instruction")

    # =========================================================================
    # PHASE 7: Spawn Using Foundation Primitive
    # =========================================================================

    from amplifier_foundation.spawn import spawn_bundle

    from .session_store import SessionStore

    try:
        result = await spawn_bundle(
            bundle=inline_bundle,
            instruction=instruction,
            parent_session=parent_session,
            # Inheritance controls - already filtered in merged_config
            inherit_providers=False,
            inherit_tools=False,
            inherit_hooks=False,
            # Session identity
            session_id=sub_session_id,
            session_name=agent_name,
            # Persistence
            session_storage=SessionStore(),
            # Additional setup
            system_instruction=system_instruction,
            pre_execute_hook=cli_pre_execute_hook,
            metadata_extra=metadata_extra,
        )
    finally:
        # Always restore display nesting, even on error
        if hasattr(display_system, "pop_nesting"):
            display_system.pop_nesting()

    # Return response and session ID for potential multi-turn
    return {"output": result.output, "session_id": result.session_id}


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

        from amplifier_app_cli.lib.bundle_loader import AppModuleResolver

        module_paths = {k: Path(v) for k, v in bundle_context["module_paths"].items()}
        bundle_resolver = BundleModuleResolver(module_paths=module_paths)
        logger.debug(
            f"Restored BundleModuleResolver with {len(module_paths)} module paths"
        )

        # Wrap with AppModuleResolver to provide fallback to settings resolver
        # This is critical for modules (like providers) that may not be in the saved
        # module_paths but are available via user settings/installed providers.
        # Mirrors the wrapping done in session_runner.py and tool.py
        fallback_resolver = create_foundation_resolver()
        resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=fallback_resolver,
        )
        logger.debug("Wrapped with AppModuleResolver for settings fallback")
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

    # Self-delegation depth - restore from metadata for recursion limit tracking
    self_delegation_depth = metadata.get("self_delegation_depth", 0)
    child_session.coordinator.register_capability(
        "self_delegation_depth", self_delegation_depth
    )

    # Working directory - restore from metadata for consistent path resolution
    working_dir = metadata.get("working_dir")
    if working_dir:
        child_session.coordinator.register_capability(
            "session.working_dir", working_dir
        )

    # Register session spawning capabilities on resumed session
    # This enables nested agent delegation from resumed sessions
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
        provider_override: str | None = None,
        model_override: str | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
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
            provider_override=provider_override,
            model_override=model_override,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
        )

    async def child_resume_capability(sub_session_id: str, instruction: str) -> dict:
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
        )

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )
    child_session.coordinator.register_capability(
        "session.resume", child_resume_capability
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
