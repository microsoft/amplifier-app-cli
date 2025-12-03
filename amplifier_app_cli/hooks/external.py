"""External command hooks.

Provides hook handlers that execute external shell commands.
Supports passing event data as JSON to the command and parsing
JSON results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import HookConfig, HookResult, HookType

logger = logging.getLogger(__name__)


class ExternalCommandHook:
    """Hook handler that executes an external command.

    The command receives event data as JSON on stdin and can return
    a HookResult as JSON on stdout.

    Environment variables set for the command:
    - AMPLIFIER_EVENT: Event name
    - AMPLIFIER_TOOL: Tool name (if applicable)
    - AMPLIFIER_SESSION_ID: Current session ID
    - AMPLIFIER_HOOK_NAME: Name of this hook

    Exit codes:
    - 0: Continue (command succeeded)
    - 1: Deny (command explicitly denies)
    - 2+: Error (command failed)
    """

    def __init__(
        self,
        config: HookConfig,
        working_dir: Path | None = None,
    ):
        """Initialize external command hook.

        Args:
            config: Hook configuration
            working_dir: Working directory for command execution
        """
        if config.type != HookType.COMMAND:
            raise ValueError(f"Expected command hook, got {config.type}")

        self.config = config
        self.working_dir = working_dir or Path.cwd()
        self.command = config.command
        self.script = config.script
        self.timeout = config.timeout

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Execute the external command.

        Args:
            event: Event name
            data: Event data

        Returns:
            HookResult from command or error result
        """
        import time
        start_time = time.time()

        try:
            # Build command
            cmd = self._build_command()
            if not cmd:
                return HookResult.error("No command or script specified")

            # Build environment
            env = self._build_env(event, data)

            # Serialize data as JSON for stdin
            input_json = json.dumps({
                "event": event,
                "data": self._serialize_data(data),
            })

            # Execute command
            result = await self._execute(cmd, input_json, env)

            duration_ms = (time.time() - start_time) * 1000
            result.duration_ms = duration_ms

            return result

        except asyncio.TimeoutError:
            return HookResult.error(
                f"Command timed out after {self.timeout}s"
            )
        except Exception as e:
            logger.exception(f"External hook {self.config.name} failed")
            return HookResult.error(str(e))

    def _build_command(self) -> list[str] | None:
        """Build the command to execute."""
        if self.command:
            # Parse command string into list
            if sys.platform == "win32":
                # Windows command parsing
                return self.command.split()
            else:
                return shlex.split(self.command)

        if self.script:
            script_path = Path(self.script)
            if not script_path.is_absolute():
                script_path = self.working_dir / script_path

            if not script_path.exists():
                logger.warning(f"Script not found: {script_path}")
                return None

            # Determine how to run the script
            suffix = script_path.suffix.lower()
            if suffix == ".py":
                return [sys.executable, str(script_path)]
            elif suffix == ".sh":
                return ["bash", str(script_path)]
            elif suffix == ".ps1":
                return ["powershell", "-File", str(script_path)]
            else:
                # Assume executable
                return [str(script_path)]

        return None

    def _build_env(self, event: str, data: dict[str, Any]) -> dict[str, str]:
        """Build environment variables for the command."""
        env = os.environ.copy()
        env["AMPLIFIER_EVENT"] = event
        env["AMPLIFIER_HOOK_NAME"] = self.config.name

        if "tool" in data:
            env["AMPLIFIER_TOOL"] = str(data["tool"])
        if "session_id" in data:
            env["AMPLIFIER_SESSION_ID"] = str(data["session_id"])
        if "path" in data:
            env["AMPLIFIER_PATH"] = str(data["path"])
        if "command" in data:
            env["AMPLIFIER_COMMAND"] = str(data["command"])

        return env

    def _serialize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Serialize data for JSON, handling non-serializable types."""
        def serialize_value(v):
            if isinstance(v, dict):
                return {k: serialize_value(val) for k, val in v.items()}
            elif isinstance(v, list):
                return [serialize_value(item) for item in v]
            elif hasattr(v, "to_dict"):
                return v.to_dict()
            elif hasattr(v, "__dict__"):
                return str(v)
            else:
                try:
                    json.dumps(v)
                    return v
                except (TypeError, ValueError):
                    return str(v)

        return serialize_value(data)

    async def _execute(
        self,
        cmd: list[str],
        input_json: str,
        env: dict[str, str],
    ) -> HookResult:
        """Execute the command and parse result."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.working_dir),
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_json.encode()),
                timeout=self.timeout,
            )

            stdout_str = stdout.decode() if stdout else ""
            stderr_str = stderr.decode() if stderr else ""

            if stderr_str:
                logger.debug(f"Hook {self.config.name} stderr: {stderr_str}")

            # Parse exit code
            exit_code = proc.returncode or 0

            if exit_code == 0:
                # Try to parse JSON result
                return self._parse_output(stdout_str)
            elif exit_code == 1:
                # Explicit deny
                reason = stdout_str.strip() or stderr_str.strip() or "Denied by hook"
                return HookResult.deny(reason)
            else:
                # Error
                error_msg = stderr_str.strip() or stdout_str.strip() or f"Exit code {exit_code}"
                return HookResult.error(error_msg)

        except FileNotFoundError as e:
            return HookResult.error(f"Command not found: {cmd[0]}")

    def _parse_output(self, output: str) -> HookResult:
        """Parse command output as HookResult.

        Output can be:
        - Empty: Continue
        - JSON object with action, reason, etc.
        - Plain text: Treated as output
        """
        output = output.strip()

        if not output:
            return HookResult.continue_()

        # Try to parse as JSON
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                return HookResult(
                    action=data.get("action", "continue"),
                    reason=data.get("reason"),
                    modified_data=data.get("modified_data"),
                    output=data.get("output"),
                    error=data.get("error"),
                )
        except json.JSONDecodeError:
            pass

        # Treat as plain text output
        return HookResult(action="continue", output=output)


def create_external_hook(
    config: HookConfig,
    working_dir: Path | None = None,
) -> ExternalCommandHook:
    """Factory function to create an external hook.

    Args:
        config: Hook configuration
        working_dir: Working directory for execution

    Returns:
        Configured ExternalCommandHook
    """
    return ExternalCommandHook(config, working_dir)
