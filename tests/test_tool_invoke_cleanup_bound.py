"""`amplifier tool invoke` must emit the run's outcome before cleanup can eat it.

The defect (recipes-mfi): `_invoke_tool_from_bundle_async` returned the tool's
result from a `try` whose `finally` awaited an unbounded `session.cleanup()`.
`return result` does not reach the caller until that `finally` completes, and
the caller is the only thing that prints. So a module whose cleanup blocked did
not merely delay teardown -- it destroyed the run's outcome: the work
succeeded, the outputs were on disk, and no caller could ever learn it
(measured: a completed recipe run, then 28 minutes of silence, exiting
instantly on SIGTERM).

These tests pin both halves of the fix:

* the outcome is emitted BEFORE cleanup is entered (so a wedged teardown costs
  a delayed exit, never the answer), and
* cleanup is bounded, cancelled, and -- if it ignores cancellation too -- the
  process still exits, saying so.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

# `from amplifier_app_cli.commands import tool` yields the click *Group* --
# commands/__init__.py rebinds the name. Reach the module explicitly.
tool_mod = importlib.import_module("amplifier_app_cli.commands.tool")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _NeverReturningCleanupSession:
    """A session whose `cleanup()` never returns, honouring cancellation."""

    def __init__(self, result: Any = None) -> None:
        self.cleanup_started = asyncio.Event()
        self.cleanup_finished = False
        self._result = result
        tool_instance = MagicMock()
        tool_instance.execute = AsyncMock(return_value=result)
        self.coordinator = MagicMock()
        self.coordinator.get = MagicMock(return_value={"probe": tool_instance})
        self.coordinator.session_id = "test-session"

    async def initialize(self) -> None:  # pragma: no cover - trivial
        return None

    async def cleanup(self) -> None:
        self.cleanup_started.set()
        await asyncio.sleep(3600)
        self.cleanup_finished = True  # pragma: no cover - unreachable


class _UncancellableCleanupSession(_NeverReturningCleanupSession):
    """Worse: cleanup swallows cancellation and keeps blocking.

    `release` exists only so the *test* can let the task finish once its
    assertions are done -- without it, `asyncio.run()`'s own shutdown would
    hang cancel-and-gathering this task, which is precisely the reason
    `_cleanup_session_bounded` reaches for `os._exit` in production.
    """

    def __init__(self, result: Any = None) -> None:
        super().__init__(result)
        self.release = False

    async def cleanup(self) -> None:
        self.cleanup_started.set()
        while not self.release:
            try:
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                continue


def _patch_bundle_layer(session: Any):
    """Patch everything between `_invoke_tool_from_bundle_async` and a session.

    Returns a list of started patchers the caller is responsible for stopping;
    use as a context manager stack via `contextlib.ExitStack` in the tests.
    """
    prepared = MagicMock()
    prepared.create_session = AsyncMock(return_value=session)
    prepared.resolver = MagicMock()

    settings = MagicMock()
    settings.get_merged_settings = MagicMock(return_value={})

    return [
        patch(
            "amplifier_app_cli.runtime.config.resolve_config_async",
            AsyncMock(return_value=(MagicMock(), prepared)),
        ),
        patch("amplifier_app_cli.lib.settings.AppSettings", return_value=settings),
        patch("amplifier_app_cli.commands.tool.inject_user_providers", MagicMock()),
        patch("amplifier_app_cli.paths.create_foundation_resolver", MagicMock()),
        patch("amplifier_app_cli.lib.bundle_loader.AppModuleResolver", MagicMock()),
        patch("amplifier_app_cli.session_runner.register_session_spawning", MagicMock()),
    ]


@contextlib.contextmanager
def _bundle_layer(session: Any):
    with contextlib.ExitStack() as stack:
        for patcher in _patch_bundle_layer(session):
            stack.enter_context(patcher)
        yield


# ---------------------------------------------------------------------------
# The bound itself
# ---------------------------------------------------------------------------


def test_bounded_cleanup_abandons_a_never_returning_cleanup(caplog):
    """A cleanup that never returns is abandoned at the deadline, by name."""
    session = _NeverReturningCleanupSession()

    async def run() -> str:
        return await tool_mod._cleanup_session_bounded(session, timeout=0.2, grace=0.2)

    with caplog.at_level(logging.WARNING):
        started = time.monotonic()
        status = asyncio.run(run())
        elapsed = time.monotonic() - started

    assert status == tool_mod.CLEANUP_ABANDONED
    assert session.cleanup_started.is_set()
    assert not session.cleanup_finished
    assert elapsed < 5.0, f"bound did not hold: {elapsed:.1f}s"
    assert any(
        "ABANDONED" in record.getMessage() for record in caplog.records
    ), "cleanup abandonment must be logged at WARNING, by name"


def test_bounded_cleanup_returns_promptly_when_cleanup_is_well_behaved():
    """The bound must not add latency to a cleanup that simply works."""
    session = MagicMock()
    session.cleanup = AsyncMock(return_value=None)

    async def run() -> str:
        return await tool_mod._cleanup_session_bounded(session, timeout=30.0)

    started = time.monotonic()
    status = asyncio.run(run())
    elapsed = time.monotonic() - started

    assert status == tool_mod.CLEANUP_COMPLETED
    assert elapsed < 1.0
    session.cleanup.assert_awaited_once()


def test_bounded_cleanup_reports_a_raising_cleanup_without_raising(caplog):
    """A cleanup that fails is reported, never promoted into the run's result."""
    session = MagicMock()
    session.cleanup = AsyncMock(side_effect=RuntimeError("teardown blew up"))

    async def run() -> str:
        return await tool_mod._cleanup_session_bounded(session, timeout=5.0)

    with caplog.at_level(logging.WARNING):
        status = asyncio.run(run())

    assert status == tool_mod.CLEANUP_COMPLETED
    assert any("teardown blew up" in r.getMessage() for r in caplog.records)


