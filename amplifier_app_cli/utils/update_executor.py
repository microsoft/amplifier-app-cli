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

    # 2. Execute self-update if needed (check for umbrella with updates)
    from .umbrella_discovery import discover_umbrella_source

    umbrella_info = discover_umbrella_source()

    # Check if umbrella itself has updates
    # (For simplicity, we check if ANY sources have updates, we suggest self-update)
    # More precise: compare umbrella SHA, but that requires fetching umbrella deps
    if umbrella_info and report.has_updates:
        logger.info("Updating Amplifier...")
        result = await execute_self_update(umbrella_info)

        all_updated.extend(result.updated)
        all_failed.extend(result.failed)
        all_messages.extend(result.messages)
        all_errors.update(result.errors)

        if not result.success:
            overall_success = False

    # 3. Compile final result
    return ExecutionResult(
        success=overall_success and len(all_failed) == 0,
        updated=all_updated,
        failed=all_failed,
        messages=all_messages,
        errors=all_errors,
    )
