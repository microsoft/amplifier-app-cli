"""Consolidated session initialization - single entry point for all session creation.

This module provides a unified approach to session initialization, eliminating code
duplication and ensuring consistent behavior across:
- New sessions vs resumed sessions
- Interactive (REPL) vs single-shot execution
- Bundle mode (only mode supported)

The canonical initialization order (enforced by this module):
1. Check first run / auto-install providers
2. Generate session ID if needed
3. Create CLI UX systems
4. Create session (bundle mode)
5. Register mention handling capability
6. Register session spawning capability
7. Restore transcript (resume only)
8. Register approval provider

Philosophy:
- Make it impossible for initialization paths to diverge
- Single source of truth for session setup
- Ruthless simplicity in the public API
"""

from __future__ import annotations

import logging
import sys
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_core import ModuleValidationError

from .session_store import SessionStore
from .ui.error_display import display_validation_error

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle
    from rich.console import Console

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    """All parameters needed to create and initialize a session.

    This dataclass captures every axis of variation:
    - config/search_paths/verbose: Always required
    - session_id: Generated if not provided (new session)
    - initial_transcript: If provided, this is a resume
    - prepared_bundle: Required for bundle mode
    - bundle_name: For display and metadata

    Note: bundle_base_path for @mention resolution is handled internally by
    foundation's PreparedBundle.create_session() via source_base_paths dict.
    """

    # Required configuration
    config: dict
    search_paths: list[Path]
    verbose: bool

    # Session identity
    session_id: str | None = None  # None = generate new UUID
    bundle_name: str = "unknown"

    # Resume mode (if provided, this is a resume)
    initial_transcript: list[dict] | None = None

    # Bundle mode (required)
    prepared_bundle: "PreparedBundle | None" = None

    # Execution mode
    output_format: str = "text"  # text | json | json-trace

    @property
    def is_resume(self) -> bool:
        """True if this is resuming an existing session."""
        return self.initial_transcript is not None


@dataclass
class InitializedSession:
    """Result of session initialization - ready for execution."""

    session: AmplifierSession
    session_id: str
    config: SessionConfig
    store: SessionStore = field(default_factory=SessionStore)

    async def cleanup(self):
        """Clean up session resources."""
        await self.session.cleanup()


