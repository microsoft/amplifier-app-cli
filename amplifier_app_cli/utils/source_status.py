"""Source-granular status checking for updates.

Checks each library/module source independently (file, git cache, package).
Uses existing StandardModuleSourceResolver infrastructure.
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import httpx  # Fail fast if missing - required for GitHub Atom feeds

logger = logging.getLogger(__name__)


@dataclass
class LocalFileStatus:
    """Status of a local file source."""

    name: str
    source_type: str = "file"
    path: Path | None = None
    layer: str | None = None  # env, workspace, settings, etc.

    # Local git info
    local_sha: str | None = None
    remote_url: str | None = None

    # Comparison with remote (if traceable)
    has_remote: bool = False
    remote_sha: str | None = None
    commits_behind: int = 0

    # Local state
    uncommitted_changes: bool = False
    unpushed_commits: bool = False


@dataclass
class CachedGitStatus:
    """Status of a cached git source."""

    name: str
    source_type: str = "git"
    url: str | None = None
    ref: str | None = None
    layer: str | None = None

    # SHA comparison
    cached_sha: str | None = None
    remote_sha: str | None = None
    has_update: bool = False
    age_days: int = 0


@dataclass
class CollectionStatus:
    """Status of an installed collection."""

    name: str
    source_type: str = "collection"
    source: str | None = None
    layer: str = "user"

    # SHA comparison
    installed_sha: str | None = None
    remote_sha: str | None = None
    has_update: bool = False
    installed_at: str | None = None


@dataclass
class UpdateReport:
    """Comprehensive update status for all sources."""

    local_file_sources: list[LocalFileStatus]
    cached_git_sources: list[CachedGitStatus]
    collection_sources: list[CollectionStatus] = field(default_factory=list)
    cached_modules_checked: int = 0  # How many cache entries were examined

    @property
    def has_updates(self) -> bool:
        """Check if any updates available (uses has_update flags)."""
        # Local files with remote ahead
        local_updates = any(
            s.has_remote and s.remote_sha and s.remote_sha != s.local_sha for s in self.local_file_sources
        )

        # Cached git sources with updates (check has_update flag)
        git_updates = any(s.has_update for s in self.cached_git_sources)

        # Collections with updates (check has_update flag)
        collection_updates = any(s.has_update for s in self.collection_sources)

        return local_updates or git_updates or collection_updates

    @property
    def has_local_changes(self) -> bool:
        """Check if any local uncommitted/unpushed changes."""
        return any(s.uncommitted_changes or s.unpushed_commits for s in self.local_file_sources)


async def check_all_sources(include_all_cached: bool = False, force: bool = False) -> UpdateReport:
    """Check all libraries and modules for updates.

    Uses source-granular approach - checks each entity independently.
    Uses existing StandardModuleSourceResolver infrastructure.

    Args:
        include_all_cached: Include all cached modules, not just active ones
        force: When True, include ALL sources for forced update (skip SHA comparison)

    Returns:
        UpdateReport with all source statuses
    """
    from amplifier_module_resolution import FileSource
    from amplifier_module_resolution import GitSource

    # Get all sources to check
    all_sources = await _get_all_sources_to_check()

    local_statuses = []
    git_statuses = []

    # Resolve each source independently
    for name, source_info in all_sources.items():
        source = source_info["source"]
        layer = source_info["layer"]

        try:
            if isinstance(source, FileSource):
                status = await _check_file_source(source, name, layer)
                local_statuses.append(status)

            elif isinstance(source, GitSource):
                status = await _check_git_source(source, name, layer, force=force)
                if status:  # Add ALL cached sources (with has_update flag)
                    git_statuses.append(status)

            # PackageSource: skip (can't check for updates)

        except Exception as e:
            logger.debug(f"Failed to check {name}: {e}")
            continue

    # If include_all_cached, also scan ALL cached modules (not just active)
    cached_modules_checked = 0
    if include_all_cached:
        cached_statuses, cached_modules_checked = await _check_all_cached_modules(force=force)
        # Add any not already in git_statuses (avoid duplicates from active profile)
        existing_names = {s.name for s in git_statuses}
        for status in cached_statuses:
            if status.name not in existing_names:
                git_statuses.append(status)

    # Filter out cached sources that have local overrides (local takes precedence)
    local_override_names = {s.name for s in local_statuses}
    git_statuses = [s for s in git_statuses if s.name not in local_override_names]

    # Check installed collections
    collection_statuses = await _check_collection_sources(force=force)

    return UpdateReport(
        local_file_sources=local_statuses,
        cached_git_sources=git_statuses,
        collection_sources=collection_statuses,
        cached_modules_checked=cached_modules_checked,
    )


async def _get_all_sources_to_check() -> dict[str, dict]:
    """Get all libraries and modules with their resolved sources.

    Uses existing StandardModuleSourceResolver!

    Returns:
        Dict of name -> {source, layer, entity_type}
    """
    from ..data.profiles import get_system_default_profile
    from ..paths import create_config_manager
    from ..paths import create_module_resolver
    from ..paths import create_profile_loader
    from .umbrella_discovery import discover_umbrella_source
    from .umbrella_discovery import fetch_umbrella_dependencies

    sources = {}

    # Get resolver (uses existing infrastructure!)
    resolver = create_module_resolver()
    config_manager = create_config_manager()

    # 1. Get libraries from umbrella
    umbrella_info = discover_umbrella_source()
    if umbrella_info:
        try:
            umbrella_deps = await fetch_umbrella_dependencies(umbrella_info)

            for lib_name in umbrella_deps:
                try:
                    source, layer = resolver.resolve_with_layer(lib_name)
                    sources[lib_name] = {"source": source, "layer": layer, "entity_type": "library"}
                except Exception:
                    # Can't resolve - skip
                    pass
        except Exception as e:
            logger.debug(f"Could not fetch umbrella dependencies: {e}")

    # 2. Get active modules from profile
    active_profile = config_manager.get_active_profile() or get_system_default_profile()
    profile_loader = create_profile_loader()

    try:
        profile = profile_loader.load_profile(active_profile)

        # Get all module IDs from profile
        module_ids = set()

        if profile.providers:
            for p in profile.providers:
                if hasattr(p, "module"):
                    module_ids.add(p.module)

        if profile.tools:
            for t in profile.tools:
                if hasattr(t, "module"):
                    module_ids.add(t.module)

        if profile.hooks:
            for h in profile.hooks:
                if hasattr(h, "module"):
                    module_ids.add(h.module)

        # Include session orchestrator and context
        if profile.session:
            if profile.session.orchestrator and hasattr(profile.session.orchestrator, "module"):
                module_ids.add(profile.session.orchestrator.module)
            if profile.session.context and hasattr(profile.session.context, "module"):
                module_ids.add(profile.session.context.module)

        # Resolve each module
        for module_id in module_ids:
            if module_id in sources:  # Already added as library
                continue

            try:
                source, layer = resolver.resolve_with_layer(module_id)
                sources[module_id] = {"source": source, "layer": layer, "entity_type": "module"}
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"Could not load profile modules: {e}")

    return sources


async def _check_file_source(source, name: str, layer: str) -> LocalFileStatus:
    """Check local file source for updates.

    Checks:
    - Local git status (uncommitted, unpushed)
    - Remote comparison (if has remote URL)
    """

    local_path = source.path

    # Get local git info
    local_sha = _get_local_sha(local_path)
    remote_url = _get_remote_url(local_path)
    uncommitted = _has_uncommitted_changes(local_path)
    unpushed = _has_unpushed_commits(local_path)

    status = LocalFileStatus(
        name=name,
        path=local_path,
        layer=layer,
        local_sha=local_sha[:7] if local_sha else None,
        remote_url=remote_url,
        has_remote=remote_url is not None,
        uncommitted_changes=uncommitted,
        unpushed_commits=unpushed,
    )

    # If has remote, compare SHAs
    if remote_url and local_sha:
        try:
            # Get current branch
            current_branch = _get_current_branch(local_path)
            if current_branch:
                remote_sha = await _get_github_commit_sha(remote_url, current_branch)

                if remote_sha != local_sha:
                    status.remote_sha = remote_sha[:7]
                    status.commits_behind = _count_commits_behind(local_path)
        except Exception as e:
            logger.debug(f"Could not check remote for {name}: {e}")

    return status


async def _check_git_source(source, name: str, layer: str, force: bool = False) -> CachedGitStatus | None:
    """Check cached git source for updates.

    Compares cached SHA with remote SHA.

    Args:
        source: GitSource to check
        name: Module name
        layer: Resolution layer
        force: When True, mark as needing update even if SHAs match

    Returns:
        CachedGitStatus or None if not cached
    """

    # Get cache path
    cache_key = source._get_cache_key() if hasattr(source, "_get_cache_key") else None
    cache_path = source.cache_dir / cache_key / source.ref if cache_key else None

    if not cache_path or not cache_path.exists():
        return None  # Not cached yet

    # Read cache metadata
    metadata_file = cache_path / ".amplifier_cache_metadata.json"

    if not metadata_file.exists():
        return None  # No metadata

    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))

        # Skip immutable refs
        if not metadata.get("is_mutable", True):
            return None

        cached_sha = metadata.get("sha")
        if not cached_sha:
            return None

        # Check remote
        remote_sha = await _get_github_commit_sha(source.url, source.ref)

        # Return ALL cached sources (not just those with updates)
        has_update = (remote_sha != cached_sha) or force
        return CachedGitStatus(
            name=name,
            url=source.url,
            ref=source.ref,
            layer=layer,
            cached_sha=cached_sha[:7],
            remote_sha=remote_sha[:7],
            has_update=has_update,
            age_days=_cache_age_days(metadata),
        )

    except Exception as e:
        logger.debug(f"Failed to check cached git source {name}: {e}")
        return None


# Helper functions for git operations


def _get_local_sha(repo_path: Path) -> str | None:
    """Get current commit SHA of local git repo."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Could not get local SHA for {repo_path}: {e}")
    return None


