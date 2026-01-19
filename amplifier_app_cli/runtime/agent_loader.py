"""Agent loader for extracting metadata from agent .md files.

Loads agent definitions and extracts the meta section (name, description)
for display in the task tool and agent listings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LoadedAgent:
    """Agent metadata loaded from an agent .md file.

    Attributes:
        name: Agent name from meta.name
        description: Agent description from meta.description
        meta: Full meta dict from frontmatter
    """

    name: str
    description: str
    meta: dict[str, Any]

    def to_mount_plan_fragment(self) -> dict[str, Any]:
        """Return the agent config for mount plan.

        Returns a dict with name, description, and any other meta fields.
        This is merged into the agents section of the mount plan.
        """
        result = {"name": self.name, "description": self.description}
        # Include any additional meta fields (tools, etc.) if present
        for key, value in self.meta.items():
            if key not in ("name", "description"):
                result[key] = value
        return result


class AgentLoader:
    """Loads agent metadata from agent .md files.

    Parses the YAML frontmatter of agent files to extract the meta section,
    which contains name and description used for delegation decisions and
    agent listings.
    """

    def load_agent_from_path(self, path: Path, name: str) -> LoadedAgent:
        """Load agent metadata from a specific file path.

        Args:
            path: Path to the agent .md file
            name: Agent name (used as fallback if not in meta)

        Returns:
            LoadedAgent with metadata extracted from frontmatter

        Raises:
            FileNotFoundError: If the agent file doesn't exist
            ValueError: If the file has no valid meta section
        """
        if not path.exists():
            raise FileNotFoundError(f"Agent file not found: {path}")

        text = path.read_text(encoding="utf-8")

        # Parse frontmatter using foundation's parser if available,
        # otherwise use a simple inline parser
        try:
            from amplifier_foundation.io.frontmatter import parse_frontmatter

            frontmatter, _body = parse_frontmatter(text)
        except ImportError:
            # Fallback: simple frontmatter parsing
            frontmatter = self._parse_frontmatter_simple(text)

        # Extract meta section (agents use meta: not bundle:)
        meta = frontmatter.get("meta", {})
        if not meta:
            # Some agents might have flat frontmatter without meta wrapper
            # Check for name/description at root level
            if "name" in frontmatter or "description" in frontmatter:
                meta = frontmatter
            else:
                logger.warning(f"Agent file {path} has no meta section")
                meta = {}

        agent_name = meta.get("name", name)
        description = meta.get("description", "")

        return LoadedAgent(name=agent_name, description=description, meta=meta)

    def load_agent(self, name: str) -> LoadedAgent:
        """Load agent by name (fallback method).

        This is called when resolve_agent_path returns None.
        Returns a stub agent with just the name.

        Args:
            name: Agent name

        Returns:
            LoadedAgent with minimal metadata
        """
        # Return stub - the agent couldn't be resolved to a path
        logger.debug(f"Agent '{name}' could not be resolved to a path, using stub")
        return LoadedAgent(name=name, description="", meta={"name": name})

    def _parse_frontmatter_simple(self, text: str) -> dict[str, Any]:
        """Simple frontmatter parser as fallback.

        Args:
            text: Markdown text with YAML frontmatter

        Returns:
            Parsed frontmatter dict, or empty dict if none found
        """
        import re

        import yaml

        pattern = r"^---\s*\n(.*?)\n---\s*\n?"
        match = re.match(pattern, text, re.DOTALL)

        if not match:
            return {}

        frontmatter_str = match.group(1)
        try:
            return yaml.safe_load(frontmatter_str) or {}
        except yaml.YAMLError:
            logger.warning("Failed to parse frontmatter YAML")
            return {}
