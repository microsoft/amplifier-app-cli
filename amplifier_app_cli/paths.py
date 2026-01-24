"""CLI-specific path policy and dependency injection helpers.

This module centralizes ALL path-related policy decisions for the CLI.
Libraries receive paths via injection; this module provides the CLI's choices.
"""

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from amplifier_foundation import BundleRegistry

from amplifier_app_cli.lib.config_compat import ConfigManager
from amplifier_app_cli.lib.config_compat import ConfigPaths
from amplifier_app_cli.lib.config_compat import Scope

# LEGACY: These imports are no longer available - bundles are the only supported mode
# CollectionResolver, ProfileLoader, StandardModuleSourceResolver, AgentLoader are removed
# Functions that used these will raise NotImplementedError
StandardModuleSourceResolver = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from amplifier_core import AmplifierSession

    from amplifier_app_cli.lib.bundle_loader.resolvers import FoundationSettingsResolver

    # AgentLoader type hint only - runtime usage will fail
    AgentLoader = Any  # type: ignore[assignment,misc]

# Type alias for scope names used in CLI
ScopeType = Literal["local", "project", "global"]

# Map CLI scope names to Scope enum
_SCOPE_MAP: dict[ScopeType, Scope] = {
    "local": Scope.LOCAL,
    "project": Scope.PROJECT,
    "global": Scope.USER,
}

# Type alias for scope checker - accepts ConfigManager or AppSettings
# Both have is_scope_available() but with different parameter types
ScopeChecker = Any  # ConfigManager | AppSettings - duck typed

# ===== COMMON PATH HELPERS =====


def _get_user_and_project_paths(
    resource_type: str, *, check_exists: bool = True
) -> list[Path]:
    """Get project and user paths for a resource type.

    This is a DRY helper that extracts the common pattern of:
    1. Check project .amplifier/<resource_type>/ (highest precedence)
    2. Check user ~/.amplifier/<resource_type>/

    Args:
        resource_type: The subdirectory name (e.g., "bundles", "agents")
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
    config: "ScopeChecker",
    *,
    allow_fallback: bool = False,
) -> ScopeType:
    """Validate that a scope is available for write operations.

    Args:
        scope: The requested scope ("local", "project", or "global")
        config: ConfigManager or AppSettings instance to check (implements ScopeChecker)
        allow_fallback: If True, fall back to "global" when scope unavailable

    Returns:
        The validated scope (may be "global" if fallback allowed)

    Raises:
        ScopeNotAvailableError: If scope is not available and fallback not allowed
    """
    # Check scope availability - works with both ConfigManager (Scope enum) and AppSettings (string)
    # ConfigManager.is_scope_available takes Scope enum
    # AppSettings.is_scope_available takes string scope
    is_available = False
    if isinstance(config, ConfigManager):
        scope_enum = _SCOPE_MAP[scope]
        is_available = config.is_scope_available(scope_enum)
    else:
        # AppSettings or other ScopeChecker - uses string scope
        is_available = config.is_scope_available(scope)  # type: ignore[arg-type]

    if is_available:
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
    config: "ScopeChecker",
    *,
    default_scope: ScopeType = "local",
) -> tuple[ScopeType, bool]:
    """Get the effective scope, handling fallbacks gracefully.

    When no scope is explicitly requested and the default isn't available,
    falls back to "global" scope with a warning.

    Args:
        requested_scope: Explicitly requested scope, or None for default
        config: ConfigManager or AppSettings instance to check (implements ScopeChecker)
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
        return validate_scope_for_write(
            requested_scope, config, allow_fallback=False
        ), False

    # No explicit request - use default with fallback
    effective = validate_scope_for_write(default_scope, config, allow_fallback=True)
    was_fallback = effective != default_scope
    return effective, was_fallback


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

    Per DESIGN PHILOSOPHY: Bundles are the only supported configuration mode.

    Args:
        home: Home directory for registry state and cache (default: AMPLIFIER_HOME).

    Returns:
        BundleRegistry with foundation source handlers and well-known bundles registered.
    """
    from amplifier_foundation.paths.resolution import get_amplifier_home

    from .lib.bundle_loader.discovery import AppBundleDiscovery

    # Use default home
    if home is None:
        home = get_amplifier_home()

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
        session_cwd=Path.cwd(),  # CLI uses CWD for local @-mentions
    )

    return session


def get_agent_search_paths_for_bundle(bundle_name: str | None = None) -> list[Path]:
    """Get agent search paths when using BUNDLE mode.

    Only includes bundle-specific agents.
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

    # Bundle agents only
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


def get_agent_search_paths(bundle_name: str | None = None) -> list[Path]:
    """Get CLI-specific agent search paths.

    Args:
        bundle_name: Optional specific bundle name to load agents from

    Returns:
        List of paths to search for agents
    """
    return get_agent_search_paths_for_bundle(bundle_name)




def create_foundation_resolver() -> "FoundationSettingsResolver":
    """Create CLI-configured foundation resolver with settings providers.

    This resolver uses foundation's source handlers which create the NEW cache format:
    {repo-name}-{hash}/ format.

    Returns:
        FoundationSettingsResolver with CLI providers injected
    """
    from amplifier_app_cli.lib.bundle_loader.resolvers import FoundationSettingsResolver

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
            for category in [
                "providers",
                "tools",
                "hooks",
                "orchestrators",
                "contexts",
            ]:
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


    return FoundationSettingsResolver(
        settings_provider=CLISettingsProvider(),
        workspace_dir=get_workspace_dir(),
    )
