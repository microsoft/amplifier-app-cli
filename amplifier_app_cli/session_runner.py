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
            prompt_first_run_init(console)

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
        enable_collections=False,  # Collections deprecated
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
        # Provider/model override for recipe steps
        provider_override: str | None = None,
        model_override: str | None = None,
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
        )

    async def resume_capability(sub_session_id: str, instruction: str) -> dict:
        return await resume_sub_session(
            sub_session_id=sub_session_id,
            instruction=instruction,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)
    session.coordinator.register_capability("session.resume", resume_capability)
