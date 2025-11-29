"""CLI-specific path policy and dependency injection helpers.

This module centralizes ALL path-related policy decisions for the CLI.
Libraries receive paths via injection; this module provides the CLI's choices.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_collections import CollectionResolver
from amplifier_config import ConfigManager
from amplifier_config import ConfigPaths
from amplifier_module_resolution import StandardModuleSourceResolver
from amplifier_profiles import ProfileLoader

if TYPE_CHECKING:
    from amplifier_profiles import AgentLoader

# ===== CONFIG PATHS =====


def get_cli_config_paths() -> ConfigPaths:
    """Get CLI-specific configuration paths (APP LAYER POLICY).

    Returns:
        ConfigPaths with CLI conventions:
        - User: ~/.amplifier/settings.yaml
        - Project: .amplifier/settings.yaml
        - Local: .amplifier/settings.local.yaml
    """
    return ConfigPaths(
        user=Path.home() / ".amplifier" / "settings.yaml",
        project=Path(".amplifier") / "settings.yaml",
        local=Path(".amplifier") / "settings.local.yaml",
    )


# ===== COLLECTION PATHS =====


def get_collection_search_paths() -> list[Path]:
    """Get CLI-specific collection search paths (APP LAYER POLICY).

    Search order (highest precedence first):
    1. Project collections (.amplifier/collections/)
    2. User collections (~/.amplifier/collections/)
    3. Bundled collections (package data)

    Returns:
        List of paths to search for collections
    """
    package_dir = Path(__file__).parent
    bundled = package_dir / "data" / "collections"

    return [
        Path.cwd() / ".amplifier" / "collections",  # Project (highest)
        Path.home() / ".amplifier" / "collections",  # User
        bundled,  # Bundled (lowest)
    ]


def get_collection_lock_path(local: bool = False) -> Path:
    """Get CLI-specific collection lock path (APP LAYER POLICY).

    Args:
        local: If True, use project lock; if False, use user lock

    Returns:
        Path to collection lock file
    """
    if local:
        return Path(".amplifier") / "collections.lock"
    return Path.home() / ".amplifier" / "collections.lock"


# ===== PROFILE PATHS =====


def get_profile_search_paths() -> list[Path]:
    """Get CLI-specific profile search paths using library mechanisms (DRY).

    Per RUTHLESS_SIMPLICITY: Use library, don't duplicate logic.
    Per DRY: CollectionResolver + discover_collection_resources are single source.

    Search order (highest precedence first):
    1. Project profiles (.amplifier/profiles/)
    2. User profiles (~/.amplifier/profiles/)
    3. Collection profiles (via CollectionResolver - DRY!)
    4. Bundled profiles (package data)

    Returns:
        List of paths to search for profiles
    """
    from amplifier_collections import discover_collection_resources

    package_dir = Path(__file__).parent
    paths = []

    # Project (highest precedence)
    project_profiles = Path.cwd() / ".amplifier" / "profiles"
    if project_profiles.exists():
        paths.append(project_profiles)

    # User
    user_profiles = Path.home() / ".amplifier" / "profiles"
    if user_profiles.exists():
        paths.append(user_profiles)

    # Collection profiles (USE LIBRARY MECHANISMS - DRY!)
    # This replaces manual iteration with library mechanism
    resolver = create_collection_resolver()
    for _metadata_name, collection_path in resolver.list_collections():
        # Use library's resource discovery (handles ALL structures: flat, nested, hybrid)
        resources = discover_collection_resources(collection_path)

        if resources.profiles:
            # All profiles are in same directory per convention
            # Add the parent directory of first profile
            profile_dir = resources.profiles[0].parent
            if profile_dir not in paths:
                paths.append(profile_dir)

    # Bundled profiles
    bundled_profiles = package_dir / "data" / "profiles"
    if bundled_profiles.exists():
        paths.append(bundled_profiles)

    return paths


# ===== MODULE RESOLUTION PATHS =====


def get_workspace_dir() -> Path:
    """Get CLI-specific workspace directory for local modules (APP LAYER POLICY).

    Returns:
        Path to workspace directory (.amplifier/modules/)
    """
    return Path(".amplifier") / "modules"


# ===== DEPENDENCY FACTORIES =====


def create_config_manager() -> ConfigManager:
    """Create CLI-configured config manager.

    Returns:
        ConfigManager with CLI path policy injected
    """
    return ConfigManager(paths=get_cli_config_paths())


def create_collection_resolver() -> CollectionResolver:
    """Create CLI-configured collection resolver.

    Returns:
        CollectionResolver with CLI search paths injected
    """
    return CollectionResolver(search_paths=get_collection_search_paths())


def create_profile_loader(
    collection_resolver: CollectionResolver | None = None,
) -> ProfileLoader:
    """Create CLI-configured profile loader with dependencies.

    Args:
        collection_resolver: Optional collection resolver (creates one if not provided)

    Returns:
        ProfileLoader with CLI paths and protocols injected
    """
    if collection_resolver is None:
        collection_resolver = create_collection_resolver()

    from .lib.mention_loading import MentionLoader

    return ProfileLoader(
        search_paths=get_profile_search_paths(),
        collection_resolver=collection_resolver,
        mention_loader=MentionLoader(),  # CLI mention loader with default resolver
    )


def get_agent_search_paths() -> list[Path]:
    """Get CLI-specific agent search paths using library mechanisms (DRY).

    Identical pattern to get_profile_search_paths() but for agents.

    Search order (highest precedence first):
    1. Project agents (.amplifier/agents/)
    2. User agents (~/.amplifier/agents/)
    3. Collection agents (via CollectionResolver - DRY!)
    4. Bundled agents (package data)

    Returns:
        List of paths to search for agents
    """
    from amplifier_collections import discover_collection_resources

    paths = []

    # Project (highest precedence)
    project_agents = Path.cwd() / ".amplifier" / "agents"
    if project_agents.exists():
        paths.append(project_agents)

    # User
    user_agents = Path.home() / ".amplifier" / "agents"
    if user_agents.exists():
        paths.append(user_agents)

    # Collection agents (USE LIBRARY MECHANISMS - DRY!)
    resolver = create_collection_resolver()
    for _metadata_name, collection_path in resolver.list_collections():
        resources = discover_collection_resources(collection_path)

        if resources.agents:
            agent_dir = resources.agents[0].parent
            if agent_dir not in paths:
                paths.append(agent_dir)

    return paths


def create_agent_loader(
    collection_resolver: CollectionResolver | None = None,
) -> "AgentLoader":
    """Create CLI-configured agent loader with dependencies.

    Args:
        collection_resolver: Optional collection resolver (creates one if not provided)

    Returns:
        AgentLoader with CLI paths and protocols injected
    """
    if collection_resolver is None:
        collection_resolver = create_collection_resolver()

    from amplifier_profiles import AgentLoader
    from amplifier_profiles import AgentResolver

    from .lib.mention_loading import MentionLoader

    resolver = AgentResolver(
        search_paths=get_agent_search_paths(),
        collection_resolver=collection_resolver,
    )

    return AgentLoader(
        resolver=resolver,
        mention_loader=MentionLoader(),  # CLI mention loader with default resolver
    )


def create_module_resolver() -> StandardModuleSourceResolver:
    """Create CLI-configured module resolver with settings and collection providers.

    Returns:
        StandardModuleSourceResolver with CLI providers injected
    """
    config = create_config_manager()

    # CLI implements SettingsProviderProtocol
    class CLISettingsProvider:
        """CLI implementation of SettingsProviderProtocol."""

        def get_module_sources(self) -> dict[str, str]:
            """Get all module sources from CLI settings.

            Merges sources from multiple locations:
            1. settings.sources (explicit source overrides)
            2. settings.modules.providers[] (registered provider modules)
            3. settings.modules.tools[] (registered tool modules)
            4. settings.modules.hooks[] (registered hook modules)

            Module-specific sources take precedence over explicit overrides
            to ensure user-added modules are properly resolved.
            """
            # Start with explicit source overrides
            sources = dict(config.get_module_sources())

            # Extract sources from registered modules (modules.providers[], modules.tools[], etc.)
            merged = config.get_merged_settings()
            modules_section = merged.get("modules", {})

            # Check each module type category
            for category in ["providers", "tools", "hooks", "orchestrators", "contexts"]:
                module_list = modules_section.get(category, [])
                if isinstance(module_list, list):
                    for entry in module_list:
                        if isinstance(entry, dict):
                            module_id = entry.get("module")
                            source = entry.get("source")
                            if module_id and source:
                                # Module-specific sources override explicit overrides
                                sources[module_id] = source

            return sources

        def get_module_source(self, module_id: str) -> str | None:
            """Get module source from CLI settings."""
            return self.get_module_sources().get(module_id)

    # CLI implements CollectionModuleProviderProtocol
    class CLICollectionModuleProvider:
        """CLI implementation of CollectionModuleProviderProtocol.

        Uses filesystem discovery (same as profiles/agents) for consistency.
        Lock file tracks metadata (source URLs, SHAs) for updates, not existence.
        """

        def get_collection_modules(self) -> dict[str, str]:
            """Get module_id -> absolute_path from installed collections.

            Uses filesystem discovery via CollectionResolver - same pattern as
            profile/agent discovery for consistency across all resource types.
            """
            from amplifier_collections import discover_collection_resources

            resolver = create_collection_resolver()
            modules = {}

            for _metadata_name, collection_path in resolver.list_collections():
                resources = discover_collection_resources(collection_path)

                for module_path in resources.modules:
                    # Module name is the directory name
                    module_name = module_path.name
                    modules[module_name] = str(module_path)

            return modules

    # pyright: ignore[reportCallIssue] - collection_provider param exists, pyright can't resolve from editable install
    return StandardModuleSourceResolver(
        settings_provider=CLISettingsProvider(),
        collection_provider=CLICollectionModuleProvider(),  # type: ignore[call-arg]
        workspace_dir=get_workspace_dir(),
    )
