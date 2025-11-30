"""Execute updates by delegating to external tools.

Philosophy: Orchestrate, don't reimplement. Delegate to uv and existing commands.
Selective updates: Only update modules that actually have updates, then re-download.
"""

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from .source_status import CachedGitStatus
from .source_status import CollectionStatus
from .source_status import UpdateReport
from .umbrella_discovery import UmbrellaInfo

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of update execution."""

    success: bool
    updated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


async def execute_selective_module_update(
    modules_to_update: list[CachedGitStatus],
    progress_callback: Callable[[str, str], None] | None = None,
) -> ExecutionResult:
    """Selectively update only modules that have updates.

    Philosophy: Only update what needs updating, then re-download immediately.
    Uses centralized module_cache utilities for DRY compliance.

    Args:
        modules_to_update: List of CachedGitStatus with has_update=True
        progress_callback: Optional callback(module_name, status) for progress reporting

    Returns:
        ExecutionResult with per-module success/failure
    """
    from .module_cache import find_cached_module
    from .module_cache import update_module

    if not modules_to_update:
        return ExecutionResult(
            success=True,
            messages=["No modules need updating"],
        )

    updated = []
    failed = []
    errors = {}

    for status in modules_to_update:
        module_name = status.name
        if progress_callback:
            progress_callback(module_name, "updating")

        try:
            # Use URL and ref from status if available
            if status.url and status.ref:
                logger.debug(f"Updating {module_name} from {status.url}@{status.ref}")
                update_module(url=status.url, ref=status.ref, progress_callback=progress_callback)
                updated.append(f"{module_name}@{status.ref}")
                if progress_callback:
                    progress_callback(module_name, "done")
            else:
                # Fallback: find module by name to get URL and ref
                cached = find_cached_module(module_name)
                if cached:
                    update_module(url=cached.url, ref=cached.ref, progress_callback=progress_callback)
                    updated.append(f"{module_name}@{cached.ref}")
                    if progress_callback:
                        progress_callback(module_name, "done")
                else:
                    failed.append(module_name)
                    errors[module_name] = "Could not find cache entry"

        except Exception as e:
            logger.warning(f"Failed to update {module_name}: {e}")
            failed.append(module_name)
            errors[module_name] = str(e)
            if progress_callback:
                progress_callback(module_name, "failed")

    return ExecutionResult(
        success=len(failed) == 0,
        updated=updated,
        failed=failed,
        errors=errors,
        messages=[f"Updated {len(updated)} module(s)"] if updated else [],
    )


async def execute_selective_collection_update(
    collections_to_update: list[CollectionStatus],
    progress_callback: Callable[[str, str], None] | None = None,
) -> ExecutionResult:
    """Selectively update only collections that have updates.

    Philosophy: Only update what needs updating, consistent with module behavior.
    Uses amplifier_collections directly for DRY compliance.

    Args:
        collections_to_update: List of CollectionStatus with has_update=True
        progress_callback: Optional callback(collection_name, status) for progress reporting

    Returns:
        ExecutionResult with per-collection success/failure
    """
    import shutil

    from amplifier_collections import CollectionLock
    from amplifier_collections import install_collection
    from amplifier_module_resolution import GitSource

    from ..paths import get_collection_lock_path

    if not collections_to_update:
        return ExecutionResult(
            success=True,
            messages=["No collections need updating"],
        )

    # Load collection lock to find installation paths
    lock = CollectionLock(get_collection_lock_path(local=False))
    entries = {e.name: e for e in lock.list_entries()}

    updated = []
    failed = []
    errors = {}

    for status in collections_to_update:
        collection_name = status.name
        if progress_callback:
            progress_callback(collection_name, "updating")

        try:
            # Find entry in lock file
            entry = entries.get(collection_name)
            if not entry:
                failed.append(collection_name)
                errors[collection_name] = "Not found in collection lock"
                if progress_callback:
                    progress_callback(collection_name, "failed")
                continue

            # Parse source and re-install
            source = GitSource.from_uri(entry.source)
            target_dir = Path(entry.path)

            # Remove existing installation
            if target_dir.exists():
                shutil.rmtree(target_dir)

            # Re-install
            metadata = await install_collection(source=source, target_dir=target_dir, lock=lock)

            updated.append(f"{collection_name}@{metadata.version}")
            if progress_callback:
                progress_callback(collection_name, "done")

        except Exception as e:
            logger.warning(f"Failed to update collection {collection_name}: {e}")
            failed.append(collection_name)
            errors[collection_name] = str(e)
            if progress_callback:
                progress_callback(collection_name, "failed")

    return ExecutionResult(
        success=len(failed) == 0,
        updated=updated,
        failed=failed,
        errors=errors,
        messages=[f"Updated {len(updated)} collection(s)"] if updated else [],
    )


async def fetch_library_git_dependencies(repo_url: str, ref: str) -> dict[str, dict]:
    """Fetch git dependencies from a library's pyproject.toml.

    Args:
        repo_url: GitHub repository URL
        ref: Branch/tag to fetch from

    Returns:
        Dict of library name -> {url, branch}
    """
    import tomllib

    import httpx

    from .umbrella_discovery import extract_github_org

    try:
        # Construct raw GitHub URL for pyproject.toml
        github_org = extract_github_org(repo_url)
        repo_name = repo_url.split("/")[-1].replace(".git", "")

        raw_url = f"https://raw.githubusercontent.com/{github_org}/{repo_name}/{ref}/pyproject.toml"

        logger.debug(f"Fetching library dependencies from: {raw_url}")

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

            logger.debug(f"Found {len(deps)} git dependencies in {repo_name}")
            return deps

    except Exception as e:
        logger.debug(f"Could not fetch dependencies for {repo_url}: {e}")
        return {}


async def check_umbrella_dependencies_for_updates(umbrella_info: UmbrellaInfo) -> bool:
    """Check if any dependencies (recursively) have updates.

    Checks umbrella dependencies AND their transitive git dependencies.
    For example: umbrella → amplifier-app-cli → amplifier-profiles

    Args:
        umbrella_info: Discovered umbrella source info

    Returns:
        True if any dependency (at any level) has updates, False otherwise
    """
    import importlib.metadata
    import json

    from .source_status import _get_github_commit_sha
    from .umbrella_discovery import fetch_umbrella_dependencies

    try:
        # Step 1: Fetch umbrella's direct dependencies
        umbrella_deps = await fetch_umbrella_dependencies(umbrella_info)

        # Step 2: Recursively fetch transitive git dependencies
        all_deps = dict(umbrella_deps)  # Start with umbrella deps
        checked = set()  # Track what we've already checked to avoid cycles

        for dep_name, dep_info in list(umbrella_deps.items()):
            if dep_name in checked:
                continue
            checked.add(dep_name)

            # Fetch this dependency's git dependencies
            transitive_deps = await fetch_library_git_dependencies(dep_info["url"], dep_info["branch"])

            for trans_name, trans_info in transitive_deps.items():
                if trans_name not in all_deps:
                    all_deps[trans_name] = trans_info
                    logger.debug(f"Found transitive dependency: {trans_name} (via {dep_name})")

        logger.debug(f"Checking {len(all_deps)} dependencies (including transitive) for updates")

        # Step 3: Check each dependency for updates
        for dep_name, dep_info in all_deps.items():
            try:
                # Get installed SHA (from direct_url.json)
                dist = importlib.metadata.distribution(dep_name)
                if not hasattr(dist, "read_text"):
                    continue

                direct_url_text = dist.read_text("direct_url.json")
                if not direct_url_text:
                    continue

                direct_url = json.loads(direct_url_text)

                # Skip editable/local installs
                if "dir_info" in direct_url:
                    continue

                # Get installed commit SHA
                if "vcs_info" not in direct_url:
                    continue

                installed_sha = direct_url["vcs_info"].get("commit_id")
                if not installed_sha:
                    continue

                # Get remote SHA
                remote_sha = await _get_github_commit_sha(dep_info["url"], dep_info["branch"])

                # Compare
                if installed_sha != remote_sha:
                    logger.info(f"Dependency {dep_name} has updates: {installed_sha[:7]} → {remote_sha[:7]}")
                    return True

            except Exception as e:
                logger.debug(f"Could not check dependency {dep_name}: {e}")
                continue

        logger.debug("All dependencies up to date")
        return False

    except Exception as e:
        logger.warning(f"Could not check umbrella dependencies: {e}")
        return False


async def execute_self_update(umbrella_info: UmbrellaInfo) -> ExecutionResult:
    """Delegate to 'uv tool install --force'.

    Philosophy: uv is designed for this, use it.
    """
    url = f"git+{umbrella_info.url}@{umbrella_info.ref}"

    try:
        result = subprocess.run(
            ["uv", "tool", "install", "--force", url],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                updated=["amplifier"],
                messages=["Amplifier updated successfully", "Restart amplifier to use new version"],
            )
        error_msg = result.stderr.strip() or "Unknown error"
        return ExecutionResult(
            success=False,
            failed=["amplifier"],
            errors={"amplifier": error_msg},
            messages=[f"Self-update failed: {error_msg}"],
        )

    except subprocess.TimeoutExpired:
        return ExecutionResult(
            success=False,
            failed=["amplifier"],
            errors={"amplifier": "Timeout after 120 seconds"},
            messages=["Self-update timed out"],
        )
    except FileNotFoundError:
        return ExecutionResult(
            success=False,
            failed=["amplifier"],
            errors={"amplifier": "uv not found"},
            messages=["uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"],
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            failed=["amplifier"],
            errors={"amplifier": str(e)},
            messages=[f"Self-update error: {e}"],
        )


async def execute_updates(report: UpdateReport, umbrella_info: UmbrellaInfo | None = None) -> ExecutionResult:
    """Orchestrate all updates based on report.

    Philosophy: Sequential execution (modules first, then self) for safety.

    Args:
        report: Update status report from check_all_sources
        umbrella_info: Optional umbrella info if already checked for updates
    """
    all_updated = []
    all_failed = []
    all_messages = []
    all_errors = {}
    overall_success = True

    # 1. Execute selective module update (only modules with updates)
    modules_needing_update = [s for s in report.cached_git_sources if s.has_update]
    if modules_needing_update:
        logger.info(f"Selectively updating {len(modules_needing_update)} module(s)...")
        result = await execute_selective_module_update(modules_needing_update)

        all_updated.extend(result.updated)
        all_failed.extend(result.failed)
        all_messages.extend(result.messages)
        all_errors.update(result.errors)

        if not result.success:
            overall_success = False

    # 2. Execute selective collection update (only collections with updates)
    collections_needing_update = [s for s in report.collection_sources if s.has_update]
    if collections_needing_update:
        logger.info(f"Selectively updating {len(collections_needing_update)} collection(s)...")
        result = await execute_selective_collection_update(collections_needing_update)

        all_updated.extend(result.updated)
        all_failed.extend(result.failed)
        all_messages.extend(result.messages)
        all_errors.update(result.errors)

        if not result.success:
            overall_success = False

    # 3. Execute self-update if umbrella_info provided (already checked by caller)
    if umbrella_info:
        logger.info("Updating Amplifier (umbrella dependencies have updates)...")
        result = await execute_self_update(umbrella_info)

        all_updated.extend(result.updated)
        all_failed.extend(result.failed)
        all_messages.extend(result.messages)
        all_errors.update(result.errors)

        if not result.success:
            overall_success = False

    # 4. Compile final result
    return ExecutionResult(
        success=overall_success and len(all_failed) == 0,
        updated=all_updated,
        failed=all_failed,
        messages=all_messages,
        errors=all_errors,
    )