async def create_initialized_session(
    config: SessionConfig,
    console: "Console",
) -> InitializedSession:
    """Create and fully initialize a session.

    This is the SINGLE entry point for all session creation.
    It handles:
    - Provider auto-installation (check_first_run)
    - Bundle mode session creation
    - New session vs resume
    - All capability registration in canonical order

    The canonical initialization order:
    1. Check first run / auto-install providers if needed
    2. Generate session ID if not provided
    3. Create CLI UX systems
    4. Create session (bundle mode)
    5. Register mention handling capability
    6. Register session spawning capability
    7. Restore transcript (resume only)
    8. Register approval provider

    Args:
        config: SessionConfig with all parameters
        console: Rich console for output

    Returns:
        InitializedSession ready for execution

    Raises:
        SystemExit: On initialization failure (after displaying error)
    """
    from .commands.init import check_first_run
    from .commands.init import prompt_first_run_init
    from .ui import CLIApprovalSystem
    from .ui import CLIDisplaySystem

    # Step 1: Check first run / auto-install providers
    # This is critical - without this, resume commands fail after updates
    if check_first_run():
        # For new interactive sessions, prompt for setup
        # For resume/single-shot, check_first_run() handles auto-fix internally
        if not config.is_resume:
            if sys.stdin.isatty():
                prompt_first_run_init(console)
            else:
                # Non-interactive context (CI, Docker, shadow env)
                # Auto-init from environment variables
                from .commands.init import auto_init_from_env

                auto_init_from_env(console)

    # Step 2: Generate session ID if not provided
    session_id = config.session_id or str(uuid.uuid4())

    # Step 3: Create CLI UX systems (app-layer policy)
    approval_system = CLIApprovalSystem()
    display_system = CLIDisplaySystem()

    # Step 4: Create session (bundle mode only)
    session = await _create_bundle_session(
        config=config,
        session_id=session_id,
        approval_system=approval_system,
        display_system=display_system,
        console=console,
    )

    # Set root session metadata (propagates to child sessions via config deep-merge)
    # These enable hook modules to create rich context metadata
    session.config["root_session_id"] = session_id
    session.config["application_host"] = "Amplifier CLI"
    if getattr(config, "bundle_name", None):
        session.config["bundle_name"] = config.bundle_name
    try:
        cwd = str(Path.cwd().resolve())
        session.config["working_dir"] = cwd
        # Project identity: slug for deterministic lookups, full path for filesystem context
        from .project_utils import get_project_slug

        session.config["project_slug"] = get_project_slug()
        session.config["project_dir"] = cwd
    except OSError:
        pass  # CWD may be unavailable in sandboxed/container environments

    # Step 7: Restore transcript (resume only)
    if config.is_resume and config.initial_transcript:
        context = session.coordinator.get("context")
        if context and hasattr(context, "set_messages"):
            # CRITICAL: create_session() already added a fresh system prompt.
            # We need to preserve it because the transcript might have lost its system message
            # during compaction (bug fixed in context-simple, but old sessions are affected).
            fresh_system_msg = None
            if hasattr(context, "get_messages"):
                current_msgs = await context.get_messages()
                system_msgs = [m for m in current_msgs if m.get("role") == "system"]
                if system_msgs:
                    fresh_system_msg = system_msgs[0]
                    logger.debug(
                        "Preserved fresh system prompt (%d chars)",
                        len(fresh_system_msg.get("content", "")),
                    )

            # Restore the transcript
            await context.set_messages(config.initial_transcript)
            logger.info(
                "Restored %d messages from transcript", len(config.initial_transcript)
            )

            # Check if transcript has a system message; if not, re-inject the fresh one
            if fresh_system_msg:
                restored_msgs = await context.get_messages()
                has_system = any(m.get("role") == "system" for m in restored_msgs)
                if not has_system:
                    logger.warning(
                        "Transcript missing system prompt - re-injecting from bundle"
                    )
                    # Prepend system message to restored messages
                    await context.set_messages([fresh_system_msg] + restored_msgs)
                    logger.info(
                        "Re-injected system prompt (%d chars)",
                        len(fresh_system_msg.get("content", "")),
                    )
        elif config.initial_transcript:
            logger.warning(
                "Context module lacks set_messages - transcript NOT restored"
            )

    # Step 10: Register approval provider (app-layer policy)
    from .approval_provider import CLIApprovalProvider

    register_provider = session.coordinator.get_capability("approval.register_provider")
    if register_provider:
        approval_provider = CLIApprovalProvider(console)
        register_provider(approval_provider)
        logger.debug("Registered CLIApprovalProvider for interactive approvals")

    return InitializedSession(
        session=session,
        session_id=session_id,
        config=config,
    )


