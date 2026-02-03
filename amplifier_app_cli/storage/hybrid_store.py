"""Hybrid session store adapter.

Wraps the local SessionStore with cloud sync capabilities via
amplifier-session-storage's HybridFileStorage.

Key features:
- Offline-first: All operations use local SessionStore immediately
- Background sync: HybridFileStorage syncs local files to Cosmos DB
- Directory exclusion: Sessions in excluded directories are local-only
- Auth failure handling: Graceful degradation on auth errors
- Preserves existing .amplifier folder structure
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_session_storage.hybrid.file_storage import (  # type: ignore[import-not-found]
    HybridFileStorage,
    HybridFileStorageConfig,
)
from amplifier_session_storage.cosmos.file_storage import (  # type: ignore[import-not-found]
    CosmosFileConfig,
)

from .exclusion_filter import DirectoryExclusionFilter

if TYPE_CHECKING:
    from ..session_store import SessionStore

logger = logging.getLogger(__name__)


def _get_user_id() -> str:
    """Get user identifier for storage.

    Uses AMPLIFIER_USER_ID env var if set, otherwise falls back to
    system username or 'local-user'.
    """
    user_id = os.environ.get("AMPLIFIER_USER_ID")
    if user_id:
        return user_id

    # Try system username
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return "local-user"


class HybridSessionStore:
    """Session store with hybrid local+cloud storage.

    Provides a SessionStore-compatible interface by delegating all
    local operations to SessionStore, while using HybridFileStorage
    to sync data to Cosmos DB in the background.

    Contract:
    - Inputs: session_id, transcript (list), metadata (dict), working_dir
    - Outputs: Saved data to local storage + async cloud sync
    - Side effects: Filesystem writes, background cloud sync
    - Errors: Graceful degradation on cloud errors (local always works)

    Usage:
        # Create with configuration
        store = HybridSessionStore.from_settings(settings, working_dir=Path.cwd())

        # Start async operations (required for cloud sync)
        await store.start()

        # Use like SessionStore
        store.save(session_id, transcript, metadata)
        transcript, metadata = store.load(session_id)

        # Clean shutdown
        await store.stop()
    """

    def __init__(
        self,
        local_store: "SessionStore",
        hybrid_storage: HybridFileStorage | None = None,
        exclusion_filter: DirectoryExclusionFilter | None = None,
        working_dir: Path | None = None,
        project_slug: str | None = None,
        cloud_enabled: bool = False,
    ) -> None:
        """Initialize hybrid session store.

        Args:
            local_store: SessionStore for local file operations
            hybrid_storage: HybridFileStorage for cloud sync (optional)
            exclusion_filter: Optional filter for directory-based exclusion
            working_dir: Working directory for exclusion checks
            project_slug: Project identifier for session organization
            cloud_enabled: Whether cloud sync is enabled
        """
        self._local_store = local_store
        self._hybrid_storage = hybrid_storage
        self._exclusion_filter = exclusion_filter
        self._working_dir = working_dir or Path.cwd()
        self._project_slug = project_slug or "default"
        self._cloud_enabled = cloud_enabled
        self._started = False
        self._sync_task: asyncio.Task[None] | None = None

    @classmethod
    def from_settings(
        cls,
        storage_config: dict[str, Any],
        working_dir: Path | None = None,
        project_slug: str | None = None,
        base_dir: Path | None = None,
    ) -> "HybridSessionStore":
        """Create HybridSessionStore from settings configuration.

        Args:
            storage_config: Storage configuration dict from settings.yaml
            working_dir: Working directory for exclusion checks
            project_slug: Project identifier
            base_dir: Base directory for local storage

        Returns:
            Configured HybridSessionStore instance
        """
        from ..session_store import SessionStore

        working_dir = working_dir or Path.cwd()

        # Create local store first
        local_store = SessionStore(base_dir=base_dir)

        # Parse exclusion patterns
        exclusions = storage_config.get("exclusions", [])
        exclusion_filter = DirectoryExclusionFilter(exclusions) if exclusions else None

        # Determine if cloud sync is enabled based on mode and exclusions
        mode = storage_config.get("mode", "local")
        cloud_enabled = False

        if mode == "local":
            cloud_enabled = False
        elif mode == "cloud":
            cloud_enabled = True
        else:  # hybrid
            if exclusion_filter and exclusion_filter.should_exclude(working_dir):
                cloud_enabled = False
                logger.info(
                    f"Directory {working_dir} matches exclusion pattern - using local-only storage"
                )
            else:
                cloud_enabled = True

        # Resolve base_dir for local storage (same path used by SessionStore)
        if base_dir is None:
            base_dir = Path.home() / ".amplifier" / "projects"

        # Build HybridFileStorage config if cloud is enabled
        hybrid_storage: HybridFileStorage | None = None
        cosmos_settings = storage_config.get("cosmos", {})

        if cloud_enabled:
            # Get endpoint from config or env
            endpoint = cosmos_settings.get("endpoint") or os.environ.get(
                "AMPLIFIER_COSMOS_ENDPOINT"
            )

            if endpoint:
                # Auth method is a string: 'key' or 'default_credential'
                auth_method = cosmos_settings.get("auth_method", "default_credential")

                cosmos_config = CosmosFileConfig(
                    endpoint=endpoint,
                    database_name=cosmos_settings.get("database")
                    or os.environ.get(
                        "AMPLIFIER_COSMOS_DATABASE", "amplifier-sessions"
                    ),
                    auth_method=auth_method,
                    key=cosmos_settings.get("key")
                    or os.environ.get("AMPLIFIER_COSMOS_KEY"),
                )

                # Create HybridFileStorage config
                hybrid_config = HybridFileStorageConfig(
                    base_path=base_dir,
                    cosmos_config=cosmos_config,
                    exclusion_patterns=exclusions,
                    sync_on_write=False,  # We'll trigger sync manually
                    user_id=_get_user_id(),
                )

                hybrid_storage = HybridFileStorage(hybrid_config)
            else:
                logger.warning(
                    "Cloud storage enabled but no Cosmos endpoint configured - falling back to local"
                )
                cloud_enabled = False

        return cls(
            local_store=local_store,
            hybrid_storage=hybrid_storage,
            exclusion_filter=exclusion_filter,
            working_dir=working_dir,
            project_slug=project_slug,
            cloud_enabled=cloud_enabled,
        )

    async def start(self) -> None:
        """Start the hybrid storage and background sync.

        Call this after initialization to enable cloud sync.
        Safe to call multiple times.
        """
        if self._started:
            return

        if self._hybrid_storage and self._cloud_enabled:
            try:
                await self._hybrid_storage.initialize()
                # Start background sync task
                self._sync_task = await self._hybrid_storage.start_background_sync(
                    interval_seconds=60.0
                )
                logger.info("Hybrid storage started with cloud sync enabled")
            except Exception as e:
                logger.warning(
                    f"Cloud sync unavailable, running in local-only mode: {e}"
                )
                self._cloud_enabled = False
        else:
            logger.info("Hybrid storage started in local-only mode")

        self._started = True

    async def stop(self) -> None:
        """Stop background sync and clean up resources.

        Flushes pending sync operations before stopping.
        """
        if not self._started:
            return

        # Cancel background sync task
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        # Close hybrid storage
        if self._hybrid_storage:
            await self._hybrid_storage.close()

        self._started = False

    @property
    def cloud_available(self) -> bool:
        """Check if cloud storage is available."""
        if not self._cloud_enabled or not self._hybrid_storage:
            return False
        return self._hybrid_storage.cloud_available

    @property
    def is_local_only(self) -> bool:
        """Check if running in local-only mode."""
        return not self._cloud_enabled

    # ---- SessionStore-compatible interface (delegated to local store) ----

    def save(self, session_id: str, transcript: list, metadata: dict) -> None:
        """Save session state to local storage.

        Cloud sync happens in the background via HybridFileStorage.

        Args:
            session_id: Unique session identifier
            transcript: List of message objects
            metadata: Session metadata dictionary
        """
        # Delegate to local store
        self._local_store.save(session_id, transcript, metadata)

        # Trigger async sync if cloud is enabled
        if self._cloud_enabled and self._hybrid_storage and self._started:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._sync_session(session_id))
            except RuntimeError:
                pass  # No event loop - sync will happen in background task

    async def _sync_session(self, session_id: str) -> None:
        """Sync a session to cloud storage."""
        if not self._hybrid_storage:
            return

        try:
            result = await self._hybrid_storage.sync_session(
                project_slug=self._project_slug,
                session_id=session_id,
            )
            if result.success and (
                result.messages_synced > 0 or result.events_synced > 0
            ):
                logger.debug(
                    f"Synced session {session_id}: "
                    f"{result.messages_synced} messages, {result.events_synced} events"
                )
        except Exception as e:
            logger.debug(f"Failed to sync session {session_id}: {e}")

    def load(self, session_id: str) -> tuple[list, dict]:
        """Load session state from local storage.

        Args:
            session_id: Session identifier to load

        Returns:
            Tuple of (transcript, metadata)

        Raises:
            FileNotFoundError: If session does not exist
        """
        return self._local_store.load(session_id)

    def exists(self, session_id: str) -> bool:
        """Check if session exists in local storage.

        Args:
            session_id: Session identifier to check

        Returns:
            True if session exists
        """
        return self._local_store.exists(session_id)

    def list_sessions(self, *, top_level_only: bool = True) -> list[str]:
        """List sessions from local storage.

        Args:
            top_level_only: If True, only return top-level sessions

        Returns:
            List of session IDs
        """
        return self._local_store.list_sessions(top_level_only=top_level_only)

    def get_metadata(self, session_id: str) -> dict:
        """Get session metadata from local storage.

        Args:
            session_id: Session identifier

        Returns:
            Session metadata dictionary
        """
        return self._local_store.get_metadata(session_id)

    def update_metadata(self, session_id: str, updates: dict) -> dict:
        """Update session metadata in local storage.

        Args:
            session_id: Session identifier
            updates: Dictionary of metadata updates

        Returns:
            Updated metadata dictionary
        """
        result = self._local_store.update_metadata(session_id, updates)

        # Trigger async sync if cloud is enabled
        if self._cloud_enabled and self._hybrid_storage and self._started:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._sync_session(session_id))
            except RuntimeError:
                pass

        return result

    def delete(self, session_id: str) -> bool:
        """Delete a session from local storage.

        Note: Cloud deletion is not implemented - cloud data remains for recovery.

        Args:
            session_id: Session identifier to delete

        Returns:
            True if session was deleted
        """
        if hasattr(self._local_store, "delete"):
            return self._local_store.delete(session_id)  # type: ignore[attr-defined]
        return False

    # ---- Sync operations ----

    async def sync_now(self, session_id: str | None = None) -> None:
        """Trigger immediate sync to cloud.

        Args:
            session_id: Specific session to sync, or None for all
        """
        if not self._cloud_enabled or not self._hybrid_storage:
            return

        if session_id:
            await self._hybrid_storage.sync_session(
                project_slug=self._project_slug,
                session_id=session_id,
            )
        else:
            await self._hybrid_storage.sync_all()

    def get_sync_status(self) -> dict[str, Any]:
        """Get current sync status.

        Returns:
            Dictionary with sync status information
        """
        return {
            "mode": "hybrid" if self._cloud_enabled else "local",
            "cloud_available": self.cloud_available,
            "local_only": not self._cloud_enabled,
            "sync_enabled": self._cloud_enabled and self._started,
        }
