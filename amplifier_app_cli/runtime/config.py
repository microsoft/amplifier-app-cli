"""Configuration assembly utilities for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from amplifier_core.utils.truncate import SENSITIVE_KEYS
from rich.console import Console

from ..lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES
from ..lib.settings import AppSettings, NotificationFlags, get_custom_routing_dir
from ..lib.merge_utils import merge_module_items
from ..lib.merge_utils import merge_tool_configs
from ..lib.merge_utils import _normalize_module_entry
from ..provider_loader import get_provider_info


if TYPE_CHECKING:
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

        # CLI self-expertise (app-cli:cli-expert + a thin awareness pointer).
        # Always composed: every session should be able to answer "how does
        # this CLI work?" by delegating rather than guessing. Sourced from the
        # installed package on disk, never a git URI, so the expert's docs
        # always match the running CLI version.
        compose_behaviors.extend(_build_app_cli_behaviors())

        # Skills system (tool-skills module + curated Microsoft skills
        # collection + visibility config + context instructions). Always
        # composed, regardless of base bundle, so a tool-skills entry always
        # exists for _ensure_default_skills_dirs() to append the CLI's own
        # packaged skills dir onto.
        compose_behaviors.extend(_build_skills_behaviors())

        # Wayfinder (in-session guidance channel). Always composed so every
        # user gets the public wayfinder channel by default -- not just those
        # who add an internal app bundle (e.g. made-support) that also brings
        # it. Only the PUBLIC behavior is composed here; internal content packs
        # ride separately via made-support's own hooks-wayfinder content_sources
        # config, which layers on AFTER this (app bundles compose last), so no
        # internal content leaks into the public default.
        compose_behaviors.extend(_build_wayfinder_behaviors())

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
    # those more-specific sections take precedence on overlapping keys.
    #
    # overrides.<id>.config is keyed by module IDENTITY, not by mount
    # location -- so it must reach a module wherever it's declared, including
    # inside a sub-agent's own frontmatter (config["agents"][<name>]["tools"]
    # etc.), not just the root bundle's providers/tools/hooks lists. Without
    # this, a tool an agent introduces that never appears in the root lists
    # (e.g. a query tool declared only in an agent's tools: section) never
    # receives its override and silently falls back to module defaults / env
    # vars.
    config_overrides = app_settings.get_config_overrides()
    if config_overrides:
        for section_key in ("providers", "tools", "hooks"):
            section = bundle_config.get(section_key)
            if not section:
                continue
            bundle_config[section_key] = _apply_config_overrides_to_section(
                section, config_overrides
            )

        agents_section = bundle_config.get("agents")
        if isinstance(agents_section, dict):
            for agent_cfg in agents_section.values():
                if not isinstance(agent_cfg, dict):
                    continue
                for section_key in ("providers", "tools", "hooks"):
                    agent_section = agent_cfg.get(section_key)
                    if not agent_section:
                        continue
                    agent_cfg[section_key] = _apply_config_overrides_to_section(
                        agent_section, config_overrides
                    )

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
            # Use overrides directly, but inject sensible raw payload default.
            # This ensures llm:request/response events carry full payloads for
            # observability when using provider-agnostic bundles.
            bundle_config["providers"] = _ensure_raw_defaults(provider_overrides)

    # Map settings 'id' → mount plan 'instance_id' so the kernel can identify
    # provider instances for multi-instance routing.
    # Settings YAML uses 'id'; kernel reads 'instance_id' — this bridges the gap.
    if bundle_config.get("providers"):
        bundle_config["providers"] = _map_id_to_instance_id(bundle_config["providers"])

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
        # Always advertise the user's custom routing dir so a matrix named by
        # routing.matrix that ONLY exists at get_custom_routing_dir() (e.g.
        # written by `amplifier init`/`amplifier routing save`) is resolvable
        # at runtime, not just listable via `amplifier routing list`. This is
        # the fix for "Matrix file not found -- routing disabled" when the
        # matrix genuinely exists in ~/.amplifier/routing/.
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
        # A hook the bundle does not already carry needs a `source`, or the
        # module cannot load at all. `_apply_hook_overrides` APPENDS an
        # override whose module is absent from the bundle's hooks list, and
        # the appended dict is the mount-plan entry verbatim -- so without a
        # source here, `hooks-routing` reaches the session as
        # `{"module": "hooks-routing", "config": {...}}` and the kernel fails
        # it by name at mount:
        #
        #   Failed to load hook 'hooks-routing': Module 'hooks-routing' not
        #   found in prepared bundle. Available modules: [...]
        #
        # Measured on a bundle that does not include routing-matrix
        # (`anchors-amp-dev`) with `routing.matrix: anthropic` in
        # settings.yaml: the routing banner left the system prompt, the
        # delegate tool dropped its `model_role` parameter (no
        # `model_role_resolver` capability), and every `model_role` fell
        # through to the default provider. Silent -- the user sees no
        # routing, not an error.
        #
        # Only attached when the bundle has no hooks-routing of its own: the
        # in-place merge path in `_apply_hook_overrides` lets the override's
        # top-level keys win (merge_module_items: "child overrides parent,
        # including 'source'"), so attaching unconditionally would clobber a
        # bundle's deliberately pinned source. The URI is the routing-matrix
        # bundle's canonical remote from WELL_KNOWN_BUNDLES -- the same one
        # `amplifier routing` and `amplifier update` fetch -- narrowed to the
        # hook module's subdirectory, matching the bundle's own
        # behaviors/routing.yaml declaration.
        if not _bundle_declares_hook(bundle_config.get("hooks"), "hooks-routing"):
            routing_hook_override["source"] = _routing_hook_source()
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

    if console:
        console.print(f"[dim]Bundle '{bundle_name}' prepared successfully[/dim]")

    # Fail loud (before mount) on an unresolved *required* credential
    # placeholder, instead of letting env-var expansion silently turn it
    # into "" and letting the provider module fall back to its own ambient
    # credential -- see _validate_provider_credentials for why this matters
    # for the reuse-or-separate multi-instance flow.
    raw_providers = bundle_config.get("providers")
    if isinstance(raw_providers, list):
        _validate_provider_credentials(raw_providers)

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


def _ensure_raw_defaults(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure raw payload default is present when using provider overrides directly.

    When a provider-agnostic bundle (like foundation) uses provider overrides
    from user settings, those settings typically lack the ``raw`` flag since
    configure_provider() doesn't add it. This function injects a sensible
    default for observability:
    - raw: true (includes full redacted API payload on llm:request/response events)

    Users who explicitly set ``raw: false`` will have that respected (we only
    set a default, not an override).

    Stale flags from the old 3-tier verbosity system (``debug``, ``raw_debug``)
    are stripped unconditionally — providers no longer read them, and leaving
    them in the config causes the ``/config`` display to show misleading keys.

    Args:
        providers: Provider configurations from user settings.

    Returns:
        Provider configurations with ``raw`` default injected and stale
        ``debug``/``raw_debug`` flags removed.
    """
    result = []
    for provider in providers:
        if isinstance(provider, dict):
            provider_copy = provider.copy()
            config = provider_copy.get("config", {})
            if isinstance(config, dict):
                config = config.copy()
                # Remove stale flags from the old 3-tier verbosity system;
                # providers no longer read them.
                config.pop("debug", None)
                config.pop("raw_debug", None)
                # Inject raw: true as the default unless explicitly set.
                if "raw" not in config:
                    config["raw"] = True
                provider_copy["config"] = config
            result.append(provider_copy)
        else:
            result.append(provider)
    return result