async def _create_bundle_session(
    config: SessionConfig,
    session_id: str,
    approval_system: Any,
    display_system: Any,
    console: "Console",
) -> AmplifierSession:
    """Create session using bundle mode (foundation handles most setup).

    Steps performed:
    4a. Wrap bundle resolver with app-layer fallback
    4b. Inject user providers
    4c. Call prepared_bundle.create_session() (handles init internally)
    5. Register mention handling (wraps foundation's resolver)
    6. Register session spawning
    """
    from .lib.bundle_loader import AppModuleResolver
    from .paths import create_foundation_resolver
    from .runtime.config import inject_user_providers

    prepared_bundle = config.prepared_bundle
    assert prepared_bundle is not None  # Guaranteed by is_bundle_mode check

    # Step 4a: Wrap bundle resolver with app-layer fallback
    fallback_resolver = create_foundation_resolver()
    prepared_bundle.resolver = AppModuleResolver(  # type: ignore[assignment]
        bundle_resolver=prepared_bundle.resolver,
        settings_resolver=fallback_resolver,
    )

    # Step 4b: Inject user providers
    inject_user_providers(config.config, prepared_bundle)

    # Step 4c: Create session (foundation handles init internally)
    # Self-healing: The kernel intentionally swallows module load errors to be resilient.
    # If providers fail to load due to stale install state (missing dependencies),
    # the session is created but with no providers mounted. We detect this and retry.
    core_logger = logging.getLogger("amplifier_core")
    original_level = core_logger.level
    if not config.verbose:
        core_logger.setLevel(logging.CRITICAL)

    try:
        with console.status("[dim]Loading...[/dim]", spinner="dots"):
            session = await prepared_bundle.create_session(
                session_id=session_id,
                approval_system=approval_system,
                display_system=display_system,
                session_cwd=Path.cwd(),  # CLI uses CWD for local @-mentions
                is_resumed=config.is_resume,  # Pass resume flag to kernel
            )

            # Self-healing check: if configured modules failed to load,
            # this likely indicates stale install state (missing dependencies).
            # Invalidate all install state and retry once.
            if _should_attempt_self_healing(session, prepared_bundle):
                logger.warning(
                    "Some modules failed to load despite being configured. "
                    "Likely stale install state - invalidating and retrying..."
                )
                _invalidate_all_install_state(prepared_bundle)
                # Retry once - if it fails again, it's a real error
                session = await prepared_bundle.create_session(
                    session_id=session_id,
                    approval_system=approval_system,
                    display_system=display_system,
                )
                # Warn if retry still has issues
                if _should_attempt_self_healing(session, prepared_bundle):
                    logger.warning(
                        "Self-healing retry completed but some modules still failed to load. "
                        "Check module configuration, credentials, and dependencies."
                    )
    except (ModuleValidationError, RuntimeError) as e:
        core_logger.setLevel(original_level)
        if not display_validation_error(console, e, verbose=config.verbose):
            console.print(f"[red]Error:[/red] {e}")
            if config.verbose:
                console.print_exception()
        sys.exit(1)
    finally:
        core_logger.setLevel(original_level)

    # Step 5: Register mention handling (wrap foundation's resolver)
    register_mention_handling(session)

    # Step 6: Register session spawning
    register_session_spawning(session)

    return session


def register_mention_handling(session: AmplifierSession) -> None:
    """Register mention resolver capability on a session.

    Wraps foundation's BaseMentionResolver (registered by create_session)
    with AppMentionResolver to add app shortcuts (@user:, @project:, @~/).
    Foundation resolver handles all bundle namespaces (@recipes:, @foundation:, etc.)

    Per KERNEL_PHILOSOPHY: Foundation provides mechanism (bundle namespaces),
    app provides policy (shortcuts, resolution order).

    Args:
        session: The AmplifierSession to register capabilities on
    """
    from .lib.mention_loading import AppMentionResolver

    # Wrap foundation's resolver with app shortcuts
    # Foundation resolver already has all bundle namespaces from composition
    foundation_resolver = session.coordinator.get_capability("mention_resolver")
    mention_resolver = AppMentionResolver(
        foundation_resolver=foundation_resolver,
    )

    session.coordinator.register_capability("mention_resolver", mention_resolver)


def register_session_spawning(session: AmplifierSession) -> None:
    """Register session spawning capabilities for agent delegation.

    This is app-layer policy that enables kernel modules (like tool-task) to
    spawn sub-sessions without directly importing from the app layer.

    The capabilities registered:
    - session.spawn: Create new agent sub-session
    - session.resume: Resume existing sub-session

    Args:
        session: The AmplifierSession to register capabilities on
    """
    from .session_spawner import resume_sub_session
    from .session_spawner import spawn_sub_session

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: AmplifierSession,
        agent_configs: dict[str, dict],
        sub_session_id: str | None = None,
        tool_inheritance: dict[str, list[str]] | None = None,
        hook_inheritance: dict[str, list[str]] | None = None,
        orchestrator_config: dict | None = None,
        parent_messages: list[dict] | None = None,
        # Provider/model override (legacy - use provider_preferences instead)
        provider_override: str | None = None,
        model_override: str | None = None,
        # Provider preferences (ordered fallback chain)
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
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
            provider_override=provider_override,
            model_override=model_override,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
        )

    async def resume_capability(sub_session_id: str, instruction: str) -> dict:
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)
    session.coordinator.register_capability("session.resume", resume_capability)


