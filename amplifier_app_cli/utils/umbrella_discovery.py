"""Umbrella package source discovery.

Dynamically discovers where Amplifier was installed from without hardcoding URLs.
Works for standard installs, forks, and different branches.
"""

import importlib.metadata
import json
import logging
import tomllib
from collections import Counter
from dataclasses import dataclass

import httpx  # Fail fast if missing - required for fetching umbrella dependencies

logger = logging.getLogger(__name__)


@dataclass
class UmbrellaInfo:
    """Information about the umbrella package source."""

    url: str  # https://github.com/microsoft/amplifier
    ref: str  # main, etc.
    commit_id: str | None


def discover_umbrella_source() -> UmbrellaInfo | None:
    """Discover umbrella package source dynamically.

    Strategy:
    1. Try to read from umbrella package directly (production)
    2. Reconstruct from library git URLs (fallback)
    3. Return None if can't determine (local dev)

    Returns:
        UmbrellaInfo if discovered, None otherwise
    """
    # Strategy 1: Read umbrella package direct_url.json
    try:
        dist = importlib.metadata.distribution("amplifier")
        if hasattr(dist, "read_text"):
            try:
                direct_url_text = dist.read_text("direct_url.json")
                if not direct_url_text:
                    return None
                direct_url = json.loads(direct_url_text)

                # Check if it's a git install
                if "vcs_info" in direct_url:
                    logger.info(
                        f"Discovered umbrella from package: {direct_url['url']}"
                    )
                    return UmbrellaInfo(
                        url=direct_url["url"],
                        ref=direct_url["vcs_info"].get("requested_revision", "main"),
                        commit_id=direct_url["vcs_info"].get("commit_id"),
                    )
            except Exception as e:
                logger.debug(f"Could not read umbrella direct_url.json: {e}")
    except importlib.metadata.PackageNotFoundError:
        logger.debug("Umbrella package 'amplifier' not installed")

    # Strategy 2: Reconstruct from library git URLs
    logger.debug("Reconstructing umbrella URL from libraries")
    return reconstruct_umbrella_from_libraries()


def reconstruct_umbrella_from_libraries() -> UmbrellaInfo | None:
    """Reconstruct umbrella URL by analyzing library sources.

    Logic:
    - Check amplifier-core, amplifier-app-cli git URLs
    - Extract GitHub org/owner
    - Determine branch consensus
    - Construct umbrella URL: https://github.com/{org}/amplifier

    Returns:
        UmbrellaInfo if successful, None if can't determine
    """
    # Libraries to check (in priority order)
    library_names = [
        "amplifier-core",
        "amplifier-app-cli",
        "amplifier-profiles",
        "amplifier-config",
    ]

    git_sources = []

    for lib_name in library_names:
        try:
            dist = importlib.metadata.distribution(lib_name)
            if hasattr(dist, "read_text"):
                try:
                    direct_url_text = dist.read_text("direct_url.json")
                    if not direct_url_text:
                        continue
                    direct_url = json.loads(direct_url_text)

                    # Skip editable/local installs
                    if "dir_info" in direct_url:
                        continue

                    # Extract git info
                    if "vcs_info" in direct_url:
                        git_sources.append(
                            {
                                "lib_name": lib_name,
                                "url": direct_url["url"],
                                "ref": direct_url["vcs_info"].get(
                                    "requested_revision", "main"
                                ),
                                "commit_id": direct_url["vcs_info"].get("commit_id"),
                            }
                        )
                except Exception as e:
                    logger.debug(f"Could not read {lib_name} direct_url.json: {e}")
        except importlib.metadata.PackageNotFoundError:
            continue

    if not git_sources:
        logger.debug("No git sources found in libraries")
        return None

    # Extract GitHub org from first library
    first_source = git_sources[0]
    github_org = extract_github_org(first_source["url"])

    if not github_org:
        logger.debug(f"Could not extract GitHub org from {first_source['url']}")
        return None

    # Determine branch consensus (most common branch)
    branches = [s["ref"] for s in git_sources]
    most_common_branch = Counter(branches).most_common(1)[0][0]

    # Construct umbrella URL
    umbrella_url = f"https://github.com/{github_org}/amplifier"

    logger.info(
        f"Reconstructed umbrella URL: {umbrella_url}@{most_common_branch} (from {len(git_sources)} libraries)"
    )

    return UmbrellaInfo(url=umbrella_url, ref=most_common_branch, commit_id=None)


