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
                await update_module(
                    url=status.url, ref=status.ref, progress_callback=progress_callback
                )
                updated.append(f"{module_name}@{status.ref}")
                if progress_callback:
                    progress_callback(module_name, "done")
            else:
                # Fallback: find module by name to get URL and ref
                cached = find_cached_module(module_name)
                if cached:
                    await update_module(
                        url=cached.url,
                        ref=cached.ref,
                        progress_callback=progress_callback,
                    )
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
                    deps[name] = {
                        "url": source_info["git"],
                        "branch": source_info.get("branch", "main"),
                    }

            logger.debug(f"Found {len(deps)} git dependencies in {repo_name}")
            return deps

    except Exception as e:
        logger.debug(f"Could not fetch dependencies for {repo_url}: {e}")
        return {}


async def check_umbrella_dependencies_for_updates(umbrella_info: UmbrellaInfo) -> bool:
    """Check if any dependencies (recursively) have updates.

    Checks umbrella dependencies AND their transitive git dependencies.
    For example: umbrella → amplifier-app-cli → amplifier-foundation

    Args:
        umbrella_info: Discovered umbrella source info

    Returns:
        True if any dependency (at any level) has updates, False otherwise
    """
    import importlib.metadata
    import json

    import httpx

    from .source_status import _get_github_commit_sha
    from .umbrella_discovery import fetch_umbrella_dependencies

    try:
        # Use single shared httpx client for all requests
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: Fetch umbrella's direct dependencies
            umbrella_deps = await fetch_umbrella_dependencies(client, umbrella_info)

            # Step 2: Recursively fetch transitive git dependencies
            all_deps = dict(umbrella_deps)  # Start with umbrella deps
            checked = set()  # Track what we've already checked to avoid cycles

            for dep_name, dep_info in list(umbrella_deps.items()):
                if dep_name in checked:
                    continue
                checked.add(dep_name)

                # Fetch this dependency's git dependencies
                transitive_deps = await fetch_library_git_dependencies(
                    dep_info["url"], dep_info["branch"]
                )

                for trans_name, trans_info in transitive_deps.items():
                    if trans_name not in all_deps:
                        all_deps[trans_name] = trans_info
                        logger.debug(
                            f"Found transitive dependency: {trans_name} (via {dep_name})"
                        )

            logger.debug(
                f"Checking {len(all_deps)} dependencies (including transitive) for updates"
            )

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
                    remote_sha = await _get_github_commit_sha(
                        client, dep_info["url"], dep_info["branch"]
                    )

                    # Compare
                    if installed_sha != remote_sha:
                        logger.info(
                            f"Dependency {dep_name} has updates: {installed_sha[:7]} → {remote_sha[:7]}"
                        )
                        return True

                except Exception as e:
                    logger.debug(f"Could not check dependency {dep_name}: {e}")
                    continue

            logger.debug("All dependencies up to date")
            return False

    except Exception as e:
        logger.warning(f"Could not check umbrella dependencies: {e}")
        return False


def _extract_dependencies_from_pyproject(pyproject_path: Path) -> list[str]:
    """Extract dependency package names from a pyproject.toml file.

    Args:
        pyproject_path: Path to pyproject.toml file.

    Returns:
        List of dependency package names (preserving original case for metadata lookup).
    """
    import re
    import tomllib

    if not pyproject_path.exists():
        return []

    try:
        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        logger.debug(f"Could not parse {pyproject_path}: {e}")
        return []

    deps = []

    # Get dependencies from [project.dependencies]
    project_deps = config.get("project", {}).get("dependencies", [])
    for dep in project_deps:
        # Parse dependency string like "aiohttp>=3.8", "requests[security]", or "zope.interface>=5.0"
        # Extract the full package name including dots (for namespace packages)
        # Stops at: whitespace, extras [...], version specifiers [<>=!~], markers [;], URL [@]
        match = re.match(r"^([a-zA-Z0-9._-]+?)(?:\s|\[|[<>=!~;@]|$)", dep)
        if match:
            deps.append(match.group(1))

    return deps


