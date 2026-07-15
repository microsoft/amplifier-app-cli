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
7.5. Restore cumulative session cost (resume only)
7.6. Warn (and confirm) on provider/model mismatch at resume (resume only)
8. Register approval provider

Philosophy:
- Make it impossible for initialization paths to diverge
- Single source of truth for session setup
- Ruthless simplicity in the public API
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import click
from amplifier_core import AmplifierSession
from amplifier_core import ModuleValidationError

from .effective_config import get_effective_config_summary
from .lib.settings import AppSettings
from .session_store import SessionStore
from .ui.error_display import display_validation_error
from .utils.error_format import escape_markup

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
    configurator: Any = None

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
    7.6. Warn (and confirm) on provider/model mismatch (resume only)
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

    # Set root session metadata once — propagates to all child sessions via config deep-merge.
    # Guards ensure values are only stamped on first creation (root session); child sessions
    # inherit parent values via config deep-merge and the guards prevent overwriting them.
    # cwd is initialised before the try so the post-session block can always reference it
    # (empty string is the safe fallback for sandboxed/container environments).
    cwd = ""
    try:
        from .project_utils import get_project_slug

        cwd = str(Path.cwd().resolve())
        config.config["working_dir"] = cwd
        if "root_session_id" not in config.config:
            config.config["root_session_id"] = session_id
        if "application_host" not in config.config:
            config.config["application_host"] = "Amplifier CLI"
        if "bundle_name" not in config.config:
            config.config["bundle_name"] = config.bundle_name
        if "project_slug" not in config.config:
            config.config["project_slug"] = get_project_slug()
        if "project_dir" not in config.config:
            config.config["project_dir"] = cwd
        if "project_name" not in config.config:
            config.config["project_name"] = Path(cwd).name
    except OSError:
        pass  # CWD may be unavailable in sandboxed/container environments

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

    # Belt-and-suspenders: ensure session.config (== coordinator.config) carries the same
    # root-level metadata that was written into config.config above.  This matters because
    # the foundation layer may copy config.config into a fresh dict when building the
    # coordinator, so session.config and config.config are not guaranteed to be the same
    # object.  Hooks read from coordinator.config, so we must ensure the values are there.
    # Guards mirror the pre-session guards: child sessions inherit values from the parent
    # via config deep-merge, so we only fill in missing values, never overwrite.
    session.config["working_dir"] = cwd
    if "root_session_id" not in session.config:
        session.config["root_session_id"] = config.config.get(
            "root_session_id", session_id
        )
    if "application_host" not in session.config:
        session.config["application_host"] = config.config.get(
            "application_host", "Amplifier CLI"
        )
    if "bundle_name" not in session.config:
        session.config["bundle_name"] = config.bundle_name
    if "project_slug" not in session.config:
        session.config["project_slug"] = config.config.get("project_slug", "")
    if "project_dir" not in session.config:
        session.config["project_dir"] = config.config.get("project_dir", cwd)
    if "project_name" not in session.config:
        session.config["project_name"] = config.config.get(
            "project_name", Path(cwd).name if cwd else ""
        )

    # Step 7: Restore transcript (resume only)
    # NOTE: Transcript repair (orphaned tool calls, ordering violations) is handled
    # by _repair_transcript_if_needed() in main.py, which runs before every LLM call.
    # This covers both resume and mid-session Ctrl+C cases in a single code path.
    if config.is_resume and config.initial_transcript:
        transcript_to_restore = config.initial_transcript

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

            # Restore the (possibly repaired) transcript
            await context.set_messages(transcript_to_restore)
            logger.info(
                "Restored %d messages from transcript", len(transcript_to_restore)
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

    # Step 7.5: Restore cumulative session cost (resume only) - issue #284
    # Provider cost accumulators live in each provider's mount() closure and are
    # zeroed on resume, so the running session-cost total would otherwise restart
    # from zero. Re-seed the "session.cost" channel from the persisted
    # events.jsonl (sibling of transcript.jsonl in the session directory) by
    # registering a synthetic historical contributor on this session's own
    # coordinator. Best-effort: never blocks startup.
    if config.is_resume:
        from .cost_history import restore_session_cost

        try:
            events_path = SessionStore().base_dir / session_id / "events.jsonl"
            restore_session_cost(session.coordinator, session_id, events_path)
        except Exception:
            logger.debug("Prior session cost restore skipped", exc_info=True)

    # Step 7.6: Warn (and, on a tty, confirm) on provider/model mismatch at
    # resume - amplifier-support#208 Wave 2 / Option A. This is the ONE
    # chokepoint every resume surface (amplifier run --resume, amplifier
    # session resume, amplifier resume, amplifier continue) passes through,
    # before the first session.execute() call. Runs only for resumes; silent
    # (no console output, no prompt) when there's nothing to warn about.
    if config.is_resume:
        await _warn_on_resume_provider_mismatch(config, session_id, console, session)

    # Step 10: Register approval provider (app-layer policy)
    from .approval_provider import CLIApprovalProvider
    from .stdin_arbiter import StdinArbiter

    arbiter = StdinArbiter()
    session.coordinator.register_capability("cli.stdin_arbiter", arbiter)

    register_provider = session.coordinator.get_capability("approval.register_provider")
    if register_provider:
        approval_provider = CLIApprovalProvider(console, arbiter=arbiter)
        register_provider(approval_provider)
        logger.debug("Registered CLIApprovalProvider for interactive approvals")

    # Step 11: Create SessionConfigurator (if foundation available and bundle present)
    configurator = None
    if config.prepared_bundle is not None:
        try:
            from amplifier_foundation.configurator import SessionConfigurator

            configurator = SessionConfigurator(session, config.prepared_bundle)
            app_settings = AppSettings()
            merged = app_settings.get_merged_settings()
            configurator_settings = merged.get("configurator") or {}
            await configurator.apply_saved_settings(configurator_settings)
            configurator.take_snapshot()
        except ImportError:
            logger.debug(
                "amplifier_foundation.configurator not available, "
                "skipping SessionConfigurator setup"
            )
            configurator = None
        except Exception as e:
            logger.warning("SessionConfigurator initialization failed: %s", e)
            configurator = None

    return InitializedSession(
        session=session,
        session_id=session_id,
        config=config,
        configurator=configurator,
    )


# =============================================================================
# Resume-time provider/model mismatch check (amplifier-support#208, Wave 2)
# =============================================================================
#
# Sessions that switch providers mid-conversation can persist provider-specific
# content (e.g. Anthropic thinking-block signatures) that bricks the session
# when replayed against a different provider (400s on invalid signatures).
# Wave 1 sanitized/repaired that content at the provider boundary. This is
# the resume-time guardrail (Option A, ack'd by the maintainer): warn -- and,
# interactively, require confirmation -- when the provider/model about to
# handle a resumed session differs from whichever provider/model last wrote
# its metadata.


def _normalize_provider_identity(value: str | None) -> str:
    """Normalize a provider identity for mismatch comparison.

    Provider identity may be recorded as a module id ("provider-anthropic")
    or a bare provider name ("anthropic") depending on the source/vintage of
    the metadata. Strip the "provider-" prefix and lowercase so both forms
    compare equal, regardless of which side (stored metadata vs. active
    config) uses which form.

    Mirrors the nested _normalize_to_provider_name() helper inside
    _should_attempt_self_healing() above, but is exposed at module level
    since it is also needed here to compare against the "provider" field
    persisted by main.py's session-save code.

    Args:
        value: Provider identity string (module id or bare name), or None.

    Returns:
        Lowercased bare provider name (e.g. "anthropic"), or "" if value is
        falsy.
    """
    if not value:
        return ""
    normalized = value.strip().lower()
    if normalized.startswith("provider-"):
        normalized = normalized[len("provider-") :]
    return normalized


async def _warn_on_resume_provider_mismatch(
    config: SessionConfig,
    session_id: str,
    console: "Console",
    session: AmplifierSession,
) -> None:
    """Warn (and, on a tty, require confirmation) on provider/model mismatch.

    Compares the provider/model that is about to handle this resumed session
    (the ACTIVE config, resolved with any --provider/--model overrides already
    applied) against whichever provider/model last wrote this session's
    metadata.json (the PRIOR writer).

    Feature-detection: pre-existing sessions (saved before this PR) have no
    "provider" field in their metadata. For those, comparison falls back to
    model-only. Sessions saved by this PR (or later) carry "provider" and get
    the full provider+model comparison.

    On match, or when there is no prior metadata to compare against at all:
    completely silent -- zero behavior change and zero console output.

    On mismatch:
    - stdin is a tty: print the warning, then require explicit confirmation
      via `click.confirm` (offloaded to a worker thread -- see the
      KeyboardInterrupt handler in main.py for why this offload is mandatory:
      click.confirm() performs a blocking, synchronous stdin read that would
      otherwise freeze the event loop). Declining raises click.Abort(),
      which aborts the resume before any session.execute() can run.
    - stdin is not a tty (CI, piped input, scripted flows): print the warning
      and continue automatically. Scripted flows must never hang waiting for
      input that will never arrive.

    This function is best-effort with respect to loading prior metadata: any
    failure there (corrupt/missing metadata.json, invalid session_id, etc.)
    is swallowed and treated as "nothing to compare against" -- this check
    must never turn a resume that would otherwise have succeeded into a
    failure.

    Args:
        config: The (already resume-mode) SessionConfig for this session.
            config.config is the fully-resolved active configuration (CLI
            --provider/--model overrides are applied before this point, so
            comparing against it automatically respects them).
        session_id: The resolved session id being resumed.
        console: Rich console for the warning. In JSON output modes,
            create_initialized_session's caller has already redirected
            console.file to stderr before create_initialized_session is
            invoked, so this can never contaminate JSON stdout.
        session: The already-created AmplifierSession (Steps 1-7.5 have run
            by this point). On decline, this is cleaned up before raising
            click.Abort so we never leave a half-initialized session behind.

    Raises:
        click.Abort: if stdin is a tty and the user declines to continue.
    """
    try:
        prior_metadata = SessionStore().get_metadata(session_id)
    except Exception:
        logger.debug(
            "Resume provider-mismatch check: could not load prior metadata "
            "for session %s",
            session_id,
            exc_info=True,
        )
        return

    prior_model = prior_metadata.get("model")
    prior_provider = prior_metadata.get("provider")

    # Nothing recorded to compare against (e.g. corrupted/minimal recovered
    # metadata, or a session saved before "model" was ever written) -- there
    # is nothing to warn about.
    if not prior_model and not prior_provider:
        return

    summary = get_effective_config_summary(config.config, config.bundle_name)
    active_provider = summary.provider_module
    active_model = summary.model

    if prior_provider:
        # Full comparison: normalized provider identity AND model.
        if (
            _normalize_provider_identity(prior_provider)
            == _normalize_provider_identity(active_provider)
            and prior_model == active_model
        ):
            return  # Exact match -- silent, zero behavior change.
    else:
        # Feature-detection fallback for pre-existing sessions that predate
        # the "provider" metadata field: compare model only.
        if prior_model == active_model:
            return  # Silent -- nothing more to check without a prior provider.

    prior_label = f"{prior_provider or 'unknown'}/{prior_model or 'unknown'}"
    active_label = f"{active_provider}/{active_model}"

    console.print(
        f"[yellow]\u26a0 Provider/model mismatch:[/yellow] session was last "
        f"written by {prior_label} \u2014 now resuming with {active_label}\n"
        "[dim]  Cross-provider replay can fail on provider-specific content "
        "(e.g. thinking blocks).[/dim]"
    )

    if sys.stdin.isatty():
        # click.confirm() performs a synchronous, canonical-mode blocking
        # stdin read (input()) with no executor offload. Calling it directly
        # here would block the ENTIRE asyncio event loop thread until Enter
        # is pressed. Offload to a worker thread, mirroring the existing
        # pattern in main.py's KeyboardInterrupt handler and
        # approval_provider.py's _get_user_input().
        if not await asyncio.to_thread(
            click.confirm,
            "Continue resuming with the new provider?",
            default=False,
        ):
            console.print("[dim]Resume cancelled.[/dim]")
            # Never leave a half-initialized session behind: Steps 1-7.5 have
            # already created and partially wired up `session` by this point.
            try:
                await session.cleanup()
            except Exception:
                logger.debug(
                    "Session cleanup after declined resume failed", exc_info=True
                )
            raise click.Abort()
        _record_provider_mismatch(session_id, prior_label, active_label)
    else:
        # Non-interactive (CI, piped stdin, shadow environments): warn-only.
        # Scripted flows must not hang waiting for input that can't arrive.
        _record_provider_mismatch(session_id, prior_label, active_label)


def _record_provider_mismatch(session_id: str, prior: str, active: str) -> None:
    """Best-effort audit trail for an accepted (or warned-through) mismatch.

    Mirrors _record_bundle_override() in commands/session.py: appends a
    timestamped entry to a metadata list rather than overwriting anything.
    Failures here are swallowed -- this is a diagnostics nicety, never a
    condition that should fail the resume itself.

    Args:
        session_id: The session whose metadata should record the mismatch.
        prior: Display label for the provider/model that last wrote the
            session (e.g. "provider-anthropic/claude-x").
        active: Display label for the provider/model now resuming it.
    """
    try:
        store = SessionStore()
        metadata = store.get_metadata(session_id)
        mismatches = list(metadata.get("provider_mismatches", []))
        mismatches.append(
            {
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "previous": prior,
                "resumed_with": active,
            }
        )
        store.update_metadata(session_id, {"provider_mismatches": mismatches})
    except Exception:
        logger.debug(
            "Could not record provider-mismatch audit trail for session %s",
            session_id,
            exc_info=True,
        )


_CLEANUP_EVENTS: tuple[str, ...] = (
    # PR #183 — cleanup-window diagnostic events emitted by app-cli's main.py only
    "cleanup:render_begin",
    "cleanup:render_end",
    "cleanup:store_begin",
    "cleanup:store_end",
    "cleanup:finally_begin",
    "cleanup:finally_end",
)


def _inject_observability_events(prepared_bundle: "PreparedBundle") -> None:
    """Register app-cli cleanup-window event names (PR #183).

    ``session:config`` is now handled by foundation's create_session().
    These events are emitted by app-cli's main.py only, so they belong here.

    Args:
        prepared_bundle: The PreparedBundle whose mount_plan will be updated
            in-place.  Must be called after inject_user_providers() (step 4b)
            but before create_session() (step 4c).
    """
    from amplifier_foundation import inject_additional_events

    inject_additional_events(prepared_bundle.mount_plan, _CLEANUP_EVENTS)


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

    # Step 4b-post: Register app-cli observability event names with subscriber hooks.
    # hooks-logging and hook-context-intelligence read config["additional_events"] in
    # their mount() / _setup_and_register() paths and register handlers for each name.
    # This MUST run before create_session() (which calls mount() internally) so the
    # config dict is populated when each hook module is mounted.
    _inject_observability_events(prepared_bundle)

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
                    session_cwd=Path.cwd(),  # CLI uses CWD for local @-mentions
                    is_resumed=config.is_resume,  # Pass resume flag to kernel
                )
                # Warn if retry still has issues
                if _should_attempt_self_healing(session, prepared_bundle):
                    logger.warning(
                        "Self-healing retry completed but some modules still failed to load. "
                        "Check module configuration, credentials, and dependencies."
                    )
    except (ModuleValidationError, RuntimeError) as e:
        if not display_validation_error(console, e, verbose=config.verbose):
            console.print(f"[red]Error:[/red] {escape_markup(e)}")
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

    # Extract provider IDs for comparison.
    # Prefer instance_id (mount plan field) or id (settings field) when present so that
    # two entries with the same module but different instance_ids are treated as distinct.
    # Fall back to the module name for legacy entries that carry no instance identifier.
    configured_provider_ids = []
    for p in configured_providers:
        if isinstance(p, dict):
            pid = p.get("instance_id") or p.get("id") or p.get("module", "")
            configured_provider_ids.append(pid)
        else:
            configured_provider_ids.append(str(p))
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
    # Don't trigger self-healing for partial failures (often benign).
    # Use Counter instead of set so duplicate type names (multi-instance providers)
    # are counted accurately rather than collapsed.
    configured_counts = Counter(configured_provider_names)
    mounted_counts = Counter(mounted_provider_ids)
    if sum(configured_counts.values()) > sum(mounted_counts.values()):
        missing = dict(configured_counts - mounted_counts)
        logger.warning(
            f"Partial provider failure: {sum(mounted_counts.values())}/{sum(configured_counts.values())} loaded. "
            f"Missing: {missing}. Loaded: {mounted_provider_ids}. "
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
