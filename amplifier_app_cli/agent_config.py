"""Agent configuration utilities.

Utilities for agent overlay merging and validation.
Agents are loaded via profiles library (amplifier-profiles).
"""

import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


def merge_configs(parent: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge parent config with agent overlay.

    Rules:
    - Overlay values replace parent values (override)
    - Omitted keys inherited from parent
    - Dicts merged recursively
    - Arrays replaced entirely (not appended)

    Args:
        parent: Parent session's complete mount plan
        overlay: Agent's partial mount plan (config overlay)

    Returns:
        Merged mount plan for child session
    """
    result = deepcopy(parent)

    for key, value in overlay.items():
        if key not in result:
            # New key from overlay
            result[key] = deepcopy(value)

        elif isinstance(value, dict) and isinstance(result[key], dict):
            # Both dicts → recursive merge
            result[key] = merge_configs(result[key], value)

        else:
            # Scalar or array → overlay replaces parent
            result[key] = deepcopy(value)

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
        raise ValueError("Agent config must have 'name' (either at top level or in 'meta' section)")

    # System instruction is optional but recommended
    if "system" in config and "instruction" not in config.get("system", {}):
        logger.warning("Agent has 'system' section but no 'instruction'")

    return True
