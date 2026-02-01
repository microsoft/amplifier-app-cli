"""Incremental session save hook for transcript persistence.

Saves transcript.jsonl after each tool completion (tool:post event),
providing crash recovery between tool calls rather than just between turns.

This is a non-blocking hook that uses the existing SessionStore for atomic writes.
"""

from __future__ import annotations

import logging
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from amplifier_core import AmplifierSession

from .session_store import SessionStore

logger = logging.getLogger(__name__)


class IncrementalSaveHook:
    """Hook that saves session transcript after each tool completion.

    Contract:
    - Listens for: tool:post events
    - Side effects: Writes transcript.jsonl and metadata.json via SessionStore
    - Debouncing: Skips save if message count hasn't changed since last save
    - Thread safety: Uses SessionStore's atomic write mechanism

    Usage:
        hook = IncrementalSaveHook(session, store, session_id, bundle_name, config)
        hooks.register("tool:post", hook.on_tool_post, priority=900, name="incremental_save")
    """

    def __init__(
        self,
        session: "AmplifierSession",
        store: SessionStore,
        session_id: str,
        bundle_name: str,
        config: dict[str, Any],
    ):
        """Initialize incremental save hook.

        Args:
            session: The AmplifierSession to save transcripts for
            store: SessionStore instance for persistence
            session_id: Session identifier
            bundle_name: Bundle name for metadata (e.g., "bundle:foundation")
            config: Session configuration for extracting model info
        """
        self.session = session
        self.store = store
        self.session_id = session_id
        self.bundle_name = bundle_name
        self.config = config
        self._last_message_count = 0

    async def on_tool_post(self, event: str, data: dict[str, Any]):
        """Save transcript after tool completion.

        Debounces by checking if message count has changed since last save.
        This prevents redundant saves while ensuring we capture new messages.

        Args:
            event: Event name (tool:post)
            data: Event data containing tool_name and result

        Returns:
            HookResult with action="continue" (never blocks)
        """
        from amplifier_core.models import HookResult

        try:
            # Get context and message count
            context = self.session.coordinator.get("context")
            if not context or not hasattr(context, "get_messages"):
                return HookResult(action="continue")

            messages = await context.get_messages()
            current_count = len(messages)

            # Debounce: skip if no new messages
            if current_count <= self._last_message_count:
                logger.debug(
                    f"Incremental save skipped: no new messages ({current_count})"
                )
                return HookResult(action="continue")

            # Update debounce counter
            self._last_message_count = current_count

            # Extract model name from config
            model_name = self._extract_model_name()

            # Load existing metadata to preserve fields like name, description
            # that may have been set by other hooks (e.g., session-naming)
            existing_metadata = self.store.get_metadata(self.session_id) or {}

            # Build metadata, preserving existing fields while updating dynamic ones
            metadata = {
                **existing_metadata,  # Preserve name, description, etc.
                "session_id": self.session_id,
                "created": existing_metadata.get(
                    "created", datetime.now(UTC).isoformat()
                ),
                "bundle": self.bundle_name,
                "model": model_name,
                "turn_count": len([m for m in messages if m.get("role") == "user"]),
                "incremental": True,  # Distinguish from final saves
                # Store working_dir for session sync between CLI and web
                "working_dir": str(Path.cwd().resolve()),
            }

            # Save via SessionStore (atomic writes)
            self.store.save(self.session_id, messages, metadata)

            tool_name = data.get("tool_name", "unknown")
            logger.debug(
                f"Incremental save after {tool_name}: {current_count} messages"
            )

        except Exception as e:
            # Log but don't fail - incremental save is best-effort
            logger.warning(f"Incremental save failed: {e}")

        return HookResult(action="continue")

    def _extract_model_name(self) -> str:
        """Extract model name from session config.

        Returns:
            Model name string or "unknown" if not found
        """
        providers = self.config.get("providers", [])
        if isinstance(providers, list) and providers:
            first_provider = providers[0]
            if isinstance(first_provider, dict) and "config" in first_provider:
                provider_config = first_provider["config"]
                return provider_config.get("model") or provider_config.get(
                    "default_model", "unknown"
                )
        return "unknown"


def register_incremental_save(
    session: "AmplifierSession",
    store: SessionStore,
    session_id: str,
    bundle_name: str,
    config: dict[str, Any],
) -> IncrementalSaveHook | None:
    """Register incremental save hook on session.

    Convenience function that creates the hook and registers it with
    the session's hooks registry.

    Args:
        session: The AmplifierSession to register on
        store: SessionStore instance for persistence
        session_id: Session identifier
        bundle_name: Bundle name for metadata (e.g., "bundle:foundation")
        config: Session configuration

    Returns:
        The created hook instance, or None if hooks not available
    """
    hooks = session.coordinator.get("hooks")
    if not hooks or not hasattr(hooks, "register"):
        logger.debug("Hooks not available, skipping incremental save registration")
        return None

    hook = IncrementalSaveHook(session, store, session_id, bundle_name, config)

    # Register with priority 900 (high, but below trace collector at 1000)
    # This ensures tracing completes before we save
    hooks.register(
        "tool:post", hook.on_tool_post, priority=900, name="incremental_save"
    )

    logger.debug(f"Registered incremental save hook for session {session_id[:8]}...")
    return hook
