"""Installation mode detection for Amplifier.

Detects whether Amplifier is running in production (git install) or local dev (editable).
Provides information about installed libraries and their sources.
"""

import importlib.metadata
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class LibraryInfo:
    """Information about an installed library."""

    name: str
    version: str
    location: Path | None
    is_editable: bool
    git_sha: str | None  # None if editable/local
    git_url: str | None  # None if editable/local


@dataclass
class InstallationInfo:
    """Information about how Amplifier is installed."""

    mode: Literal["production", "local_dev"]
    libraries: dict[str, LibraryInfo]
    monorepo_root: Path | None


def detect_installation() -> InstallationInfo:
    """Detect how Amplifier is installed (production vs local dev).

    Returns:
        InstallationInfo with mode, libraries, and optional monorepo root
    """
    # Check if amplifier-app-cli is editable
    try:
        dist = importlib.metadata.distribution("amplifier-app-cli")
        is_editable = _is_editable_install(dist)

        if is_editable:
            # Local dev mode
            location = _get_dist_location(dist)
            monorepo_root = _find_monorepo_root(location)
            libraries = _scan_local_libraries(monorepo_root)

            return InstallationInfo(mode="local_dev", libraries=libraries, monorepo_root=monorepo_root)
        # Production mode
        libraries = _scan_production_libraries()

        return InstallationInfo(mode="production", libraries=libraries, monorepo_root=None)

    except importlib.metadata.PackageNotFoundError:
        # Shouldn't happen, but fallback to production
        logger.warning("amplifier-app-cli not found - assuming production mode")
        return InstallationInfo(mode="production", libraries={}, monorepo_root=None)


def _is_editable_install(dist) -> bool:
    """Check if distribution is editable install."""
    # Check for direct_url.json with editable=true
    if hasattr(dist, "read_text"):
        try:
            direct_url_text = dist.read_text("direct_url.json")
            if direct_url_text:
                direct_url = json.loads(direct_url_text)
                if direct_url.get("dir_info", {}).get("editable"):
                    return True
        except Exception:
            pass

    # Check if location contains amplifier-dev (heuristic)
    location = _get_dist_location(dist)
    if location:
        location_str = str(location)
        return "/amplifier-dev/" in location_str or "\\amplifier-dev\\" in location_str

    return False


def _get_dist_location(dist) -> Path | None:
    """Get distribution location.

    For editable installs, reads from direct_url.json to get source location.
    For regular installs, uses _path.
    """
    # Try to get location from direct_url.json (editable installs)
    if hasattr(dist, "read_text"):
        try:
            direct_url_text = dist.read_text("direct_url.json")
            if direct_url_text:
                direct_url = json.loads(direct_url_text)
                if "dir_info" in direct_url:
                    # Editable install - use source directory
                    url = direct_url["url"]
                    if url.startswith("file://"):
                        return Path(url[7:])
                    return Path(url)
        except Exception:
            pass

    # Fallback to _path (regular installs)
    if hasattr(dist, "_path"):
        return Path(dist._path).parent
    return None


def _find_monorepo_root(location: Path | None) -> Path | None:
    """Find amplifier-dev monorepo root by walking up directories."""
    if not location:
        return None

    current = location
    for _ in range(10):  # Max 10 levels up
        if current.name == "amplifier-dev":
            return current
        if current.parent == current:  # Reached filesystem root
            break
        current = current.parent

    return None


def _scan_local_libraries(monorepo_root: Path | None) -> dict[str, LibraryInfo]:
    """Scan locally installed editable libraries."""
    if not monorepo_root:
        return {}

    libraries = {}

    # Core libraries we track
    lib_names = [
        "amplifier-core",
        "amplifier-app-cli",
        "amplifier-profiles",
        "amplifier-config",
        "amplifier-collections",
        "amplifier-module-resolution",
    ]

    for lib_name in lib_names:
        lib_path = monorepo_root / lib_name
        if lib_path.exists():
            libraries[lib_name] = LibraryInfo(
                name=lib_name,
                version="dev",
                location=lib_path,
                is_editable=True,
                git_sha=_get_local_git_sha(lib_path),
                git_url=_get_local_git_remote(lib_path),
            )

    return libraries


def _scan_production_libraries() -> dict[str, LibraryInfo]:
    """Scan production-installed libraries."""
    libraries = {}

    lib_names = [
        "amplifier-core",
        "amplifier-app-cli",
        "amplifier-profiles",
        "amplifier-config",
        "amplifier-collections",
        "amplifier-module-resolution",
    ]

    for lib_name in lib_names:
        try:
            dist = importlib.metadata.distribution(lib_name)
            location = _get_dist_location(dist)

            # Try to read installed SHA from metadata
            installed_sha = _get_installed_sha_from_dist(dist)

            libraries[lib_name] = LibraryInfo(
                name=lib_name,
                version=dist.version,
                location=location,
                is_editable=False,
                git_sha=installed_sha,
                git_url=None,  # Will be determined from umbrella
            )
        except importlib.metadata.PackageNotFoundError:
            logger.debug(f"Library {lib_name} not installed")
            continue

    return libraries


def _get_local_git_sha(repo_path: Path) -> str | None:
    """Get current commit SHA of local git repo."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Could not get git SHA for {repo_path}: {e}")
    return None


def _get_local_git_remote(repo_path: Path) -> str | None:
    """Get remote URL of local git repo."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], cwd=repo_path, capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Could not get git remote for {repo_path}: {e}")
    return None


def _get_installed_sha_from_dist(dist) -> str | None:
    """Get SHA from distribution's direct_url.json."""
    if not hasattr(dist, "read_text"):
        return None

    try:
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            direct_url = json.loads(direct_url_text)
            if "vcs_info" in direct_url:
                return direct_url["vcs_info"].get("commit_id")
    except Exception as e:
        logger.debug(f"Could not read SHA from {dist.name}: {e}")

    return None