def test_bounded_cleanup_hard_exits_when_cancellation_is_ignored(caplog):
    """Last resort: a cleanup that ignores cancellation still ends the process."""
    session = _UncancellableCleanupSession()
    hard_exits: list[int] = []

    async def run() -> str:
        status = await tool_mod._cleanup_session_bounded(
            session,
            timeout=0.2,
            grace=0.2,
            exit_code=0,
            hard_exit=hard_exits.append,
        )
        # Production would already be gone via os._exit here. In-process, let
        # the wedged task finish so asyncio.run()'s shutdown has nothing to
        # cancel-and-gather (which would hang -- see the class docstring).
        session.release = True
        await asyncio.sleep(0.1)
        return status

    with caplog.at_level(logging.WARNING):
        status = asyncio.run(run())

    assert status == tool_mod.CLEANUP_UNCANCELLABLE
    assert hard_exits == [0]
    assert any("os._exit" in r.getMessage() for r in caplog.records), (
        "the hard exit must say in the log that it happened"
    )


# ---------------------------------------------------------------------------
# Emit-before-cleanup
# ---------------------------------------------------------------------------


def test_result_is_emitted_before_cleanup_is_entered():
    """The outcome reaches the caller before cleanup gets a chance to block."""
    session = _NeverReturningCleanupSession(result={"ok": True, "n": 3})
    emitted: list[tuple[bool, Any]] = []

    def emit(ok: bool, payload: Any) -> None:
        # Cleanup must not even have started when the outcome is handed over.
        assert not session.cleanup_started.is_set()
        emitted.append((ok, payload))

    async def run() -> Any:
        return await tool_mod._invoke_tool_from_bundle_async(
            "any-bundle", "probe", {}, emit=emit
        )

    with _bundle_layer(session):
        with patch.dict("os.environ", {tool_mod.CLEANUP_TIMEOUT_ENV: "0.2"}):
            started = time.monotonic()
            result = asyncio.run(run())
            elapsed = time.monotonic() - started

    assert emitted == [(True, {"ok": True, "n": 3})]
    assert result == {"ok": True, "n": 3}
    assert session.cleanup_started.is_set(), "cleanup should still have been attempted"
    assert elapsed < 5.0, f"bound did not hold: {elapsed:.1f}s"


def test_failure_is_emitted_before_cleanup_is_entered():
    """The same guarantee for the error path -- an error is an outcome too."""
    session = _NeverReturningCleanupSession()
    session.coordinator.get = MagicMock(return_value={})  # no tools mounted
    emitted: list[tuple[bool, Any]] = []

    async def run() -> Any:
        return await tool_mod._invoke_tool_from_bundle_async(
            "any-bundle", "probe", {}, emit=lambda ok, p: emitted.append((ok, p))
        )

    with _bundle_layer(session):
        with patch.dict("os.environ", {tool_mod.CLEANUP_TIMEOUT_ENV: "0.2"}):
            with pytest.raises(ValueError):
                asyncio.run(run())

    assert len(emitted) == 1
    ok, payload = emitted[0]
    assert ok is False
    assert isinstance(payload, ValueError)


def test_cli_invoke_prints_json_result_despite_a_wedged_cleanup():
    """Acceptance criterion, at the CLI surface: the result JSON is printed."""
    session = _NeverReturningCleanupSession(result={"status": "list", "items": []})
    runner = CliRunner()

    with _bundle_layer(session):
        with patch.dict("os.environ", {tool_mod.CLEANUP_TIMEOUT_ENV: "0.2"}):
            with patch(
                "amplifier_app_cli.commands.tool._ensure_provider_configured",
                MagicMock(),
            ):
                started = time.monotonic()
                result = runner.invoke(
                    tool_mod.tool,
                    ["invoke", "probe", "-b", "any-bundle", "-o", "json"],
                )
                elapsed = time.monotonic() - started

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["tool"] == "probe"
    assert payload["result"] == {"status": "list", "items": []}
    assert elapsed < 10.0, f"bound did not hold: {elapsed:.1f}s"