def _check_dependency_installed(dep_name: str) -> bool:
    """Check if a dependency is installed in the current environment.

    Uses importlib.metadata to check by distribution name, which correctly
    handles packages where the import name differs from the package name
    (e.g., Pillow → PIL, beautifulsoup4 → bs4, scikit-learn → sklearn).

    Args:
        dep_name: Package/distribution name (e.g., "aiohttp", "Pillow").

    Returns:
        True if the package is installed, False otherwise.
    """
    import importlib.metadata

    # Normalize for comparison: PEP 503 says package names are case-insensitive
    # and treats hyphens/underscores as equivalent
    normalized = dep_name.lower().replace("-", "_").replace(".", "_")

    try:
        # Try exact name first
        importlib.metadata.distribution(dep_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        pass

    # Try normalized variations (handles case differences and hyphen/underscore)
    for variation in [normalized, normalized.replace("_", "-")]:
        try:
            importlib.metadata.distribution(variation)
            return True
        except importlib.metadata.PackageNotFoundError:
            continue

    return False


def _invalidate_modules_with_missing_deps() -> tuple[int, int]:
    """Surgically invalidate only modules whose dependencies are missing.

    After `uv tool install --force` recreates the Python environment, previously
    installed module dependencies (like aiohttp for tool-web) may be removed.
    This function checks each module's dependencies and only invalidates entries
    for modules that have missing dependencies.

    Returns:
        Tuple of (modules_checked, modules_invalidated).

    Note:
        TODO: Consider consolidating with InstallStateManager from amplifier-foundation.
        Currently manipulates the JSON file directly for simplicity, but a shared
        API would be cleaner. See: amplifier_foundation.modules.install_state
    """
    import json

    from amplifier_app_cli.paths import get_install_state_path

    install_state_file = get_install_state_path()

    if not install_state_file.exists():
        logger.debug("No install-state.json to check")
        return (0, 0)

    try:
        with open(install_state_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Could not read install-state.json: {e}")
        return (0, 0)

    modules = state.get("modules", {})
    if not modules:
        logger.debug("No modules in install-state.json")
        return (0, 0)

    modules_checked = 0
    modules_to_invalidate = []

    for module_path_str in modules:
        module_path = Path(module_path_str)
        if not module_path.exists():
            # Module directory no longer exists - mark for invalidation
            modules_to_invalidate.append(module_path_str)
            continue

        pyproject_path = module_path / "pyproject.toml"
        deps = _extract_dependencies_from_pyproject(pyproject_path)
        modules_checked += 1

        # Check if all dependencies are installed (by distribution name)
        missing_deps = []
        for dep in deps:
            if not _check_dependency_installed(dep):
                missing_deps.append(dep)

        if missing_deps:
            logger.debug(
                f"Module {module_path.name} has missing deps: {missing_deps}"
            )
            modules_to_invalidate.append(module_path_str)

    # Remove invalidated entries
    if modules_to_invalidate:
        for path_str in modules_to_invalidate:
            del state["modules"][path_str]
            module_name = Path(path_str).name
            logger.info(f"Invalidated install state for {module_name} (missing dependencies)")

        # Write back the modified state
        try:
            with open(install_state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            logger.warning(f"Failed to update install-state.json: {e}")

    return (modules_checked, len(modules_to_invalidate))


async def execute_self_update(umbrella_info: UmbrellaInfo) -> ExecutionResult:
    """Delegate to 'uv tool install --force'.

    Philosophy: uv is designed for this, use it.

    After successful update, surgically invalidates only modules whose
    dependencies are no longer available in the new Python environment.
    This avoids unnecessary reinstallation of modules whose dependencies
    are still satisfied.
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
            # Surgically invalidate only modules with missing dependencies.
            # The --force flag may recreate the Python environment, wiping
            # some module dependencies. Rather than clearing all install state,
            # we check which modules actually have missing deps and only
            # invalidate those entries.
            checked, invalidated = _invalidate_modules_with_missing_deps()
            if invalidated > 0:
                logger.info(
                    f"Invalidated {invalidated}/{checked} modules with missing dependencies"
                )

            return ExecutionResult(
                success=True,
                updated=["amplifier"],
                messages=["Amplifier updated successfully"],
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
            messages=[
                "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
            ],
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            failed=["amplifier"],
            errors={"amplifier": str(e)},
            messages=[f"Self-update error: {e}"],
        )


async def execute_updates(
    report: UpdateReport, umbrella_info: UmbrellaInfo | None = None
) -> ExecutionResult:
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

    # 2. Execute self-update if umbrella_info provided (already checked by caller)
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
