"""Dynamic version generation for user-facing display.

Generates human-readable versions like: 2025.12.02-abc1234
Format: YYYY.MM.DD-<short-sha>

Philosophy: Keep pyproject.toml version static (0.1.0) for packaging,
but show users a meaningful, traceable version derived from the actual
installed commit.
"""

import importlib.metadata
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Fallback version when dynamic detection fails
FALLBACK_VERSION = "0.1.0"


@dataclass
class VersionInfo:
    """Structured version information."""

    display: str  # Human-readable: "2025.12.02-abc1234"
    sha: str | None  # Full or short SHA
    date: str | None  # YYYY.MM.DD format
    is_local: bool  # True if from local/editable install
    has_changes: bool  # True if local repo has uncommitted changes


@lru_cache(maxsize=1)
def get_version_info() -> VersionInfo:
    """Get comprehensive version information.

    Detection priority:
    1. Git install (from direct_url.json vcs_info)
    2. Local/editable install (run git commands in source dir)
    3. Fallback to static version

    Returns:
        VersionInfo with display string and metadata
    """
    # Try amplifier-app-cli first (the actual CLI package)
    for package_name in ["amplifier-app-cli", "amplifier"]:
        info = _get_package_version_info(package_name)
        if info and info.sha:
            return info

    # Fallback
    return VersionInfo(
        display=FALLBACK_VERSION,
        sha=None,
        date=None,
        is_local=False,
        has_changes=False,
    )


def _get_package_version_info(package_name: str) -> VersionInfo | None:
    """Get version info from a specific package.

    Args:
        package_name: Package to inspect

    Returns:
        VersionInfo if successful, None otherwise
    """
    try:
        dist = importlib.metadata.distribution(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None

    if not hasattr(dist, "read_text"):
        return None

    try:
        direct_url_text = dist.read_text("direct_url.json")
        if not direct_url_text:
            return None
        direct_url = json.loads(direct_url_text)
    except Exception:
        return None

    # Case 1: Git URL install (production)
    if "vcs_info" in direct_url:
        commit_id = direct_url["vcs_info"].get("commit_id", "")
        if commit_id:
            short_sha = commit_id[:7]
            # Use commit date if available, otherwise today
            date_str = _get_commit_date_from_api(direct_url.get("url", ""), commit_id)
            if not date_str:
                date_str = datetime.now(UTC).strftime("%Y.%m.%d")

            return VersionInfo(
                display=f"{date_str}-{short_sha}",
                sha=short_sha,
                date=date_str,
                is_local=False,
                has_changes=False,
            )

    # Case 2: Local/editable install
    if "dir_info" in direct_url:
        path = direct_url.get("url", "").replace("file://", "")
        if path:
            return _get_local_git_version(Path(path))

    return None


def _get_local_git_version(repo_path: Path) -> VersionInfo | None:
    """Get version info from local git repository.

    Args:
        repo_path: Path to local repository

    Returns:
        VersionInfo if git repo, None otherwise
    """
    try:
        # Check if git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        # Get HEAD SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        full_sha = result.stdout.strip()
        short_sha = full_sha[:7]

        # Get commit date
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y.%m.%d"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            date_str = result.stdout.strip()
        else:
            date_str = datetime.now(UTC).strftime("%Y.%m.%d")

        # Check for uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        has_changes = bool(result.stdout.strip()) if result.returncode == 0 else False

        # Format display with indicator for local changes
        display = f"{date_str}-{short_sha}"
        if has_changes:
            display += "*"

        return VersionInfo(
            display=display,
            sha=short_sha,
            date=date_str,
            is_local=True,
            has_changes=has_changes,
        )

    except Exception as e:
        logger.debug(f"Could not get local git version from {repo_path}: {e}")
        return None


def _get_commit_date_from_api(git_url: str, commit_sha: str) -> str | None:
    """Try to get commit date from GitHub API.

    Args:
        git_url: Git repository URL
        commit_sha: Full commit SHA

    Returns:
        Date string in YYYY.MM.DD format, or None if can't fetch
    """
    # Only attempt for GitHub URLs
    if "github.com" not in git_url:
        return None

    try:
        # Extract owner/repo from URL
        # https://github.com/microsoft/amplifier-app-cli -> microsoft/amplifier-app-cli
        url = git_url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        parts = url.split("github.com/")[-1].split("/")
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]

        # Use git log to get date (faster than API, works offline for cloned repos)
        # This is a best-effort approach - if it fails, we use today's date
        import httpx

        api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}"
        response = httpx.get(api_url, timeout=5.0)
        if response.status_code == 200:
            data = response.json()
            commit_date = data.get("commit", {}).get("committer", {}).get("date", "")
            if commit_date:
                # Parse ISO date: 2025-12-02T10:30:00Z
                dt = datetime.fromisoformat(commit_date.replace("Z", "+00:00"))
                return dt.strftime("%Y.%m.%d")
    except Exception as e:
        logger.debug(f"Could not fetch commit date from API: {e}")

    return None


def get_version() -> str:
    """Get the display version string.

    Returns:
        Human-readable version like "2025.12.02-abc1234"
    """
    return get_version_info().display


def clear_version_cache() -> None:
    """Clear the cached version info (useful for testing)."""
    get_version_info.cache_clear()
