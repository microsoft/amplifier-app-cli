"""Agent configuration utilities.

Utilities for agent overlay merging and validation.
Agents are loaded via bundles (amplifier-foundation).
"""

import logging
from typing import Any

from .lib.merge_utils import merge_profile_dicts

logger = logging.getLogger(__name__)


def apply_spawn_tool_policy(parent: dict[str, Any]) -> dict[str, Any]:
    """
    Apply spawn tool inheritance policy to parent config before merging.

    The `spawn` section in a bundle controls what tools spawned agents receive:
    - spawn.exclude_tools: list of tool module IDs to exclude (inherit all except these)
    - spawn.tools: explicit list of tools for agents (replaces inheritance)

    If spawn.tools is specified, it completely replaces tool inheritance.
    If spawn.exclude_tools is specified, those tools are filtered out.
    If neither is specified, agents inherit all parent tools (default/backward-compatible).

    Args:
        parent: Parent session's complete mount plan

    Returns:
        Parent config with tools filtered according to spawn policy
    """
    spawn_config = parent.get("spawn", {})
    if not spawn_config:
        # No spawn policy - return parent unchanged (backward compatible)
        return parent

    filtered_parent = parent.copy()
    parent_tools = parent.get("tools", [])

    # Check for explicit spawn.tools (replaces inheritance entirely)
    if "tools" in spawn_config:
        spawn_tools = spawn_config["tools"]
        if isinstance(spawn_tools, list):
            # Use explicit tool list for agents
            filtered_parent["tools"] = spawn_tools
            logger.debug(
                f"Spawn policy: using explicit tools list ({len(spawn_tools)} tools)"
            )
        return filtered_parent

    # Check for spawn.exclude_tools (inherit all except these)
    exclude_tools = spawn_config.get("exclude_tools", [])
    if exclude_tools and isinstance(exclude_tools, list):
        # Filter out excluded tools
        filtered_tools = [
            tool for tool in parent_tools if tool.get("module") not in exclude_tools
        ]
        filtered_parent["tools"] = filtered_tools
        logger.debug(
            f"Spawn policy: excluded {len(parent_tools) - len(filtered_tools)} tools"
        )

    return filtered_parent


def merge_configs(parent: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge parent config with agent overlay.

    Uses merge logic for bundle agent inheritance:
    - Module lists (providers, tools, hooks) merge by module ID
    - Config dicts merge recursively (child keys override parent keys)
    - Sources inherit (agent doesn't need to repeat parent's source)
    - Scalar values override (child replaces parent)

    Special handling for spawn tool policy:
    - Parent's `spawn.exclude_tools` filters tools before inheritance
    - Parent's `spawn.tools` replaces tool inheritance entirely
    - Default (no spawn section): agents inherit all parent tools

    Special handling for agents field (sub-agent access control):
    - Agent's `agents` field is a Smart Single Value ("all", "none", or list of names)
    - Parent's `agents` field is already resolved to a dict of agent configs
    - This function filters parent's agents dict based on agent's Smart Single Value

    Args:
        parent: Parent session's complete mount plan
        overlay: Agent's partial mount plan (config overlay)

    Returns:
        Merged mount plan for child session

    See Also:
        merge_profile_dicts - The underlying merge implementation
    """
    # Apply spawn tool policy to parent before merging
    filtered_parent = apply_spawn_tool_policy(parent)

    # Extract agent filter before merge (prevents overwriting parent's agents dict)
    overlay_copy = overlay.copy()
    agent_filter = overlay_copy.pop("agents", None)

    # Standard merge (parent's agents dict preserved since we removed it from overlay)
    result = merge_profile_dicts(filtered_parent, overlay_copy)

    # Apply agent filtering (Smart Single Value → filtered dict)
    # Note: "all" and None both mean "inherit parent's agents unchanged" (already in result)
    if agent_filter == "none":
        # Disable all sub-agent delegation
        result["agents"] = {}
    elif isinstance(agent_filter, list):
        # Filter to only specified agent names
        parent_agents = parent.get("agents", {})
        result["agents"] = {k: v for k, v in parent_agents.items() if k in agent_filter}

    return result


def validate_agent_config(config: dict[str, Any]) -> bool:
    """
    Validate agent configuration structure.

    Args:
        config: Agent configuration to validate

    Returns:
        True if valid

    Raises:
        ValueError: If configuration is invalid
    """
    # Must have name either at top level or in meta section
    has_top_level_name = "name" in config
    has_meta_name = "meta" in config and "name" in config.get("meta", {})

    if not has_top_level_name and not has_meta_name:
        raise ValueError(
            "Agent config must have 'name' (either at top level or in 'meta' section)"
        )

    # System instruction is optional but recommended
    if "system" in config and "instruction" not in config.get("system", {}):
        logger.warning("Agent has 'system' section but no 'instruction'")

    return True
