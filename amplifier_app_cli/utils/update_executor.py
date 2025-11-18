"""Execute updates by delegating to external tools.

Philosophy: Orchestrate, don't reimplement. Delegate to uv and existing commands.
"""

import logging
import subprocess
from dataclasses import dataclass
from dataclasses import field

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


async def execute_module_refresh() -> ExecutionResult:
    """Delegate to 'amplifier module refresh'.

    Philosophy: Don't reimplement - it already exists and works.
    """
    try:
        result = subprocess.run(
            ["amplifier", "module", "refresh"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                updated=["cached modules"],
                messages=["Module cache refreshed successfully"],
            )
        error_msg = result.stderr.strip() or "Unknown error"
        return ExecutionResult(
            success=False,
            failed=["cached modules"],
            errors={"cached modules": error_msg},
            messages=[f"Module refresh failed: {error_msg}"],
        )

    except subprocess.TimeoutExpired:
        return ExecutionResult(
            success=False,
            failed=["cached modules"],
            errors={"cached modules": "Timeout after 60 seconds"},
            messages=["Module refresh timed out"],
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            failed=["cached modules"],
            errors={"cached modules": str(e)},
            messages=[f"Module refresh error: {e}"],
        )


async def execute_collection_refresh() -> ExecutionResult:
    """Delegate to 'amplifier collection refresh'.

    Philosophy: Orchestrate, don't reimplement. Collection refresh already works.
    """
    try:
        result = subprocess.run(
            ["amplifier", "collection", "refresh"],
            capture_output=True,
            text=True,
            timeout=120,  # Collections can be large, use longer timeout
        )

        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                updated=["collections"],
                messages=["Collections refreshed successfully"],
            )
        error_msg = result.stderr.strip() or "Unknown error"
        return ExecutionResult(
            success=False,
            failed=["collections"],
            errors={"collections": error_msg},
            messages=[f"Collection refresh failed: {error_msg}"],
        )

    except subprocess.TimeoutExpired:
        return ExecutionResult(
            success=False,
            failed=["collections"],
            errors={"collections": "Timeout after 120 seconds"},
            messages=["Collection refresh timed out"],
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            failed=["collections"],
            errors={"collections": str(e)},
            messages=[f"Collection refresh error: {e}"],
        )


async def check_umbrella_dependencies_for_updates(umbrella_info: UmbrellaInfo) -> bool:
    """Check if any dependencies declared in umbrella's pyproject.toml have updates.

    Args:
        umbrella_info: Discovered umbrella source info

    Returns:
        True if any dependency has updates, False otherwise
    """
    import importlib.metadata
    import json

    from .source_status import _get_github_commit_sha
    from .umbrella_discovery import fetch_umbrella_dependencies

    try:
        # Fetch umbrella's dependencies
        umbrella_deps = await fetch_umbrella_dependencies(umbrella_info)

        logger.debug(f"Checking {len(umbrella_deps)} umbrella dependencies for updates")

        # Check each dependency
        for dep_name, dep_info in umbrella_deps.items():
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

        logger.debug("All umbrella dependencies up to date")
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


async def execute_updates(report: UpdateReport) -> ExecutionResult:
    """Orchestrate all updates based on report.

    Philosophy: Sequential execution (modules first, then self) for safety.
    """
    all_updated = []
    all_failed = []
    all_messages = []
    all_errors = {}
    overall_success = True

    # 1. Execute module refresh if needed
    if report.cached_git_sources:
        logger.info("Refreshing cached modules...")
        result = await execute_module_refresh()

        all_updated.extend(result.updated)
        all_failed.extend(result.failed)
        all_messages.extend(result.messages)
        all_errors.update(result.errors)

        if not result.success:
            overall_success = False

    # 2. Execute collection refresh if needed
    if report.collection_sources:
        logger.info("Refreshing collections...")
        result = await execute_collection_refresh()

        all_updated.extend(result.updated)
        all_failed.extend(result.failed)
        all_messages.extend(result.messages)
        all_errors.update(result.errors)

        if not result.success:
            overall_success = False

    # 3. Execute self-update if needed (check umbrella dependencies for updates)
    from .umbrella_discovery import discover_umbrella_source

    umbrella_info = discover_umbrella_source()

    # Check if any umbrella dependencies (amplifier-app-cli, amplifier-core, etc.) have updates
    if umbrella_info:
        has_dependency_updates = await check_umbrella_dependencies_for_updates(umbrella_info)

        if has_dependency_updates:
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
