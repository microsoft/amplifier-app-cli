"""LEGACY: Minimal agent loading. DELETE when profiles/collections removed.

This module re-exports agent APIs from amplifier_profiles for backward
compatibility. The bundle codepath uses lib/settings.py instead - it should
NEVER import from this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

# Re-export complex classes from amplifier_profiles
# These have many methods that are used throughout the codebase
from amplifier_profiles import AgentLoader
from amplifier_profiles import AgentResolver

# ===== Agent Schema =====


@dataclass
class AgentMetadata:
    """Agent metadata from frontmatter."""

    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"


@dataclass
class Agent:
    """A loaded agent definition."""

    metadata: AgentMetadata
    config: dict[str, Any]
    source_path: Path | None = None

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description


# ===== Agent Parsing =====


_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_agent_file(path: Path) -> Agent:
    """Parse an agent markdown file with YAML frontmatter.

    Agent format:
    ```
    ---
    name: agent-name
    description: What this agent does
    tags: [tag1, tag2]
    version: 1.0.0
    ---

    # System Instructions

    You are an expert at...

    ```yaml
    providers:
      - module: provider-anthropic
        config:
          model: claude-opus-4
    tools:
      - module: tool-filesystem
    ```
    """
    content = path.read_text(encoding="utf-8")

    # Extract frontmatter
    frontmatter: dict[str, Any] = {}
    body = content

    match = _FRONTMATTER_PATTERN.match(content)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = content[match.end() :]

    # Extract config from YAML blocks
    config = _extract_yaml_config(body)

    # Extract system instruction (non-YAML content)
    system_instruction = _extract_system_instruction(body)
    if system_instruction:
        config["system_instruction"] = system_instruction

    # Build metadata
    metadata = AgentMetadata(
        name=frontmatter.get("name", path.stem),
        description=frontmatter.get("description", ""),
        tags=frontmatter.get("tags", []),
        version=frontmatter.get("version", "1.0.0"),
    )

    return Agent(metadata=metadata, config=config, source_path=path)


def _extract_yaml_config(body: str) -> dict[str, Any]:
    """Extract configuration from YAML code blocks."""
    config: dict[str, Any] = {}

    yaml_pattern = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)
    for match in yaml_pattern.finditer(body):
        block_content = yaml.safe_load(match.group(1))
        if isinstance(block_content, dict):
            # Deep merge
            for key, value in block_content.items():
                if key in config and isinstance(config[key], dict) and isinstance(value, dict):
                    config[key].update(value)
                else:
                    config[key] = value

    return config


def _extract_system_instruction(body: str) -> str:
    """Extract system instruction (non-code-block content) from body."""
    # Remove YAML code blocks
    yaml_pattern = re.compile(r"```ya?ml\s*\n.*?\n```", re.DOTALL)
    instruction = yaml_pattern.sub("", body)

    # Clean up
    instruction = instruction.strip()

    # Skip if empty or just whitespace
    if not instruction:
        return ""

    return instruction


# AgentLoader and AgentResolver are re-exported from amplifier_profiles at top of file
# (complex classes with many methods - load_agent, get_agent_source, load_agent_from_path, etc.)


# ===== Collection Agents Resolver Protocol =====


class CollectionAgentsResolver:
    """Protocol for resolving agents from collections.

    Implementations should override resolve_collection_agent to provide
    actual resolution logic.
    """

    def resolve_collection_agent(
        self,
        collection_name: str,
        agent_name: str,
    ) -> Path | None:
        """Resolve an agent path within a collection."""
        return None  # Default: no resolution


# ===== Exports =====


__all__ = [
    # Schema
    "AgentMetadata",
    "Agent",
    # Parsing
    "parse_agent_file",
    # Loader
    "AgentLoader",
    # Resolver
    "AgentResolver",
    "CollectionAgentsResolver",
]