def _map_id_to_instance_id(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map 'id' field from settings entries to 'instance_id' in mount plan entries.

    The settings YAML uses 'id' as the provider instance identity field:
        config:
          providers:
            - module: provider-anthropic
              id: anthropic-sonnet    # ← settings uses "id"

    The kernel (amplifier-core) reads 'instance_id' from the mount plan:
        instance_id = provider_config.get("instance_id")  # ← kernel reads "instance_id"

    This function maps 'id' → 'instance_id' for entries that have an explicit 'id'.
    Entries without 'id' are left unchanged — they are treated as the "default" instance
    that mounts under the provider's default name (e.g. "anthropic" for provider-anthropic).
    The kernel's snapshot-based remapping handles the case where a default instance coexists
    with explicitly-named instances.

    Args:
        providers: List of provider config dicts from the assembled mount plan.

    Returns:
        New list of provider dicts with instance_id added where applicable.
        Original dicts are not mutated.
    """
    result = []
    for provider in providers:
        if (
            isinstance(provider, dict)
            and "id" in provider
            and "instance_id" not in provider
        ):
            provider = {**provider, "instance_id": provider["id"]}
        result.append(provider)
    return result


def _apply_config_overrides_to_section(
    section: list[Any], config_overrides: dict[str, Any]
) -> list[Any]:
    """Apply overrides.<module-id>.config to every entry in a module list section.

    Shared by the root ``providers``/``tools``/``hooks`` override loop in
    :func:`resolve_bundle_config` and by the same application to each agent's
    own ``providers``/``tools``/``hooks`` sections (``config["agents"][name]``).
    ``overrides.<id>.config`` is keyed by module identity, not by mount
    location, so it must reach a module wherever it's declared.

    Entries may be bare strings (shorthand for ``{"module": <string>}``) or
    dicts -- the same shapes :func:`merge_module_lists` already tolerates via
    ``_normalize_module_entry``. For each entry:

    - Normalize (read-only) to find its module id. Entries that don't
      normalize to a dict with a ``module`` id are returned unchanged.
    - If there's no matching override, the ORIGINAL entry is returned
      unchanged -- bare strings stay bare, dicts are returned by the same
      reference (no gratuitous copy), so untouched entries are byte-identical.
    - If there is a matching override, a NEW dict entry is produced: the
      existing config (if any) deep-merged with the override (override wins
      on key conflicts), with all other entry keys (``source``, ``module``,
      ...) preserved.

    Args:
        section: A module list (providers/tools/hooks), possibly containing
            bare strings and/or dicts.
        config_overrides: The ``overrides.<id>.config`` map from settings.

    Returns:
        A new list with overrides applied. The original ``section`` list and
        its untouched entries are not mutated.
    """
    if not section or not config_overrides:
        return section

    result: list[Any] = []
    for item in section:
        normalized = _normalize_module_entry(item)
        if normalized is None:
            result.append(item)
            continue
        module_id = normalized.get("module")
        override_cfg = config_overrides.get(module_id) if module_id else None
        if not override_cfg:
            result.append(item)
            continue
        base_cfg = normalized.get("config", {}) or {}
        merged_entry = dict(normalized)
        merged_entry["config"] = deep_merge(base_cfg, override_cfg)
        result.append(merged_entry)
    return result


def _apply_provider_overrides(
    providers: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply provider overrides to bundle providers.

    Merges override configs into matching providers by module ID.
    """
    if not overrides:
        return providers

    # Build lookup for overrides keyed by id-or-module
    override_map = {}
    for override in overrides:
        if isinstance(override, dict) and "module" in override:
            key = override.get("id") or override["module"]
            override_map[key] = override

    # Apply overrides to matching providers
    result = []
    for provider in providers:
        if isinstance(provider, dict):
            key = provider.get("id") or provider.get("module", "")
            if key in override_map:
                merged = merge_module_items(provider, override_map[key])
                result.append(merged)
            else:
                result.append(provider)
        else:
            result.append(provider)

    return result


def _prune_to_secret_keys(value: Any) -> Any | None:
    """Return a copy of ``value`` keeping ONLY secret-bearing branches.

    Companion to ``redact_secrets()`` (amplifier_core.utils.truncate), which
    is what *creates* the problem this solves: persisting a session redacts
    every key in ``SENSITIVE_KEYS`` to the literal ``"[REDACTED]"``.  A resume
    therefore needs to restore exactly those keys from live settings -- and
    nothing else.  Using the SAME key set in both directions is deliberate:
    if redaction ever learns a new secret key, the refresh learns it too, with
    no second list to keep in sync.

    Pruning rules:
      - dict: keep a key outright if its name is a sensitive key; otherwise
        recurse and keep the key only if something secret survives beneath it.
        An empty result is reported as ``None`` (nothing to restore).
      - list: kept WHOLE if any element carries a secret anywhere, else
        dropped. Lists are *replaced* (not merged) by ``deep_merge``, so a
        partially-pruned list would silently truncate the merged result --
        all-or-nothing is the only safe choice.
      - scalars: never secret on their own (only a *key* marks a secret).

    Returns:
        Pruned structure, or None when it holds no secret at any depth.
    """
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, sub_value in value.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
                kept[key] = sub_value
                continue
            pruned = _prune_to_secret_keys(sub_value)
            if pruned is not None:
                kept[key] = pruned
        return kept or None
    if isinstance(value, list):
        if any(_prune_to_secret_keys(item) is not None for item in value):
            return value
        return None
    return None


def narrow_overrides_to_secrets(
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reduce settings overrides to their secret-bearing config keys only.

    WHY THIS EXISTS (model_performance-rc0 / -n1i)
    ---------------------------------------------
    ``resume_sub_session`` re-applies live ``settings.yaml`` provider
    overrides onto a resumed sub-session's PERSISTED mount plan, for one
    stated reason: on-disk metadata has its secrets redacted, so a resumed
    session would otherwise send ``Bearer [REDACTED]``.

    But the merge it used was the full one (``merge_module_items`` ->
    ``deep_merge``, "overlay winning conflicts"), so EVERY settings key --
    not just the secrets -- was re-imposed on the child's own plan.  The
    load-bearing casualty is ``config.priority``: a sub-session spawned with
    ``model_role``/``provider_preferences`` carries ``priority: 0`` on the
    promoted provider, and the settings priority overwrote it.  The resumed
    leg then silently re-resolved to whatever sits at settings priority 0.
    Measured across a 2,078-session archive: 39 of 66 delegate resumes
    changed model across the boundary, 37 of them cheap -> expensive, every
    one reporting ``basis: "priority"`` on both sides (i.e. not a fallback --
    a wipe).  Root sessions were untouched (0 of 179), exactly as the
    mechanism predicts: a root plan has no promotion to lose.

    ``priority`` is not a secret.  Narrowing the override to the keys that
    were actually redacted restores the credential *without* handing settings
    a second, unintended vote on provider resolution.

    Identity keys (``module``, ``id``) are carried through so the override
    still matches its target entry; every other top-level key is dropped, so
    a settings override cannot rewrite ``source`` or any sibling field at
    resume time either.

    NOTE ON SCOPE: this is for the RESUME refresh only.  Root/fresh config
    assembly (``resolve_bundle_config``) still merges overrides in full --
    there the settings ARE the intended source of truth, and there is no
    persisted child promotion to protect.

    Args:
        overrides: Settings override entries (``{module, id?, config}``).

    Returns:
        A new list holding only entries that carry at least one secret, each
        narrowed to its secret-bearing config keys. Entries without secrets
        are dropped entirely (they have nothing to restore).
    """
    narrowed: list[dict[str, Any]] = []
    for override in overrides or []:
        if not isinstance(override, dict) or "module" not in override:
            continue
        config = override.get("config")
        if not isinstance(config, dict):
            continue
        secret_config = _prune_to_secret_keys(config)
        if not secret_config:
            continue
        entry: dict[str, Any] = {
            "module": override["module"],
            "config": secret_config,
        }
        if override.get("id"):
            entry["id"] = override["id"]
        narrowed.append(entry)
    return narrowed


def _routing_hook_source() -> str:
    """Canonical source URI for the ``hooks-routing`` module.

    Derived from the routing-matrix bundle's registered remote so the CLI has
    exactly one place that knows where that bundle lives (``amplifier routing``
    and ``amplifier update`` read the same entry). The ``#subdirectory``
    fragment mirrors the bundle's own ``behaviors/routing.yaml``.
    """
    remote = str(WELL_KNOWN_BUNDLES["routing-matrix"]["remote"])
    return f"{remote}#subdirectory=modules/hooks-routing"


def _bundle_declares_hook(hooks: Any, module_id: str) -> bool:
    """True when *hooks* (a bundle's hooks list, possibly absent) names *module_id*."""
    if not isinstance(hooks, list):
        return False
    return any(isinstance(h, dict) and h.get("module") == module_id for h in hooks)


def _apply_hook_overrides(
    hooks: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply hook overrides to bundle hooks.

    Merges override configs into matching hooks by module ID.
    This enables settings like ntfy topic for hooks-notify-push
    to be applied from user settings.

    Hooks that are present in ``overrides`` but absent from the bundle
    ``hooks`` list are **appended** to the result, mirroring the behaviour
    of :func:`_apply_tool_overrides`.  This means a routing config
    (``hooks-routing``) supplied via settings will reach the session even
    when the active bundle does not pre-register that hook.

    Note on hook execution order: list position does not control execution
    order.  ``hooks-routing`` registers with explicit ``priority`` values
    (5 and 15), so appending at the end of the list is safe.

    Args:
        hooks: List of hook configurations from bundle
        overrides: List of hook override dicts with module and config keys

    Returns:
        Merged list of hook configurations (in-place merges first, then
        any absent hooks appended in override order)
    """
    if not overrides:
        return hooks

    # Build lookup for overrides by module ID
    override_map = {}
    for override in overrides:
        if isinstance(override, dict) and "module" in override:
            override_map[override["module"]] = override

    # Apply overrides to matching hooks (in-place merge path)
    result = []
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("module") in override_map:
            override = override_map[hook["module"]]
            # Merge the hook-level fields first
            merged = merge_module_items(hook, override)
            # Deep-merge configs so nested sub-dicts are merged rather than clobbered.
            base_config = hook.get("config", {}) or {}
            override_config = override.get("config", {}) or {}
            if base_config or override_config:
                merged["config"] = deep_merge(base_config, override_config)
            result.append(merged)
        else:
            result.append(hook)

    # Change B: Append overrides whose module is absent from the original bundle
    # hooks list.  Using the *original* hooks set means a hook that was merged
    # in-place above is NOT in existing_modules and would be double-added — but
    # that cannot happen because the in-place merge path consumed it first, so
    # the set must be built from the original ``hooks`` argument, not ``result``.
    existing_modules = {h.get("module") for h in hooks if isinstance(h, dict)}
    for override in overrides:
        if (
            isinstance(override, dict)
            and override.get("module") not in existing_modules
        ):
            result.append(override)

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
        return _ensure_cli_tool_policies(tools)

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

    return _ensure_cli_tool_policies(result)


def _ensure_cli_tool_policies(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply all CLI policy injections to tool configs.

    Chains all tool-specific policy functions. Each function targets a specific
    tool module and injects CLI-level defaults that the module itself should not
    hardcode (because modules sit below the app layer).
    """
    tools = _ensure_cwd_in_write_paths(tools)
    tools = _ensure_default_skills_dirs(tools)
    return tools


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


def _ensure_default_skills_dirs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure workspace, user, and packaged skill directories are in tool-skills config.

    This is a CLI policy decision: .amplifier/skills/ (workspace) and
    ~/.amplifier/skills/ (user) follow the same project-first, user-second
    convention as bundles, agents, and modules. Without this, when behaviors
    configure explicit remote skill sources, the module's get_default_skills_dirs()
    fallback is bypassed and workspace skills become invisible.

    Also appends the CLI's own packaged skills directory
    (amplifier_app_cli/data/skills/), resolved from the installed package's
    location on disk -- never a git URI. This keeps packaged skills
    version-locked to the installed CLI wheel: a user pinned to CLI v0.1.1
    gets the skill assets that shipped in v0.1.1, not whatever is on a
    branch tip.

    Args:
        tools: List of tool configurations

    Returns:
        Tools with workspace, user, and packaged skill dirs in tool-skills's config.skills
    """
    packaged_skills_dir = Path(__file__).parent.parent / "data" / "skills"
    default_paths = [
        ".amplifier/skills",
        "~/.amplifier/skills",
        str(packaged_skills_dir),
    ]

    result = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("module") == "tool-skills":
            tool = tool.copy()
            config = (tool.get("config") or {}).copy()
            skills = list(config.get("skills", []))
            for path in default_paths:
                if path not in skills:
                    skills.append(path)
            config["skills"] = skills
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

    # Add all base modules, keying by id first, then module name
    for module in base_modules:
        if isinstance(module, dict) and "module" in module:
            key = module.get("id") or module["module"]
            result_dict[key] = module

    # Merge or add overlay modules
    for module in overlay_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module.get("id") or module["module"]
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
            module_id = module.get("id") or module["module"]
            if module_id not in seen_ids:
                result.append(result_dict[module_id])
                seen_ids.add(module_id)

    for module in overlay_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module.get("id") or module["module"]
            if module_id not in seen_ids:
                result.append(module)
                seen_ids.add(module_id)

    return result


ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?}")


def _validate_provider_credentials(providers: list[Any]) -> None:
    """Fail loudly, before session mount, when a provider instance's
    configured credential placeholder resolves to nothing.

    Why this exists: ``expand_env_vars`` (below) treats an unset ``${VAR}``
    as an empty string. Several provider modules treat an empty/absent
    ``api_key`` config value as "not configured" and fall back to their own
    canonical ambient env var (e.g. ``OPENAI_API_KEY``). For a *separate*
    per-instance credential binding left unset for runtime injection (see
    the reuse-or-separate wizard flow in ``commands/provider.py`` /
    ``provider_config_utils.py``, design doc §5.2), that fallback silently
    routes the instance through a *different* account's key -- exactly the
    wrong-account failure the reuse-or-separate flow exists to prevent.
    Raising here turns that into a clear, actionable error at session start
    instead of a silent cross-account credential mixup.

    Only enforced for fields the provider declares as
    ``field_type == "secret"`` AND ``required`` (default True): optional /
    keyless secrets (e.g. a local Chat Completions server with no API key)
    are intentionally left alone, and a placeholder with an inline
    ``${VAR:-default}`` default is also left alone (the default already
    covers "unset").

    App-CLI policy only -- no core, provider-contract, or settings-schema
    changes. When provider metadata can't be loaded (custom/removed
    provider, import error, etc.), validation is skipped for that entry --
    consistent with how the rest of this module already treats a missing
    ``get_provider_info()`` result.
    """
    for entry in providers:
        if not isinstance(entry, dict):
            continue
        module_id = entry.get("module")
        config = entry.get("config")
        if not isinstance(module_id, str) or not isinstance(config, dict):
            continue

        info = get_provider_info(module_id)
        if not info:
            continue

        for field in info.get("config_fields") or []:
            if not isinstance(field, dict) or field.get("field_type") != "secret":
                continue
            if not field.get("required", True):
                continue

            field_id = field.get("id")
            if not field_id:
                continue
            raw_value = config.get(field_id)
            if not isinstance(raw_value, str):
                continue

            match = ENV_PATTERN.fullmatch(raw_value)
            if not match:
                continue
            var_name, default = match.group(1), match.group(2)
            if default is not None:
                # An inline default already covers "unset" -- not our concern.
                continue
            if os.environ.get(var_name):
                continue

            label = entry.get("id") or module_id
            raise ValueError(
                f"Credential environment variable {var_name} for provider "
                f"'{label}' is not set. Set it before starting the "
                f"session; Amplifier will not fall back to another "
                f"provider's credential."
            )


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

    This function merges the user's provider settings from resolve_bundle_config()
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


def _format_progress(action: str, detail: str) -> str:
    """Format a progress callback into a human-readable label for the spinner.

    Maps foundation progress actions to user-friendly descriptions.

    Args:
        action: Progress action (e.g., "loading", "composing", "activating").
        detail: Detail string (e.g., module name, bundle name).

    Returns:
        Human-readable progress label.
    """
    labels = {
        "loading": f"Loading {detail}",
        "composing": f"Composing {detail}",
        "installing_package": f"Installing package {detail}",
        "activating": f"Activating {detail}",
        "installing": f"Installing {detail}",
    }
    return labels.get(action, f"{action}: {detail}")


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


def _build_skills_behaviors() -> list[str]:
    """Return the skills behavior URI for composition.

    Always composes amplifier-bundle-skills' tool-skills module + curated
    Microsoft skills collection + visibility config + context instructions,
    regardless of which base bundle the user selected. This guarantees a
    tool-skills entry always exists in the final merged tools list, so
    _ensure_default_skills_dirs() always has an entry to append the CLI's own
    packaged skills dir onto -- previously that depended on incidentally
    getting tool-skills from whatever other bundle (e.g. foundation) the user
    happened to compose.
    """
    return [
        # Only the behavior, NOT the root bundle.md (which would pull foundation
        # and could clobber the user's system prompt -- same reasoning as
        # _build_modes_behaviors / _build_app_cli_behaviors).
        "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=behaviors/skills.yaml",
    ]


def _build_wayfinder_behaviors() -> list[str]:
    """Return the wayfinder behavior URI for composition.

    Wayfinder is an in-session guidance channel: it surfaces one authored,
    curated tip per session and offers capabilities the user already has,
    always behind an explicit propose -> show -> ack -> act gate. Composed on
    every session so the PUBLIC channel reaches every user by default --
    previously it only reached users who had added an internal app bundle
    (made-support) that happened to bring it.

    Only the PUBLIC behavior is composed here. Internal/team content packs
    (e.g. amplifier-online) are NOT included: they ride separately through
    made-support's own hooks-wayfinder ``content_sources`` config, which
    composes AFTER app bundles and deep-merges its extra content source onto
    this hook -- so nothing internal leaks into the public default.
    """
    return [
        # Only the behavior, NOT the root bundle.md (which would pull foundation
        # and could clobber the user's system prompt -- same reasoning as
        # _build_modes_behaviors / _build_skills_behaviors).
        "git+https://github.com/microsoft/amplifier-bundle-wayfinder@main#subdirectory=behaviors/wayfinder.yaml",
    ]


# The app-cli bundle overlay lives at the REPO ROOT (bundle.md, behaviors/,
# agents/, context/, docs/), matching how amplifier, amplifier-core, and
# amplifier-foundation lay out their overlays.
#
# Those repos are cloned into ~/.amplifier/cache, so their repo-root dirs are
# on disk at runtime. This repo is not -- it installs from a wheel -- so
# pyproject.toml force-includes the same tree into the wheel under
# `amplifier_app_cli/_bundle/`. Both layouts are therefore possible at
# runtime, and _find_app_cli_bundle_root() resolves whichever is present.
_APP_CLI_PACKAGE_DIR = Path(__file__).parent.parent
_APP_CLI_BEHAVIOR_RELPATH = "behaviors/cli-expertise.yaml"


def _find_app_cli_bundle_root() -> Path | None:
    """Locate the app-cli bundle root for this install.

    Two supported layouts, checked in order:

    1. ``<package>/_bundle/`` -- installed wheel (force-included by
       pyproject.toml). Checked first: when present it is authoritative.
    2. ``<package>/..`` -- the repo root in a dev checkout / editable install,
       where ``behaviors/`` sits beside ``amplifier_app_cli/``.

    Probing for the behavior file itself (not just the directory) means a
    half-populated tree is treated as missing rather than silently yielding
    an unloadable URI.

    Returns:
        Bundle root directory, or None if neither layout is present.
    """
    for candidate in (_APP_CLI_PACKAGE_DIR / "_bundle", _APP_CLI_PACKAGE_DIR.parent):
        if (candidate / _APP_CLI_BEHAVIOR_RELPATH).is_file():
            return candidate
    return None


def _build_app_cli_behaviors() -> list[str]:
    """Return the CLI self-expertise behavior URI for composition.

    This wires up ``app-cli:cli-expert`` -- the expert consultant on the CLI
    application itself (provider pinning, slash commands, sessions, context
    loading, output formats, spawn precedence) -- plus a thin always-on
    awareness pointer telling the root session to delegate CLI questions
    rather than answer them from memory.

    Two deliberate choices, both load-bearing:

    1. **Sourced from disk, never a git URI.** The path is computed from this
       package's own location, exactly as ``_ensure_default_skills_dirs()``
       does for packaged skills. A CLI expert pinned at ``@main`` could
       document flags the installed CLI does not have; resolving in-package
       makes that version skew structurally impossible.

    2. **Only the behavior, never the root ``bundle.md``.** ``Bundle.compose()``
       replaces the instruction whenever the composed bundle has a non-empty
       markdown body (foundation ``_dataclass.py``: ``if other.instruction:
       result.instruction = other.instruction``). ``bundle.md`` has a body;
       the behavior YAML does not. Composing the root bundle here would
       silently clobber the user's system prompt.

    The ``file://`` scheme is required -- ``parse_uri()`` only extracts the
    ``#subdirectory=`` fragment for ``file://`` URIs. A bare absolute path
    with a fragment is parsed as a single literal path and fails to resolve.

    Returns:
        Single-element list with the behavior URI.

    Raises:
        RuntimeError: If the bundle overlay is missing from this install.
            This is deliberately fatal rather than a warning-and-skip. The
            behavior composition loop in
            ``lib/bundle_loader/prepare.py`` catches per-behavior load
            failures and continues, so a bad URI returned from here would be
            swallowed and the expert would silently vanish -- exactly the
            failure mode that is hardest to notice and worst to ship. A
            missing overlay means the wheel was built without the
            ``force-include`` block in pyproject.toml, which is a packaging
            regression that should surface on the first run, not in a bug
            report six weeks later.
    """
    bundle_root = _find_app_cli_bundle_root()
    if bundle_root is None:
        searched = " and ".join(
            str(candidate / _APP_CLI_BEHAVIOR_RELPATH)
            for candidate in (
                _APP_CLI_PACKAGE_DIR / "_bundle",
                _APP_CLI_PACKAGE_DIR.parent,
            )
        )
        raise RuntimeError(
            "amplifier-app-cli is missing its bundle overlay "
            f"({_APP_CLI_BEHAVIOR_RELPATH}). Searched: {searched}. "
            "This install cannot provide the app-cli:cli-expert agent. "
            "If this is a built wheel, the [tool.hatch.build.targets.wheel."
            "force-include] block in pyproject.toml is missing or wrong; "
            "if this is a source checkout, the repo-root behaviors/ "
            "directory is absent."
        )

    return [f"file://{bundle_root}#subdirectory={_APP_CLI_BEHAVIOR_RELPATH}"]


def _build_notification_behaviors(flags: NotificationFlags) -> list[str]:
    """Build list of notification behavior URIs based on resolved flags.

    Notifications are an app-level policy. Rather than injecting hooks after
    bundle preparation, we compose notification behavior bundles BEFORE
    prepare() so their modules get properly downloaded and installed.

    The resolved ``NotificationFlags`` must come from
    ``AppSettings.get_notification_flags()`` — that method is the single
    source of truth for the "is notifications.X enabled?" question. The
    sibling consumer ``AppSettings.get_notification_hook_overrides()`` reads
    the same flags, so the two paths cannot drift apart on defaults.

    Args:
        flags: Resolved notification enablement.

    Returns:
        List of behavior bundle URIs to compose onto the main bundle.
        Empty list if no notifications are enabled.
    """
    if not (flags.desktop_enabled or flags.push_enabled):
        return []

    behaviors: list[str] = []

    # Root bundle first — a minimal marker that just identifies the repo
    # and ensures the bundle gets cached with proper SHA metadata (fixes
    # the "unknown" version issue during `amplifier update`). The actual
    # functionality comes from the subdirectory behaviors below.
    behaviors.append("git+https://github.com/microsoft/amplifier-bundle-notify@main")

    if flags.desktop_enabled:
        behaviors.append(
            "git+https://github.com/microsoft/amplifier-bundle-notify@main#subdirectory=behaviors/desktop-notifications.yaml"
        )

    if flags.push_enabled:
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
    "resolve_bundle_config",
    "deep_merge",
    "expand_env_vars",
    "inject_user_providers",
    "_apply_provider_overrides",
    "_ensure_raw_defaults",
    "_map_id_to_instance_id",
]
