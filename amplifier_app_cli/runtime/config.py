"""Configuration assembly utilities for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING
from typing import Any

from rich.console import Console

from ..lib.settings import AppSettings
from ..lib.merge_utils import merge_module_items
from ..lib.merge_utils import merge_tool_configs


if TYPE_CHECKING:
    from amplifier_foundation import BundleRegistry
    from amplifier_foundation.bundle import PreparedBundle

logger = logging.getLogger(__name__)


async def resolve_bundle_config(
    bundle_name: str,
    app_settings: AppSettings,
    console: Console | None = None,
    *,
    session_id: str | None = None,
    project_slug: str | None = None,
) -> tuple[dict[str, Any], PreparedBundle]:
    """Resolve configuration from bundle using foundation's prepare workflow.

    This is the CORRECT way to use bundles with remote modules:
    1. Discover bundle URI via CLI search paths
    2. Load bundle via foundation (handles file://, git+, http://, zip+)
    3. Prepare: download modules from git sources, install deps
    4. Return mount plan AND PreparedBundle for session creation

    Args:
        bundle_name: Bundle name to load (e.g., "foundation").
        app_settings: App settings for provider overrides.
        console: Optional console for status messages.
        session_id: Optional session ID to include session-scoped tool overrides.
        project_slug: Optional project slug (required if session_id provided).

    Returns:
        Tuple of (mount_plan_config, PreparedBundle).
        - mount_plan_config: Dict ready for merging with settings/CLI overrides
        - PreparedBundle: Has create_session() and resolver for module resolution

    Raises:
        FileNotFoundError: If bundle not found.
        RuntimeError: If preparation fails.
    """
    from ..lib.bundle_loader import AppBundleDiscovery
    from ..lib.bundle_loader.prepare import load_and_prepare_bundle
    from ..paths import get_bundle_search_paths

    discovery = AppBundleDiscovery(search_paths=get_bundle_search_paths())

    if console:
        console.print(f"[dim]Preparing bundle '{bundle_name}'...[/dim]")

    # Build behavior URIs from app-level settings
    # These are app-level policies: compose behavior bundles before prepare()
    # so modules get properly downloaded and installed via normal bundle machinery
    compose_behaviors: list[str] = []

    # Modes system (runtime behavior overlays like /mode plan, /mode review)
    # Always available - users choose to use /mode commands or not
    compose_behaviors.extend(_build_modes_behaviors())

    # Notification behaviors (desktop and push notifications)
    compose_behaviors.extend(
        _build_notification_behaviors(app_settings.get_notification_config())
    )

    # Add app bundles (user-configured bundles that are always composed)
    # App bundles are explicit user configuration, composed AFTER notification behaviors
    app_bundles = app_settings.get_app_bundles()
    if app_bundles:
        compose_behaviors = compose_behaviors + app_bundles

    # Get source overrides from unified settings
    # This enables settings.yaml overrides to take effect at prepare time
    source_overrides = app_settings.get_source_overrides()

    # Get module sources from 'amplifier source add' (sources.modules in settings.yaml)
    module_sources = app_settings.get_module_sources()

    # CRITICAL: Also extract provider sources from config.providers[]
    # Providers are configured via 'amplifier provider use' and stored in config.providers,
    # not in overrides section. Bundle.prepare() needs these sources to download provider modules.
    provider_overrides = app_settings.get_provider_overrides()
    provider_sources = {
        provider["module"]: provider["source"]
        for provider in provider_overrides
        if isinstance(provider, dict) and "module" in provider and "source" in provider
    }

    # Merge all source overrides with proper precedence:
    # sources.modules (general) < overrides.<id>.source (specific) < config.providers[].source (most specific)
    combined_sources = {**module_sources, **source_overrides, **provider_sources}

    # Load and prepare bundle (downloads modules from git sources)
    # If compose_behaviors is provided, those behaviors are composed onto the bundle
    # BEFORE prepare() runs, so their modules get installed correctly
    # If combined_sources is provided, module sources are resolved before download
    prepared = await load_and_prepare_bundle(
        bundle_name,
        discovery,
        compose_behaviors=compose_behaviors if compose_behaviors else None,
        source_overrides=combined_sources if combined_sources else None,
    )

    # Load full agent metadata from .md files (for descriptions)
    # Foundation handles this via load_agent_metadata() after source_base_paths is populated
    prepared.bundle.load_agent_metadata()

    # Get the mount plan from the prepared bundle (now includes agent descriptions)
    bundle_config = prepared.mount_plan

    # Apply provider overrides
    provider_overrides = app_settings.get_provider_overrides()
    if provider_overrides:
        if bundle_config.get("providers"):
            # Bundle has providers - merge overrides with existing
            bundle_config["providers"] = _apply_provider_overrides(
                bundle_config["providers"], provider_overrides
            )
        else:
            # Bundle has no providers (e.g., provider-agnostic foundation bundle)
            # Use overrides directly, but inject sensible debug defaults
            # This ensures observability when using provider-agnostic bundles
            bundle_config["providers"] = _ensure_debug_defaults(provider_overrides)

    # Apply tool overrides from settings (e.g., allowed_write_paths for tool-filesystem)
    # Include session-scoped settings if session context provided
    tool_overrides = app_settings.get_tool_overrides(
        session_id=session_id, project_slug=project_slug
    )
    if tool_overrides:
        if bundle_config.get("tools"):
            # Bundle has tools - merge overrides with existing
            bundle_config["tools"] = _apply_tool_overrides(
                bundle_config["tools"], tool_overrides
            )
        else:
            # Bundle has no tools - use overrides directly
            bundle_config["tools"] = tool_overrides

    # Apply hook overrides from notification settings
    # This maps config.notifications.ntfy.* to hooks-notify-push config etc.
    hook_overrides = app_settings.get_notification_hook_overrides()
    if hook_overrides and bundle_config.get("hooks"):
        bundle_config["hooks"] = _apply_hook_overrides(
            bundle_config["hooks"], hook_overrides
        )

    if console:
        console.print(f"[dim]Bundle '{bundle_name}' prepared successfully[/dim]")

    # Expand environment variables (same as resolve_app_config)
    # IMPORTANT: Must expand BEFORE syncing to mount_plan, so ${ANTHROPIC_API_KEY} etc. become actual values
    bundle_config = expand_env_vars(bundle_config)

    # CRITICAL: Sync providers, tools, and hooks to prepared.mount_plan so create_session() uses them
    # prepared.mount_plan is what create_session() uses, not bundle_config
    # This must happen AFTER env var expansion so API keys are actual values, not "${VAR}" literals
    if bundle_config.get("providers"):
        prepared.mount_plan["providers"] = bundle_config["providers"]
    if tool_overrides:
        prepared.mount_plan["tools"] = bundle_config["tools"]
    # Sync hooks (now with notification config overrides applied)
    if bundle_config.get("hooks"):
        prepared.mount_plan["hooks"] = bundle_config["hooks"]

    # CRITICAL: Also sync settings.yaml overrides back to the Bundle dataclass.
    #
    # PreparedBundle holds two representations:
    #   - mount_plan (dict): used by create_session() for the root session
    #   - bundle (Bundle dataclass): used by PreparedBundle.spawn() for child sessions
    #
    # Without this sync, settings.yaml providers exist in mount_plan but NOT in
    # bundle.providers. When foundation's PreparedBundle.spawn() builds a child
    # session, it calls self.bundle.compose(child_bundle).to_mount_plan() — reading
    # from the Bundle dataclass, not mount_plan. Child sessions then get zero
    # providers, causing coordinator.get("providers") to return empty and tool
    # modules that depend on providers (e.g., image generation) to fail.
    #
    # See: https://github.com/microsoft/amplifier-app-cli/pull/110
    _sync_overrides_to_bundle(prepared, bundle_config, sync_tools=bool(tool_overrides))

    # Note: Notification hooks are now composed via compose_behaviors parameter
    # to load_and_prepare_bundle(), so they get properly installed during prepare().
    # The behavior bundles handle root-session-only logic internally via parent_id check.

    return bundle_config, prepared


def _sync_overrides_to_bundle(
    prepared: "PreparedBundle",
    bundle_config: dict[str, Any],
    *,
    sync_tools: bool = False,
) -> None:
    """Sync settings.yaml overrides from mount_plan back to the Bundle dataclass.

    PreparedBundle holds two representations of the session configuration:
      - ``mount_plan`` (dict) — used by ``create_session()`` for the root session
      - ``bundle`` (Bundle dataclass) — used by ``PreparedBundle.spawn()`` to
        build child sessions via ``bundle.compose(child).to_mount_plan()``

    After ``resolve_bundle_config()`` injects settings.yaml providers, tools, and
    hooks into ``prepared.mount_plan``, this function copies those overrides into
    ``prepared.bundle`` so that child sessions spawned through the foundation
    layer inherit them correctly.

    Without this sync, ``coordinator.get("providers")`` returns an empty dict in
    child sessions because ``bundle.providers`` was never populated with the
    settings.yaml provider modules.
    """
    bundle = getattr(prepared, "bundle", None)
    if bundle is None:
        return

    providers = bundle_config.get("providers")
    if providers and hasattr(bundle, "providers"):
        bundle.providers = list(providers)
        logger.debug(
            "Synced %d provider(s) from settings to bundle.providers: %s",
            len(providers),
            [p.get("module", "?") for p in providers],
        )

    if sync_tools:
        tools = bundle_config.get("tools")
        if tools and hasattr(bundle, "tools"):
            bundle.tools = list(tools)

    hooks = bundle_config.get("hooks")
    if hooks and hasattr(bundle, "hooks"):
        bundle.hooks = list(hooks)


def resolve_app_config(
    *,
    config_manager,
    app_settings: AppSettings,
    cli_config: dict[str, Any] | None = None,
    bundle_name: str | None = None,
    bundle_registry: BundleRegistry | None = None,
    console: Console | None = None,
) -> dict[str, Any]:
    """Resolve configuration with precedence, returning a mount plan dictionary.

    Configuration comes from bundles. If bundle_name is specified and
    bundle_registry is provided, that bundle is loaded. Otherwise, falls back
    to the default bundle.
    """
    # 1. Base mount plan defaults
    config: dict[str, Any] = {
        "session": {
            "orchestrator": "loop-basic",
            "context": "context-simple",
        },
        "providers": [],
        "tools": [],
        "agents": [],
        "hooks": [],
    }

    provider_overrides = app_settings.get_provider_overrides()

    # 2. Apply bundle configuration
    provider_applied_via_config = False

    if bundle_name and bundle_registry:
        # Use bundle-based configuration
        try:
            # load() with a name returns a single Bundle (not dict)
            loaded = asyncio.run(bundle_registry.load(bundle_name))
            if isinstance(loaded, dict):
                raise ValueError(
                    f"Expected single bundle, got dict for '{bundle_name}'"
                )
            bundle = loaded

            # Load full agent metadata from .md files (for descriptions)
            # Foundation handles this via load_agent_metadata()
            bundle.load_agent_metadata()

            bundle_config = bundle.to_mount_plan()

            # Apply provider overrides to bundle config
            if provider_overrides and bundle_config.get("providers"):
                bundle_config["providers"] = _apply_provider_overrides(
                    bundle_config["providers"], provider_overrides
                )
                provider_applied_via_config = True

            config = deep_merge(config, bundle_config)
        except Exception as exc:  # noqa: BLE001
            message = f"Warning: Could not load bundle '{bundle_name}': {exc}"
            if console:
                console.print(f"[yellow]{message}[/yellow]")
            else:
                logger.warning(message)

    if provider_overrides and not provider_applied_via_config:
        config["providers"] = provider_overrides

    # 3. Apply merged settings (user → project → local)
    merged_settings = config_manager.get_merged_settings()

    modules_config = merged_settings.get("modules", {})
    settings_overlay: dict[str, Any] = {}

    for key in ("tools", "hooks", "agents"):
        if key in modules_config:
            settings_overlay[key] = modules_config[key]

    if settings_overlay:
        config = deep_merge(config, settings_overlay)

    # 4. Apply CLI overrides
    if cli_config:
        config = deep_merge(config, cli_config)

    # 5. Expand environment variables
    return expand_env_vars(config)


def _ensure_debug_defaults(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure debug defaults are present when using provider overrides directly.

    When a provider-agnostic bundle (like foundation) uses provider overrides
    from user settings, those settings typically lack debug flags since
    configure_provider() doesn't add them. This function injects sensible
    defaults for observability:
    - debug: true (enables INFO-level llm:request/response summaries)
    - raw_debug: true (enables complete API I/O for llm:request:raw/response:raw)

    Users who explicitly set debug: false will have that respected (we only
    set defaults, not overrides).

    Args:
        providers: Provider configurations from user settings.

    Returns:
        Provider configurations with debug defaults injected.
    """
    result = []
    for provider in providers:
        if isinstance(provider, dict):
            provider_copy = provider.copy()
            config = provider_copy.get("config", {})
            if isinstance(config, dict):
                config = config.copy()
                # Only set defaults if not explicitly configured
                if "debug" not in config:
                    config["debug"] = True
                if "raw_debug" not in config:
                    config["raw_debug"] = True
                provider_copy["config"] = config
            result.append(provider_copy)
        else:
            result.append(provider)
    return result


def _apply_provider_overrides(
    providers: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply provider overrides to bundle providers.

    Merges override configs into matching providers by module ID.
    """
    if not overrides:
        return providers

    # Build lookup for overrides by module ID
    override_map = {}
    for override in overrides:
        if isinstance(override, dict) and "module" in override:
            override_map[override["module"]] = override

    # Apply overrides to matching providers
    result = []
    for provider in providers:
        if isinstance(provider, dict) and provider.get("module") in override_map:
            # Merge override into provider
            merged = merge_module_items(provider, override_map[provider["module"]])
            result.append(merged)
        else:
            result.append(provider)

    return result


def _apply_hook_overrides(
    hooks: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply hook overrides to bundle hooks.

    Merges override configs into matching hooks by module ID.
    This enables settings like ntfy topic for hooks-notify-push
    to be applied from user settings.

    Args:
        hooks: List of hook configurations from bundle
        overrides: List of hook override dicts with module and config keys

    Returns:
        Merged list of hook configurations
    """
    if not overrides:
        return hooks

    # Build lookup for overrides by module ID
    override_map = {}
    for override in overrides:
        if isinstance(override, dict) and "module" in override:
            override_map[override["module"]] = override

    # Apply overrides to matching hooks
    result = []
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("module") in override_map:
            override = override_map[hook["module"]]
            # Merge the hook-level fields first
            merged = merge_module_items(hook, override)
            # Then merge configs (simple override, no special union logic needed for hooks)
            base_config = hook.get("config", {}) or {}
            override_config = override.get("config", {}) or {}
            if base_config or override_config:
                merged["config"] = {**base_config, **override_config}
            result.append(merged)
        else:
            result.append(hook)

    return result


def _apply_tool_overrides(
    tools: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply tool overrides to bundle tools.

    Merges override configs into matching tools by module ID.
    This enables settings like allowed_write_paths for tool-filesystem
    to be applied from user settings.

    Permission fields (allowed_write_paths, allowed_read_paths) are UNIONED
    rather than replaced, so session-scoped paths ADD to bundle defaults.

    Policy: Current working directory (".") is always included in allowed_write_paths
    for tool-filesystem, ensuring users can always write within their project.
    """
    if not overrides:
        return _ensure_cwd_in_write_paths(tools)

    # Build lookup for overrides by module ID
    override_map = {}
    for override in overrides:
        if isinstance(override, dict) and "module" in override:
            override_map[override["module"]] = override

    # Apply overrides to matching tools
    result = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("module") in override_map:
            override = override_map[tool["module"]]
            # Merge the tool-level fields first
            merged = merge_module_items(tool, override)
            # Then merge configs with permission field union policy
            base_config = tool.get("config", {}) or {}
            override_config = override.get("config", {}) or {}
            if base_config or override_config:
                merged["config"] = merge_tool_configs(base_config, override_config)
            result.append(merged)
        else:
            result.append(tool)

    # Add any new tools from overrides that aren't in the base
    existing_modules = {t.get("module") for t in tools if isinstance(t, dict)}
    for override in overrides:
        if (
            isinstance(override, dict)
            and override.get("module") not in existing_modules
        ):
            result.append(override)

    return _ensure_cwd_in_write_paths(result)


def _ensure_cwd_in_write_paths(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure current working directory is always in allowed_write_paths for tool-filesystem.

    This is a CLI policy decision: users should always be able to write within their
    current working directory and its subdirectories. Without this, explicit paths in
    settings.yaml would completely replace the module's default, locking users out of
    their own project directories.

    Args:
        tools: List of tool configurations

    Returns:
        Tools with "." guaranteed in tool-filesystem's allowed_write_paths
    """
    result = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("module") == "tool-filesystem":
            tool = tool.copy()
            config = (tool.get("config") or {}).copy()
            paths = list(config.get("allowed_write_paths", []))
            if "." not in paths:
                paths.insert(0, ".")
            config["allowed_write_paths"] = paths
            tool["config"] = config
        result.append(tool)
    return result


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge dictionaries with special handling for module lists."""
    result = base.copy()

    module_list_keys = {"providers", "tools", "hooks", "agents"}

    for key, value in overlay.items():
        if key in module_list_keys and key in result:
            if isinstance(result[key], list) and isinstance(value, list):
                result[key] = _merge_module_lists(result[key], value)
            else:
                result[key] = value
        elif (
            key in result and isinstance(result[key], dict) and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def _merge_module_lists(
    base_modules: list[dict[str, Any]], overlay_modules: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Merge module lists on module ID, with deep merging.

    Delegates to canonical merger.merge_module_items for DRY compliance.
    Merges module lists by module ID with deep merging.
    """
    # Build dict by ID for efficient lookup
    result_dict: dict[str, dict[str, Any]] = {}

    # Add all base modules
    for module in base_modules:
        if isinstance(module, dict) and "module" in module:
            result_dict[module["module"]] = module

    # Merge or add overlay modules
    for module in overlay_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module["module"]
            if module_id in result_dict:
                # Module exists in base - deep merge using canonical function
                result_dict[module_id] = merge_module_items(
                    result_dict[module_id], module
                )
            else:
                # New module in overlay - add it
                result_dict[module_id] = module

    # Return as list, preserving base order + new overlays
    result = []
    seen_ids: set[str] = set()

    for module in base_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module["module"]
            if module_id not in seen_ids:
                result.append(result_dict[module_id])
                seen_ids.add(module_id)

    for module in overlay_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module["module"]
            if module_id not in seen_ids:
                result.append(module)
                seen_ids.add(module_id)

    return result


ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?}")


def expand_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """Expand ${VAR} references within configuration values."""

    def replace_value(value: Any) -> Any:
        if isinstance(value, str):
            return ENV_PATTERN.sub(_replace_match, value)
        if isinstance(value, dict):
            return {k: replace_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace_value(item) for item in value]
        return value

    def _replace_match(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")

    return replace_value(config)


def inject_user_providers(config: dict, prepared_bundle: "PreparedBundle") -> None:
    """Inject user-configured providers into bundle's mount plan.

    For provider-agnostic bundles (like foundation), the bundle provides mechanism
    (tools, agents, context) while the app layer provides policy (which provider).

    This function merges the user's provider settings from resolve_app_config()
    into the bundle's mount_plan before session creation.

    Args:
        config: App configuration dict containing "providers" key
        prepared_bundle: PreparedBundle instance to inject providers into

    Note:
        Only injects if bundle has no providers defined (provider-agnostic design).
        Bundles with explicit providers are preserved unchanged.
    """
    if "providers" in config and not prepared_bundle.mount_plan.get("providers"):
        prepared_bundle.mount_plan["providers"] = config["providers"]


def _build_modes_behaviors() -> list[str]:
    """Return modes behavior URIs for composition.

    Modes are always available - users choose to use /mode commands or not.
    No enable/disable needed since modes have no cost when unused.

    Returns:
        List containing the modes behavior URI.
    """
    return [
        # Only load the behavior, NOT the root bundle (which includes foundation)
        "git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=behaviors/modes.yaml",
    ]


def _build_notification_behaviors(
    notifications_config: dict[str, Any] | None,
) -> list[str]:
    """Build list of notification behavior URIs based on settings.

    Notifications are an app-level policy. Rather than injecting hooks after
    bundle preparation, we compose notification behavior bundles BEFORE prepare()
    so their modules get properly downloaded and installed.

    Args:
        notifications_config: Dict from settings.yaml config.notifications section

    Returns:
        List of behavior bundle URIs to compose onto the main bundle.
        Empty list if no notifications are enabled.

    Expected config structure:
        notifications:
          desktop:
            enabled: true
            ...
          push:
            enabled: true
            ...
    """
    if not notifications_config:
        return []

    behaviors: list[str] = []

    # Check if any notification type is enabled
    desktop_config = notifications_config.get("desktop", {})
    desktop_enabled = desktop_config.get("enabled", False)

    push_config = notifications_config.get("push", {})
    ntfy_config = notifications_config.get("ntfy", {})
    push_enabled = push_config.get("enabled", False) or ntfy_config.get(
        "enabled", False
    )

    # If any notification is enabled, add the ROOT bundle first.
    # This ensures the root bundle gets cached with proper SHA metadata,
    # fixing the "unknown" version issue during `amplifier update`.
    # The root bundle is a minimal marker that just identifies the repo;
    # the actual functionality comes from the subdirectory behaviors below.
    if desktop_enabled or push_enabled:
        behaviors.append(
            "git+https://github.com/microsoft/amplifier-bundle-notify@main"
        )

    # Desktop notifications behavior
    if desktop_enabled:
        behaviors.append(
            "git+https://github.com/microsoft/amplifier-bundle-notify@main#subdirectory=behaviors/desktop-notifications.yaml"
        )

    # Push notifications behavior (includes desktop as a dependency for the event)
    # Support both "push:" and "ntfy:" config keys for convenience
    if push_enabled:
        behaviors.append(
            "git+https://github.com/microsoft/amplifier-bundle-notify@main#subdirectory=behaviors/push-notifications.yaml"
        )

    return behaviors


async def resolve_config_async(
    *,
    bundle_name: str | None = None,
    app_settings: AppSettings,
    console: Console | None = None,
    session_id: str | None = None,
    project_slug: str | None = None,
) -> tuple[dict[str, Any], "PreparedBundle | None"]:
    """Unified config resolution (async) - THE golden path for all config loading.

    This is the SINGLE source of truth for resolving configuration.
    All code paths (run, continue, session resume, tool commands) should use this.

    Use this async version when already in an async context (e.g., tool.py).
    Use resolve_config() for synchronous contexts (e.g., click commands).

    Args:
        bundle_name: Bundle to load (defaults to 'foundation' if not specified)
        app_settings: Application settings
        console: Optional console for output
        session_id: Optional session ID for session-scoped tool overrides
        project_slug: Optional project slug (required if session_id provided)

    Returns:
        Tuple of (config_data dict, PreparedBundle)
    """
    if bundle_name:
        # Bundle mode: use resolve_bundle_config which handles:
        # - Git module downloads
        # - Dependency installation (install_deps=True by default)
        # - Bundle preparation
        config_data, prepared_bundle = await resolve_bundle_config(
            bundle_name=bundle_name,
            app_settings=app_settings,
            console=console,
            session_id=session_id,
            project_slug=project_slug,
        )
        return config_data, prepared_bundle
    else:
        default_bundle = "foundation"
        if console:
            console.print(
                f"[dim]No bundle specified, using default: {default_bundle}[/dim]"
            )
        config_data, prepared_bundle = await resolve_bundle_config(
            bundle_name=default_bundle,
            app_settings=app_settings,
            console=console,
            session_id=session_id,
            project_slug=project_slug,
        )
        return config_data, prepared_bundle


def resolve_config(
    *,
    bundle_name: str | None = None,
    app_settings: AppSettings,
    console: Console | None = None,
    session_id: str | None = None,
    project_slug: str | None = None,
) -> tuple[dict[str, Any], "PreparedBundle | None"]:
    """Unified config resolution (sync wrapper) - THE golden path for all config loading.

    Synchronous wrapper around resolve_config_async() for use in click commands.
    For async contexts, use resolve_config_async() directly.

    Args:
        bundle_name: Bundle to load (defaults to 'foundation' if not specified)
        app_settings: Application settings
        console: Optional console for output
        session_id: Optional session ID for session-scoped tool overrides
        project_slug: Optional project slug (required if session_id provided)

    Returns:
        Tuple of (config_data dict, PreparedBundle)
    """
    import gc

    # Suppress asyncio warnings that occur when httpx.AsyncClient instances are
    # garbage collected after their event loop closes. This happens when provider
    # SDKs are instantiated during first-run wizard (init flow) - their internal
    # httpx clients persist and fail to clean up when THIS asyncio.run() closes.
    # The warning is cosmetic (session works fine) but confusing for new users.
    asyncio_logger = logging.getLogger("asyncio")
    original_level = asyncio_logger.level
    asyncio_logger.setLevel(logging.CRITICAL)
    try:
        result = asyncio.run(
            resolve_config_async(
                bundle_name=bundle_name,
                app_settings=app_settings,
                console=console,
                session_id=session_id,
                project_slug=project_slug,
            )
        )
        # Force GC while logger is suppressed to clean up orphaned httpx clients
        gc.collect()
        return result
    finally:
        asyncio_logger.setLevel(original_level)


__all__ = [
    "resolve_config",
    "resolve_config_async",
    "resolve_app_config",
    "resolve_bundle_config",
    "deep_merge",
    "expand_env_vars",
    "inject_user_providers",
    "_apply_provider_overrides",
    "_ensure_debug_defaults",
    "_build_notification_behaviors",
]
