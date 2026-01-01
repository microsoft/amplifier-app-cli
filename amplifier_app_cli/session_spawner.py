"""Session spawning for agent delegation.

Implements sub-session creation with configuration inheritance and overlays.
"""

import logging

from amplifier_core import AmplifierSession
from amplifier_foundation import generate_sub_session_id

from .agent_config import merge_configs

logger = logging.getLogger(__name__)


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
        filtered_tools = [
            t for t in tools
            if t.get("module") in inherit_tools
        ]
    elif exclude_tools:
        # Blocklist mode: exclude specified tools
        filtered_tools = [
            t for t in tools
            if t.get("module") not in exclude_tools
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


async def spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
    tool_inheritance: dict[str, list[str]] | None = None,
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

    # Generate child session ID using W3C Trace Context span_id pattern
    # Use 16 hex chars (8 bytes) for fixed-length, filesystem-safe IDs
    if not sub_session_id:
        sub_session_id = generate_sub_session_id(
            agent_name=agent_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )

    # Create child session with parent_id and inherited UX systems (kernel mechanism)
    child_session = AmplifierSession(
        config=merged_config,
        loader=parent_session.loader,
        session_id=sub_session_id,
        parent_id=parent_session.session_id,  # Links to parent
        approval_system=parent_session.coordinator.approval_system,  # Inherit from parent
        display_system=parent_session.coordinator.display_system,  # Inherit from parent
    )

    # Initialize child session (mounts modules per merged config)
    await child_session.initialize()

    # Register app-layer capabilities for child session
    # Inherit from parent session where available to preserve bundle context and deduplication state
    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Module source resolver - inherit from parent to preserve BundleModuleResolver in bundle mode
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)
    else:
        # Fallback to fresh resolver if parent doesn't have one (profile mode)
        resolver = create_foundation_resolver()
        await child_session.coordinator.mount("module-source-resolver", resolver)

    # Mention resolver - inherit from parent to preserve bundle_override context
    parent_mention_resolver = parent_session.coordinator.get_capability("mention_resolver")
    if parent_mention_resolver:
        child_session.coordinator.register_capability("mention_resolver", parent_mention_resolver)
    else:
        # Fallback to fresh resolver if parent doesn't have one
        child_session.coordinator.register_capability("mention_resolver", AppMentionResolver(enable_collections=True))

    # Mention deduplicator - inherit from parent to preserve session-wide deduplication state
    parent_deduplicator = parent_session.coordinator.get_capability("mention_deduplicator")
    if parent_deduplicator:
        child_session.coordinator.register_capability("mention_deduplicator", parent_deduplicator)
    else:
        # Fallback to fresh deduplicator if parent doesn't have one
        child_session.coordinator.register_capability("mention_deduplicator", ContentDeduplicator())

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability("approval.register_provider")
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(f"Registered approval provider for child session {sub_session_id}")

    # Inject agent's system instruction
    system_instruction = agent_config.get("system", {}).get("instruction")
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
    }

    store = SessionStore()
    store.save(sub_session_id, transcript, metadata)
    logger.debug(f"Sub-session {sub_session_id} state persisted")

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
        raise RuntimeError(f"Failed to load sub-session '{sub_session_id}': {str(e)}") from e

    # Extract reconstruction data
    merged_config = metadata.get("config")
    if not merged_config:
        raise RuntimeError(
            f"Corrupted session metadata for '{sub_session_id}'. Cannot reconstruct session without config."
        )

    parent_id = metadata.get("parent_id")
    agent_name = metadata.get("agent_name", "unknown")

    # Recreate child session with same ID and loaded config
    # Note: We don't have parent session ref here, so create fresh UX systems
    from amplifier_app_cli.ui import CLIApprovalSystem
    from amplifier_app_cli.ui import CLIDisplaySystem

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

    # Initialize session (mounts modules per config)
    await child_session.initialize()

    # Register app-layer capabilities for resumed child session
    # Note: Resumed sessions create fresh instances since parent session is not available.
    # Bundle context (including BundleModuleResolver) would need to be serialized to metadata
    # to preserve bundle mode for resumed sessions - using FoundationSettingsResolver as fallback.
    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Module source resolver - uses FoundationSettingsResolver for resumed sessions
    # (BundleModuleResolver inheritance only works for live sub-session spawning)
    resolver = create_foundation_resolver()
    await child_session.coordinator.mount("module-source-resolver", resolver)

    # Mention resolver - create fresh (no parent available for resumed sessions)
    child_session.coordinator.register_capability("mention_resolver", AppMentionResolver(enable_collections=True))

    # Mention deduplicator - create fresh (deduplication state doesn't persist across resumes)
    child_session.coordinator.register_capability("mention_deduplicator", ContentDeduplicator())

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability("approval.register_provider")
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(f"Registered approval provider for resumed child session {sub_session_id}")

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
    logger.debug(f"Sub-session {sub_session_id} state updated (turn {metadata['turn_count']})")

    # Cleanup child session
    await child_session.cleanup()

    # Return response and same session ID
    return {"output": response, "session_id": sub_session_id}
