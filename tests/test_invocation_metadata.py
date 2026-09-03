"""Tests for invocation-provenance metadata on the root session's mount plan.

app-cli records HOW a session was invoked (resolved mode, tty-ness of stdin and
stdout, launching component, cross-process launcher session id) under
``mount_plan["session"]["metadata"]["invocation"]``.  The kernel's CP-SM
passthrough channel carries it to ``session:start``, where it persists as
``data.metadata.invocation`` in events.jsonl.

What these tests pin down:
  * the exact field set -- notably that argv is NOT among it
  * the *resolved* mode, per entry point (chat vs single)
  * both tty flags, mocked both ways
  * pre-existing metadata is never clobbered
  * an unset launcher env var yields null, never a fabricated id
  * the mount plan is mutated BEFORE create_session() reads it
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.session_runner import (
    INVOCATION_SCHEMA,
    LAUNCHER_SESSION_ID_ENV,
    SessionConfig,
    _build_invocation_metadata,
    _inject_invocation_metadata,
)

_MODULE = "amplifier_app_cli.session_runner"
_MAIN = "amplifier_app_cli.main"

EXPECTED_FIELDS = {
    "schema",
    "mode",
    "stdin_isatty",
    "stdout_isatty",
    "launched_by",
    "launched_by_session_id",
}


def _prepared_bundle(mount_plan=None):
    """A PreparedBundle stand-in carrying a real (mutable) mount plan dict."""
    bundle = MagicMock()
    bundle.mount_plan = mount_plan if mount_plan is not None else {}
    return bundle


def _invocation(bundle) -> dict:
    return bundle.mount_plan["session"]["metadata"]["invocation"]


# ---------------------------------------------------------------------------
# Field set and shape
# ---------------------------------------------------------------------------


class TestInvocationShape:
    def test_field_set_is_exactly_the_designed_one(self, monkeypatch):
        monkeypatch.delenv(LAUNCHER_SESSION_ID_ENV, raising=False)
        assert set(_build_invocation_metadata("single")) == EXPECTED_FIELDS

    def test_no_argv_or_command_line_is_recorded(self, monkeypatch):
        """argv is a live secrets surface -- it must never reach events.jsonl.

        `amplifier run "$(cat prod-token.txt)"` and `--api-key ...` both land in
        argv, and events.jsonl is long-lived, greppable, and exported.  This
        test exists so a future 'just add argv, it's useful' change has to
        delete an explicit assertion rather than quietly widen the record.
        """
        invocation = _build_invocation_metadata("single")
        forbidden = ("argv", "cmd", "command", "args", "prompt", "env")
        for key in invocation:
            assert not any(bad in key.lower() for bad in forbidden), (
                f"Field {key!r} looks like a raw command-line/environment dump. "
                f"Invocation provenance is deliberately limited to "
                f"{sorted(EXPECTED_FIELDS)}."
            )

    def test_schema_is_versioned(self, monkeypatch):
        monkeypatch.delenv(LAUNCHER_SESSION_ID_ENV, raising=False)
        assert _build_invocation_metadata("single")["schema"] == INVOCATION_SCHEMA
        assert isinstance(INVOCATION_SCHEMA, int)

    def test_launched_by_is_cli_from_this_construction_site(self):
        assert _build_invocation_metadata("single")["launched_by"] == "cli"


# ---------------------------------------------------------------------------
# Resolved mode
# ---------------------------------------------------------------------------


class TestResolvedMode:
    @pytest.mark.parametrize("mode", ["chat", "single"])
    def test_mode_recorded_verbatim(self, mode):
        bundle = _prepared_bundle()
        _inject_invocation_metadata(bundle, mode=mode)
        assert _invocation(bundle)["mode"] == mode

    def test_absent_mode_is_unknown_not_guessed(self):
        """A wrong mode is worse than an absent one (absent => UNKNOWN)."""
        bundle = _prepared_bundle()
        _inject_invocation_metadata(bundle, mode=None)
        assert _invocation(bundle)["mode"] == "unknown"


# ---------------------------------------------------------------------------
# tty matrix
# ---------------------------------------------------------------------------


class TestTtyFlags:
    @pytest.mark.parametrize(
        ("stdin_tty", "stdout_tty"),
        [(True, True), (True, False), (False, True), (False, False)],
    )
    def test_isatty_matrix(self, stdin_tty, stdout_tty):
        fds = {0: stdin_tty, 1: stdout_tty}
        with patch(f"{_MODULE}.os.isatty", side_effect=lambda fd: fds[fd]):
            invocation = _build_invocation_metadata("single")
        assert invocation["stdin_isatty"] is stdin_tty
        assert invocation["stdout_isatty"] is stdout_tty

    def test_closed_fd_reads_as_not_a_tty_instead_of_raising(self):
        """A daemonised harness with a closed stdin must not fail to start."""
        with patch(f"{_MODULE}.os.isatty", side_effect=OSError("Bad file descriptor")):
            invocation = _build_invocation_metadata("single")
        assert invocation["stdin_isatty"] is False
        assert invocation["stdout_isatty"] is False


# ---------------------------------------------------------------------------
# Cross-process launcher id
# ---------------------------------------------------------------------------


class TestLauncherSessionId:
    def test_unset_env_var_yields_null_never_a_fabricated_id(self, monkeypatch):
        monkeypatch.delenv(LAUNCHER_SESSION_ID_ENV, raising=False)
        assert _build_invocation_metadata("single")["launched_by_session_id"] is None

    def test_empty_env_var_is_also_null(self, monkeypatch):
        monkeypatch.setenv(LAUNCHER_SESSION_ID_ENV, "")
        assert _build_invocation_metadata("single")["launched_by_session_id"] is None

    def test_set_env_var_is_recorded(self, monkeypatch):
        monkeypatch.setenv(LAUNCHER_SESSION_ID_ENV, "launcher-session-abc")
        assert (
            _build_invocation_metadata("single")["launched_by_session_id"]
            == "launcher-session-abc"
        )

    def test_env_var_uses_the_amplifier_prefix(self):
        """foundation's subprocess env allowlist forwards AMPLIFIER_* for free."""
        assert LAUNCHER_SESSION_ID_ENV.startswith("AMPLIFIER_")


