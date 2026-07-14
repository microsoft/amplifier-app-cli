"""Cancellation-safe adapter for Foundation's isolated session runner."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import stat
import sys
import tempfile
from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, TypeVar, cast

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_FOUNDATION_MODULE = "amplifier_foundation.subprocess_runner"
_CHILD_MODULE = "amplifier_app_cli.runtime.subprocess_adapter"
_CLI_POLICY_KEY = "_amplifier_app_cli"
_REQUIRED_API = (
    "RESULT_START_MARKER",
    "RESULT_END_MARKER",
    "AmplifierSession",
    "_build_child_env",
    "_extract_framed_result",
    "_get_semaphore",
    "_run_child_session",
    "_sanitize_error",
    "_validate_project_path",
    "serialize_subprocess_config",
)


class _FoundationSession(Protocol):
    def initialize(self) -> Awaitable[object]: ...


class _FoundationSessionFactory(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> _FoundationSession: ...


class _FoundationRuntime(Protocol):
    RESULT_START_MARKER: str
    RESULT_END_MARKER: str
    AmplifierSession: _FoundationSessionFactory

    def _build_child_env(self) -> dict[str, str]: ...

    def _extract_framed_result(self, output: str) -> str: ...

    def _get_semaphore(self) -> AbstractAsyncContextManager[object]: ...

    def _run_child_session(self, config_path: str) -> Awaitable[str]: ...

    def _sanitize_error(self, error: str) -> str: ...

    def _validate_project_path(self, project_path: str) -> None: ...

    def serialize_subprocess_config(self, **kwargs: object) -> str: ...


def _foundation() -> _FoundationRuntime:
    module = importlib.import_module(_FOUNDATION_MODULE)
    missing = [name for name in _REQUIRED_API if not hasattr(module, name)]
    if missing:
        raise RuntimeError(
            "Installed amplifier-foundation lacks subprocess runner APIs: "
            + ", ".join(missing)
        )
    return cast(_FoundationRuntime, module)


async def _await_cleanup(awaitable: Awaitable[_T]) -> _T:
    """Finish process cleanup even if the parent task is cancelled again."""
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _stop_process(
    process: asyncio.subprocess.Process,
    communicate_task: asyncio.Task[tuple[bytes, bytes]],
    *,
    kill_first: bool = False,
    grace_seconds: float = 5.0,
) -> None:
    """Terminate and reap a child, escalating to kill after a short grace period."""

    async def stop() -> None:
        if process.returncode is None:
            try:
                process.kill() if kill_first else process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(asyncio.shield(communicate_task), grace_seconds)
            return
        except (asyncio.TimeoutError, Exception):
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        try:
            await asyncio.wait_for(asyncio.shield(communicate_task), grace_seconds)
        except (asyncio.TimeoutError, Exception):
            if process.returncode is None:
                logger.warning("Subprocess %s could not be reaped", process.pid)

    await _await_cleanup(stop())


async def run_session_in_subprocess(
    config: dict[str, Any],
    prompt: str,
    parent_id: str,
    project_path: str,
    session_id: str | None = None,
    timeout: int = 1800,
    module_paths: dict[str, str] | None = None,
    bundle_package_paths: list[str] | None = None,
    sys_paths: list[str] | None = None,
    mention_mappings: dict[str, str] | None = None,
    bypass_permissions: bool = False,
) -> str:
    """Run a Foundation child and guarantee it is reaped before cancellation."""
    if not isinstance(bypass_permissions, bool):
        raise TypeError("bypass_permissions must be a bool")
    foundation = _foundation()
    foundation._validate_project_path(project_path)
    serialized = foundation.serialize_subprocess_config(
        config=config,
        prompt=prompt,
        parent_id=parent_id,
        project_path=project_path,
        session_id=session_id,
        module_paths=module_paths,
        bundle_package_paths=bundle_package_paths,
        sys_paths=sys_paths,
        mention_mappings=mention_mappings,
    )
    payload = json.loads(serialized)
    if not isinstance(payload, dict):
        raise ValueError("Foundation subprocess payload must be a JSON object")
    payload[_CLI_POLICY_KEY] = {"bypass_permissions": bypass_permissions}
    serialized = json.dumps(payload)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="amp_subprocess_", delete=False
        ) as config_file:
            tmp_path = config_file.name
            config_file.write(serialized)
        if stat.S_IMODE(os.stat(tmp_path).st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            os.chmod(tmp_path, 0o600)

        async with foundation._get_semaphore():
            spawn_task = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    _CHILD_MODULE,
                    tmp_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=project_path,
                    env=foundation._build_child_env(),
                )
            )
            try:
                process = await asyncio.shield(spawn_task)
            except asyncio.CancelledError:
                process = await _await_cleanup(spawn_task)
                communicate_task = asyncio.create_task(process.communicate())
                await _stop_process(process, communicate_task)
                raise

            communicate_task = asyncio.create_task(process.communicate())
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communicate_task), timeout
                )
            except asyncio.TimeoutError:
                await _stop_process(process, communicate_task, kill_first=True)
                raise TimeoutError(f"Subprocess session timed out after {timeout}s")
            except asyncio.CancelledError:
                await _stop_process(process, communicate_task)
                raise

            raw_stdout = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            logger.debug("Subprocess stderr: %s", stderr_text)
            if process.returncode != 0:
                if foundation.RESULT_START_MARKER in raw_stdout:
                    return foundation._extract_framed_result(raw_stdout)
                sanitized = foundation._sanitize_error(stderr_text)
                raise RuntimeError(
                    f"Subprocess session failed (exit code {process.returncode}): "
                    f"{sanitized}"
                )
            return foundation._extract_framed_result(raw_stdout)
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("Failed to clean up temp file: %s", tmp_path)


async def _run_patched_foundation_child(
    config_path: str, foundation: _FoundationRuntime | None = None
) -> str:
    """Run Foundation's child entry point with app-owned runtime policy applied."""
    runtime = foundation if foundation is not None else _foundation()
    child_runner = runtime._run_child_session
    original_session = runtime.AmplifierSession

    from amplifier_app_cli.runtime.amplifier_compat import (
        install_hook_serialization_compatibility,
    )
    from amplifier_app_cli.ui import CLIApprovalSystem
    from amplifier_app_cli.ui import CLIDisplaySystem

    install_hook_serialization_compatibility()

    with open(config_path, encoding="utf-8") as config_file:
        payload = json.load(config_file)
    policy = payload.get(_CLI_POLICY_KEY, {}) if isinstance(payload, dict) else {}
    bypass_permissions = bool(
        isinstance(policy, dict) and policy.get("bypass_permissions") is True
    )

    class JsonSafeSessionProxy:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault(
                "approval_system",
                CLIApprovalSystem(bypass_permissions=bypass_permissions),
            )
            kwargs.setdefault("display_system", CLIDisplaySystem())
            self._session = original_session(*args, **kwargs)

        async def initialize(self) -> Any:
            return await self._session.initialize()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._session, name)

    runtime.AmplifierSession = JsonSafeSessionProxy
    try:
        return await child_runner(config_path)
    finally:
        runtime.AmplifierSession = original_session


def _child_main() -> int:
    foundation = _foundation()
    if len(sys.argv) != 2:
        print(f"Usage: python -m {_CHILD_MODULE} <config_path>", file=sys.stderr)
        return 1

    try:
        output = asyncio.run(_run_patched_foundation_child(sys.argv[1], foundation))
        payload = {
            "output": output,
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }
        exit_code = 0
    except Exception as error:
        payload = {
            "output": "",
            "status": "error",
            "error": str(error),
            "turn_count": 0,
            "metadata": {},
        }
        print(f"Subprocess session error: {error}", file=sys.stderr)
        exit_code = 1

    print(foundation.RESULT_START_MARKER)
    print(json.dumps(payload))
    print(foundation.RESULT_END_MARKER)
    return exit_code


__all__ = ["run_session_in_subprocess"]


if __name__ == "__main__":
    raise SystemExit(_child_main())
