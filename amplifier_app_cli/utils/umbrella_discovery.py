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
    ref: str  # next, main, etc.
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
                    logger.info(f"Discovered umbrella from package: {direct_url['url']}")
                    return UmbrellaInfo(
                        url=direct_url["url"],
                        ref=direct_url["vcs_info"].get("requested_revision", "next"),
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
                                "ref": direct_url["vcs_info"].get("requested_revision", "main"),
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

    logger.info(f"Reconstructed umbrella URL: {umbrella_url}@{most_common_branch} (from {len(git_sources)} libraries)")

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


async def fetch_umbrella_dependencies(umbrella_info: UmbrellaInfo) -> dict[str, dict]:
    """Fetch dependency info from umbrella pyproject.toml.

    Args:
        umbrella_info: Discovered umbrella source info

    Returns:
        Dict of library name -> {url, branch}
    """

    # Construct raw GitHub URL for pyproject.toml
    github_org = extract_github_org(umbrella_info.url)

    raw_url = f"https://raw.githubusercontent.com/{github_org}/amplifier/{umbrella_info.ref}/pyproject.toml"

    logger.debug(f"Fetching umbrella pyproject.toml from: {raw_url}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(raw_url)
        response.raise_for_status()

        # Parse TOML
        config = tomllib.loads(response.text)

        # Extract git sources
        sources = config.get("tool", {}).get("uv", {}).get("sources", {})

        deps = {}
        for name, source_info in sources.items():
            if isinstance(source_info, dict) and "git" in source_info:
                deps[name] = {"url": source_info["git"], "branch": source_info.get("branch", "main")}

        logger.info(f"Found {len(deps)} library dependencies in umbrella")
        return deps