# =============================================================================
# Self-healing helpers for stale install state
# =============================================================================


def _should_attempt_self_healing(
    session: AmplifierSession, prepared_bundle: "PreparedBundle"
) -> bool:
    """Check if self-healing should be attempted for a session.

    Self-healing is needed when modules were configured but COMPLETELY failed to load.
    The kernel intentionally swallows module load errors for resilience,
    so we detect "configured but not loaded" by comparing mount plan to
    actually mounted modules.

    This typically happens when install-state.json says modules are installed,
    but dependencies are missing (e.g., after uv tool reinstall).

    IMPORTANT: We only trigger self-healing on COMPLETE failure (no modules loaded),
    not partial failure (some modules loaded). Partial failures are often benign
    (e.g., Azure OpenAI failing if user doesn't need it) and the session can
    continue with the providers that did load. Self-healing on partial failures
    causes more problems than it solves because it can't actually fix the issue
    (would need to re-prepare the bundle).

    Module types checked:
    - providers: coordinator.get("providers") returns dict
    - tools: coordinator.get("tools") returns dict
    - orchestrator/context: Required, raise RuntimeError on failure (no check needed)
    - hooks: HookRegistry always exists, individual failures hard to detect (skipped)

    Args:
        session: The created session to check.
        prepared_bundle: The bundle that was used to create the session.

    Returns:
        True if self-healing should be attempted (only on complete failure).
    """
    mount_plan = prepared_bundle.mount_plan
    coordinator = session.coordinator

    # --- Providers ---
    # coordinator.get("providers") returns dict (public API)
    configured_providers = mount_plan.get("providers", [])
    mounted_providers = coordinator.get("providers") or {}

    # Extract provider IDs for logging
    configured_provider_ids = [
        p.get("module", p) if isinstance(p, dict) else str(p)
        for p in configured_providers
    ]
    mounted_provider_ids = list(mounted_providers.keys())

    # Normalize module IDs to provider names for accurate comparison
    # Module IDs are like "provider-anthropic", provider names are like "anthropic"
    def _normalize_to_provider_name(module_id: str) -> str:
        """Convert module ID to provider name by stripping 'provider-' prefix."""
        if module_id.startswith("provider-"):
            return module_id[9:]  # Strip "provider-" prefix
        return module_id

    configured_provider_names = [
        _normalize_to_provider_name(pid) for pid in configured_provider_ids
    ]

    logger.debug(
        f"self_healing_check: configured_providers={configured_provider_ids}, "
        f"mounted_providers={mounted_provider_ids}"
    )

    # Only heal on COMPLETE failure - no providers loaded at all
    if configured_providers and not mounted_providers:
        logger.info(
            f"COMPLETE provider failure detected: {len(configured_providers)} configured, "
            f"0 loaded. Configured: {configured_provider_ids}. Triggering self-healing."
        )
        return True

    # Partial provider failure - log warning but continue with what loaded
    # Don't trigger self-healing for partial failures (often benign)
    if len(mounted_providers) < len(configured_providers):
        failed_providers = set(configured_provider_names) - set(mounted_provider_ids)
        logger.warning(
            f"Partial provider failure: {len(mounted_providers)}/{len(configured_providers)} loaded. "
            f"Failed: {failed_providers}. Loaded: {mounted_provider_ids}. "
            "Session continuing with available providers (self-healing NOT triggered for partial failure)."
        )
        # Don't return True - let session continue with partial providers

    # --- Tools ---
    # coordinator.get("tools") returns dict (public API)
    configured_tools = mount_plan.get("tools", [])
    mounted_tools = coordinator.get("tools") or {}

    # Extract tool IDs for logging
    configured_tool_ids = [
        t.get("module", t) if isinstance(t, dict) else str(t) for t in configured_tools
    ]
    mounted_tool_ids = list(mounted_tools.keys())

    logger.debug(
        f"self_healing_check: configured_tools={len(configured_tool_ids)}, "
        f"mounted_tools={len(mounted_tool_ids)}"
    )

    # Only heal on COMPLETE failure - no tools loaded at all
    if configured_tools and not mounted_tools:
        logger.info(
            f"COMPLETE tool failure detected: {len(configured_tools)} configured, "
            f"0 loaded. Triggering self-healing."
        )
        return True

    # Partial tool failure - log warning but continue with what loaded
    if len(mounted_tools) < len(configured_tools):
        failed_tools = set(configured_tool_ids) - set(mounted_tool_ids)
        logger.warning(
            f"Partial tool failure: {len(mounted_tools)}/{len(configured_tools)} loaded. "
            f"Failed: {failed_tools}. "
            "Session continuing with available tools (self-healing NOT triggered for partial failure)."
        )
        # Don't return True - let session continue with partial tools

    # --- Hooks ---
    # HookRegistry always exists at coordinator.get("hooks"), individual hook
    # failures are swallowed and hard to detect via public API. Skipped for now.

    # --- Orchestrator/Context ---
    # These are required and raise RuntimeError on failure during session.initialize().
    # If we reach this point, they loaded successfully. No check needed.

    logger.debug(
        "self_healing_check: no complete failures detected, self-healing not needed"
    )
    return False