def _get_remote_url(repo_path: Path) -> str | None:
    """Get remote URL of local git repo."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Could not get remote URL for {repo_path}: {e}")
    return None


def _get_current_branch(repo_path: Path) -> str | None:
    """Get current branch of local git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch != "HEAD":  # Not detached HEAD
                return branch
    except Exception as e:
        logger.debug(f"Could not get current branch for {repo_path}: {e}")
    return None


def _has_uncommitted_changes(repo_path: Path) -> bool:
    """Check if repo has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _has_unpushed_commits(repo_path: Path) -> bool:
    """Check if repo has unpushed commits."""
    try:
        result = subprocess.run(
            ["git", "log", "@{u}..", "--oneline"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _count_commits_behind(repo_path: Path) -> int:
    """Count how many commits behind remote."""
    try:
        # Fetch first
        subprocess.run(["git", "fetch"], cwd=repo_path, capture_output=True, timeout=5)

        result = subprocess.run(
            ["git", "log", "..@{u}", "--oneline"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            return len(result.stdout.strip().split("\n"))
    except Exception:
        pass
    return 0


async def _check_all_cached_modules(force: bool = False) -> tuple[list[CachedGitStatus], int]:
    """Check ALL cached modules for updates (not just active ones).

    Uses centralized scan_cached_modules() utility for DRY compliance.

    Args:
        force: When True, include all cached modules even if up to date

    Returns:
        Tuple of (list of CachedGitStatus for modules with updates, total modules checked)
    """
    from .module_cache import scan_cached_modules

    # Get all cached modules using centralized utility
    cached_modules = scan_cached_modules()

    if not cached_modules:
        return [], 0

    statuses = []
    modules_checked = len(cached_modules)

    for module in cached_modules:
        # Skip immutable refs
        if not module.is_mutable:
            continue

        if not module.sha or not module.url or not module.ref:
            continue

        try:
            # Check remote SHA
            remote_sha = await _get_github_commit_sha(module.url, module.ref)

            # Add ALL cached modules (with has_update flag)
            # Note: module.sha is already truncated to 8 chars by scan_cached_modules
            has_update = (remote_sha[:8] != module.sha) or force

            statuses.append(
                CachedGitStatus(
                    name=module.module_id,
                    url=module.url,
                    ref=module.ref,
                    layer="cache",
                    cached_sha=module.sha[:7],  # Consistent 7 chars for display comparison
                    remote_sha=remote_sha[:7],
                    has_update=has_update,
                    age_days=_cache_age_days_from_string(module.cached_at),
                )
            )

        except (httpx.HTTPError, httpx.TimeoutException) as e:
            # Network/API errors - log but continue checking other modules
            logger.warning(f"Could not check {module.module_id}: {e}")
            continue
        except Exception as e:
            # Unexpected errors - log at ERROR and continue
            logger.error(f"Unexpected error checking {module.module_id}: {type(e).__name__}: {e}")
            continue

    return statuses, modules_checked


def _cache_age_days(metadata: dict) -> int:
    """Calculate cache age in days from metadata dict."""
    return _cache_age_days_from_string(metadata.get("cached_at", ""))


def _cache_age_days_from_string(cached_at: str) -> int:
    """Calculate cache age in days from ISO format string."""
    try:
        from datetime import datetime

        if not cached_at:
            return 0
        cached_at_dt = datetime.fromisoformat(cached_at)
        age = datetime.now() - cached_at_dt
        return age.days
    except Exception:
        return 0


async def _check_collection_sources(force: bool = False) -> list[CollectionStatus]:
    """Check installed collections for updates.

    Reads collection lock file, compares installed SHAs with remote SHAs.

    Args:
        force: When True, mark all collections as needing update

    Returns:
        List of CollectionStatus for ALL collections (with has_update flag)
    """
    from amplifier_collections import CollectionLock
    from amplifier_module_resolution import GitSource

    from ..paths import get_collection_lock_path

    # Load collection lock (user-global only for now)
    try:
        lock = CollectionLock(get_collection_lock_path(local=False))
        entries = lock.list_entries()
    except Exception as e:
        logger.debug(f"Could not load collection lock: {e}")
        return []

    if not entries:
        return []

    statuses = []
    for entry in entries:
        # Skip non-git sources
        if not entry.source or not entry.source.startswith("git+"):
            continue

        if not entry.commit:
            continue  # No SHA tracked

        try:
            # Parse source
            source = GitSource.from_uri(entry.source)

            # Check remote SHA
            remote_sha = await _get_github_commit_sha(source.url, source.ref)

            # Add ALL collections to report (not just those with updates)
            has_update = (remote_sha != entry.commit) or force
            statuses.append(
                CollectionStatus(
                    name=entry.name,
                    source=entry.source,
                    layer="user",
                    installed_sha=entry.commit[:7] if entry.commit else None,
                    remote_sha=remote_sha[:7],
                    has_update=has_update,
                    installed_at=entry.installed_at,
                )
            )

        except Exception as e:
            logger.debug(f"Could not check collection {entry.name}: {e}")
            continue

    return statuses


# GitHub API helpers (exposed for update_check.py)


async def _get_github_commit_sha(repo_url: str, ref: str) -> str:
    """Get SHA for ref using GitHub Atom feed (no API, no auth, no rate limits).

    Uses public Atom feed: https://github.com/{owner}/{repo}/commits/{ref}.atom
    This avoids GitHub API rate limiting (60 req/hr unauthenticated).
    """
    # Remove .git suffix properly (not with rstrip - it removes any char in '.git'!)
    url_clean = repo_url[:-4] if repo_url.endswith(".git") else repo_url
    parts = url_clean.split("github.com/")[-1].split("/")
    if len(parts) < 2:
        raise ValueError(f"Could not parse GitHub URL: {repo_url}")

    owner, repo = parts[0], parts[1]

    # Fetch Atom feed (public, no auth, no rate limits)
    atom_url = f"https://github.com/{owner}/{repo}/commits/{ref}.atom"

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(atom_url)
        response.raise_for_status()

        # Parse XML for first commit SHA
        # Format: <id>tag:github.com,2008:Grit::Commit/{SHA}</id>
        match = re.search(r"<id>tag:github\.com,2008:Grit::Commit/([a-f0-9]{40})</id>", response.text)
        if not match:
            raise ValueError(f"Could not extract commit SHA from Atom feed: {atom_url}")

        return match.group(1)


async def _get_commit_details(repo_url: str, sha: str) -> dict:
    """Get commit details for better UX."""
    # Remove .git suffix properly
    url_clean = repo_url[:-4] if repo_url.endswith(".git") else repo_url
    parts = url_clean.split("github.com/")[-1].split("/")
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
            "message": data["commit"]["message"].split("\n")[0],
            "date": data["commit"]["author"]["date"],
            "author": data["commit"]["author"]["name"],
        }


def _get_github_auth_headers() -> dict:
    """Get GitHub auth headers if token available."""
    import os

    token = os.getenv("GITHUB_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}

    gh_config = Path.home() / ".config" / "gh" / "hosts.yml"
    if gh_config.exists():
        try:
            import yaml

            config = yaml.safe_load(gh_config.read_text())
            if token := config.get("github.com", {}).get("oauth_token"):
                return {"Authorization": f"Bearer {token}"}
        except Exception:
            pass

    return {}
