"""LEGACY: Profile loading wrappers. DELETE when profiles/collections removed.

This module provides a single import location for profile-related APIs
from the deprecated amplifier-profiles library. The bundle codepath uses
lib/settings.py instead - it should NEVER import from this file.

All complex classes (ProfileLoader, etc.) are re-exported from amplifier_profiles.
Schema/merger/compiler implementations are local for simpler APIs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import yaml

# Re-export complex classes from amplifier_profiles
from amplifier_profiles import Profile as AmplifierProfile
from amplifier_profiles import ProfileLoader
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

if TYPE_CHECKING:
    from .agents import AgentLoader

logger = logging.getLogger(__name__)

# ===== Pydantic Schema (for Pydantic-based consumers) =====


class PydanticProfileMetadata(BaseModel):
    """Profile metadata and identification (Pydantic version)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Unique profile identifier")
    version: str = Field(default="1.0.0", description="Semantic version")
    description: str = Field(default="", description="Human-readable description")
    model: str | None = Field(None, description="Model in 'provider/model' format")
    extends: str | None = Field(None, description="Parent profile to inherit from")


class ModuleConfig(BaseModel):
    """Configuration for a single module (Pydantic version)."""

    model_config = ConfigDict(frozen=True)

    module: str = Field(..., description="Module ID to load")
    source: str | dict[str, Any] | None = Field(None, description="Module source (git URL, file path, etc.)")
    config: dict[str, Any] | None = Field(None, description="Module-specific configuration")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format for Mount Plan."""
        result: dict[str, Any] = {"module": self.module}
        if self.source is not None:
            result["source"] = self.source
        if self.config is not None:
            result["config"] = self.config
        return result


class SessionConfig(BaseModel):
    """Core session configuration (Pydantic version)."""

    model_config = ConfigDict(frozen=True)

    orchestrator: ModuleConfig = Field(..., description="Orchestrator module configuration")
    context: ModuleConfig = Field(..., description="Context module configuration")


class PydanticProfile(BaseModel):
    """Complete profile specification (Pydantic version for schema validation)."""

    model_config = ConfigDict(frozen=True)

    profile: PydanticProfileMetadata
    session: SessionConfig
    agents: Literal["all", "none"] | list[str] | None = Field(None, description="Agent configuration")
    providers: list[ModuleConfig] = Field(default_factory=list)
    tools: list[ModuleConfig] = Field(default_factory=list)
    hooks: list[ModuleConfig] = Field(default_factory=list)
    exclude: dict[str, Any] | None = Field(None, description="Selective inheritance exclusions")


# Alias for backwards compatibility with amplifier_profiles.schema imports
Profile = PydanticProfile


# ===== Dataclass Schema (for simple internal use) =====


@dataclass
class ProfileMetadata:
    """Profile metadata from frontmatter (dataclass version)."""

    name: str
    extends: str | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class DataclassProfile:
    """A loaded profile with metadata and configuration (dataclass version)."""

    metadata: ProfileMetadata
    config: dict[str, Any]
    source_path: Path | None = None

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def extends(self) -> str | None:
        return self.metadata.extends


# ===== Profile Parsing =====


_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_profile_file(path: Path) -> DataclassProfile:
    """Parse a profile markdown file with YAML frontmatter.

    Profile format:
    ```
    ---
    name: profile-name
    extends: base-profile  # optional
    description: Profile description
    tags: [tag1, tag2]
    ---

    ```yaml
    session:
      orchestrator: loop-streaming
    providers:
      - module: provider-anthropic
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

    # Parse YAML code blocks from body
    config = _extract_yaml_config(body)

    # Build metadata
    metadata = ProfileMetadata(
        name=frontmatter.get("name", path.stem),
        extends=frontmatter.get("extends"),
        description=frontmatter.get("description", ""),
        tags=frontmatter.get("tags", []),
    )

    return DataclassProfile(metadata=metadata, config=config, source_path=path)


