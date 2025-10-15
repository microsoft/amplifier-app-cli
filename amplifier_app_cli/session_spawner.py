"""Session spawning for agent delegation.

Implements sub-session creation with configuration inheritance and overlays.
"""

import logging
import uuid

from amplifier_core import AmplifierSession

from .agent_config import merge_configs

logger = logging.getLogger(__name__)


async def spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
) -> dict:
    """
    Spawn sub-session with agent configuration overlay.

    Args:
        agent_name: Name of agent from configuration
        instruction: Task for agent to execute
        parent_session: Parent session for inheritance
        agent_configs: Dict of agent configurations
        sub_session_id: Optional explicit ID (generates if None)

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

    # Generate child session ID
    if not sub_session_id:
        sub_session_id = f"{parent_session.session_id}-{agent_name}-{uuid.uuid4().hex[:8]}"

    # Create child session with parent_id (kernel mechanism)
    child_session = AmplifierSession(
        config=merged_config,
        loader=parent_session.loader,
        session_id=sub_session_id,
        parent_id=parent_session.session_id,  # Links to parent
    )

    # Initialize child session (mounts modules per merged config)
    await child_session.initialize()

    # Inject agent's system instruction
    system_instruction = agent_config.get("system", {}).get("instruction")
    if system_instruction:
        context = child_session.coordinator.get("context")
        if context and hasattr(context, "add_message"):
            await context.add_message({"role": "system", "content": system_instruction})

    # Execute instruction in child session
    response = await child_session.execute(instruction)

    # Cleanup child session
    await child_session.cleanup()

    # Return response and session ID for potential multi-turn
    return {"output": response, "session_id": sub_session_id}


async def resume_sub_session(sub_session_id: str, instruction: str) -> dict:
    """
    Resume existing sub-session for multi-turn engagement.

    Args:
        sub_session_id: ID of existing sub-session to resume
        instruction: Follow-up instruction

    Returns:
        Dict with "output" and "session_id"

    Raises:
        FileNotFoundError: If session not found
    """
    # Load session from storage
    from .session_store import SessionStore

    store = SessionStore()
    if not store.exists(sub_session_id):
        raise FileNotFoundError(f"Sub-session {sub_session_id} not found")

    transcript, metadata = store.load(sub_session_id)

    # Recreate session with same config
    # Note: This is simplified - full implementation would need to
    # reconstruct the full mount plan from metadata
    raise NotImplementedError("Multi-turn sub-session resumption not yet implemented")