# ---------------------------------------------------------------------------
# Merge behaviour
# ---------------------------------------------------------------------------


class TestMergeSemantics:
    def test_existing_session_metadata_is_preserved(self):
        bundle = _prepared_bundle(
            {"session": {"metadata": {"agent_name": "kept", "run_id": "also-kept"}}}
        )
        _inject_invocation_metadata(bundle, mode="single")

        metadata = bundle.mount_plan["session"]["metadata"]
        assert metadata["agent_name"] == "kept"
        assert metadata["run_id"] == "also-kept"
        assert set(metadata) == {"agent_name", "run_id", "invocation"}

    def test_existing_session_section_keys_are_preserved(self):
        bundle = _prepared_bundle(
            {"session": {"orchestrator": "loop-basic", "context": "context-simple"}}
        )
        _inject_invocation_metadata(bundle, mode="chat")

        session = bundle.mount_plan["session"]
        assert session["orchestrator"] == "loop-basic"
        assert session["context"] == "context-simple"
        assert "invocation" in session["metadata"]

    def test_rest_of_mount_plan_is_untouched(self):
        bundle = _prepared_bundle({"providers": [{"id": "anthropic"}], "tools": []})
        _inject_invocation_metadata(bundle, mode="single")

        assert bundle.mount_plan["providers"] == [{"id": "anthropic"}]
        assert bundle.mount_plan["tools"] == []

    def test_missing_session_section_is_created(self):
        bundle = _prepared_bundle({})
        _inject_invocation_metadata(bundle, mode="single")
        assert set(_invocation(bundle)) == EXPECTED_FIELDS


