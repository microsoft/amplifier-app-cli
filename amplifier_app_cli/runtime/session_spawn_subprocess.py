"""Subprocess transport for prepared child-session requests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from amplifier_app_cli.runtime.session_spawn_models import PreparedSpawn
from amplifier_app_cli.runtime.session_spawn_models import SessionLifecycleServices


async def run_subprocess_spawn(
    prepared: PreparedSpawn,
    services: SessionLifecycleServices,
) -> dict:
    """Run a prepared child session through Foundation's isolated transport."""
    from .subprocess_adapter import run_session_in_subprocess

    request = prepared.request
    parent = request.parent_session
    project_path = str(
        parent.coordinator.get_capability("session.working_dir") or Path.cwd()
    )
    child_config = {
        key: value
        for key, value in prepared.merged_config.items()
        if key != "spawn_mode"
    }
    bundle_context = services.extract_bundle_context(parent)
    parent_hooks = parent.coordinator.get("hooks")
    if parent_hooks:
        await parent_hooks.emit(
            "session:fork",
            {
                "child_session_id": prepared.sub_session_id,
                "parent_session_id": parent.session_id,
                "agent_name": request.agent_name,
                "spawn_mode": "subprocess",
            },
        )

    async def emit_terminal(status: str, success: bool, error: str = "") -> None:
        if parent_hooks:
            await parent_hooks.emit(
                "session:end",
                {
                    "session_id": prepared.sub_session_id,
                    "parent_session_id": parent.session_id,
                    "agent_name": request.agent_name,
                    "spawn_mode": "subprocess",
                    "status": status,
                    "success": success,
                    "error": error,
                },
            )

    try:
        result = await run_session_in_subprocess(
            config=child_config,
            prompt=request.instruction,
            parent_id=parent.session_id,
            project_path=project_path,
            session_id=prepared.sub_session_id,
            module_paths=(
                bundle_context.get("module_paths") if bundle_context else None
            ),
            bundle_package_paths=(
                bundle_context.get("bundle_package_paths") if bundle_context else None
            ),
            sys_paths=[
                path for path in sys.path if path not in services.default_sys_paths
            ],
            mention_mappings=(
                bundle_context.get("mention_mappings") if bundle_context else None
            ),
            bypass_permissions=services.session_bypass_permissions(parent),
        )
    except asyncio.CancelledError:
        await emit_terminal("cancelled", False)
        raise
    except Exception as error:
        await emit_terminal("failed", False, str(error))
        raise

    response: dict | None = None
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "output" in parsed:
            response = {
                "output": parsed["output"],
                "session_id": parsed.get("session_id", prepared.sub_session_id),
                "status": parsed.get("status", "success"),
                "turn_count": parsed.get("turn_count", 1),
                "metadata": parsed.get("metadata", {}),
            }
    except (ValueError, TypeError):
        pass
    if response is None:
        response = {
            "output": result,
            "session_id": prepared.sub_session_id,
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }

    status = str(response["status"])
    success = status.lower() not in {"failed", "error", "cancelled", "canceled"}
    await emit_terminal(status, success)
    return response


__all__ = ["run_subprocess_spawn"]
