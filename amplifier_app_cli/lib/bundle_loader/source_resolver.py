"""Independent bundle source resolution for CLI app layer.

This module provides bundle source resolution WITHOUT any dependency on
profiles/collections. It uses amplifier-foundation's source handlers directly.

Per DESIGN PHILOSOPHY: Bundles have their own independent code paths optimized
for their longer term future. No coupling to profiles/collections which will
be deprecated and deleted later.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_foundation import SimpleCache
from amplifier_foundation import SimpleSourceResolver
from amplifier_foundation.paths import get_amplifier_home

if TYPE_CHECKING:
    from amplifier_foundation import CacheProviderProtocol


def get_bundle_cache_dir() -> Path:
    """Get cache directory for remote bundles.

    Returns:
        Path to cache directory under AMPLIFIER_HOME/cache/
    """
    return get_amplifier_home() / "cache"


def create_bundle_source_resolver(
    base_path: Path | None = None,
    cache_dir: Path | None = None,
) -> SimpleSourceResolver:
    """Create source resolver for bundle URIs.

    Supports all bundle source types:
    - file:// and local paths
    - git+https:// for git repositories
    - https:// and http:// for direct downloads
    - zip+https:// and zip+file:// for zip archives

    Args:
        base_path: Base path for resolving relative paths.
        cache_dir: Cache directory for remote content.

    Returns:
        Configured SimpleSourceResolver with all handlers.
    """
    return SimpleSourceResolver(
        cache_dir=cache_dir or get_bundle_cache_dir(),
        base_path=base_path or Path.cwd(),
    )


def create_bundle_cache(cache_dir: Path | None = None) -> CacheProviderProtocol:
    """Create cache for loaded bundles.

    Uses DiskCache if available (for persistence across sessions),
    falls back to SimpleCache (in-memory) otherwise.

    Args:
        cache_dir: Cache directory for bundle metadata (only used with DiskCache).

    Returns:
        Cache provider implementing CacheProviderProtocol.
    """
    # Try to use DiskCache for persistence (available in newer foundation versions)
    try:
        from amplifier_foundation.cache.disk import DiskCache

        if cache_dir is None:
            cache_dir = get_amplifier_home() / "cache" / "bundle_metadata"
        return DiskCache(cache_dir=cache_dir)
    except ImportError:
        # Fall back to in-memory cache
        return SimpleCache()
