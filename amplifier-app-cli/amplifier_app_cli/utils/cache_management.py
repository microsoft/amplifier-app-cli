"""Cache management utilities for Amplifier.

Provides atomic cache operations that can be shared across commands
(update --force, reset, etc.).

The cache directory (~/.amplifier/cache) and registry (~/.amplifier/registry.json)
are regenerable - they auto-rebuild when needed. This makes them safe to clear
without data loss.

User data (projects, settings, keys) is NEVER touched by these utilities.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def get_amplifier_dir() -> Path:
    """Return the ~/.amplifier directory path."""
    return Path.home() / ".amplifier"


def get_cache_dir() -> Path:
    """Return ~/.amplifier/cache path."""
    return get_amplifier_dir() / "cache"


def get_registry_path() -> Path:
    """Return ~/.amplifier/registry.json path."""
    return get_amplifier_dir() / "registry.json"


def clear_download_cache(dry_run: bool = False) -> tuple[int, bool]:
    """Clear downloaded bundles and modules cache.

    Args:
        dry_run: If True, only report what would be done without making changes.

    Returns:
        Tuple of (items_cleared, success)
    """
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        logger.debug("Cache directory does not exist, nothing to clear")
        return (0, True)

    try:
        # Count items before clearing
        items = list(cache_dir.iterdir())
        count = len(items)

        if dry_run:
            logger.debug(f"[dry-run] Would clear {count} items from cache")
            return (count, True)

        # Remove entire cache directory
        shutil.rmtree(cache_dir)
        logger.debug(f"Cleared {count} items from cache")

        # Recreate empty cache directory
        cache_dir.mkdir(parents=True, exist_ok=True)

        return (count, True)
    except OSError as e:
        logger.warning(f"Failed to clear cache: {e}")
        return (0, False)


def clear_registry(dry_run: bool = False) -> bool:
    """Clear bundle registry mapping.

    Args:
        dry_run: If True, only report what would be done without making changes.

    Returns:
        True if successful (or file didn't exist), False on error
    """
    registry_path = get_registry_path()

    if not registry_path.exists():
        logger.debug("Registry does not exist, nothing to clear")
        return True

    try:
        if dry_run:
            logger.debug("[dry-run] Would remove registry.json")
            return True

        registry_path.unlink()
        logger.debug("Cleared registry.json")
        return True
    except OSError as e:
        logger.warning(f"Failed to clear registry: {e}")
        return False


def clear_all_regenerable(dry_run: bool = False) -> tuple[int, bool]:
    """Clear all regenerable cache content (cache dir + registry).

    This is the recommended function for `update --force` - it clears everything
    that will auto-rebuild without touching user data.

    Args:
        dry_run: If True, only report what would be done without making changes.

    Returns:
        Tuple of (items_cleared, overall_success)
    """
    total_cleared = 0
    all_success = True

    # Clear download cache
    count, success = clear_download_cache(dry_run)
    total_cleared += count
    if not success:
        all_success = False

    # Clear registry
    if not clear_registry(dry_run):
        all_success = False
    elif get_registry_path().exists() or dry_run:
        total_cleared += 1  # Count registry as one item

    return (total_cleared, all_success)
