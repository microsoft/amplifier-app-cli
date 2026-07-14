"""Configuration assembly utilities for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from typing import Any

from rich.console import Console

from ..lib.merge_utils import _normalize_module_entry
from ..lib.settings import AppSettings
from ..lib.settings import get_custom_routing_dir
from .config_behaviors import _build_modes_behaviors
from .config_behaviors import _build_notification_behaviors
from .config_behaviors import _format_progress
from .config_merge import _merge_module_lists as _merge_module_lists
from .config_merge import deep_merge
from .config_merge import expand_env_vars
from .config_policies import _apply_hook_overrides
from .config_policies import _apply_tool_overrides
from .config_policies import _ensure_cli_hook_policies
from .config_policies import _ensure_cli_tool_policies
from .config_policies import _ensure_cwd_in_write_paths as _ensure_cwd_in_write_paths
from .config_policies import _ensure_default_skills_dirs as _ensure_default_skills_dirs
from .config_policies import (
    _ensure_streaming_ui_thinking_default as _ensure_streaming_ui_thinking_default,
)
from .config_providers import _ensure_raw_defaults
from .config_providers import _sync_overrides_to_bundle
from .config_providers import apply_provider_overrides
from .config_providers import inject_user_providers
from .config_providers import map_provider_ids_to_instance_ids


if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle


def _apply_config_overrides_to_section(
    section: list[Any], config_overrides: dict[str, Any]
) -> list[Any]:
    """Apply module config overrides without mutating untouched entries."""
    if not section or not config_overrides:
        return section

    result: list[Any] = []
    for item in section:
        normalized = _normalize_module_entry(item)
        module_id = normalized.get("module") if normalized is not None else None
        override = config_overrides.get(module_id) if module_id else None
        if normalized is None or not override:
            result.append(item)
            continue
        merged = dict(normalized)
        merged["config"] = deep_merge(normalized.get("config") or {}, override)
        result.append(merged)
    return result


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

    # Set up progress spinner for bundle preparation
    status = None
    if console:
        status = console.status(
            f"[dim]Preparing bundle '{bundle_name}'...[/dim]",
            spinner="dots",
        )
        status.start()

    def _on_progress(action: str, detail: str) -> None:
        if status:
            label = _format_progress(action, detail)
            status.update(f"[dim]Preparing '{bundle_name}': {label}[/dim]")

    try:
        # Build behavior URIs from app-level settings
        # These are app-level policies: compose behavior bundles before prepare()
        # so modules get properly downloaded and installed via normal bundle machinery
        compose_behaviors: list[str] = []

        # Modes system (runtime behavior overlays like /mode plan, /mode review)
        # Always available - users choose to use /mode commands or not
        compose_behaviors.extend(_build_modes_behaviors())

        # Notification behaviors (desktop and push notifications). The flags
        # object is the single source of truth for "is this enabled?" — the
        # hook-override emitter in AppSettings.get_notification_hook_overrides()
        # reads the same flags so the two paths cannot disagree.
        compose_behaviors.extend(
            _build_notification_behaviors(app_settings.get_notification_flags())
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
            if isinstance(provider, dict)
            and "module" in provider
            and "source" in provider
        }

        # Merge all source overrides with proper precedence:
        # sources.modules (general) < overrides.<id>.source (specific) < config.providers[].source (most specific)
        combined_sources = {**module_sources, **source_overrides, **provider_sources}

        # Get bundle source overrides from settings (sources.bundles in settings.yaml)
        bundle_sources = app_settings.get_bundle_sources()

        # Load and prepare bundle (downloads modules from git sources)
        # If compose_behaviors is provided, those behaviors are composed onto the bundle
        # BEFORE prepare() runs, so their modules get installed correctly
        # If combined_sources is provided, module sources are resolved before download
        prepared = await load_and_prepare_bundle(
            bundle_name,
            discovery,
            compose_behaviors=compose_behaviors if compose_behaviors else None,
            source_overrides=combined_sources if combined_sources else None,
            bundle_source_overrides=bundle_sources if bundle_sources else None,
            progress_callback=_on_progress if status else None,
        )

        # Load full agent metadata from .md files (for descriptions)
        # Foundation handles this via load_agent_metadata() after source_base_paths is populated
        prepared.bundle.load_agent_metadata()

        # Get the mount plan from the prepared bundle (now includes agent descriptions)
        bundle_config = prepared.mount_plan
    finally:
        if status:
            status.stop()

    # ── General config overrides ──────────────────────────────────────────
    # The overrides.<id>.config section in settings.yaml provides a single
    # consistent path for overriding ANY module's config — providers, tools,
    # and hooks alike.  Applied BEFORE the dedicated override sections
    # (config.providers[], modules.tools[], config.notifications.*) so that
    # those more-specific sections take precedence on overlapping keys. Module
    # identity is independent of mount location, so apply the same overrides to
    # agent-scoped declarations as well as the root mount plan.
    config_overrides = app_settings.get_config_overrides()
    if config_overrides:
        for section_key in ("providers", "tools", "hooks"):
            section = bundle_config.get(section_key)
            if not section:
                continue
            bundle_config[section_key] = _apply_config_overrides_to_section(
                section, config_overrides
            )

        agents = bundle_config.get("agents")
        if isinstance(agents, dict):
            for agent in agents.values():
                if not isinstance(agent, dict):
                    continue
                for section_key in ("providers", "tools", "hooks"):
                    section = agent.get(section_key)
                    if section:
                        agent[section_key] = _apply_config_overrides_to_section(
                            section, config_overrides
                        )

    # Apply provider overrides
    provider_overrides = app_settings.get_provider_overrides()
    if provider_overrides:
        if bundle_config.get("providers"):
            # Bundle has providers - merge overrides with existing
            bundle_config["providers"] = apply_provider_overrides(
                bundle_config["providers"], provider_overrides
            )
        else:
            # Bundle has no providers (e.g., provider-agnostic foundation bundle)
            # Use overrides directly, but inject sensible raw payload default.
            # This ensures llm:request/response events carry full payloads for
            # observability when using provider-agnostic bundles.
            bundle_config["providers"] = _ensure_raw_defaults(provider_overrides)

    if bundle_config.get("providers"):
        bundle_config["providers"] = _ensure_raw_defaults(bundle_config["providers"])

    # Map settings 'id' → mount plan 'instance_id' so the kernel can identify
    # provider instances for multi-instance routing.
    # Settings YAML uses 'id'; kernel reads 'instance_id' — this bridges the gap.
    if bundle_config.get("providers"):
        bundle_config["providers"] = map_provider_ids_to_instance_ids(
            bundle_config["providers"]
        )

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
    elif bundle_config.get("tools"):
        # No user overrides, but still apply CLI tool policies
        # (e.g., cwd in write paths, default skills dirs)
        bundle_config["tools"] = _ensure_cli_tool_policies(bundle_config["tools"])

    # Apply hook overrides from notification settings
    # This maps config.notifications.ntfy.* to hooks-notify-push config etc.
    hook_overrides = app_settings.get_notification_hook_overrides()

    # Routing matrix config injection
    routing_config = app_settings.get_routing_config()
    if routing_config:
        routing_hook_override: dict[str, Any] = {
            "module": "hooks-routing",
            "config": {},
        }
        if "matrix" in routing_config:
            routing_hook_override["config"]["default_matrix"] = routing_config["matrix"]
        if "overrides" in routing_config:
            routing_hook_override["config"]["overrides"] = routing_config["overrides"]
        custom_routing_dir = get_custom_routing_dir()
        if custom_routing_dir.is_dir():
            routing_hook_override["config"]["custom_routing_dirs"] = [
                str(custom_routing_dir)
            ]
        # Change A: Enrich with any extra keys from overrides.hooks-routing.config.
        # Routing-section keys (default_matrix, overrides) always take precedence over
        # whatever came from the general config overrides block, so the routing-built keys
        # are written AFTER the extra keys in the merge — later keys win in {**a, **b}.
        hooks_routing_extra = {
            k: v
            for k, v in config_overrides.get("hooks-routing", {}).items()
            if k not in ("default_matrix", "overrides")
        }
        routing_hook_override["config"] = {
            **hooks_routing_extra,
            **routing_hook_override["config"],
        }
        if routing_hook_override["config"]:
            hook_overrides.append(routing_hook_override)

    # Apply hook overrides: merge in-place for hooks already in the bundle, and
    # append any overrides whose module is absent from the bundle hooks list.
    # Guard now initialises hooks to [] when absent so the append-missing path can fire
    # even for bundles that ship with no hooks section at all.
    if hook_overrides:
        bundle_config.setdefault("hooks", [])
        bundle_config["hooks"] = _apply_hook_overrides(
            bundle_config["hooks"], hook_overrides
        )

    if bundle_config.get("hooks"):
        bundle_config["hooks"] = _ensure_cli_hook_policies(
            bundle_config["hooks"], config_overrides
        )

    if console:
        console.print(f"[dim]Bundle '{bundle_name}' prepared successfully[/dim]")

    # Expand environment variables
    # IMPORTANT: Must expand BEFORE syncing to mount_plan, so ${ANTHROPIC_API_KEY} etc. become actual values
    bundle_config = expand_env_vars(bundle_config)

    # CRITICAL: Sync providers, tools, and hooks to prepared.mount_plan so create_session() uses them
    # prepared.mount_plan is what create_session() uses, not bundle_config
    # This must happen AFTER env var expansion so API keys are actual values, not "${VAR}" literals
    if bundle_config.get("providers"):
        prepared.mount_plan["providers"] = bundle_config["providers"]
    # Always sync tools — CLI policy functions (cwd in write paths, default skills dirs)
    # modify bundle_config["tools"] even without user tool_overrides
    if bundle_config.get("tools"):
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
    _sync_overrides_to_bundle(
        prepared, bundle_config, sync_tools=bool(bundle_config.get("tools"))
    )

    # Note: Notification hooks are now composed via compose_behaviors parameter
    # to load_and_prepare_bundle(), so they get properly installed during prepare().
    # The behavior bundles handle root-session-only logic internally via parent_id check.

    return bundle_config, prepared


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
        bundle_name: Bundle to load (defaults to 'anchors' if not specified)
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
        default_bundle = "anchors"
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
        bundle_name: Bundle to load (defaults to 'anchors' if not specified)
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
    "resolve_bundle_config",
    "_apply_config_overrides_to_section",
    "deep_merge",
    "expand_env_vars",
    "inject_user_providers",
    "apply_provider_overrides",
    "_ensure_raw_defaults",
    "map_provider_ids_to_instance_ids",
]