def _invalidate_all_install_state(prepared_bundle: "PreparedBundle") -> None:
    """Invalidate all install state to force reinstall of all modules.

    This is a more aggressive approach than invalidating specific modules,
    but necessary when we can't determine exactly which module failed
    (because the kernel swallows errors).

    Args:
        prepared_bundle: The PreparedBundle containing the resolver.
    """
    try:
        resolver = prepared_bundle.resolver
        resolver_type = type(resolver).__name__
        logger.debug(f"invalidate_install_state: resolver type is {resolver_type}")

        # Access the activator - handle both direct BundleModuleResolver
        # and AppModuleResolver (which wraps BundleModuleResolver in _bundle)
        activator = getattr(resolver, "_activator", None)
        if activator:
            logger.debug(
                f"invalidate_install_state: found activator directly on {resolver_type}"
            )
        else:
            # Try unwrapping AppModuleResolver to get underlying BundleModuleResolver
            bundle_resolver = getattr(resolver, "_bundle", None)
            if bundle_resolver:
                bundle_resolver_type = type(bundle_resolver).__name__
                logger.debug(
                    f"invalidate_install_state: unwrapping {resolver_type} -> {bundle_resolver_type}"
                )
                activator = getattr(bundle_resolver, "_activator", None)
                if activator:
                    logger.debug(
                        f"invalidate_install_state: found activator on wrapped {bundle_resolver_type}"
                    )
            else:
                logger.debug(
                    f"invalidate_install_state: no _bundle attribute on {resolver_type}"
                )

        if not activator:
            logger.warning(
                f"No activator found on resolver ({resolver_type}) - cannot invalidate install state. "
                "This may happen if the bundle was not prepared with an activator."
            )
            return

        activator_type = type(activator).__name__
        logger.debug(f"invalidate_install_state: activator type is {activator_type}")

        # Access install state manager
        install_state = getattr(activator, "_install_state", None)
        if not install_state:
            logger.warning(
                f"No install state manager found on activator ({activator_type}) - cannot invalidate. "
                "This may happen if ModuleActivator was created without install state tracking."
            )
            return

        install_state_type = type(install_state).__name__
        logger.debug(
            f"invalidate_install_state: install_state type is {install_state_type}"
        )

        # Invalidate all modules
        install_state.invalidate(None)
        install_state.save()
        logger.info(
            "Successfully invalidated all install state for self-healing. "
            "Modules will be reinstalled on next activation."
        )

        # Clear the activator's activated set so it will re-activate all modules
        activated = getattr(activator, "_activated", None)
        if activated:
            num_activated = len(activated)
            activated.clear()
            logger.debug(
                f"Cleared activator's activated set ({num_activated} modules were marked as activated)"
            )

    except Exception as e:
        logger.warning(f"Failed to invalidate install state: {e}")
