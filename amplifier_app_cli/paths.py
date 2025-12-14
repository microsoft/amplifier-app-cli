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
from amplifier_foundation import BundleRegistry
from amplifier_module_resolution import StandardModuleSourceResolver
from amplifier_profiles import ProfileLoader

if TYPE_CHECKING:
    from amplifier_core import AmplifierSession
    from amplifier_profiles import AgentLoader

# Type alias for scope names used in CLI
ScopeType = Literal["local", "project", "global"]

# Map CLI scope names to Scope enum
_SCOPE_MAP: dict[ScopeType, Scope] = {
    "local": Scope.LOCAL,
    "project": Scope.PROJECT,
    "global": Scope.USER,
}

# ===== COMMON PATH HELPERS =====


def _get_user_and_project_paths(resource_type: str, *, check_exists: bool = True) -> list[Path]:
    """Get project and user paths for a resource type.

    This is a DRY helper that extracts the common pattern of:
    1. Check project .amplifier/<resource_type>/ (highest precedence)
    2. Check user ~/.amplifier/<resource_type>/

    Args:
        resource_type: The subdirectory name (e.g., "profiles", "bundles", "agents")
        check_exists: If True, only include paths that exist. If False, include all.

    Returns:
        List of paths in precedence order (project first, then user)
    """
    paths = []

    # Project (highest precedence)
    project_path = Path.cwd() / ".amplifier" / resource_type
    if not check_exists or project_path.exists():
        paths.append(project_path)

    # User
    user_path = Path.home() / ".amplifier" / resource_type
    if not check_exists or user_path.exists():
        paths.append(user_path)

    return paths


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

    # Project and user paths (highest precedence)
    paths = _get_user_and_project_paths("profiles")

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


def get_bundle_search_paths() -> list[Path]:
    """Get CLI-specific bundle search paths (APP LAYER POLICY).

    Search order (highest precedence first):
    1. Project bundles (.amplifier/bundles/)
    2. User bundles (~/.amplifier/bundles/)
    3. Bundled bundles (package data/bundles/)

    Returns:
        List of paths to search for bundles
    """
    package_dir = Path(__file__).parent

    # Project and user paths (highest precedence)
    paths = _get_user_and_project_paths("bundles")

    # Bundled (lowest)
    bundled = package_dir / "data" / "bundles"
    if bundled.exists():
        paths.append(bundled)

    return paths


def create_bundle_registry(
    home: Path | None = None,
) -> BundleRegistry:
    """Create CLI-configured bundle registry with well-known bundles.

    Uses amplifier-foundation's BundleRegistry for all URI types:
    - file:// and local paths
    - git+https:// for git repositories
    - https:// and http:// for direct downloads
    - zip+https:// and zip+file:// for zip archives

    Well-known bundles (e.g., "foundation") are automatically registered,
    allowing plain names like "foundation" to resolve correctly.

    Per DESIGN PHILOSOPHY: Bundles have independent code paths optimized for
    their longer term future, with no coupling to profiles/collections.

    Args:
        home: Home directory for registry state and cache (default: AMPLIFIER_HOME).

    Returns:
        BundleRegistry with foundation source handlers and well-known bundles registered.
    """
    from .lib.bundle_loader import get_bundle_cache_dir
    from .lib.bundle_loader.discovery import AppBundleDiscovery

    # Use default home or derive from cache dir
    if home is None:
        home = get_bundle_cache_dir().parent.parent  # cache/bundles -> home

    # Use AppBundleDiscovery to get a registry with well-known bundles registered.
    # This ensures plain bundle names like "foundation" resolve correctly.
    discovery = AppBundleDiscovery(registry=BundleRegistry(home=home))
    return discovery.registry


async def create_session_from_bundle(
    bundle_name: str,
    *,
    session_id: str | None = None,
    approval_system: object | None = None,
    display_system: object | None = None,
    install_deps: bool = True,
) -> "AmplifierSession":
    """Create session from bundle using foundation's prepare workflow.

    This is the CORRECT way to use bundles with remote modules:
    1. Discover bundle URI via CLI search paths
    2. Load bundle via foundation (handles file://, git+, http://, zip+)
    3. Prepare: download modules from git sources, install deps
    4. Create session with BundleModuleResolver automatically mounted

    Args:
        bundle_name: Bundle name to load (e.g., "foundation").
        session_id: Optional explicit session ID.
        approval_system: Optional approval system for hooks.
        display_system: Optional display system for hooks.
        install_deps: Whether to install Python dependencies for modules.

    Returns:
        Initialized AmplifierSession ready for execute().

    Raises:
        FileNotFoundError: If bundle not found in any search path.
        RuntimeError: If preparation fails (download, install errors).

    Example:
        session = await create_session_from_bundle("foundation")
        async with session:
            response = await session.execute("Hello!")
    """
    from amplifier_core import AmplifierSession

    from .lib.bundle_loader import AppBundleDiscovery
    from .lib.bundle_loader.prepare import load_and_prepare_bundle

    discovery = AppBundleDiscovery(search_paths=get_bundle_search_paths())

    # Load and prepare bundle (downloads modules from git sources)
    prepared = await load_and_prepare_bundle(
        bundle_name,
        discovery,
        install_deps=install_deps,
    )

    # Create session with BundleModuleResolver automatically mounted
    session: AmplifierSession = await prepared.create_session(
        session_id=session_id,
        approval_system=approval_system,
        display_system=display_system,
    )

    return session


