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
    """Get CLI-specific profile search paths (APP LAYER POLICY).

    Search order (highest precedence first):
    1. Project profiles (.amplifier/profiles/)
    2. User profiles (~/.amplifier/profiles/)
    3. Collection profiles (via collections search paths)
    4. Bundled profiles (package data)

    Returns:
        List of paths to search for profiles
    """
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

    # Collection profiles (bundled + user + project collections)
    for collection_path in get_collection_search_paths():
        if collection_path.exists():
            for collection_dir in collection_path.iterdir():
                if collection_dir.is_dir():
                    profiles_dir = collection_dir / "profiles"
                    if profiles_dir.exists():
                        paths.append(profiles_dir)

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
    """Get CLI-specific agent search paths (APP LAYER POLICY).

    Search order (highest precedence first):
    1. Project agents (.amplifier/agents/)
    2. User agents (~/.amplifier/agents/)
    3. Collection agents (via collections search paths)
    4. Bundled agents (package data)

    Returns:
        List of paths to search for agents
    """
    package_dir = Path(__file__).parent
    paths = []

    # Project (highest precedence)
    project_agents = Path.cwd() / ".amplifier" / "agents"
    if project_agents.exists():
        paths.append(project_agents)

    # User
    user_agents = Path.home() / ".amplifier" / "agents"
    if user_agents.exists():
        paths.append(user_agents)

    # Collection agents (bundled + user + project collections)
    for collection_path in get_collection_search_paths():
        if collection_path.exists():
            for collection_dir in collection_path.iterdir():
                if collection_dir.is_dir():
                    agents_dir = collection_dir / "agents"
                    if agents_dir.exists():
                        paths.append(agents_dir)

    # Bundled agents
    bundled_agents = package_dir / "data" / "agents"
    if bundled_agents.exists():
        paths.append(bundled_agents)

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
    """Create CLI-configured module resolver with settings provider.

    Returns:
        StandardModuleSourceResolver with CLI settings provider injected
    """
    config = create_config_manager()

    # CLI implements SettingsProviderProtocol
    class CLISettingsProvider:
        """CLI implementation of SettingsProviderProtocol."""

        def get_module_sources(self) -> dict[str, str]:
            """Get all module sources from CLI settings."""
            return config.get_module_sources()

        def get_module_source(self, module_id: str) -> str | None:
            """Get module source from CLI settings."""
            return config.get_module_sources().get(module_id)

    return StandardModuleSourceResolver(
        settings_provider=CLISettingsProvider(),
        workspace_dir=get_workspace_dir(),
    )