def _extract_yaml_config(body: str) -> dict[str, Any]:
    """Extract configuration from YAML code blocks in markdown body."""
    config: dict[str, Any] = {}

    # Find all YAML code blocks
    yaml_pattern = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)
    for match in yaml_pattern.finditer(body):
        block_content = yaml.safe_load(match.group(1))
        if isinstance(block_content, dict):
            config = deep_merge(config, block_content)

    return config


# ===== Deep Merge Utilities =====


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts, with overlay winning conflicts.

    Arrays are replaced, not concatenated (consistent with original behavior).
    """
    result = base.copy()

    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def merge_module_lists(
    base: list[dict[str, Any]],
    overlay: list[dict[str, Any]],
    key_field: str = "module",
) -> list[dict[str, Any]]:
    """Merge module lists by module ID, with overlay configs merged into base.

    If a module appears in both lists, configs are deep-merged (overlay wins).
    Modules only in overlay are appended.
    """
    # Index base by key
    base_by_key: dict[str, dict[str, Any]] = {}
    for item in base:
        if key_field in item:
            base_by_key[item[key_field]] = item.copy()

    # Merge overlay
    for item in overlay:
        key = item.get(key_field)
        if key and key in base_by_key:
            # Merge configs
            base_by_key[key] = deep_merge(base_by_key[key], item)
        elif key:
            # New module
            base_by_key[key] = item.copy()

    return list(base_by_key.values())


# ProfileLoader is re-exported from amplifier_profiles at top of file
# (complex class with many methods - load_profile, get_profile_source, etc.)


# ===== Profile Config Merging =====


def merge_profile_configs(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge two profile configurations with special handling for module lists.

    Module lists (providers, tools, hooks) are merged by module ID.
    Other values are deep-merged with overlay winning.
    """
    result = deep_merge(base, overlay)

    # Special handling for module list sections
    module_sections = ["providers", "tools", "hooks"]
    for section in module_sections:
        if section in base and section in overlay:
            base_list = base[section] if isinstance(base[section], list) else []
            overlay_list = overlay[section] if isinstance(overlay[section], list) else []
            result[section] = merge_module_lists(base_list, overlay_list)

    return result


# ===== Collection Profiles Resolver Protocol =====


class CollectionProfilesResolver:
    """Protocol for resolving profiles from collections.

    Implementations should override resolve_collection_profile to provide
    actual resolution logic.
    """

    def resolve_collection_profile(
        self,
        collection_name: str,
        profile_name: str,
    ) -> Path | None:
        """Resolve a profile path within a collection."""
        return None  # Default: no resolution


# ===== Additional Merger Functions (from amplifier_profiles.merger) =====


def merge_module_items(parent_item: dict[str, Any], child_item: dict[str, Any]) -> dict[str, Any]:
    """Deep merge a single module item (hook/tool/provider config).

    Special handling for 'config' field - deep merged rather than replaced.
    All other fields follow standard merge rules (child overrides parent).

    Args:
        parent_item: Parent module item
        child_item: Child module item

    Returns:
        Merged module item
    """
    merged = parent_item.copy()

    for key, value in child_item.items():
        if key == "config" and key in merged:
            # Deep merge configs
            if isinstance(merged["config"], dict) and isinstance(value, dict):
                merged["config"] = deep_merge(merged["config"], value)
            else:
                # Type mismatch or not dicts - child overrides
                merged["config"] = value
        else:
            # All other fields: child overrides parent (including 'source')
            merged[key] = value

    return merged


