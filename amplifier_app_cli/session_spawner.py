# pyright: reportMissingImports=false
"""Session spawning for agent delegation.

Implements sub-session creation with configuration inheritance and overlays.
"""

import asyncio
import copy
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar, cast

from amplifier_core import AmplifierSession
from amplifier_foundation import (
    RUNTIME_SKILL_OVERLAY_CAPABILITY,
    bridge_child_cost,
    generate_sub_session_id,
)

from .agent_config import merge_configs

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class _SubSessionLifecycle:
    """Track and reliably release resources acquired by an in-process child."""

    def __init__(self) -> None:
        self.child_session: Any | None = None
        self.display_system: Any | None = None
        self.nesting_pushed = False
        self.unregister_hook: Callable[[], Any] | None = None
        self.parent_cancellation: Any | None = None
        self.child_cancellation: Any | None = None
        self._persist_interruption: Callable[[], Awaitable[None]] | None = None
        self._finalization_started = False
        self._teardown_started = False

    def push_nesting(self, display_system: Any) -> None:
        self.display_system = display_system
        if hasattr(display_system, "push_nesting"):
            # Mark first so even a partially successful push is balanced.
            self.nesting_pushed = True
            display_system.push_nesting()

    def register_cancellation(
        self, parent_cancellation: Any, child_cancellation: Any
    ) -> None:
        parent_cancellation.register_child(child_cancellation)
        self.parent_cancellation = parent_cancellation
        self.child_cancellation = child_cancellation

    def persist_on_interruption(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register persistence to run only when child execution was cancelled."""
        self._persist_interruption = callback

    async def finalize(self, *, preserve_error: bool) -> None:
        """Persist an interrupted execution, then release every resource once."""
        if self._finalization_started:
            return
        self._finalization_started = True

        if self._persist_interruption is not None:
            persist_interruption = self._persist_interruption
            self._persist_interruption = None
            try:
                await persist_interruption()
            except BaseException:
                # Persistence is best effort and must never replace the original
                # execution cancellation.
                logger.exception("Failed to persist interrupted sub-session")

        await self.teardown(preserve_error=preserve_error)

    async def teardown(self, *, preserve_error: bool) -> None:
        """Attempt every acquired teardown action exactly once."""
        if self._teardown_started:
            return
        self._teardown_started = True
        errors: list[BaseException] = []

        if self.unregister_hook is not None:
            unregister_hook = self.unregister_hook
            self.unregister_hook = None
            try:
                unregister_hook()
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed to unregister sub-session completion hook")

        if self.parent_cancellation is not None and self.child_cancellation is not None:
            parent_cancellation = self.parent_cancellation
            child_cancellation = self.child_cancellation
            self.parent_cancellation = None
            self.child_cancellation = None
            try:
                parent_cancellation.unregister_child(child_cancellation)
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed to unregister child cancellation token")

        if self.nesting_pushed and self.display_system is not None:
            display_system = self.display_system
            self.nesting_pushed = False
            try:
                if hasattr(display_system, "pop_nesting"):
                    display_system.pop_nesting()
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed to pop sub-session display nesting")

        if self.child_session is not None:
            child_session = self.child_session
            self.child_session = None
            try:
                await child_session.cleanup()
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed to clean up sub-session")

        if errors and not preserve_error:
            raise errors[0]


async def _run_with_finalizer(
    operation: Awaitable[_T], lifecycle: _SubSessionLifecycle
) -> _T:
    """Run an operation, then shield one separately scheduled finalizer task."""
    result = cast(_T, None)
    primary_error: BaseException | None = None

    try:
        result = await operation
    except BaseException as error:  # noqa: BLE001 - teardown must follow any exit
        primary_error = error

    finalizer = asyncio.create_task(
        lifecycle.finalize(preserve_error=primary_error is not None)
    )
    while True:
        try:
            await asyncio.shield(finalizer)
            break
        except asyncio.CancelledError as error:
            if primary_error is None:
                primary_error = error
            # Re-await the same shielded task. A new task would duplicate work,
            # while awaiting this task directly would let cancellation reach it.
            if not finalizer.done():
                continue
            if finalizer.cancelled():
                break
        except BaseException as finalization_error:
            if primary_error is None:
                raise
            logger.error(
                "Sub-session finalization failed after primary error",
                exc_info=finalization_error,
            )
            break

    if primary_error is not None:
        raise primary_error
    return result


# Capture default sys.path entries at import time.
# Used to filter out bundle-added paths when forwarding sys_paths to subprocess children.
_DEFAULT_SYS_PATHS: frozenset[str] = frozenset(sys.path)


def _extract_bundle_context(session: "AmplifierSession") -> dict | None:
    """Extract serializable bundle context from session.

    Extracts both module resolution paths and mention mappings needed to
    reconstruct bundle context on resume.

    Args:
        session: The session to extract bundle context from.

    Returns:
        Dict with module_paths and mention_mappings, or None if not bundle mode.
    """
    # Get module resolver
    resolver = session.coordinator.get("module-source-resolver")
    if resolver is None:
        return None

    # Extract module paths from resolver
    # Handle both AppModuleResolver (wraps _bundle) and BundleModuleResolver directly
    module_paths: dict[str, str] = {}

    if hasattr(resolver, "_bundle") and hasattr(resolver._bundle, "_paths"):
        # AppModuleResolver wrapping BundleModuleResolver
        module_paths = {k: str(v) for k, v in resolver._bundle._paths.items()}
    elif hasattr(resolver, "_paths"):
        # Direct BundleModuleResolver
        module_paths = {k: str(v) for k, v in resolver._paths.items()}

    if not module_paths:
        # Not bundle mode - no paths to preserve
        return None

    # Extract mention mappings from mention resolver (for @namespace:path resolution)
    mention_mappings: dict[str, str] = {}
    mention_resolver = session.coordinator.get_capability("mention_resolver")
    if mention_resolver and hasattr(mention_resolver, "_bundle_mappings"):
        mention_mappings = {
            k: str(v) for k, v in mention_resolver._bundle_mappings.items()
        }

    return {
        "module_paths": module_paths,
        "mention_mappings": mention_mappings,
    }


def _filter_tools(
    config: dict,
    tool_inheritance: dict[str, list[str]],
    agent_explicit_tools: list[str] | None = None,
) -> dict:
    """Filter tools in config based on tool inheritance policy.

    Args:
        config: Session config containing "tools" list
        tool_inheritance: Policy dict with either:
            - "exclude_tools": list of tool module names to exclude
            - "inherit_tools": list of tool module names to include (allowlist)
        agent_explicit_tools: Optional list of tool module names explicitly declared
            by the agent. These are preserved even if they would be excluded.
            Formula: final_tools = (inherited - excluded) + explicit

    Returns:
        New config dict with filtered tools list
    """
    tools = config.get("tools", [])
    if not tools:
        return config

    exclude_tools = tool_inheritance.get("exclude_tools", [])
    inherit_tools = tool_inheritance.get("inherit_tools")

    # Get explicit tool module names (these are always preserved)
    explicit_modules = set(agent_explicit_tools or [])

    if inherit_tools is not None:
        # Allowlist mode: only include specified tools OR explicit
        filtered_tools = [
            t
            for t in tools
            if t.get("module") in inherit_tools or t.get("module") in explicit_modules
        ]
    elif exclude_tools:
        # Blocklist mode: exclude specified tools UNLESS explicit
        filtered_tools = [
            t
            for t in tools
            if t.get("module") not in exclude_tools
            or t.get("module") in explicit_modules
        ]
    else:
        # No filtering
        return config

    # Return new config with filtered tools
    new_config = dict(config)
    new_config["tools"] = filtered_tools

    logger.debug(
        "Filtered tools: %d -> %d (exclude=%s, inherit=%s)",
        len(tools),
        len(filtered_tools),
        exclude_tools,
        inherit_tools,
    )

    return new_config


def _filter_hooks(
    config: dict,
    hook_inheritance: dict[str, list[str]],
    agent_explicit_hooks: list[str] | None = None,
) -> dict:
    """Filter hooks in config based on hook inheritance policy.

    Args:
        config: Session config containing "hooks" list
        hook_inheritance: Policy dict with either:
            - "exclude_hooks": list of hook module names to exclude
            - "inherit_hooks": list of hook module names to include (allowlist)
        agent_explicit_hooks: Optional list of hook module names explicitly declared
            by the agent. These are preserved even if they would be excluded.
            Formula: final_hooks = (inherited - excluded) + explicit

    Returns:
        New config dict with filtered hooks list
    """
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    exclude_hooks = hook_inheritance.get("exclude_hooks", [])
    inherit_hooks = hook_inheritance.get("inherit_hooks")

    # Get explicit hook module names (these are always preserved)
    explicit_modules = set(agent_explicit_hooks or [])

    if inherit_hooks is not None:
        # Allowlist mode: only include specified hooks OR explicit
        filtered_hooks = [
            h
            for h in hooks
            if h.get("module") in inherit_hooks or h.get("module") in explicit_modules
        ]
    elif exclude_hooks:
        # Blocklist mode: exclude specified hooks UNLESS explicit
        filtered_hooks = [
            h
            for h in hooks
            if h.get("module") not in exclude_hooks
            or h.get("module") in explicit_modules
        ]
    else:
        # No filtering
        return config

    # Return new config with filtered hooks
    new_config = dict(config)
    new_config["hooks"] = filtered_hooks

    logger.debug(
        "Filtered hooks: %d -> %d (exclude=%s, inherit=%s)",
        len(hooks),
        len(filtered_hooks),
        exclude_hooks,
        inherit_hooks,
    )

    return new_config


_REDACTION_SENTINEL = "[REDACTED]"


def _find_redacted_values(value: object, path: str = "") -> list[str]:
    """Recursively collect dotted/bracketed paths still holding the redaction sentinel.

    Used at resume time (see resume_sub_session's credential refresh) to detect
    secret-bearing config fields that were NOT successfully re-hydrated from
    live settings. redact_secrets() (amplifier_core.utils.truncate) replaces
    sensitive values with the literal string "[REDACTED]" before persisting
    session metadata to disk; this is the inverse-direction check that flags
    any such literal still present after the refresh pass.

    Args:
        value: Any nested dict/list/scalar structure (e.g. merged_config["hooks"]).
        path: Internal accumulator for the current traversal path.

    Returns:
        List of paths (e.g. "[2].config.destinations[0].api_key") where the
        sentinel value was found. Empty list if nothing is redacted.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, sub_value in value.items():
            found.extend(_find_redacted_values(sub_value, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_redacted_values(item, f"{path}[{index}]"))
    elif value == _REDACTION_SENTINEL:
        found.append(path or "<root>")
    return found


async def _spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
    tool_inheritance: dict[str, list[str]] | None = None,
    hook_inheritance: dict[str, list[str]] | None = None,
    orchestrator_config: dict | None = None,
    parent_messages: list[dict] | None = None,
    provider_preferences: list | None = None,
    self_delegation_depth: int = 0,
    session_metadata: dict | None = None,
    use_subprocess: bool = False,
    *,
    lifecycle: _SubSessionLifecycle,
) -> dict:
    """
    Spawn sub-session with agent configuration overlay.

    Precedence policy (this app's choice, not a kernel contract): see
    ``docs/SPAWN_PRECEDENCE.md``. Other apps that register the
    ``session.spawn`` capability may use different precedence.

    Args:
        agent_name: Name of agent from configuration
        instruction: Task for agent to execute
        parent_session: Parent session for inheritance
        agent_configs: Dict of agent configurations
        sub_session_id: Optional explicit ID (generates if None)
        tool_inheritance: Optional tool filtering policy:
            - {"exclude_tools": ["tool-task"]} - inherit all EXCEPT these
            - {"inherit_tools": ["tool-filesystem"]} - inherit ONLY these
        hook_inheritance: Optional hook filtering policy:
            - {"exclude_hooks": ["hooks-logging"]} - inherit all EXCEPT these
            - {"inherit_hooks": ["hooks-approval"]} - inherit ONLY these
        orchestrator_config: Optional orchestrator config to merge into session
            (e.g., {"min_delay_between_calls_ms": 500} for rate limiting)
        parent_messages: Optional list of messages from parent session to inject
            into child's context. Enables context inheritance where child can
            reference parent's conversation history.
        provider_preferences: Optional ordered list of ProviderPreference objects.
            Each preference has provider and model. System tries each in order
            until finding an available provider. Model names support glob patterns.
        self_delegation_depth: Current depth in the self-delegation chain (default: 0).
            Incremented for self-delegation, reset to 0 for named agents.
            Used to prevent infinite recursion.
        use_subprocess: If True, run the agent in a subprocess via
            run_session_in_subprocess instead of in-process. Also
            triggered when spawn_mode: "subprocess" is set in
            merged config. Returns early with output dict.

    Returns:
        Dict with "output" (response) and "session_id" (for multi-turn)

    Raises:
        ValueError: If agent not found or config invalid
    """
    # Get agent configuration
    # Special handling for "self" - spawn with parent's config (no agent overlay)
    if agent_name == "self":
        agent_config = {}  # Empty overlay = inherit parent config as-is
        logger.debug("Self-delegation: using parent config without agent overlay")
    elif agent_name not in agent_configs:
        raise ValueError(f"Agent '{agent_name}' not found in configuration")
    else:
        agent_config = agent_configs[agent_name]

    # Merge parent config with agent overlay
    merged_config = merge_configs(parent_session.config, agent_config)

    # === Issue #233 fix: propagate live agent registry to child ===
    #
    # parent_session.config is the STATIC snapshot captured at session-init.
    # Runtime additions (mode contributions via RuntimeOverlay) live in
    # parent_session.coordinator.config["agents"] and are NOT in the static
    # snapshot. Without this propagation, mode-contributed agents cannot
    # delegate to same-mode siblings.
    #
    # Design: read coordinator.config directly (source of truth), not from
    # any caller-supplied parameter. This ensures the fix works regardless
    # of which code path invoked spawn (tool-delegate, recipe orchestrator,
    # programmatic spawn, etc.). Local (agent_config) declarations win over
    # inherited live registry — never overwrite.
    #
    # Snapshot semantics: child gets a deep-copy at spawn time. Subsequent
    # mode changes in the parent do NOT propagate to an already-running child.
    parent_coord = getattr(parent_session, "coordinator", None)
    if parent_coord is not None:
        try:
            live_agents = (parent_coord.config or {}).get("agents") or {}
        except AttributeError:
            live_agents = {}

        # Reconcile this propagation with the overlay's OWN access-control
        # declaration (agent_config["agents"] -- a Smart Single Value:
        # "none" | list-of-names | "all"/absent). merge_configs() already
        # applied this same declaration once, but only against the STATIC
        # parent snapshot it can see -- a mode-contributed live agent named
        # in an explicit allowlist isn't in that snapshot, so merge_configs
        # resolves it to an empty dict there (this was PR #178/#233's own
        # documented under-delivery: "even an explicit agents: [sibling_b]
        # declaration wouldn't reach sibling_b -- the source dict is the
        # wrong one"). Left unhandled, the block below would then blindly
        # union in the FULL live registry regardless of that declaration,
        # silently re-opening delegation for an agent that said "none" or
        # handing it agents outside its stated allowlist -- defeating the
        # sub-agent access-control contract merge_configs() exists to
        # enforce (commit d609bb2; documented in AGENT_AUTHORING.md).
        #
        # So: apply the declaration a second time here, against the live
        # registry, before it gets merged in. This reconciles both intents
        # in one place -- #233's "mode siblings must be reachable" and the
        # original "agents: declares exactly who I can delegate to."
        #
        # Gate on the OVERLAY (agent_config), NOT on merged_config["agents"].
        # An unrestricted agent (no `agents:` key) whose parent simply has
        # no STATIC agents also produces an empty merged_config["agents"] --
        # gating on emptiness there would wrongly suppress propagation for
        # that agent too. The overlay's own declared value is the only
        # signal that distinguishes "restricted to nothing/some" from
        # "unrestricted, parent just has nothing (yet) in the snapshot."
        agent_filter = agent_config.get("agents")
        if agent_filter == "none":
            live_agents = {}
        elif isinstance(agent_filter, list):
            live_agents = {
                name: cfg for name, cfg in live_agents.items() if name in agent_filter
            }
        # else: "all", None, or absent -- inherit the live registry unchanged
        # (current/original #233 behavior).

        if live_agents:
            # Build a FRESH dict and rebind it; never mutate the dict
            # merged_config already holds. merge_configs() deep-copies the
            # agents dict only when it is non-empty, and merge_agent_dicts()
            # starts from a shallow parent.copy() -- so an EMPTY parent
            # "agents" dict arrives here as the parent session's own object.
            # setdefault()-then-mutate would then write the live registry
            # straight into the parent's live config and hand the child the
            # very same dict (cross-session state leak).
            child_agents = dict(merged_config.get("agents") or {})
            for name, cfg in live_agents.items():
                if name not in child_agents:
                    child_agents[name] = copy.deepcopy(cfg)
            merged_config["agents"] = child_agents
    # === end issue #233 fix (agents) ===

    # Apply tool inheritance filtering if specified
    if tool_inheritance and "tools" in merged_config:
        # Get agent's explicit tool modules to preserve them
        agent_tool_modules = [t.get("module") for t in agent_config.get("tools", [])]
        merged_config = _filter_tools(
            merged_config, tool_inheritance, agent_tool_modules
        )

    # Apply hook inheritance filtering if specified
    if hook_inheritance and "hooks" in merged_config:
        # Get agent's explicit hook modules to preserve them
        agent_hook_modules = [h.get("module") for h in agent_config.get("hooks", [])]
        merged_config = _filter_hooks(
            merged_config, hook_inheritance, agent_hook_modules
        )

    # Defense-in-depth: read routing-resolved provider_preferences from agent config
    # when no explicit preferences were passed by the caller.
    # The routing hook (hooks-routing) writes provider_preferences into agent configs
    # at session:start when resolving model_role declarations in agent frontmatter.
    # Tool-delegate normally reads these and passes them as a function argument, but
    # this fallback ensures spawn_sub_session works without that middleman — any
    # direct caller benefits from frontmatter routing too.
    if not provider_preferences:
        agent_prefs_raw = agent_config.get("provider_preferences")
        if agent_prefs_raw:
            from amplifier_foundation.spawn_utils import ProviderPreference

            provider_preferences = [
                ProviderPreference.from_dict(p) if isinstance(p, dict) else p
                for p in agent_prefs_raw
            ]
            logger.debug(
                "Using routing-resolved provider_preferences from agent config "
                "for agent '%s' (%d preference(s))",
                agent_name,
                len(provider_preferences),
            )

    # Apply provider preferences if specified (ordered fallback chain)
    if provider_preferences:
        from amplifier_foundation import apply_provider_preferences_with_resolution

        merged_config = await apply_provider_preferences_with_resolution(
            merged_config, provider_preferences, parent_session.coordinator
        )

    # Apply orchestrator config override if specified (recipe-level rate limiting)
    # Session reads orchestrator config from: config["session"]["orchestrator"]["config"]
    if orchestrator_config:
        if "session" not in merged_config:
            merged_config["session"] = {}
        if "orchestrator" not in merged_config["session"]:
            merged_config["session"]["orchestrator"] = {}
        if "config" not in merged_config["session"]["orchestrator"]:
            merged_config["session"]["orchestrator"]["config"] = {}
        # Merge orchestrator config (caller's config takes precedence)
        merged_config["session"]["orchestrator"]["config"].update(orchestrator_config)
        logger.debug(
            "Applied orchestrator config override to session.orchestrator.config: %s",
            orchestrator_config,
        )

    # Inject session metadata if provided (enables kernel CP-SM passthrough on session:start/fork)
    # Metadata is surfaced on session:start and session:fork events for observability consumers.
    if session_metadata:
        if "session" not in merged_config:
            merged_config["session"] = {}
        merged_config["session"]["metadata"] = session_metadata
        logger.debug(
            "Injected session_metadata into child session config: %s",
            session_metadata,
        )

    # Generate child session ID using W3C Trace Context span_id pattern
    # Use 16 hex chars (8 bytes) for fixed-length, filesystem-safe IDs
    if not sub_session_id:
        sub_session_id = generate_sub_session_id(
            agent_name=agent_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )
    assert sub_session_id is not None  # Always generated above if not provided

    # Route to subprocess runner if requested via parameter or config
    spawn_mode = merged_config.get("spawn_mode")
    if use_subprocess or spawn_mode == "subprocess":
        from amplifier_foundation.subprocess_runner import run_session_in_subprocess

        project_path = str(
            parent_session.coordinator.get_capability("session.working_dir")
            or Path.cwd()
        )
        child_config = {k: v for k, v in merged_config.items() if k != "spawn_mode"}

        # Extract bundle context to propagate to subprocess child.
        # Without this, bundle-loaded modules and packages are not importable in the child.
        bundle_ctx = _extract_bundle_context(parent_session)
        bundle_pkg_paths = parent_session.coordinator.get_capability(
            "bundle_package_paths"
        )

        result = await run_session_in_subprocess(
            config=child_config,
            prompt=instruction,
            parent_id=parent_session.session_id,
            project_path=project_path,
            session_id=sub_session_id,
            module_paths=bundle_ctx.get("module_paths") if bundle_ctx else None,
            bundle_package_paths=(
                bundle_pkg_paths() if callable(bundle_pkg_paths) else bundle_pkg_paths
            ),
            sys_paths=[p for p in sys.path if p not in _DEFAULT_SYS_PATHS],
            mention_mappings=bundle_ctx.get("mention_mappings") if bundle_ctx else None,
        )

        # Emit session:fork event from parent hooks (finding #14)
        parent_hooks = parent_session.coordinator.get("hooks")
        if parent_hooks:
            await parent_hooks.emit(
                "session:fork",
                {
                    "child_session_id": sub_session_id,
                    "parent_session_id": parent_session.session_id,
                    "agent_name": agent_name,
                    "spawn_mode": "subprocess",
                },
            )

        import json as _json

        try:
            parsed = _json.loads(result)
            if isinstance(parsed, dict) and "output" in parsed:
                return {
                    "output": parsed["output"],
                    "session_id": parsed.get("session_id", sub_session_id),
                    "status": parsed.get("status", "success"),
                    "turn_count": parsed.get("turn_count", 1),
                    "metadata": parsed.get("metadata", {}),
                }
        except (ValueError, TypeError):
            pass
        return {
            "output": result,
            "session_id": sub_session_id,
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }

    # Create child session with parent_id and inherited UX systems (kernel mechanism)
    # NOTE: We intentionally do NOT share parent's loader here.
    # The loader caches modules with their config, so sharing would cause child sessions
    # to get the parent's cached orchestrator config instead of their own.
    # Each session needs its own loader to respect session-specific config (e.g., rate limiting).
    display_system = parent_session.coordinator.display_system
    child_session = AmplifierSession(
        config=merged_config,
        loader=None,  # Let child create its own loader to respect its config
        session_id=sub_session_id,
        parent_id=parent_session.session_id,  # Links to parent
        approval_system=parent_session.coordinator.approval_system,  # Inherit from parent
        display_system=display_system,  # Inherit from parent
    )
    lifecycle.child_session = child_session

    # Notify display system we're entering a nested session (for indentation)
    lifecycle.push_nesting(display_system)

    # NOTE: Parent message injection moved to AFTER initialize() because
    # the context module is only mounted during initialize().

    # Register app-layer capabilities for child session BEFORE initialization
    # These must be mounted before initialize() because module loading needs the resolver
    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Module source resolver - inherit from parent to preserve BundleModuleResolver in bundle mode
    # CRITICAL: Must be mounted BEFORE initialize() so modules with source: directives can be resolved
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)
    else:
        # Fallback to fresh resolver if parent doesn't have one
        resolver = create_foundation_resolver()
        await child_session.coordinator.mount("module-source-resolver", resolver)

    # Share sys.path additions from parent BEFORE initialize()
    # This ensures bundle packages (like amplifier_bundle_python_dev) are importable
    # when child session loads modules that depend on them.
    #
    # Two sources of paths need to be shared:
    # 1. loader._added_paths - individual module paths added during loading
    # 2. bundle_package_paths capability - bundle src/ directories (e.g., python-dev)
    paths_to_share: list[str] = []

    # Source 1: Module paths from parent loader
    parent_loader = getattr(parent_session, "loader", None)
    if parent_loader is not None:
        parent_added_paths = getattr(parent_loader, "_added_paths", [])
        paths_to_share.extend(parent_added_paths)

    # Source 2: Bundle package paths (src/ directories from bundles like python-dev)
    # These are registered as a capability during bundle preparation
    bundle_package_paths = parent_session.coordinator.get_capability(
        "bundle_package_paths"
    )
    if bundle_package_paths:
        paths_to_share.extend(bundle_package_paths)

    # Add all paths to sys.path
    if paths_to_share:
        for path in paths_to_share:
            if path not in sys.path:
                sys.path.insert(0, path)
        logger.debug(
            f"Shared {len(paths_to_share)} sys.path entries from parent to child session"
        )

    # Working directory - register BEFORE initialize(). Any capability a module
    # consumes while mounting or in on_session_ready must be registered before
    # initialize(), because module mounting and on_session_ready both run during
    # initialize(); a capability registered afterwards is invisible to them (the
    # module sees it as absent). This affects ANY module, not just hooks.
    # Fall back to cwd so the value is never empty even when the parent
    # session was created without an explicit working_dir capability.
    _child_working_dir = parent_session.coordinator.get_capability(
        "session.working_dir"
    ) or str(Path.cwd().resolve())
    child_session.coordinator.register_capability(
        "session.working_dir", _child_working_dir
    )

    # Initialize child session (mounts modules per merged config)
    # Now the resolver is available for loading modules with source: directives
    await child_session.initialize()

    # === Issue #233 fix: propagate runtime_skill_overlay capability ===
    #
    # Mode-contributed skills are registered as a coordinator capability
    # (RUNTIME_SKILL_OVERLAY_CAPABILITY) rather than in static config.
    # tool-skills in a sub-session reads its OWN coordinator's capability,
    # which is empty unless we propagate from parent here.
    #
    # Note: RUNTIME_CONTEXT_OVERLAY_CAPABILITY is intentionally NOT propagated.
    # Mode-contributed context belongs to "the mode is active here" — that state
    # is root-session only (hooks-mode's provider:request handler lives there).
    # Skills are different: they're discoverable resources, not mode state.
    child_coord = getattr(child_session, "coordinator", None)
    if parent_coord is not None and child_coord is not None:
        try:
            overlay_skills = parent_coord.get_capability(
                RUNTIME_SKILL_OVERLAY_CAPABILITY
            )
        except (AttributeError, KeyError):
            overlay_skills = None
        if overlay_skills:
            try:
                child_coord.register_capability(
                    RUNTIME_SKILL_OVERLAY_CAPABILITY,
                    list(overlay_skills),  # snapshot copy
                )
            except AttributeError:
                pass  # child coordinator without capability support; safe to skip
    # === end issue #233 fix (skill capability) ===

    # Note: Parent context inheritance is now handled by tool-task formatting
    # the parent messages directly into the instruction text. This ensures the
    # child agent sees the context regardless of session/orchestrator behavior.
    # The parent_messages parameter is kept for potential future use.

    # Wire up cancellation propagation: parent cancellation should propagate to child
    # This enables graceful Ctrl+C handling for nested agent sessions
    parent_cancellation = parent_session.coordinator.cancellation
    child_cancellation = child_session.coordinator.cancellation
    lifecycle.register_cancellation(parent_cancellation, child_cancellation)
    logger.debug(
        f"Registered child cancellation token for sub-session {sub_session_id}"
    )

    # Mention resolver - inherit from parent to preserve bundle_override context
    parent_mention_resolver = parent_session.coordinator.get_capability(
        "mention_resolver"
    )
    if parent_mention_resolver:
        child_session.coordinator.register_capability(
            "mention_resolver", parent_mention_resolver
        )
    else:
        # Fallback to fresh resolver if parent doesn't have one
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )

    # Mention deduplicator - inherit from parent to preserve session-wide deduplication state
    parent_deduplicator = parent_session.coordinator.get_capability(
        "mention_deduplicator"
    )
    if parent_deduplicator:
        child_session.coordinator.register_capability(
            "mention_deduplicator", parent_deduplicator
        )
    else:
        # Fallback to fresh deduplicator if parent doesn't have one
        child_session.coordinator.register_capability(
            "mention_deduplicator", ContentDeduplicator()
        )

    # Routing capability — inherit so child's hooks-routing can compose runtime overrides.
    # When the parent has a session.routing capability (registered by the routing-matrix
    # bundle), the child's hooks-routing reads it to apply capability_overrides to the
    # effective matrix. Without inheritance the child gets no overrides and may resolve
    # model_role against a different effective matrix than the parent intended.
    parent_routing = parent_session.coordinator.get_capability("session.routing")
    if parent_routing:
        child_session.coordinator.register_capability("session.routing", parent_routing)

    # Self-delegation depth tracking (for recursion limits)
    # This is a simple value capability, not a function
    child_session.coordinator.register_capability(
        "self_delegation_depth", self_delegation_depth
    )

    # Register session spawning capabilities on child session
    # This enables nested agent delegation (child can spawn grandchildren)
    # The capabilities are closures that reference the spawn/resume functions
    async def child_spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: AmplifierSession,
        agent_configs: dict[str, dict],
        sub_session_id: str | None = None,
        tool_inheritance: dict[str, list[str]] | None = None,
        hook_inheritance: dict[str, list[str]] | None = None,
        orchestrator_config: dict | None = None,
        parent_messages: list[dict] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        session_metadata: dict | None = None,
        use_subprocess: bool = False,
    ) -> dict:
        return await spawn_sub_session(
            agent_name=agent_name,
            instruction=instruction,
            parent_session=parent_session,
            agent_configs=agent_configs,
            sub_session_id=sub_session_id,
            tool_inheritance=tool_inheritance,
            hook_inheritance=hook_inheritance,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
            session_metadata=session_metadata,
            use_subprocess=use_subprocess,
        )

    async def child_resume_capability(sub_session_id: str, instruction: str) -> dict:
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
            parent_session=parent_session,
        )

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )
    child_session.coordinator.register_capability(
        "session.resume", child_resume_capability
    )

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(f"Registered approval provider for child session {sub_session_id}")

    # Inject agent's system instruction
    # Check top-level instruction first (from agent .md file body), then nested system.instruction
    system_instruction = agent_config.get("instruction") or agent_config.get(
        "system", {}
    ).get("instruction")
    if system_instruction:
        context = child_session.coordinator.get("context")
        # Expand @-mentions in the agent body before injecting as system message.
        # Content lands inline as <context_file> XML blocks prepended to the instruction.
        _resolver = child_session.coordinator.get_capability("mention_resolver")
        if _resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _deduplicator = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _wd = child_session.coordinator.get_capability("session.working_dir")
            _rel_to = Path(_wd) if _wd else Path.cwd()
            system_instruction = await expand_mentions_in_instruction(
                system_instruction,
                resolver=_resolver,
                deduplicator=_deduplicator,
                relative_to=_rel_to,
            )
        if context and hasattr(context, "set_system_prompt_factory"):
            # Register a factory rather than a static system message so
            # hooks that compose onto the system prompt (e.g. the skills
            # visibility hook's "prefix" placement) have a surface to wrap.
            # Without this, those hooks fall back to re-injecting their
            # content on every provider:request, outside the cached prefix.
            # Mirrors amplifier-foundation _prepared.py spawn path.
            _resolved_system_instruction = system_instruction

            async def _system_prompt_factory() -> str:
                return _resolved_system_instruction

            await context.set_system_prompt_factory(_system_prompt_factory)
        elif context and hasattr(context, "add_message"):
            await context.add_message({"role": "system", "content": system_instruction})

    # Register temporary hook to capture orchestrator:complete data
    # This gives us status, turn_count, and metadata from the orchestrator
    completion_data: dict = {}
    hooks = child_session.coordinator.get("hooks")
    unregister_hook = None
    if hooks:
        from amplifier_core.hooks import HookResult

        async def _capture_completion(event: str, data: dict) -> HookResult:
            completion_data.update(data)
            return HookResult()

        unregister_hook = hooks.register(
            "orchestrator:complete",
            _capture_completion,
            priority=999,
            name="_spawn_capture",
        )
        lifecycle.unregister_hook = unregister_hook

    # Expand @-mentions in delegation instruction before executing.
    # Content lands inline as <context_file> XML blocks prepended to the instruction.
    if instruction:
        _instr_resolver = child_session.coordinator.get_capability("mention_resolver")
        if _instr_resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _instr_dedup = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _instr_wd = child_session.coordinator.get_capability("session.working_dir")
            _instr_rel = Path(_instr_wd) if _instr_wd else Path.cwd()
            instruction = await expand_mentions_in_instruction(
                instruction,
                resolver=_instr_resolver,
                deduplicator=_instr_dedup,
                relative_to=_instr_rel,
            )

    # Prepare canonical reconstruction state before execution so a cancelled
    # child can be resumed from whatever transcript it produced.
    from datetime import UTC, datetime

    from .session_store import SessionStore

    context = child_session.coordinator.get("context")
    parent_trace_id = getattr(parent_session, "trace_id", parent_session.session_id)

    child_span: str | None = None
    if sub_session_id and "_" in sub_session_id and "-" in sub_session_id:
        base = sub_session_id.rsplit("_", 1)[0]
        child_span = base.rsplit("-", 1)[-1]

    metadata = {
        "session_id": sub_session_id,
        "parent_id": parent_session.session_id,
        "trace_id": parent_trace_id,
        "agent_name": agent_name,
        "child_span": child_span,
        "created": datetime.now(UTC).isoformat(),
        "config": merged_config,
        "agent_overlay": agent_config,
        "turn_count": 1,
        "bundle_context": _extract_bundle_context(parent_session),
        "self_delegation_depth": self_delegation_depth,
        "working_dir": str(Path.cwd().resolve()),
    }
    store = SessionStore()

    # Execute and collect the transcript for normal persistence. If either await
    # is cancelled, the wrapper persists interruption state and tears down in
    # its shielded finalizer.
    try:
        response = await child_session.execute(instruction)
        transcript = await context.get_messages() if context else []
    except asyncio.CancelledError:

        async def _persist_interrupted_spawn() -> None:
            transcript = []
            if context:
                try:
                    transcript = await context.get_messages()
                except BaseException:
                    logger.exception(
                        "Failed to read interrupted sub-session %s transcript; "
                        "saving fallback transcript",
                        sub_session_id,
                    )
            try:
                store.save(
                    sub_session_id,
                    transcript,
                    {**metadata, "status": "interrupted"},
                )
                logger.debug(
                    "Interrupted sub-session %s state persisted", sub_session_id
                )
            except BaseException:
                logger.exception(
                    "Failed to persist interrupted sub-session %s", sub_session_id
                )

        lifecycle.persist_on_interruption(_persist_interrupted_spawn)
        raise

    # Persist state for multi-turn resumption
    store.save(sub_session_id, transcript, metadata)
    logger.debug(f"Sub-session {sub_session_id} state persisted")

    # Bridge child session costs to parent coordinator (bridge_child_cost never raises)
    await bridge_child_cost(
        child_coordinator=child_session.coordinator,
        parent_coordinator=parent_session.coordinator,
        child_session_id=sub_session_id,
    )

    # Return response and session ID for potential multi-turn
    # Include enriched fields from orchestrator:complete hook
    return {
        "output": response,
        "session_id": sub_session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count", 1),
        "metadata": completion_data.get("metadata", {}),
    }