def get_agent_search_paths_for_bundle(bundle_name: str | None = None) -> list[Path]:
    """Get agent search paths when using BUNDLE mode.

    Only includes bundle-specific agents, NOT profile/collection agents.
    This ensures clean separation: bundles use bundle stuff only.

    Search order (highest precedence first):
    1. Project agents (.amplifier/agents/)
    2. User agents (~/.amplifier/agents/)
    3. Specific bundle's agents (if bundle_name provided)
    4. All discoverable bundle agents (foundation, etc.)

    Args:
        bundle_name: Optional specific bundle to load agents from

    Returns:
        List of paths to search for agents (bundle sources only)
    """
    from .lib.bundle_loader import AppBundleDiscovery

    # Project and user paths (highest precedence) - user's own agents always included
    paths = _get_user_and_project_paths("agents")

    # Bundle agents only (NO collections)
    bundle_discovery = AppBundleDiscovery()

    if bundle_name:
        # If specific bundle requested, prioritize its agents
        bundle_uri = bundle_discovery.find(bundle_name)
        if bundle_uri and bundle_uri.startswith("file://"):
            bundle_path = Path(bundle_uri[7:])
            if bundle_path.is_file():
                bundle_path = bundle_path.parent
            agents_dir = bundle_path / "agents"
            if agents_dir.exists() and agents_dir not in paths:
                paths.append(agents_dir)
    else:
        # Load all discoverable bundle agents
        for b_name in bundle_discovery.list_bundles():
            bundle_uri = bundle_discovery.find(b_name)
            if bundle_uri and bundle_uri.startswith("file://"):
                bundle_path = Path(bundle_uri[7:])
                if bundle_path.is_file():
                    bundle_path = bundle_path.parent
                agents_dir = bundle_path / "agents"
                if agents_dir.exists() and agents_dir not in paths:
                    paths.append(agents_dir)

    return paths


def get_agent_search_paths_for_profile() -> list[Path]:
    """Get agent search paths when using PROFILE mode.

    Only includes profile/collection agents, NOT bundle agents.
    This ensures clean separation: profiles use profile/collection stuff only.

    Search order (highest precedence first):
    1. Project agents (.amplifier/agents/)
    2. User agents (~/.amplifier/agents/)
    3. Collection agents (via CollectionResolver)
    4. Bundled agents (package data)

    Returns:
        List of paths to search for agents (profile/collection sources only)
    """
    from amplifier_collections import discover_collection_resources

    # Project and user paths (highest precedence)
    paths = _get_user_and_project_paths("agents")

    # Collection agents only (NO bundles)
    resolver = create_collection_resolver()
    for _metadata_name, collection_path in resolver.list_collections():
        resources = discover_collection_resources(collection_path)

        if resources.agents:
            agent_dir = resources.agents[0].parent
            if agent_dir not in paths:
                paths.append(agent_dir)

    return paths


def get_agent_search_paths(use_bundle: bool = False, bundle_name: str | None = None) -> list[Path]:
    """Get CLI-specific agent search paths based on mode.

    Args:
        use_bundle: If True, use bundle-only paths. If False, use profile/collection paths.
        bundle_name: Optional specific bundle name when use_bundle=True

    Returns:
        List of paths to search for agents
    """
    if use_bundle:
        return get_agent_search_paths_for_bundle(bundle_name)
    return get_agent_search_paths_for_profile()


def create_agent_loader(
    collection_resolver: CollectionResolver | None = None,
    *,
    use_bundle: bool = False,
    bundle_name: str | None = None,
    bundle_base_path: Path | None = None,
) -> "AgentLoader":
    """Create CLI-configured agent loader with dependencies.

    Args:
        collection_resolver: Optional collection resolver (creates one if not provided)
        use_bundle: If True, load only bundle agents (not profile/collection agents)
        bundle_name: Specific bundle to load agents from (when use_bundle=True)
        bundle_base_path: Base path of the bundle for resolving @bundle_name:... mentions

    Returns:
        AgentLoader with CLI paths and protocols injected
    """
    if collection_resolver is None:
        collection_resolver = create_collection_resolver()

    from amplifier_profiles import AgentLoader
    from amplifier_profiles import AgentResolver

    from .lib.mention_loading import MentionLoader
    from .lib.mention_loading import MentionResolver

    resolver = AgentResolver(
        search_paths=get_agent_search_paths(use_bundle=use_bundle, bundle_name=bundle_name),
        collection_resolver=collection_resolver,
    )

    # Create MentionResolver with bundle override if in bundle mode
    bundle_override = None
    if use_bundle and bundle_name and bundle_base_path:
        bundle_override = (bundle_name, bundle_base_path)

    mention_resolver = MentionResolver(bundle_override=bundle_override)

    return AgentLoader(
        resolver=resolver,
        mention_loader=MentionLoader(resolver=mention_resolver),
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
