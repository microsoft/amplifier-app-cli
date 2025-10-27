"""
Collections module - Shareable bundles of Amplifier resources.

This module provides APP LAYER POLICY for collection management.
Per KERNEL_PHILOSOPHY: Search paths and resolution are policy, not mechanism.

Public API:
- CollectionMetadata: Parse pyproject.toml metadata
- CollectionResolver: Resolve collection names to paths (APP LAYER POLICY)
- get_collection_search_paths: Get search paths (APP LAYER POLICY)
- CollectionResources: Discovered resources in a collection
- discover_collection_resources: Auto-discover resources by convention
- install_collection: Install collection from git
- uninstall_collection: Remove installed collection
- CollectionLock: Track installed collections
"""

from .discovery import CollectionResources
from .discovery import discover_collection_resources
from .installer import CollectionInstallError
from .installer import install_collection
from .installer import install_scenario_tools
from .installer import is_collection_installed
from .installer import uninstall_collection
from .installer import uninstall_scenario_tools
from .lock import CollectionLock
from .lock import CollectionLockEntry
from .resolver import CollectionResolver
from .schema import CollectionMetadata
from .utils import get_collection_search_paths

__all__ = [
    "CollectionMetadata",
    "CollectionResolver",
    "CollectionResources",
    "discover_collection_resources",
    "get_collection_search_paths",
    "install_collection",
    "uninstall_collection",
    "is_collection_installed",
    "install_scenario_tools",
    "uninstall_scenario_tools",
    "CollectionInstallError",
    "CollectionLock",
    "CollectionLockEntry",
]
