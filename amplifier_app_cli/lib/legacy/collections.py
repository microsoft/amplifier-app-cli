"""LEGACY: Collection utilities centralized for Phase 4 deletion.

DELETE WHEN: Profiles/collections removed in Phase 4.

This module centralizes all collection-related imports so they can be
deleted in a single place when collections are removed. The bundle
codepath uses lib/settings.py instead - it should NEVER import from here.

All APIs are re-exported from amplifier_collections.
"""

from __future__ import annotations

import re
from pathlib import Path

# ===== Re-exports from amplifier_collections =====
# These are APIs used by commands/collection.py and other files that haven't
# been internalized yet. Centralizing imports here allows single-point deletion.
from amplifier_collections import CollectionInstallError
from amplifier_collections import CollectionLock
from amplifier_collections import CollectionMetadata  # Use real one, not local stub
from amplifier_collections import CollectionResolver  # Use real one, not local stub
from amplifier_collections import discover_collection_resources
from amplifier_collections import install_collection
from amplifier_collections import list_agents
from amplifier_collections import list_profiles
from amplifier_collections import uninstall_collection

# NOTE: CollectionMetadata and CollectionResolver are now imported from
# amplifier_collections above. Local stubs were removed - the real classes
# have the correct interface needed by commands/collection.py and paths.py.


# ===== Utility Functions =====


def extract_collection_name_from_path(path: str | Path) -> str | None:
    """Extract collection name from a path containing '/collections/'.

    Examples:
        ~/.amplifier/collections/my-collection/profiles/dev.md -> 'my-collection'
        /path/to/collections/toolkit/agents/analyst.md -> 'toolkit'
        /path/without/collection/file.md -> None
    """
    path_str = str(path)

    # Look for /collections/ in the path
    match = re.search(r"/collections/([^/]+)", path_str)
    if match:
        return match.group(1)

    return None


def is_collection_path(path: str | Path) -> bool:
    """Check if a path appears to be within a collection."""
    return extract_collection_name_from_path(path) is not None


def get_collection_subpath(path: str | Path, collection_name: str) -> str | None:
    """Get the path relative to a collection root.

    Example:
        path: ~/.amplifier/collections/toolkit/profiles/dev.md
        collection_name: toolkit
        returns: profiles/dev.md
    """
    path_str = str(path)
    marker = f"/collections/{collection_name}/"

    idx = path_str.find(marker)
    if idx >= 0:
        return path_str[idx + len(marker) :]

    return None


# ===== Module ID Resolution =====


def parse_collection_module_id(module_id: str) -> tuple[str, str] | None:
    """Parse a collection:module module ID.

    Returns (collection_name, module_name) or None if not a collection reference.

    Examples:
        'collection:toolkit:agent-planner' -> ('toolkit', 'agent-planner')
        'provider-anthropic' -> None (not a collection reference)
    """
    if module_id.startswith("collection:"):
        parts = module_id.split(":", 2)
        if len(parts) == 3:
            return (parts[1], parts[2])
    return None


def make_collection_module_id(collection_name: str, module_name: str) -> str:
    """Create a collection:module module ID."""
    return f"collection:{collection_name}:{module_name}"


# ===== Exports =====


__all__ = [
    # Re-exports from amplifier_collections
    "CollectionInstallError",
    "CollectionLock",
    "CollectionMetadata",
    "CollectionResolver",
    "discover_collection_resources",
    "install_collection",
    "list_agents",
    "list_profiles",
    "uninstall_collection",
    # Local utilities
    "extract_collection_name_from_path",
    "is_collection_path",
    "get_collection_subpath",
    "parse_collection_module_id",
    "make_collection_module_id",
]