async def spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
    tool_inheritance: dict[str, list[str]] | None = None,
    hook_inheritance: dict[str, list[str]] | None = None,
    orchestrator_config: dict | None = None,
    parent_messages: list[dict] | None = None,
    provider_preferences: list | None = None,
    self_delegation_depth: int = 0,
    session_metadata: dict | None = None,
    use_subprocess: bool = False,
) -> dict:
    """Run a spawned child under lifecycle management from construction onward."""
    lifecycle = _SubSessionLifecycle()
    operation = _spawn_sub_session(
        agent_name=agent_name,
        instruction=instruction,
        parent_session=parent_session,
        agent_configs=agent_configs,
        sub_session_id=sub_session_id,
        tool_inheritance=tool_inheritance,
        hook_inheritance=hook_inheritance,
        orchestrator_config=orchestrator_config,
        parent_messages=parent_messages,
        provider_preferences=provider_preferences,
        self_delegation_depth=self_delegation_depth,
        session_metadata=session_metadata,
        use_subprocess=use_subprocess,
        lifecycle=lifecycle,
    )
    return await _run_with_finalizer(operation, lifecycle)


async def _resume_sub_session(
    sub_session_id: str,
    instruction: str,
    parent_session: AmplifierSession | None = None,
    *,
    lifecycle: _SubSessionLifecycle,
    child_spawn_capability,
) -> dict:
    """Resume existing sub-session for multi-turn engagement.

    Loads previously saved sub-session state, recreates the session with
    full context, executes new instruction, and saves updated state.

    Args:
        sub_session_id: ID of existing sub-session to resume
        instruction: Follow-up instruction to execute

    Returns:
        Dict with "output" (response) and "session_id" (same ID)

    Raises:
        FileNotFoundError: If session not found in storage
        RuntimeError: If session metadata corrupted or incomplete
        ValueError: If session_id is invalid
    """
    from datetime import UTC, datetime

    from .session_store import SessionStore

    # Load session state from storage
    store = SessionStore()

    if not store.exists(sub_session_id):
        raise FileNotFoundError(
            f"Sub-session '{sub_session_id}' not found. Session may have expired or was never created."
        )

    try:
        transcript, metadata = store.load(sub_session_id)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load sub-session '{sub_session_id}': {e!s}"
        ) from e

    # Extract reconstruction data
    merged_config = metadata.get("config")
    if not merged_config:
        raise RuntimeError(
            f"Corrupted session metadata for '{sub_session_id}'. Cannot reconstruct session without config."
        )

    # --- Credential refresh ---------------------------------------------------
    # On-disk metadata has secrets (provider api_keys, and hook/destination
    # secrets like the context-intelligence hook's private destination
    # api_key) redacted to "[REDACTED]" (security fix in
    # SessionStore._save_metadata -> redact_secrets()).
    #
    # redact_secrets() builds a NEW dict and never mutates its input, so it
    # only ever touches the PERSISTED snapshot -- the live parent session
    # config held in memory is never poisoned. That's why a FRESH spawn
    # (spawn_sub_session, which merges from parent_session.config above) is
    # unaffected: it always carries real credentials.
    #
    # RESUME is different: `merged_config` here was loaded straight from the
    # redacted on-disk snapshot (metadata["config"]), so EVERY section that
    # can carry a secret must be re-derived from live settings + environment
    # before session creation -- the same pipeline that assembles the ROOT
    # session config in runtime/config.py:resolve_bundle_config() (provider
    # overrides, then hook overrides, then env-var expansion) -- just applied
    # to the loaded snapshot instead of a freshly prepared bundle.
    # --------------------------------------------------------------------------
    if merged_config.get("providers") or merged_config.get("hooks"):
        from amplifier_app_cli.lib.settings import AppSettings
        from amplifier_app_cli.runtime.config import (
            _apply_hook_overrides,
            _apply_provider_overrides,
            _map_id_to_instance_id,
            deep_merge,
            expand_env_vars,
        )

        _live_settings = AppSettings()

        if merged_config.get("providers"):
            _live_provider_overrides = _live_settings.get_provider_overrides()
            if _live_provider_overrides:
                _refreshed_providers = _apply_provider_overrides(
                    merged_config["providers"], _live_provider_overrides
                )
                _refreshed_providers = _map_id_to_instance_id(_refreshed_providers)
                merged_config = {**merged_config, "providers": _refreshed_providers}
                logger.debug(
                    "Refreshed credentials for %d provider(s) at resume time",
                    len(_refreshed_providers),
                )

        if merged_config.get("hooks"):
            # Generalization of the provider refresh above. Re-derive hook
            # config from the SAME two live sources resolve_bundle_config()
            # uses to build a fresh session's hooks section:
            #   1. "overrides.<module>.config" in settings.yaml -- applies to
            #      ANY module id, hooks included (AppSettings.get_config_overrides()).
            #   2. Dedicated notification hook overrides
            #      (AppSettings.get_notification_hook_overrides()).
            # This is the piece that was previously MISSING: only providers
            # were refreshed, so a resumed sub-session kept sending
            # `Bearer [REDACTED]` for any hook/destination api_key.
            _config_overrides = _live_settings.get_config_overrides()
            _refreshed_hooks = merged_config["hooks"]
            if _config_overrides:
                _refreshed_hooks = [
                    {
                        **hook,
                        "config": deep_merge(
                            hook.get("config", {}) or {},
                            _config_overrides[hook["module"]],
                        ),
                    }
                    if isinstance(hook, dict)
                    and hook.get("module") in _config_overrides
                    else hook
                    for hook in _refreshed_hooks
                ]
            _notification_overrides = _live_settings.get_notification_hook_overrides()
            if _notification_overrides:
                _refreshed_hooks = _apply_hook_overrides(
                    _refreshed_hooks, _notification_overrides
                )
            merged_config = {**merged_config, "hooks": _refreshed_hooks}
            logger.debug(
                "Refreshed credentials for %d hook(s) at resume time",
                len(_refreshed_hooks),
            )

        # Expand any ${VAR} references now that live overrides have been
        # spliced in -- covers both providers and hooks in one pass.
        merged_config = expand_env_vars(merged_config)

        # Fail-loud guard: if a secret-bearing field STILL reads the
        # redaction sentinel after the refresh above, no live override
        # existed to restore it (e.g. the secret was baked into the bundle
        # definition itself rather than sourced from settings.yaml).
        # Do NOT silently mount "[REDACTED]" as if it were a usable value --
        # that is exactly how a resumed sub-session ends up sending
        # `Bearer [REDACTED]` and getting a genuine-looking 401 that masks
        # the real cause. Leave the sentinel in place (a downstream guard at
        # header-assembly time is expected to reject/disable it rather than
        # send it) and log loudly so the gap is visible, not swallowed.
        #
        # Scan the ENTIRE merged config, not just hooks. The same silent-
        # sentinel failure mode exists wherever a secret can live: a provider
        # entry with no matching live override keeps its redacted key, tools
        # are not re-hydrated on resume, and any of these can also appear
        # agent-scoped under agents[*]. _find_redacted_values already recurses
        # arbitrary structures, so pointing it at the whole config closes the
        # gap at no extra cost.
        _redacted_paths = _find_redacted_values(merged_config)
        if _redacted_paths:
            logger.warning(
                "Sub-session %s: %d config field(s) still hold the "
                "redaction sentinel '%s' after credential refresh (no live "
                "override found to restore them): %s. These fields are "
                "mounted as-is; the destination/consumer is expected to "
                "reject them rather than receive a fake credential.",
                sub_session_id,
                len(_redacted_paths),
                _REDACTION_SENTINEL,
                _redacted_paths,
            )

    parent_id = metadata.get("parent_id")
    agent_name = metadata.get("agent_name", "unknown")
    trace_id = metadata.get("trace_id")

    # Sub-session resume creates fresh UX systems. Parent UX context (approval history,
    # display state) is not preserved across resume. This is acceptable because:
    # 1. Sub-sessions are typically short-lived agent delegations
    # 2. Serializing full UX state would add significant complexity
    # 3. The parent session may no longer be running when sub-session resumes
    # 4. Approval decisions are contextual to the current execution state
    from amplifier_app_cli.ui import CLIApprovalSystem, CLIDisplaySystem

    logger.debug(
        "Resuming sub-session %s (agent=%s, parent=%s, trace=%s). "
        "UX context (approval history, display state) not preserved - using fresh UX systems.",
        sub_session_id,
        agent_name,
        parent_id,
        trace_id,
    )

    approval_system = CLIApprovalSystem()
    display_system = CLIDisplaySystem()

    child_session = AmplifierSession(
        config=merged_config,
        loader=None,  # Use default loader
        session_id=sub_session_id,  # REUSE same ID
        parent_id=parent_id,
        approval_system=approval_system,
        display_system=display_system,
    )
    lifecycle.child_session = child_session
    lifecycle.push_nesting(display_system)

    # Register app-layer capabilities for resumed child session BEFORE initialization
    # Must be mounted before initialize() so modules with source: directives can be resolved
    from pathlib import Path

    from amplifier_foundation.mentions import ContentDeduplicator

    from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
    from amplifier_app_cli.paths import create_foundation_resolver

    # Extract bundle context from metadata (saved during spawn_sub_session)
    bundle_context = metadata.get("bundle_context")

    # Module source resolver - restore from bundle context if available
    # CRITICAL: Must be mounted BEFORE initialize() so modules with source: directives can be resolved
    if bundle_context and bundle_context.get("module_paths"):
        # Restore BundleModuleResolver with saved module paths
        from amplifier_foundation.bundle import BundleModuleResolver

        from amplifier_app_cli.lib.bundle_loader import AppModuleResolver

        module_paths = {k: Path(v) for k, v in bundle_context["module_paths"].items()}
        bundle_resolver = BundleModuleResolver(module_paths=module_paths)
        logger.debug(
            f"Restored BundleModuleResolver with {len(module_paths)} module paths"
        )

        # Wrap with AppModuleResolver to provide fallback to settings resolver
        # This is critical for modules (like providers) that may not be in the saved
        # module_paths but are available via user settings/installed providers.
        # Mirrors the wrapping done in session_runner.py and tool.py
        fallback_resolver = create_foundation_resolver()
        resolver = AppModuleResolver(
            bundle_resolver=bundle_resolver,
            settings_resolver=fallback_resolver,
        )
        logger.debug("Wrapped with AppModuleResolver for settings fallback")
    else:
        # Fallback to FoundationSettingsResolver
        resolver = create_foundation_resolver()
    await child_session.coordinator.mount("module-source-resolver", resolver)

    # Working directory - register BEFORE initialize() so any module reading it
    # while mounting or in on_session_ready (both run during initialize()) sees
    # the capability. This affects ANY module, not just hooks.
    # Prefer the value saved in metadata at original spawn time, then fall back
    # to the parent's working_dir if a parent session was supplied, and finally
    # to cwd — so the capability is never absent/empty.
    _child_resume_working_dir = (
        metadata.get("working_dir")
        or (
            parent_session.coordinator.get_capability("session.working_dir")
            if parent_session is not None
            else None
        )
        or str(Path.cwd().resolve())
    )
    child_session.coordinator.register_capability(
        "session.working_dir", _child_resume_working_dir
    )

    # Initialize session (mounts modules per config)
    # Now the resolver is available for loading modules with source: directives
    await child_session.initialize()

    # Mention resolver - restore bundle mappings if available
    if bundle_context and bundle_context.get("mention_mappings"):
        # Restore AppMentionResolver with saved bundle mappings for @namespace:path resolution
        mention_mappings = {
            k: Path(v) for k, v in bundle_context["mention_mappings"].items()
        }
        child_session.coordinator.register_capability(
            "mention_resolver",
            AppMentionResolver(bundle_mappings=mention_mappings),
        )
        logger.debug(
            f"Restored AppMentionResolver with {len(mention_mappings)} bundle mappings"
        )
    else:
        # Fallback to fresh resolver without bundle mappings
        child_session.coordinator.register_capability(
            "mention_resolver", AppMentionResolver()
        )

    # Mention deduplicator - create fresh (deduplication state doesn't persist across resumes)
    child_session.coordinator.register_capability(
        "mention_deduplicator", ContentDeduplicator()
    )

    # Self-delegation depth - restore from metadata for recursion limit tracking
    self_delegation_depth = metadata.get("self_delegation_depth", 0)
    child_session.coordinator.register_capability(
        "self_delegation_depth", self_delegation_depth
    )

    # Register session spawning capabilities on resumed child session.
    # child_spawn_capability is defined by the public resume function so the
    # established source layout and public introspection behavior remain intact.
    async def child_resume_capability(sub_session_id: str, instruction: str) -> dict:
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
            parent_session=child_session,
        )

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )
    child_session.coordinator.register_capability(
        "session.resume", child_resume_capability
    )

    # Approval provider (for hooks-approval module, if active)
    register_provider_fn = child_session.coordinator.get_capability(
        "approval.register_provider"
    )
    if register_provider_fn:
        from rich.console import Console

        from amplifier_app_cli.approval_provider import CLIApprovalProvider

        console = Console()
        approval_provider = CLIApprovalProvider(console)
        register_provider_fn(approval_provider)
        logger.debug(
            f"Registered approval provider for resumed child session {sub_session_id}"
        )

    # Emit session:resume event for observability
    hooks = child_session.coordinator.get("hooks")
    if hooks:
        await hooks.emit(
            "session:resume",
            {
                "session_id": sub_session_id,
                "parent_id": parent_id,
                "agent_name": agent_name,
                "turn_count": len(transcript) + 1,
            },
        )

    # Restore transcript to context
    context = child_session.coordinator.get("context")
    if context and hasattr(context, "add_message"):
        for message in transcript:
            await context.add_message(message)
    else:
        logger.warning(
            f"Context module does not support add_message() - transcript not restored for session {sub_session_id}"
        )

    # Register temporary hook to capture orchestrator:complete data
    # This gives us status, turn_count, and metadata from the orchestrator
    completion_data: dict = {}
    hooks = child_session.coordinator.get("hooks")
    unregister_hook = None
    if hooks:
        from amplifier_core.hooks import HookResult

        async def _capture_completion(event: str, data: dict) -> HookResult:
            completion_data.update(data)
            return HookResult()

        unregister_hook = hooks.register(
            "orchestrator:complete",
            _capture_completion,
            priority=999,
            name="_spawn_capture",
        )
        lifecycle.unregister_hook = unregister_hook

    # Wire up cancellation propagation if parent session provided
    # Enables graceful Ctrl+C to stop the child after its current tool call
    if parent_session is not None:
        resume_parent_cancellation = parent_session.coordinator.cancellation
        resume_child_cancellation = child_session.coordinator.cancellation
        lifecycle.register_cancellation(
            resume_parent_cancellation, resume_child_cancellation
        )
        logger.debug(
            f"Registered child cancellation token for resumed sub-session {sub_session_id}"
        )

    # Expand @-mentions in the resumed instruction (consistent with spawn path).
    # Content lands inline as <context_file> XML blocks prepended to the instruction.
    if instruction:
        _resume_resolver = child_session.coordinator.get_capability("mention_resolver")
        if _resume_resolver is not None:
            from amplifier_foundation.mentions import expand_mentions_in_instruction

            _resume_dedup = child_session.coordinator.get_capability(
                "mention_deduplicator"
            )
            _resume_wd = child_session.coordinator.get_capability("session.working_dir")
            _resume_rel = Path(_resume_wd) if _resume_wd else Path.cwd()
            instruction = await expand_mentions_in_instruction(
                instruction,
                resolver=_resume_resolver,
                deduplicator=_resume_dedup,
                relative_to=_resume_rel,
            )

    # Execute and collect the transcript for normal persistence. If either await
    # is cancelled, the wrapper persists interruption state and tears down in
    # its shielded finalizer.
    try:
        response = await child_session.execute(instruction)
        updated_transcript = await context.get_messages() if context else []
    except asyncio.CancelledError:

        async def _persist_interrupted_resume() -> None:
            updated_transcript = transcript
            if context:
                try:
                    updated_transcript = await context.get_messages()
                except BaseException:
                    logger.exception(
                        "Failed to read interrupted sub-session %s transcript; "
                        "saving loaded transcript as fallback",
                        sub_session_id,
                    )
            metadata["turn_count"] = len(updated_transcript)
            metadata["last_updated"] = datetime.now(UTC).isoformat()
            metadata["status"] = "interrupted"
            try:
                store.save(sub_session_id, updated_transcript, metadata)
                logger.debug(
                    "Interrupted sub-session %s state persisted", sub_session_id
                )
            except BaseException:
                logger.exception(
                    "Failed to persist interrupted sub-session %s", sub_session_id
                )

        lifecycle.persist_on_interruption(_persist_interrupted_resume)
        raise

    # Update state for next resumption and clear any prior interruption marker.
    metadata["turn_count"] = len(updated_transcript)
    metadata["last_updated"] = datetime.now(UTC).isoformat()
    metadata.pop("status", None)

    store.save(sub_session_id, updated_transcript, metadata)
    logger.debug(
        f"Sub-session {sub_session_id} state updated (turn {metadata['turn_count']})"
    )

    # Bridge child session costs to parent coordinator (bridge_child_cost never raises)
    if parent_session is not None:
        await bridge_child_cost(
            child_coordinator=child_session.coordinator,
            parent_coordinator=parent_session.coordinator,
            child_session_id=sub_session_id,
        )

    # Return response and same session ID
    # Include enriched fields from orchestrator:complete hook
    return {
        "output": response,
        "session_id": sub_session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count", 1),
        "metadata": completion_data.get("metadata", {}),
    }