def extract_github_org(git_url: str) -> str | None:
    """Extract GitHub org/owner from git URL.

    Examples:
        https://github.com/microsoft/amplifier-core -> microsoft
        git@github.com:microsoft/amplifier-core -> microsoft

    Args:
        git_url: Git URL string

    Returns:
        Organization/owner name, or None if can't parse
    """
    # Remove .git suffix properly (not with rstrip!)
    git_url = git_url[:-4] if git_url.endswith(".git") else git_url

    # Handle HTTPS URLs
    if "github.com/" in git_url:
        parts = git_url.split("github.com/")[-1].split("/")
        if len(parts) >= 1:
            return parts[0]

    # Handle SSH URLs
    if "github.com:" in git_url:
        parts = git_url.split("github.com:")[-1].split("/")
        if len(parts) >= 1:
            return parts[0]

    return None


async def fetch_umbrella_dependencies(
    client: httpx.AsyncClient, umbrella_info: UmbrellaInfo
) -> dict[str, dict]:
    """Fetch dependency info from umbrella pyproject.toml.

    Args:
        client: Shared httpx client for all HTTP requests
        umbrella_info: Discovered umbrella source info

    Returns:
        Dict of library name -> {url, branch}
    """

    # Construct raw GitHub URL for pyproject.toml
    github_org = extract_github_org(umbrella_info.url)

    raw_url = f"https://raw.githubusercontent.com/{github_org}/amplifier/{umbrella_info.ref}/pyproject.toml"

    logger.debug(f"Fetching umbrella pyproject.toml from: {raw_url}")

    response = await client.get(raw_url)
    response.raise_for_status()

    # Parse TOML
    config = tomllib.loads(response.text)

    # Extract git sources
    sources = config.get("tool", {}).get("uv", {}).get("sources", {})

    deps = {}
    for name, source_info in sources.items():
        if isinstance(source_info, dict) and "git" in source_info:
            deps[name] = {
                "url": source_info["git"],
                "branch": source_info.get("branch", "main"),
            }

    logger.info(f"Found {len(deps)} library dependencies in umbrella")
    return deps


@dataclass
class EcosystemPackage:
    """Information about an ecosystem package discovered via [tool.uv.sources]."""

    name: str
    url: str
    branch: str
    depth: int  # 0=direct umbrella dep, 1=transitive, etc.