# ---------------------------------------------------------------------------
# Ordering -- the mount plan must be mutated before create_session() reads it
# ---------------------------------------------------------------------------


class TestOrdering:
    @pytest.mark.anyio
    async def test_mount_plan_carries_invocation_when_create_session_is_called(
        self, tmp_path: Path
    ):
        """The kernel reads session.metadata during create_session().

        Injecting after that call would be a silent no-op, so this asserts on
        the mount plan as observed *at the moment* create_session() runs, not
        afterwards.
        """
        from amplifier_app_cli.session_runner import _create_bundle_session

        seen: dict = {}

        prepared_bundle = MagicMock()
        prepared_bundle.mount_plan = {"providers": [], "tools": []}

        async def _capture_mount_plan(**_kwargs):
            # Deep-ish copy of just what we assert on, captured at call time.
            seen["invocation"] = dict(
                prepared_bundle.mount_plan["session"]["metadata"]["invocation"]
            )
            return MagicMock()

        prepared_bundle.create_session = AsyncMock(side_effect=_capture_mount_plan)

        cfg = SessionConfig(
            config={},
            search_paths=[tmp_path],
            verbose=False,
            prepared_bundle=prepared_bundle,
            bundle_name="test-bundle",
            invocation_mode="single",
        )

        console = MagicMock()
        console.status = MagicMock()
        console.status.return_value.__enter__ = MagicMock()
        console.status.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch(f"{_MODULE}.inject_user_providers", create=True),
            patch(f"{_MODULE}._inject_observability_events"),
            patch(f"{_MODULE}._should_attempt_self_healing", return_value=False),
            patch("amplifier_app_cli.runtime.config.inject_user_providers"),
            patch("amplifier_app_cli.lib.bundle_loader.AppModuleResolver"),
            patch("amplifier_app_cli.paths.create_foundation_resolver"),
        ):
            await _create_bundle_session(
                cfg,
                session_id="test-session-id",
                approval_system=MagicMock(),
                display_system=MagicMock(),
                console=console,
            )

        assert seen["invocation"]["mode"] == "single"
        assert set(seen["invocation"]) == EXPECTED_FIELDS


# ---------------------------------------------------------------------------
# Entry points -- the two resolved-mode paths run.py dispatches to
# ---------------------------------------------------------------------------


class TestEntryPointsRecordResolvedMode:
    """interactive_chat() => "chat"; execute_single() => "single".

    Reaching one of these functions IS the mode resolution: run.py collapses
    --mode, prompt presence, and pipe presence before dispatching, so the raw
    --mode flag (which defaults to "single") is never what gets recorded.
    """

    @pytest.mark.asyncio
    async def test_execute_single_records_single(self, tmp_path: Path):
        from amplifier_app_cli.main import execute_single

        captured: list[SessionConfig] = []

        async def _capture(session_config, _console):
            captured.append(session_config)
            raise SystemExit(0)  # stop before the rest of the session machinery

        with (
            patch(
                f"{_MAIN}.create_initialized_session",
                new=AsyncMock(side_effect=_capture),
            ),
            patch(f"{_MAIN}.console"),
            pytest.raises(SystemExit),
        ):
            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
            )

        assert captured[0].invocation_mode == "single"

    @pytest.mark.asyncio
    async def test_interactive_chat_records_chat(self, tmp_path: Path):
        from amplifier_app_cli.main import interactive_chat

        captured: list[SessionConfig] = []

        async def _capture(session_config, _console):
            captured.append(session_config)
            raise SystemExit(0)

        with (
            patch(
                f"{_MAIN}.create_initialized_session",
                new=AsyncMock(side_effect=_capture),
            ),
            patch(f"{_MAIN}.console"),
            pytest.raises(SystemExit),
        ):
            await interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
            )

        assert captured[0].invocation_mode == "chat"