def merge_profile_dicts(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Deep merge child profile dictionary into parent profile dictionary.

    Merge rules by key:
    - 'hooks', 'tools', 'providers': Merge module lists by module ID
    - Dict values: Recursive deep merge
    - Other values: Child overrides parent

    Args:
        parent: Parent profile dictionary
        child: Child profile dictionary

    Returns:
        Merged profile dictionary with child values taking precedence
    """
    merged = parent.copy()

    for key, child_value in child.items():
        if key not in merged:
            # New key in child - just add it
            merged[key] = child_value
        elif key in ("hooks", "tools", "providers"):
            # Module lists - merge by module ID
            merged[key] = merge_module_lists(merged[key], child_value)
        elif isinstance(child_value, dict) and isinstance(merged[key], dict):
            # Both are dicts - recursive deep merge
            merged[key] = deep_merge(merged[key], child_value)
        else:
            # Scalar or type mismatch - child overrides parent
            merged[key] = child_value

    return merged


# ===== Utility Functions (from amplifier_profiles.utils) =====


def parse_markdown_body(content: str) -> str:
    """Extract markdown body from profile/agent file (content after frontmatter).

    Args:
        content: Raw file content with YAML frontmatter

    Returns:
        Markdown body text (after the --- frontmatter block)
    """
    match = _FRONTMATTER_PATTERN.match(content)
    if match:
        return content[match.end() :].strip()
    return content.strip()


# ===== Profile Compiler (from amplifier_profiles.compiler) =====


def compile_profile_to_mount_plan(
    base: AmplifierProfile | PydanticProfile,
    overlays: list[AmplifierProfile | PydanticProfile] | None = None,
    agent_loader: AgentLoader | None = None,
) -> dict[str, Any]:
    """Compile a profile and its overlays into a Mount Plan.

    This function takes a base profile and optional overlay profiles and merges them
    into a single Mount Plan dictionary that can be passed to AmplifierSession.

    Args:
        base: Base profile to compile
        overlays: Optional list of overlay profiles to merge
        agent_loader: Optional agent loader for loading agent configs

    Returns:
        Mount Plan dictionary suitable for AmplifierSession
    """
    if overlays is None:
        overlays = []

    # Extract from ModuleConfig objects directly
    orchestrator = base.session.orchestrator
    orchestrator_id = orchestrator.module
    orchestrator_source = orchestrator.source
    orchestrator_config = orchestrator.config or {}

    context = base.session.context
    context_id = context.module
    context_source = context.source
    context_config = context.config or {}

    # Start with base profile
    mount_plan: dict[str, Any] = {
        "session": {
            "orchestrator": orchestrator_id,
            "context": context_id,
        },
        "providers": [],
        "tools": [],
        "hooks": [],
        "agents": {},
    }

    # Add sources if present
    if orchestrator_source:
        mount_plan["session"]["orchestrator_source"] = orchestrator_source
    if context_source:
        mount_plan["session"]["context_source"] = context_source

    # Add config sections if present
    if orchestrator_config:
        mount_plan["orchestrator"] = {"config": orchestrator_config}
    if context_config:
        mount_plan["context"] = {"config": context_config}

    # Add base modules
    mount_plan["providers"] = [p.to_dict() for p in base.providers]
    mount_plan["tools"] = [t.to_dict() for t in base.tools]
    mount_plan["hooks"] = [h.to_dict() for h in base.hooks]

    # Apply overlays
    for overlay in overlays:
        mount_plan = _merge_pydantic_profile_into_mount_plan(mount_plan, overlay)

    # Load agents using agent loading system (if agent_loader provided by app)
    if agent_loader is not None and base.agents is not None:
        agents_dict: dict[str, Any] = {}

        # Determine which agents to load based on Smart Single Value format:
        # - "all": Load all discovered agents
        # - "none": Load no agents (disabled)
        # - list[str]: Load specific agents by name
        if base.agents == "none":
            agent_names_to_load: list[str] = []
        elif base.agents == "all":
            agent_names_to_load = agent_loader.list_agents()
        elif isinstance(base.agents, list):
            agent_names_to_load = base.agents
        else:
            agent_names_to_load = []

        # Load agents from app-configured search locations
        for agent_name in agent_names_to_load:
            try:
                agent = agent_loader.load_agent(agent_name)
                agents_dict[agent_name] = agent.to_mount_plan_fragment()
                logger.debug(f"Loaded agent: {agent_name}")
            except Exception as e:
                logger.warning(f"Failed to load agent '{agent_name}': {e}")

        mount_plan["agents"] = agents_dict
        logger.info(f"Loaded {len(agents_dict)} agents into mount plan")

    return mount_plan


def _merge_pydantic_profile_into_mount_plan(
    mount_plan: dict[str, Any], overlay: AmplifierProfile | PydanticProfile
) -> dict[str, Any]:
    """Merge an overlay Pydantic profile into an existing mount plan."""
    # Override session fields if present in overlay
    if overlay.session.orchestrator:
        mount_plan["session"]["orchestrator"] = overlay.session.orchestrator.module
        if overlay.session.orchestrator.source:
            mount_plan["session"]["orchestrator_source"] = overlay.session.orchestrator.source
        else:
            mount_plan["session"].pop("orchestrator_source", None)
        if overlay.session.orchestrator.config:
            if "orchestrator" not in mount_plan:
                mount_plan["orchestrator"] = {}
            mount_plan["orchestrator"]["config"] = overlay.session.orchestrator.config

    if overlay.session.context:
        mount_plan["session"]["context"] = overlay.session.context.module
        if overlay.session.context.source:
            mount_plan["session"]["context_source"] = overlay.session.context.source
        else:
            mount_plan["session"].pop("context_source", None)
        if overlay.session.context.config:
            if "context" not in mount_plan:
                mount_plan["context"] = {}
            mount_plan["context"]["config"] = overlay.session.context.config

    # Merge module lists
    mount_plan["providers"] = _merge_pydantic_module_list(mount_plan["providers"], overlay.providers)
    mount_plan["tools"] = _merge_pydantic_module_list(mount_plan["tools"], overlay.tools)
    mount_plan["hooks"] = _merge_pydantic_module_list(mount_plan["hooks"], overlay.hooks)

    return mount_plan


def _merge_pydantic_module_list(
    base_modules: list[dict[str, Any]], overlay_modules: Sequence[Any]
) -> list[dict[str, Any]]:
    """Merge module lists, converting Pydantic ModuleConfig to dict."""
    # Convert overlay modules to dict format
    overlay_dicts = [m.to_dict() for m in overlay_modules]

    # Build dict by ID for efficient lookup
    result_dict: dict[str, dict[str, Any]] = {}

    # Add all base modules
    for base_module in base_modules:
        module_id = base_module["module"]
        result_dict[module_id] = base_module

    # Merge or add overlay modules
    for overlay_module in overlay_dicts:
        module_id = overlay_module["module"]
        if module_id in result_dict:
            result_dict[module_id] = merge_module_items(result_dict[module_id], overlay_module)
        else:
            result_dict[module_id] = overlay_module

    # Return as list, preserving base order + new overlays
    result = []
    for base_module in base_modules:
        result.append(result_dict[base_module["module"]])

    # Add new overlay modules (not in base)
    for overlay_module in overlay_dicts:
        if overlay_module["module"] not in {m["module"] for m in base_modules}:
            result.append(overlay_module)

    return result


# ===== Exports =====


__all__ = [
    # Pydantic Schema (for amplifier_profiles.schema compatibility)
    "ModuleConfig",
    "SessionConfig",
    "Profile",
    "PydanticProfile",
    "PydanticProfileMetadata",
    # Dataclass Schema (for internal use)
    "ProfileMetadata",
    "DataclassProfile",
    # Parsing
    "parse_profile_file",
    "parse_markdown_body",
    # Merging
    "deep_merge",
    "merge_module_lists",
    "merge_module_items",
    "merge_profile_configs",
    "merge_profile_dicts",
    # Compiler
    "compile_profile_to_mount_plan",
    # Loader
    "ProfileLoader",
    "CollectionProfilesResolver",
]
