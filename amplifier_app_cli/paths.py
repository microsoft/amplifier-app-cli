"""CLI-specific path policy and dependency injection helpers.

This module centralizes ALL path-related policy decisions for the CLI.
Libraries receive paths via injection; this module provides the CLI's choices.
"""

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Literal

from amplifier_collections import CollectionResolver
from amplifier_config import ConfigManager
from amplifier_config import ConfigPaths
from amplifier_config import Scope
from amplifier_module_resolution import StandardModuleSourceResolver
from amplifier_profiles import ProfileLoader

if TYPE_CHECKING:
    from amplifier_profiles import AgentLoader

# Type alias for scope names used in CLI
ScopeType = Literal["local", "project", "global"]

# Map CLI scope names to Scope enum
_SCOPE_MAP: dict[ScopeType, Scope] = {
    "local": Scope.LOCAL,
    "project": Scope.PROJECT,
    "global": Scope.USER,
}

# ===== CONFIG PATHS =====


def get_cli_config_paths() -> ConfigPaths:
    """Get CLI-specific configuration paths (APP LAYER POLICY).

    Returns:
        ConfigPaths with CLI conventions:
        - User: ~/.amplifier/settings.yaml (always enabled)
        - Project: .amplifier/settings.yaml (disabled when cwd is home)
        - Local: .amplifier/settings.local.yaml (disabled when cwd is home)

    Note:
        When running from the home directory (~), project and local scopes are
        disabled (set to None) to prevent confusion. In ~/.amplifier/, there
        should only ever be settings.yaml (user scope), never settings.local.yaml.
        This prevents the confusing case where ~/.amplifier/settings.local.yaml
        would only apply when running from exactly ~ but not from anywhere else.
    """
    home = Path.home()
    cwd = Path.cwd()

    # When cwd is home directory, disable project/local scopes
    # This prevents ~/.amplifier/settings.local.yaml confusion
    if cwd == home:
        return ConfigPaths(
            user=home / ".amplifier" / "settings.yaml",
            project=None,
            local=None,
        )

    return ConfigPaths(
        user=home / ".amplifier" / "settings.yaml",
        project=Path(".amplifier") / "settings.yaml",
        local=Path(".amplifier") / "settings.local.yaml",
    )


def is_running_from_home() -> bool:
    """Check if running from the home directory.

    Returns:
        True if cwd is the user's home directory
    """
    return Path.cwd() == Path.home()


class ScopeNotAvailableError(Exception):
    """Raised when a requested scope is not available."""

    def __init__(self, scope: ScopeType, message: str):
        self.scope = scope
        self.message = message
        super().__init__(message)


def validate_scope_for_write(
    scope: ScopeType,
    config: ConfigManager,
    *,
    allow_fallback: bool = False,
) -> ScopeType:
    """Validate that a scope is available for write operations.

    Args:
        scope: The requested scope ("local", "project", or "global")
        config: ConfigManager instance to check
        allow_fallback: If True, fall back to "global" when scope unavailable

    Returns:
        The validated scope (may be "global" if fallback allowed)

    Raises:
        ScopeNotAvailableError: If scope is not available and fallback not allowed
    """
    scope_enum = _SCOPE_MAP[scope]

    if config.is_scope_available(scope_enum):
        return scope

    # Scope not available - running from home directory
    if allow_fallback:
        # Fall back to global (user) scope
        return "global"

    # Build helpful error message
    if is_running_from_home():
        raise ScopeNotAvailableError(
            scope,
            f"The '{scope}' scope is not available when running from your home directory.\n"
            f"Use --global instead to save to ~/.amplifier/settings.yaml\n\n"
            f"Tip: Project and local scopes require being in a project directory.",
        )

    raise ScopeNotAvailableError(
        scope,
        f"The '{scope}' scope is not available.\nUse --global instead.",
    )


def get_effective_scope(
    requested_scope: ScopeType | None,
    config: ConfigManager,
    *,
    default_scope: ScopeType = "local",
) -> tuple[ScopeType, bool]:
    """Get the effective scope, handling fallbacks gracefully.

    When no scope is explicitly requested and the default isn't available,
    falls back to "global" scope with a warning.

    Args:
        requested_scope: Explicitly requested scope, or None for default
        config: ConfigManager instance to check
        default_scope: Default scope when none requested

    Returns:
        Tuple of (effective_scope, was_fallback_used)
        - effective_scope: The scope to use
        - was_fallback_used: True if we fell back from the default

    Raises:
        ScopeNotAvailableError: If an explicitly requested scope is not available
    """
    if requested_scope is not None:
        # User explicitly requested a scope - validate without fallback
        return validate_scope_for_write(requested_scope, config, allow_fallback=False), False

    # No explicit request - use default with fallback
    effective = validate_scope_for_write(default_scope, config, allow_fallback=True)
    was_fallback = effective != default_scope
    return effective, was_fallback


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
    """Create CLI-configured collection resolver with source provider.

    Returns:
        CollectionResolver with CLI search paths and source provider injected
    """
    config = create_config_manager()

    # CLI implements CollectionSourceProvider protocol
    class CLICollectionSourceProvider:
        """CLI implementation of CollectionSourceProvider.

        Provides collection source overrides from CLI settings (3-scope system).
        """

        def get_collection_source(self, collection_name: str) -> str | None:
            """Get collection source override from CLI settings."""
            return config.get_collection_sources().get(collection_name)

    # pyright: ignore[reportCallIssue] - source_provider param exists, pyright can't resolve from editable install
    return CollectionResolver(
        search_paths=get_collection_search_paths(),
        source_provider=CLICollectionSourceProvider(),  # type: ignore[call-arg]
    )


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
