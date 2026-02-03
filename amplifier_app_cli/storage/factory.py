"""Factory for creating session stores based on configuration.

Determines whether to use local-only SessionStore or HybridSessionStore
with cloud sync based on settings.yaml configuration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


@runtime_checkable
class SessionStoreProtocol(Protocol):
    """Protocol defining the session store interface.

    Both SessionStore and HybridSessionStore implement this interface,
    allowing them to be used interchangeably.
    """

    def save(self, session_id: str, transcript: list, metadata: dict) -> None:
        """Save session state."""
        ...

    def load(self, session_id: str) -> tuple[list, dict]:
        """Load session state."""
        ...

    def exists(self, session_id: str) -> bool:
        """Check if session exists."""
        ...

    def list_sessions(self, *, top_level_only: bool = True) -> list[str]:
        """List session IDs."""
        ...

    def get_metadata(self, session_id: str) -> dict:
        """Get session metadata."""
        ...

    def update_metadata(self, session_id: str, updates: dict) -> dict:
        """Update session metadata."""
        ...


def create_session_store(
    settings: "Any",
    working_dir: Path | None = None,
    project_slug: str | None = None,
    base_dir: Path | None = None,
) -> SessionStoreProtocol:
    """Create appropriate session store based on settings.

    Factory function that checks storage configuration and returns
    either a local-only SessionStore or a HybridSessionStore with
    cloud sync capabilities.

    Args:
        settings: AppSettings instance with storage configuration
        working_dir: Working directory for exclusion pattern checks
        project_slug: Project identifier for session organization
        base_dir: Base directory for local storage

    Returns:
        SessionStore or HybridSessionStore instance
    """
    from ..session_store import SessionStore

    # Get storage config
    storage_config = (
        settings.get_storage_config() if hasattr(settings, "get_storage_config") else {}
    )
    mode = storage_config.get("mode", "local")

    # If mode is local or no cosmos endpoint configured, use local store
    cosmos_config = storage_config.get("cosmos", {})
    cosmos_endpoint = cosmos_config.get("endpoint")

    # Also check environment variable
    import os

    if not cosmos_endpoint:
        cosmos_endpoint = os.environ.get("AMPLIFIER_COSMOS_ENDPOINT")

    if mode == "local" or not cosmos_endpoint:
        logger.debug(
            "Using local-only SessionStore (mode=%s, cosmos_endpoint=%s)",
            mode,
            bool(cosmos_endpoint),
        )
        return SessionStore(base_dir=base_dir)

    # Try to create hybrid store
    try:
        from .hybrid_store import HybridSessionStore

        working_dir = working_dir or Path.cwd()
        store = HybridSessionStore.from_settings(
            storage_config=storage_config,
            working_dir=working_dir,
            project_slug=project_slug,
            base_dir=base_dir,
        )

        # Log sync status
        if store.is_local_only:
            logger.info(
                "HybridSessionStore created in local-only mode "
                "(directory excluded or mode configured)"
            )
        else:
            logger.info("HybridSessionStore created with cloud sync enabled")

        return store

    except Exception as e:
        # Fall back to local store on any error
        logger.warning(
            f"Failed to create HybridSessionStore, falling back to local: {e}"
        )
        return SessionStore(base_dir=base_dir)


async def start_store_if_hybrid(store: SessionStoreProtocol) -> None:
    """Start the store if it's a HybridSessionStore.

    Call this after creating the store to enable cloud sync.
    Safe to call on any store type.

    Args:
        store: Session store instance
    """
    # Import here to avoid circular imports
    from .hybrid_store import HybridSessionStore

    if isinstance(store, HybridSessionStore):
        await store.start()
        logger.debug("Started HybridSessionStore background sync")


async def stop_store_if_hybrid(store: SessionStoreProtocol) -> None:
    """Stop the store if it's a HybridSessionStore.

    Call this during cleanup to flush pending sync and release resources.
    Safe to call on any store type.

    Args:
        store: Session store instance
    """
    from .hybrid_store import HybridSessionStore

    if isinstance(store, HybridSessionStore):
        await store.stop()
        logger.debug("Stopped HybridSessionStore background sync")


def get_store_status(store: SessionStoreProtocol) -> dict[str, Any]:
    """Get status information from the store.

    Returns sync status for HybridSessionStore, basic info for SessionStore.

    Args:
        store: Session store instance

    Returns:
        Dict with status information
    """
    from .hybrid_store import HybridSessionStore

    if isinstance(store, HybridSessionStore):
        return store.get_sync_status()

    # Basic info for regular SessionStore
    return {
        "mode": "local",
        "sync_enabled": False,
        "auth_failed": False,
        "local_only": True,
    }
