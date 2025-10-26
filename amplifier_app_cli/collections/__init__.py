"""
Collections module - Shareable bundles of Amplifier resources.

This module provides APP LAYER POLICY for collection management.
Per KERNEL_PHILOSOPHY: Search paths and resolution are policy, not mechanism.

Public API:
- CollectionMetadata: Parse pyproject.toml metadata
- CollectionResolver: Resolve collection names to paths (APP LAYER POLICY)
- get_collection_search_paths: Get search paths (APP LAYER POLICY)
"""

from .resolver import CollectionResolver
from .schema import CollectionMetadata
from .utils import get_collection_search_paths

__all__ = [
    "CollectionMetadata",
    "CollectionResolver",
    "get_collection_search_paths",
]
