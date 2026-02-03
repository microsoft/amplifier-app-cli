"""Storage module for hybrid local+cloud session persistence.

This module provides:
- DirectoryExclusionFilter: Glob-based filtering for determining which
  sessions should remain local-only vs synced to cloud storage.
- HybridSessionStore: Adapter providing SessionStore-compatible interface
  over the amplifier-session-storage library's HybridBlockStorage.
- create_session_store: Factory function for creating the appropriate store.
- SessionStoreProtocol: Protocol for type-safe store usage.

Configuration is managed via settings.yaml with support for:
- Storage mode (hybrid, local, cloud)
- Directory exclusion patterns (glob syntax)
- Cosmos DB connection settings
"""

from .exclusion_filter import DirectoryExclusionFilter
from .factory import (
    SessionStoreProtocol,
    create_session_store,
    get_store_status,
    start_store_if_hybrid,
    stop_store_if_hybrid,
)
from .hybrid_store import HybridSessionStore

__all__ = [
    "DirectoryExclusionFilter",
    "HybridSessionStore",
    "SessionStoreProtocol",
    "create_session_store",
    "get_store_status",
    "start_store_if_hybrid",
    "stop_store_if_hybrid",
]
