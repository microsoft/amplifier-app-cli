"""Fail-before / pass-after probe for recipes-mfi.

Drives the real `amplifier tool invoke` click command with a fake bundle layer
whose session cleanup never returns, and prints what the caller can actually
observe. Deliberately uses ONLY symbols that exist in the pre-fix tool.py, so
the same script runs unchanged against both revisions.

Run under an external `timeout`; a hang IS the defect.

    timeout 25 uv run python docs/lanes/cli-mfi-2v5/evidence/probe_invoke.py
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

tool_mod = importlib.import_module("amplifier_app_cli.commands.tool")


class WedgedSession:
    def __init__(self, result):
        self.cleanup_started = False
        tool_instance = MagicMock()
        tool_instance.execute = AsyncMock(return_value=result)
        self.coordinator = MagicMock()
        self.coordinator.get = MagicMock(return_value={"probe": tool_instance})

    async def initialize(self):
        return None

    async def cleanup(self):
        self.cleanup_started = True
        await asyncio.sleep(3600)


@contextlib.contextmanager
def bundle_layer(session):
    prepared = MagicMock()
    prepared.create_session = AsyncMock(return_value=session)
    prepared.resolver = MagicMock()
    settings = MagicMock()
    settings.get_merged_settings = MagicMock(return_value={})
    patchers = [
        patch(
            "amplifier_app_cli.runtime.config.resolve_config_async",
            AsyncMock(return_value=(MagicMock(), prepared)),
        ),
        patch("amplifier_app_cli.lib.settings.AppSettings", return_value=settings),
        patch("amplifier_app_cli.commands.tool.inject_user_providers", MagicMock()),
        patch("amplifier_app_cli.paths.create_foundation_resolver", MagicMock()),
        patch("amplifier_app_cli.lib.bundle_loader.AppModuleResolver", MagicMock()),
        patch("amplifier_app_cli.session_runner.register_session_spawning", MagicMock()),
        patch(
            "amplifier_app_cli.commands.tool._ensure_provider_configured", MagicMock()
        ),
    ]
    with contextlib.ExitStack() as stack:
        for p in patchers:
            stack.enter_context(p)
        yield


def main() -> int:
    session = WedgedSession({"status": "list", "recipes": ["a", "b"]})
    started = time.monotonic()
    # A 2s bound, which the fixed revision honours and the pre-fix revision
    # ignores entirely -- that is the point.
    with patch.dict("os.environ", {"AMPLIFIER_TOOL_CLEANUP_TIMEOUT": "2"}):
        with bundle_layer(session):
            res = CliRunner().invoke(
                tool_mod.tool, ["invoke", "probe", "-b", "any-bundle", "-o", "json"]
            )
    elapsed = time.monotonic() - started
    print(f"--- returned after {elapsed:.2f}s, exit_code={res.exit_code}")
    print("--- CLI OUTPUT START")
    print(res.output, end="")
    print("--- CLI OUTPUT END")
    return 0 if res.output.strip() else 2


if __name__ == "__main__":
    sys.exit(main())
