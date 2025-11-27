"""Update checking - simplified wrapper around source_status.

Provides caching, background checking, and convenience functions.
The actual source checking logic is in source_status.py.
"""

import json
import logging
import time
from pathlib import Path

from .source_status import UpdateReport
from .source_status import check_all_sources

logger = logging.getLogger(__name__)

# Update check frequency (24 hours)
UPDATE_CHECK_INTERVAL = 86400
# Result cache TTL (1 hour)
UPDATE_CACHE_TTL = 3600

# Cache file locations
UPDATE_CHECK_FILE = Path.home() / ".amplifier" / ".last_update_check"
UPDATE_CACHE_FILE = Path.home() / ".amplifier" / ".update_cache.json"


async def check_updates(include_all_cached: bool = False) -> UpdateReport:
    """Check all sources for updates.

    Main entry point. Uses source-granular approach.

    Args:
        include_all_cached: If True, check ALL cached modules (not just active)

    Returns:
        UpdateReport with all source statuses
    """
    return await check_all_sources(include_all_cached=include_all_cached)


async def check_updates_background() -> UpdateReport | None:
    """Check for updates in background with frequency control and caching.

    Returns:
        UpdateReport if check performed, None if skipped (using cache)
    """
    # Check frequency
    if not _should_check_update():
        cached = _load_cached_result()
        if cached:
            return cached
        return None

    try:
        result = await check_all_sources()
        _save_cached_result(result)
        _mark_checked()
        return result

    except Exception as e:
        logger.debug(f"Background update check failed: {e}")
        return None


# GitHub API helpers (re-exported from source_status for convenience)


async def get_github_commit_sha(repo_url: str, ref: str) -> str:
    """Get SHA for ref using GitHub API.

    Re-exported from source_status for backwards compatibility.
    """
    from .source_status import _get_github_commit_sha

    return await _get_github_commit_sha(repo_url, ref)


async def get_commit_details(repo_url: str, sha: str) -> dict:
    """Get commit details.

    Re-exported from source_status for backwards compatibility.
    """
    from .source_status import _get_commit_details

    return await _get_commit_details(repo_url, sha)


# Caching implementation


def _should_check_update() -> bool:
    """Check if enough time passed since last check."""
    if not UPDATE_CHECK_FILE.exists():
        return True

    try:
        last_check = float(UPDATE_CHECK_FILE.read_text(encoding="utf-8"))
        return (time.time() - last_check) > UPDATE_CHECK_INTERVAL
    except Exception:
        return True


def _mark_checked():
    """Record that we checked for updates."""
    UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CHECK_FILE.write_text(str(time.time()), encoding="utf-8")


def _save_cached_result(report: UpdateReport):
    """Cache update report."""
    from dataclasses import asdict

    UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache = {
        "cached_at": time.time(),
        "report": {
            "local_file_sources": [asdict(s) for s in report.local_file_sources],
            "cached_git_sources": [asdict(s) for s in report.cached_git_sources],
        },
    }
    UPDATE_CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")


def _load_cached_result() -> UpdateReport | None:
    """Load cached update report if fresh."""
    from .source_status import CachedGitStatus
    from .source_status import LocalFileStatus

    if not UPDATE_CACHE_FILE.exists():
        return None

    try:
        cache = json.loads(UPDATE_CACHE_FILE.read_text(encoding="utf-8"))
        cache_age = time.time() - cache["cached_at"]

        if cache_age < UPDATE_CACHE_TTL:
            report_data = cache["report"]

            # Reconstruct Path objects
            local_sources = []
            for s in report_data["local_file_sources"]:
                if "path" in s and s["path"]:
                    s["path"] = Path(s["path"])
                local_sources.append(LocalFileStatus(**s))

            return UpdateReport(
                local_file_sources=local_sources,
                cached_git_sources=[CachedGitStatus(**s) for s in report_data["cached_git_sources"]],
            )
    except Exception as e:
        logger.debug(f"Failed to load cached result: {e}")

    return None


# Legacy compatibility (old functions, now point to new architecture)
check_amplifier_updates_background = check_updates_background
check_module_updates_background = check_updates_background