def test_cli_invoke_prints_text_result_despite_a_wedged_cleanup():
    """Same guarantee for the default (text) output format."""
    session = _NeverReturningCleanupSession(result={"answer": "42"})
    runner = CliRunner()

    with _bundle_layer(session):
        with patch.dict("os.environ", {tool_mod.CLEANUP_TIMEOUT_ENV: "0.2"}):
            with patch(
                "amplifier_app_cli.commands.tool._ensure_provider_configured",
                MagicMock(),
            ):
                result = runner.invoke(
                    tool_mod.tool, ["invoke", "probe", "-b", "any-bundle"]
                )

    assert result.exit_code == 0, result.output
    assert "Result from probe" in result.output
    assert "42" in result.output


def test_cli_invoke_does_not_print_the_result_twice():
    """`emitted` must suppress the caller's own (legacy) print."""
    session = MagicMock()
    session.initialize = AsyncMock(return_value=None)
    session.cleanup = AsyncMock(return_value=None)
    tool_instance = MagicMock()
    tool_instance.execute = AsyncMock(return_value={"once": True})
    session.coordinator = MagicMock()
    session.coordinator.get = MagicMock(return_value={"probe": tool_instance})
    runner = CliRunner()

    with _bundle_layer(session):
        with patch(
            "amplifier_app_cli.commands.tool._ensure_provider_configured", MagicMock()
        ):
            result = runner.invoke(
                tool_mod.tool,
                ["invoke", "probe", "-b", "any-bundle", "-o", "json"],
            )

    assert result.exit_code == 0, result.output
    assert result.output.count('"status": "success"') == 1


# ---------------------------------------------------------------------------
# Timeout resolution
# ---------------------------------------------------------------------------


def test_cleanup_timeout_defaults_to_thirty_seconds():
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop(tool_mod.CLEANUP_TIMEOUT_ENV, None)
        assert (
            tool_mod._resolve_cleanup_timeout(None)
            == tool_mod.DEFAULT_CLEANUP_TIMEOUT_SECONDS
        )


def test_cleanup_timeout_reads_env_then_settings():
    settings = MagicMock()
    settings.get_merged_settings = MagicMock(
        return_value={"tool": {"cleanup_timeout_seconds": 7}}
    )

    with patch.dict("os.environ", {tool_mod.CLEANUP_TIMEOUT_ENV: "1.5"}):
        assert tool_mod._resolve_cleanup_timeout(settings) == 1.5

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop(tool_mod.CLEANUP_TIMEOUT_ENV, None)
        assert tool_mod._resolve_cleanup_timeout(settings) == 7.0


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-1"])
def test_cleanup_timeout_refuses_a_useless_bound(bad, caplog):
    """`0` must not silently mean "wait forever" -- that is the defect."""
    with patch.dict("os.environ", {tool_mod.CLEANUP_TIMEOUT_ENV: bad}):
        with caplog.at_level(logging.WARNING):
            assert (
                tool_mod._resolve_cleanup_timeout(None)
                == tool_mod.DEFAULT_CLEANUP_TIMEOUT_SECONDS
            )
    assert caplog.records, "a refused bound must be reported"


# ---------------------------------------------------------------------------
# The process really does exit (a real child process, not an in-process fake)
# ---------------------------------------------------------------------------


_CHILD = textwrap.dedent(
    """
    import asyncio, importlib

    tool_mod = importlib.import_module("amplifier_app_cli.commands.tool")

    class Wedged:
        async def cleanup(self):
            # Ignores cancellation, exactly like the teardown that hung for 28
            # minutes with sockets in CLOSE-WAIT.
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    continue

    async def main():
        print("RESULT-EMITTED", flush=True)
        await tool_mod._cleanup_session_bounded(
            Wedged(), timeout=0.3, grace=0.3, exit_code=0
        )
        print("SHOULD-NOT-REACH", flush=True)

    asyncio.run(main())
    """
)


def test_child_process_exits_even_when_cleanup_ignores_cancellation():
    """The strongest form of the claim: a real process, and it really exits."""
    repo_root = Path(tool_mod.__file__).resolve().parents[2]
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(repo_root),
    )
    elapsed = time.monotonic() - started

    assert "RESULT-EMITTED" in proc.stdout, proc.stderr
    assert "SHOULD-NOT-REACH" not in proc.stdout
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert elapsed < 30.0, f"the child did not exit promptly: {elapsed:.1f}s"
