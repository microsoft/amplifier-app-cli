"""Configuration assembly utilities for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING
from typing import Any

from rich.console import Console

from ..lib.app_settings import AppSettings
from ..lib.legacy import compile_profile_to_mount_plan
from ..lib.legacy import merge_module_items

if TYPE_CHECKING:
    from amplifier_foundation import BundleRegistry
    from amplifier_foundation.bundle import PreparedBundle

logger = logging.getLogger(__name__)


async def resolve_bundle_config(
    bundle_name: str,
    app_settings: AppSettings,
    agent_loader,
    console: Console | None = None,
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
        agent_loader: Agent loader for resolving agent metadata.
        console: Optional console for status messages.

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

    # Load and prepare bundle (downloads modules from git sources)
    prepared = await load_and_prepare_bundle(bundle_name, discovery)

    # Get the mount plan from the prepared bundle
    bundle_config = prepared.mount_plan

    # Load full agent metadata via agent_loader (for descriptions)
    if bundle_config.get("agents") and agent_loader:
        loaded_agents = {}
        for agent_name in bundle_config["agents"]:
            try:
                # Try to resolve agent from bundle's base_path first
                # This handles namespaced names like "foundation:bug-hunter"
                agent_path = prepared.bundle.resolve_agent_path(agent_name)
                if agent_path:
                    agent = agent_loader.load_agent_from_path(agent_path, agent_name)
                else:
                    # Fall back to general agent resolution
                    agent = agent_loader.load_agent(agent_name)
                loaded_agents[agent_name] = agent.to_mount_plan_fragment()
            except Exception:  # noqa: BLE001
                # Keep stub if agent loading fails
                loaded_agents[agent_name] = bundle_config["agents"][agent_name]
        bundle_config["agents"] = loaded_agents

    # Apply provider overrides
    provider_overrides = app_settings.get_provider_overrides()
    if provider_overrides:
        if bundle_config.get("providers"):
            # Bundle has providers - merge overrides with existing
            bundle_config["providers"] = _apply_provider_overrides(bundle_config["providers"], provider_overrides)
        else:
            # Bundle has no providers (e.g., provider-agnostic foundation bundle)
            # Use overrides directly, but inject sensible debug defaults
            # This ensures observability when using provider-agnostic bundles
            bundle_config["providers"] = _ensure_debug_defaults(provider_overrides)

    if console:
        console.print(f"[dim]Bundle '{bundle_name}' prepared successfully[/dim]")

    # Expand environment variables (same as resolve_app_config)
    # IMPORTANT: Must expand BEFORE syncing to mount_plan, so ${ANTHROPIC_API_KEY} etc. become actual values
    bundle_config = expand_env_vars(bundle_config)

    # CRITICAL: Sync providers to prepared.mount_plan so create_session() uses them
    # prepared.mount_plan is what create_session() uses, not bundle_config
    # This must happen AFTER env var expansion so API keys are actual values, not "${VAR}" literals
    if provider_overrides:
        prepared.mount_plan["providers"] = bundle_config["providers"]

    return bundle_config, prepared


def resolve_app_config(
    *,
    config_manager,
    profile_loader,
    agent_loader,
    app_settings: AppSettings,
    cli_config: dict[str, Any] | None = None,
    profile_override: str | None = None,
    bundle_name: str | None = None,
    bundle_registry: BundleRegistry | None = None,
    console: Console | None = None,
) -> dict[str, Any]:
    """Resolve configuration with precedence, returning a mount plan dictionary.

    Configuration can come from either:
    - A profile (traditional approach via profile_loader)
    - A bundle (new approach via bundle_registry)

    If bundle_name is specified and bundle_registry is provided, bundles take
    precedence. Otherwise, falls back to profile-based configuration.
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

    # 2. Apply bundle OR profile (bundle takes precedence if specified)
    provider_applied_via_config = False

    if bundle_name and bundle_registry:
        # Use bundle-based configuration
        try:
            # load() with a name returns a single Bundle (not dict)
            loaded = asyncio.run(bundle_registry.load(bundle_name))
            if isinstance(loaded, dict):
                raise ValueError(f"Expected single bundle, got dict for '{bundle_name}'")
            bundle = loaded
            bundle_config = bundle.to_mount_plan()

            # Load full agent metadata via agent_loader (for descriptions)
            if bundle_config.get("agents") and agent_loader:
                loaded_agents = {}
                for agent_name in bundle_config["agents"]:
                    try:
                        # Try to resolve agent from bundle's base_path first
                        # This handles namespaced names like "foundation:bug-hunter"
                        agent_path = bundle.resolve_agent_path(agent_name)
                        if agent_path:
                            agent = agent_loader.load_agent_from_path(agent_path, agent_name)
                        else:
                            # Fall back to general agent resolution
                            agent = agent_loader.load_agent(agent_name)
                        loaded_agents[agent_name] = agent.to_mount_plan_fragment()
                    except Exception:  # noqa: BLE001
                        # Keep stub if agent loading fails
                        loaded_agents[agent_name] = bundle_config["agents"][agent_name]
                bundle_config["agents"] = loaded_agents

            # Apply provider overrides to bundle config
            if provider_overrides and bundle_config.get("providers"):
                bundle_config["providers"] = _apply_provider_overrides(bundle_config["providers"], provider_overrides)
                provider_applied_via_config = True

            config = deep_merge(config, bundle_config)
        except Exception as exc:  # noqa: BLE001
            message = f"Warning: Could not load bundle '{bundle_name}': {exc}"
            if console:
                console.print(f"[yellow]{message}[/yellow]")
            else:
                logger.warning(message)
    else:
        # Use profile-based configuration (traditional approach)
        active_profile_name = profile_override or config_manager.get_active_profile()

        if active_profile_name:
            try:
                profile = profile_loader.load_profile(active_profile_name)
                profile = app_settings.apply_provider_overrides_to_profile(profile, provider_overrides)

                profile_config = compile_profile_to_mount_plan(profile, agent_loader=agent_loader)  # type: ignore[call-arg]
                config = deep_merge(config, profile_config)
                provider_applied_via_config = bool(provider_overrides)
            except Exception as exc:  # noqa: BLE001
                message = f"Warning: Could not load profile '{active_profile_name}': {exc}"
                if console:
                    console.print(f"[yellow]{message}[/yellow]")
                else:
                    logger.warning(message)

    # If we have overrides but no config applied them (no bundle/profile or failure), apply directly
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


def _apply_provider_overrides(providers: list[dict[str, Any]], overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
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
    See amplifier_profiles.merger for complete merge strategy documentation.
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
                result_dict[module_id] = merge_module_items(result_dict[module_id], module)
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
    if config.get("providers") and not prepared_bundle.mount_plan.get("providers"):
        prepared_bundle.mount_plan["providers"] = config["providers"]


__all__ = [
    "resolve_app_config",
    "resolve_bundle_config",
    "deep_merge",
    "expand_env_vars",
    "inject_user_providers",
    "_apply_provider_overrides",
    "_ensure_debug_defaults",
]
