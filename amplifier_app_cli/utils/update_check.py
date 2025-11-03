"""Update checking for Amplifier libraries and modules.

Checks for updates using SHA comparison via GitHub API (no git dependency).
Supports both production installs and local dev environments.
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Update check frequency (24 hours)
UPDATE_CHECK_INTERVAL = 86400
# Result cache TTL (1 hour)
UPDATE_CACHE_TTL = 3600

# Cache file locations
AMPLIFIER_UPDATE_CHECK_FILE = Path.home() / ".amplifier" / ".last_amplifier_check"
AMPLIFIER_UPDATE_CACHE_FILE = Path.home() / ".amplifier" / ".amplifier_update_cache.json"
MODULE_UPDATE_CHECK_FILE = Path.home() / ".amplifier" / ".last_module_check"
MODULE_UPDATE_CACHE_FILE = Path.home() / ".amplifier" / ".module_check_cache.json"


@dataclass
class UpdateInfo:
    """Information about an available update."""

    library: str
    installed_sha: str
    remote_sha: str
    url: str
    branch: str
    commit_message: str | None = None
    compare_url: str | None = None


@dataclass
class UpdateResult:
    """Result of update check."""

    mode: str  # "production" or "local_dev"
    updates_available: list[UpdateInfo]
    has_updates: bool
    umbrella_source: str | None = None
    error: str | None = None


@dataclass
class LocalDevStatus:
    """Status of local development repository."""

    library: str
    location: str
    uncommitted_changes: bool
    unpushed_commits: bool
    behind_remote: bool
    remote_commits: int


async def check_amplifier_updates() -> UpdateResult:
    """Check for Amplifier updates (all libraries).

    Automatically detects production vs local dev and uses appropriate strategy.

    Returns:
        UpdateResult with updates available or error
    """
    from .installation import detect_installation

    install_info = detect_installation()

    if install_info.mode == "local_dev":
        return await check_local_dev_updates(install_info)
    return await check_production_updates(install_info)


async def check_production_updates(install_info) -> UpdateResult:
    """Check for updates in production install.

    Uses GitHub API to compare installed SHAs with remote SHAs.
    No git dependency required.
    """
    from .umbrella_discovery import discover_umbrella_source
    from .umbrella_discovery import fetch_umbrella_dependencies

    # Discover umbrella source dynamically (no hardcoding!)
    umbrella_info = discover_umbrella_source()

    if not umbrella_info:
        logger.warning("Could not determine umbrella source - skipping update check")
        return UpdateResult(
            mode="production",
            updates_available=[],
            has_updates=False,
            error="Could not determine installation source",
        )

    logger.info(f"Checking updates from umbrella: {umbrella_info.url}@{umbrella_info.ref}")

    try:
        # Fetch dependencies from discovered umbrella
        umbrella_deps = await fetch_umbrella_dependencies(umbrella_info)

        # For each library, check if remote SHA differs
        updates_available = []

        for lib_name, lib_info in install_info.libraries.items():
            if lib_name not in umbrella_deps:
                continue

            dep_info = umbrella_deps[lib_name]

            try:
                # Get remote SHA for this library
                remote_sha = await get_github_commit_sha(dep_info["url"], dep_info["branch"])

                # Get installed SHA
                installed_sha = lib_info.git_sha

                if not installed_sha:
                    logger.debug(f"No installed SHA for {lib_name} - skipping comparison")
                    continue

                if remote_sha != installed_sha:
                    # Get commit details for better UX
                    commit_info = await get_commit_details(dep_info["url"], remote_sha)

                    updates_available.append(
                        UpdateInfo(
                            library=lib_name,
                            installed_sha=installed_sha[:7],
                            remote_sha=remote_sha[:7],
                            url=dep_info["url"],
                            branch=dep_info["branch"],
                            commit_message=commit_info.get("message"),
                            compare_url=f"{dep_info['url']}/compare/{installed_sha[:7]}...{remote_sha[:7]}",
                        )
                    )

            except Exception as e:
                logger.debug(f"Failed to check {lib_name}: {e}")
                continue

        return UpdateResult(
            mode="production",
            updates_available=updates_available,
            has_updates=len(updates_available) > 0,
            umbrella_source=f"{umbrella_info.url}@{umbrella_info.ref}",
        )

    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return UpdateResult(mode="production", updates_available=[], has_updates=False, error=str(e))


async def check_local_dev_updates(install_info) -> UpdateResult:
    """Check for updates in local dev mode.

    Different behavior:
    - Check if local repos are behind remote
    - Check if local repos have uncommitted changes
    - Don't recommend "amplifier update" (they use install-dev.sh)
    """
    status_items = []

    for lib_name, lib_info in install_info.libraries.items():
        if not lib_info.location:
            continue

        # Check git status of local repo
        git_status = await check_local_git_status(lib_info.location)

        if git_status["has_issues"]:
            status_items.append(
                LocalDevStatus(
                    library=lib_name,
                    location=str(lib_info.location),
                    uncommitted_changes=git_status["uncommitted_changes"],
                    unpushed_commits=git_status["unpushed_commits"],
                    behind_remote=git_status["behind_remote"],
                    remote_commits=git_status["remote_commits"],
                )
            )

    return UpdateResult(
        mode="local_dev",
        updates_available=status_items,
        has_updates=len(status_items) > 0,
    )


async def check_local_git_status(repo_path: Path) -> dict:
    """Check git status of local repository."""
    import subprocess

    status = {
        "has_issues": False,
        "uncommitted_changes": False,
        "unpushed_commits": False,
        "behind_remote": False,
        "remote_commits": 0,
    }

    try:
        # Check for uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            status["uncommitted_changes"] = True
            status["has_issues"] = True

        # Check for unpushed commits
        result = subprocess.run(
            ["git", "log", "@{u}..", "--oneline"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            status["unpushed_commits"] = True
            status["has_issues"] = True

        # Check if behind remote (fetch first)
        subprocess.run(["git", "fetch"], cwd=repo_path, capture_output=True, timeout=5)

        result = subprocess.run(
            ["git", "log", "..@{u}", "--oneline"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            commits = result.stdout.strip().split("\n")
            status["behind_remote"] = True
            status["remote_commits"] = len(commits)
            status["has_issues"] = True

    except Exception as e:
        logger.debug(f"Git status check failed for {repo_path}: {e}")

    return status


async def get_github_commit_sha(repo_url: str, ref: str) -> str:
    """Get SHA for ref using GitHub API (no git required).

    Args:
        repo_url: GitHub repository URL
        ref: Branch, tag, or commit reference

    Returns:
        Full commit SHA

    Raises:
        httpx.HTTPStatusError: API request failed
    """
    import httpx

    # Parse URL to get org/repo
    url_without_git = repo_url.rstrip(".git")
    parts = url_without_git.split("github.com/")[-1].split("/")
    if len(parts) < 2:
        raise ValueError(f"Could not parse GitHub URL: {repo_url}")

    owner, repo = parts[0], parts[1]

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}",
            headers={"Accept": "application/vnd.github.v3+json", **_get_github_auth_headers()},
        )
        response.raise_for_status()
        return response.json()["sha"]


async def get_commit_details(repo_url: str, sha: str) -> dict:
    """Get commit details for better UX.

    Args:
        repo_url: GitHub repository URL
        sha: Commit SHA

    Returns:
        Dict with message, date, author
    """
    import httpx

    url_without_git = repo_url.rstrip(".git")
    parts = url_without_git.split("github.com/")[-1].split("/")
    if len(parts) < 2:
        return {}

    owner, repo = parts[0], parts[1]

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
            headers={"Accept": "application/vnd.github.v3+json", **_get_github_auth_headers()},
        )
        response.raise_for_status()

        data = response.json()
        return {
            "message": data["commit"]["message"].split("\n")[0],  # First line only
            "date": data["commit"]["author"]["date"],
            "author": data["commit"]["author"]["name"],
        }


def _get_github_auth_headers() -> dict:
    """Get GitHub auth headers if token available.

    Checks:
    1. GITHUB_TOKEN environment variable
    2. GitHub CLI config (~/.config/gh/hosts.yml)

    Returns:
        Dict with Authorization header if token found, empty dict otherwise
    """
    import os

    # Check environment variable
    token = os.getenv("GITHUB_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}

    # Check GitHub CLI config
    gh_config = Path.home() / ".config" / "gh" / "hosts.yml"
    if gh_config.exists():
        try:
            import yaml

            config = yaml.safe_load(gh_config.read_text())
            if token := config.get("github.com", {}).get("oauth_token"):
                return {"Authorization": f"Bearer {token}"}
        except Exception:
            pass

    # No auth - use unauthenticated (60 req/hr limit)
    return {}


def should_check_amplifier_update() -> bool:
    """Check if enough time passed since last check."""
    if not AMPLIFIER_UPDATE_CHECK_FILE.exists():
        return True

    try:
        last_check = float(AMPLIFIER_UPDATE_CHECK_FILE.read_text())
        return (time.time() - last_check) > UPDATE_CHECK_INTERVAL
    except Exception:
        return True


def should_check_module_update() -> bool:
    """Check if enough time passed since last module check."""
    if not MODULE_UPDATE_CHECK_FILE.exists():
        return True

    try:
        last_check = float(MODULE_UPDATE_CHECK_FILE.read_text())
        return (time.time() - last_check) > UPDATE_CHECK_INTERVAL
    except Exception:
        return True


def mark_amplifier_checked():
    """Record that we checked for Amplifier updates."""
    AMPLIFIER_UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    AMPLIFIER_UPDATE_CHECK_FILE.write_text(str(time.time()))


def mark_module_checked():
    """Record that we checked for module updates."""
    MODULE_UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODULE_UPDATE_CHECK_FILE.write_text(str(time.time()))


def save_amplifier_update_cache(update_info: list[UpdateInfo]):
    """Cache Amplifier update info to avoid repeated API calls."""
    AMPLIFIER_UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache = {"cached_at": time.time(), "updates": [asdict(u) for u in update_info]}
    AMPLIFIER_UPDATE_CACHE_FILE.write_text(json.dumps(cache, indent=2))


def load_amplifier_update_cache() -> list[UpdateInfo] | None:
    """Load cached Amplifier update info if fresh."""
    if not AMPLIFIER_UPDATE_CACHE_FILE.exists():
        return None

    try:
        cache = json.loads(AMPLIFIER_UPDATE_CACHE_FILE.read_text())
        cache_age = time.time() - cache["cached_at"]

        if cache_age < UPDATE_CACHE_TTL:
            return [UpdateInfo(**u) for u in cache["updates"]]
    except Exception:
        pass

    return None


def clear_amplifier_update_cache():
    """Clear cached Amplifier update info."""
    if AMPLIFIER_UPDATE_CACHE_FILE.exists():
        AMPLIFIER_UPDATE_CACHE_FILE.unlink()


async def check_amplifier_updates_background() -> UpdateResult | None:
    """Check for Amplifier updates in background (non-blocking).

    This runs automatically on startup. Uses frequency control and caching
    to avoid excessive API calls.

    Returns:
        UpdateResult if check performed, None if skipped
    """
    # Check if we should check (frequency control)
    if not should_check_amplifier_update():
        # Return cached result if available
        cached = load_amplifier_update_cache()
        if cached:
            return UpdateResult(mode="production", updates_available=cached, has_updates=len(cached) > 0)
        return None

    try:
        result = await check_amplifier_updates()

        # Cache results
        if result.has_updates:
            save_amplifier_update_cache(result.updates_available)
        else:
            clear_amplifier_update_cache()

        mark_amplifier_checked()
        return result

    except Exception as e:
        logger.debug(f"Amplifier update check failed: {e}")
        return None


async def check_module_updates_background() -> list | None:
    """Check for module updates in background (non-blocking).

    This runs automatically on startup. Uses frequency control and caching.

    Returns:
        List of stale modules if check performed, None if skipped
    """
    # Check if we should check
    if not should_check_module_update():
        # Return cached result if available
        if MODULE_UPDATE_CACHE_FILE.exists():
            try:
                cache = json.loads(MODULE_UPDATE_CACHE_FILE.read_text())
                cache_age = time.time() - cache["cached_at"]

                if cache_age < UPDATE_CACHE_TTL:
                    return cache.get("stale_modules", [])
            except Exception:
                pass
        return None

    try:
        stale = await check_stale_modules(timeout=10.0)

        # Cache results
        MODULE_UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MODULE_UPDATE_CACHE_FILE.write_text(json.dumps({"cached_at": time.time(), "stale_modules": stale}, indent=2))

        mark_module_checked()
        return stale

    except Exception as e:
        logger.debug(f"Module update check failed: {e}")
        return None


async def check_stale_modules(timeout: float = 10.0) -> list[dict]:
    """Check which cached modules have updates available.

    Uses GitHub API - no git CLI required!

    Args:
        timeout: Timeout for each module check

    Returns:
        List of dicts with stale module info
    """
    cache_dir = Path.home() / ".amplifier" / "module-cache"
    if not cache_dir.exists():
        return []

    stale = []

    # Scan cache directories
    for cache_hash_dir in cache_dir.iterdir():
        if not cache_hash_dir.is_dir():
            continue

        for ref_dir in cache_hash_dir.iterdir():
            if not ref_dir.is_dir():
                continue

            # Read metadata
            metadata_file = ref_dir / ".amplifier_cache_metadata.json"
            if not metadata_file.exists():
                continue

            try:
                metadata = json.loads(metadata_file.read_text())

                # Skip if immutable ref
                if not metadata.get("is_mutable", True):
                    continue

                # Skip if we don't have SHA (can't compare)
                cached_sha = metadata.get("sha")
                if not cached_sha:
                    continue

                # Check remote SHA (GitHub API - no git!)
                try:
                    remote_sha = await asyncio.wait_for(
                        get_github_commit_sha(metadata["url"], metadata["ref"]), timeout=timeout
                    )

                    # Compare SHAs
                    if remote_sha != cached_sha:
                        # Extract module ID from metadata or path
                        module_id = metadata.get("module_id", ref_dir.parent.name)

                        stale.append(
                            {
                                "module_id": module_id,
                                "url": metadata["url"],
                                "ref": metadata["ref"],
                                "cached_sha": cached_sha[:7],
                                "remote_sha": remote_sha[:7],
                                "age_days": _cache_age_days(metadata),
                            }
                        )

                except TimeoutError:
                    logger.debug(f"Timeout checking {metadata['url']}@{metadata['ref']}")
                except Exception as e:
                    logger.debug(f"Failed to check {metadata['url']}@{metadata['ref']}: {e}")

            except Exception as e:
                logger.debug(f"Failed to read metadata from {metadata_file}: {e}")
                continue

    return stale


def _cache_age_days(metadata: dict) -> int:
    """Calculate cache age in days from metadata."""
    try:
        from datetime import datetime

        cached_at = datetime.fromisoformat(metadata["cached_at"])
        age = datetime.now() - cached_at
        return age.days
    except Exception:
        return 0
