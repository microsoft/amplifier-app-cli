"""Agent configuration management for Amplifier.

Loads and manages agent configuration overlays (partial mount plans)
from TOML files or profile definitions.
"""

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomli

logger = logging.getLogger(__name__)


def load_agent_configs_from_directory(directory: str | Path) -> dict[str, dict]:
    """
    Load agent configurations from TOML files in a directory.

    Args:
        directory: Path to directory containing agent TOML files

    Returns:
        Dict of {agent_name: config_overlay}
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        logger.warning(f"Agent config directory does not exist: {directory}")
        return {}

    agents = {}
    for toml_file in dir_path.glob("*.toml"):
        try:
            with open(toml_file, "rb") as f:
                config = tomli.load(f)

            # Get name from meta section or filename
            name = config.get("meta", {}).get("name", toml_file.stem)
            agents[name] = config

            logger.debug(f"Loaded agent config: {name} from {toml_file.name}")

        except Exception as e:
            logger.warning(f"Failed to load agent config from {toml_file}: {e}")

    logger.info(f"Loaded {len(agents)} agent configs from {directory}")
    return agents


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
    # Must have meta section with name
    if "meta" not in config:
        raise ValueError("Agent config must have 'meta' section")

    if "name" not in config.get("meta", {}):
        raise ValueError("Agent config 'meta' section must have 'name'")

    # System instruction is optional but recommended
    if "system" in config and "instruction" not in config.get("system", {}):
        logger.warning("Agent has 'system' section but no 'instruction'")

    return True
