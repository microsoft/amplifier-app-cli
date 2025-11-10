"""Agent configuration utilities.

Utilities for agent overlay merging and validation.
Agents are loaded via profiles library (amplifier-profiles).
"""

import logging
from typing import Any

from amplifier_profiles.merger import merge_profile_dicts

logger = logging.getLogger(__name__)


def merge_configs(parent: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge parent config with agent overlay.

    Uses the same merge logic as profile inheritance:
    - Module lists (providers, tools, hooks) merge by module ID
    - Config dicts merge recursively (child keys override parent keys)
    - Sources inherit (agent doesn't need to repeat parent's source)
    - Scalar values override (child replaces parent)

    This ensures consistent merge behavior throughout the system.

    Args:
        parent: Parent session's complete mount plan
        overlay: Agent's partial mount plan (config overlay)

    Returns:
        Merged mount plan for child session

    See Also:
        amplifier_profiles.merger.merge_profile_dicts - The underlying implementation
        amplifier-profiles/docs/AGENT_AUTHORING.md - Merge behavior documentation
    """
    return merge_profile_dicts(parent, overlay)


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
        raise ValueError("Agent config must have 'name' (either at top level or in 'meta' section)")

    # System instruction is optional but recommended
    if "system" in config and "instruction" not in config.get("system", {}):
        logger.warning("Agent has 'system' section but no 'instruction'")

    return True