async def resume_sub_session(
    sub_session_id: str,
    instruction: str,
    parent_session: AmplifierSession | None = None,
) -> dict:
    """Run a resumed child under lifecycle management from construction onward."""

    # Keep this capability closure in the public resume implementation. Besides
    # preserving source-level compatibility, it is the callable mounted on the
    # resumed child and enables subprocess grandchildren.
    async def child_spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: "AmplifierSession",
        agent_configs: dict[str, dict],
        sub_session_id: str | None = None,
        tool_inheritance: dict[str, list[str]] | None = None,
        hook_inheritance: dict[str, list[str]] | None = None,
        orchestrator_config: dict | None = None,
        parent_messages: list[dict] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        session_metadata: dict | None = None,
        use_subprocess: bool = False,
    ) -> dict:
        return await spawn_sub_session(
            agent_name=agent_name,
            instruction=instruction,
            parent_session=parent_session,
            agent_configs=agent_configs,
            sub_session_id=sub_session_id,
            tool_inheritance=tool_inheritance,
            hook_inheritance=hook_inheritance,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
            session_metadata=session_metadata,
            use_subprocess=use_subprocess,
        )

    lifecycle = _SubSessionLifecycle()
    operation = _resume_sub_session(
        sub_session_id=sub_session_id,
        instruction=instruction,
        parent_session=parent_session,
        lifecycle=lifecycle,
        child_spawn_capability=child_spawn_capability,
    )
    return await _run_with_finalizer(operation, lifecycle)
