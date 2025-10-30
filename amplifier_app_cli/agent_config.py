"""Agent configuration management for Amplifier.

Loads and manages agent configuration overlays (partial mount plans)
from Markdown files with YAML frontmatter or profile definitions.
"""

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def load_agent_configs_from_directory(directory: str | Path) -> dict[str, dict]:
    """
    Load agent configurations from Markdown files with YAML frontmatter.

    Args:
        directory: Path to directory containing agent Markdown files

    Returns:
        Dict of {agent_name: config_overlay}
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        logger.warning(f"Agent config directory does not exist: {directory}")
        return {}

    agents = {}
    for md_file in dir_path.glob("*.md"):
        try:
            # Skip README files (they're documentation, not agent configs)
            if md_file.name.upper() == "README.MD":
                logger.debug(f"Skipping README file: {md_file}")
                continue

            # Read file content
            content = md_file.read_text()

            # Parse YAML frontmatter
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
            if not match:
                logger.warning(f"No YAML frontmatter found in {md_file}")
                continue

            frontmatter_yaml = match.group(1)
            markdown_body = match.group(2).strip()

            # Load YAML configuration
            config = yaml.safe_load(frontmatter_yaml)

            # If markdown body exists, use it as system instruction
            if markdown_body:
                if "system" not in config:
                    config["system"] = {}
                config["system"]["instruction"] = markdown_body

            # Get name from meta section, top level, or filename
            name = None
            if "meta" in config and "name" in config["meta"]:
                name = config["meta"]["name"]
            elif "name" in config:
                # Support name at top level for convenience
                name = config["name"]
                # Move it to meta section for consistency
                if "meta" not in config:
                    config["meta"] = {}
                config["meta"]["name"] = name
                del config["name"]
            else:
                # Use filename stem as fallback
                name = md_file.stem
                if "meta" not in config:
                    config["meta"] = {}
                config["meta"]["name"] = name

            agents[name] = config

            logger.debug(f"Loaded agent config: {name} from {md_file.name}")

        except Exception as e:
            logger.warning(f"Failed to load agent config from {md_file}: {e}")

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
    # Must have name either at top level or in meta section
    has_top_level_name = "name" in config
    has_meta_name = "meta" in config and "name" in config.get("meta", {})

    if not has_top_level_name and not has_meta_name:
        raise ValueError("Agent config must have 'name' (either at top level or in 'meta' section)")

    # System instruction is optional but recommended
    if "system" in config and "instruction" not in config.get("system", {}):
        logger.warning("Agent has 'system' section but no 'instruction'")

    return True