async def discover_ecosystem_packages(
    client: httpx.AsyncClient,
    umbrella_info: UmbrellaInfo,
    max_depth: int = 5,
) -> list[EcosystemPackage]:
    """Recursively discover all ecosystem packages via [tool.uv.sources].

    Crawls the dependency tree starting from the umbrella, finding all packages
    that have git sources defined in [tool.uv.sources]. These are "our" ecosystem
    packages that we track for updates.

    Args:
        client: Shared httpx client for all HTTP requests
        umbrella_info: Discovered umbrella source info
        max_depth: Maximum recursion depth to prevent runaway (default 5)

    Returns:
        List of EcosystemPackage with name, url, branch, and depth
    """
    visited_urls: set[str] = set()
    packages: dict[str, EcosystemPackage] = {}

    async def fetch_uv_sources(url: str, ref: str) -> dict[str, dict]:
        """Fetch [tool.uv.sources] from a repo's pyproject.toml."""
        github_org = extract_github_org(url)
        if not github_org:
            return {}

        # Extract repo name from URL
        repo_name = url.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        raw_url = f"https://raw.githubusercontent.com/{github_org}/{repo_name}/{ref}/pyproject.toml"

        try:
            response = await client.get(raw_url)
            response.raise_for_status()
            config = tomllib.loads(response.text)
            sources = config.get("tool", {}).get("uv", {}).get("sources", {})

            deps = {}
            for name, source_info in sources.items():
                if isinstance(source_info, dict) and "git" in source_info:
                    deps[name] = {
                        "url": source_info["git"],
                        "branch": source_info.get("branch", "main"),
                    }
            return deps
        except Exception as e:
            logger.debug(f"Could not fetch uv.sources from {raw_url}: {e}")
            return {}

    async def crawl(url: str, ref: str, depth: int) -> None:
        """Recursively crawl a package's dependencies."""
        # Normalize URL for deduplication
        normalized_url = url.rstrip("/")
        if normalized_url.endswith(".git"):
            normalized_url = normalized_url[:-4]

        if normalized_url in visited_urls:
            return
        if depth > max_depth:
            logger.warning(f"Max depth {max_depth} reached, stopping crawl")
            return

        visited_urls.add(normalized_url)

        # Fetch this package's uv.sources
        sources = await fetch_uv_sources(url, ref)

        for name, info in sources.items():
            if name not in packages:
                packages[name] = EcosystemPackage(
                    name=name,
                    url=info["url"],
                    branch=info["branch"],
                    depth=depth,
                )
                logger.debug(f"Discovered ecosystem package: {name} (depth={depth})")

                # Recurse into this dependency
                await crawl(info["url"], info["branch"], depth + 1)

    # Start crawling from the umbrella
    await crawl(umbrella_info.url, umbrella_info.ref, depth=0)

    # Sort by depth then name for consistent output
    result = sorted(packages.values(), key=lambda p: (p.depth, p.name))
    logger.info(f"Discovered {len(result)} ecosystem packages")
    return result


def get_installed_package_info(package_name: str) -> dict | None:
    """Get installation info for a single package.

    Returns dict with:
        - name: package name
        - version: installed version
        - sha: git SHA if available (7 chars)
        - is_local: True if installed from local path (editable or file://)
        - is_git: True if local path is a git repository
        - has_changes: True if git repo has uncommitted changes
        - path: local path if applicable
        - source_url: git URL if installed from git

    Returns None if package is not installed.
    """
    import subprocess

    try:
        dist = importlib.metadata.distribution(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None

    version = dist.version
    sha = None
    is_local = False
    is_git = False
    has_changes = False
    path = None
    source_url = None

    if hasattr(dist, "read_text"):
        try:
            direct_url_text = dist.read_text("direct_url.json")
            if direct_url_text:
                direct_url = json.loads(direct_url_text)

                if "dir_info" in direct_url:
                    # Local install (editable or file://)
                    is_local = True
                    path = direct_url.get("url", "").replace("file://", "")

                    # Check if it's a git repo and get status
                    if path:
                        try:
                            result = subprocess.run(
                                ["git", "rev-parse", "--git-dir"],
                                cwd=path,
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if result.returncode == 0:
                                is_git = True

                                # Get HEAD SHA
                                result = subprocess.run(
                                    ["git", "rev-parse", "HEAD"],
                                    cwd=path,
                                    capture_output=True,
                                    text=True,
                                    timeout=5,
                                )
                                if result.returncode == 0:
                                    sha = result.stdout.strip()[:7]

                                # Check for uncommitted changes
                                result = subprocess.run(
                                    ["git", "status", "--porcelain"],
                                    cwd=path,
                                    capture_output=True,
                                    text=True,
                                    timeout=5,
                                )
                                if result.returncode == 0:
                                    has_changes = bool(result.stdout.strip())
                        except Exception:
                            pass

                elif "vcs_info" in direct_url:
                    # Git install from URL
                    sha = direct_url["vcs_info"].get("commit_id", "")[:7]
                    source_url = direct_url.get("url")
        except Exception:
            pass

    return {
        "name": package_name,
        "version": version,
        "sha": sha,
        "is_local": is_local,
        "is_git": is_git,
        "has_changes": has_changes,
        "path": path,
        "source_url": source_url,
    }
