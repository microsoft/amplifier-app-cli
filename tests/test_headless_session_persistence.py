"""Regression coverage for headless JSON session persistence.

The final headless save must create a new session before it tries to preserve
metadata from an earlier save.  JSON output is particularly important here:
missing prior metadata must not turn a successfully saved request into a
non-zero CLI result.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from amplifier_app_cli.session_store import SessionStore

_MAIN = "amplifier_app_cli.main"
_SESSION_ID = "headless-json-session"


def _private_console() -> Console:
    """Keep JSON redirection from retaining pytest's per-test capture stream."""
    return Console(file=io.StringIO())


def _initialized_session(*, response: str = "saved response") -> MagicMock:
    """Return the smallest session double that exercises the final save path."""
    context = MagicMock()
    context.get_messages = AsyncMock(
        return_value=[
            {"role": "user", "content": "persist this"},
            {"role": "assistant", "content": response},
        ]
    )

    def get_capability(name: str):
        if name == "context":
            return context
        if name == "providers":
            return {}
        return None

    session = MagicMock()
    session.session_id = _SESSION_ID
    session.execute = AsyncMock(return_value=response)
    session.coordinator = MagicMock()
    session.coordinator.get = get_capability
    session.coordinator.cancellation = MagicMock()
    session.coordinator.cancellation.is_cancelled = False

    initialized = MagicMock()
    initialized.creation_metadata = {"session_visibility": "internal", "session_purpose": "memory.suggestion"}
    initialized.session = session
    initialized.session_id = _SESSION_ID
    initialized.cleanup = AsyncMock()
    return initialized


@pytest.fixture
def split_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Make a HOME decoy so the assertion proves AMPLIFIER_HOME won."""
    default_home = tmp_path / "default-home"
    isolated_home = tmp_path / "isolated-amplifier-home"
    monkeypatch.setenv("HOME", str(default_home))
    monkeypatch.setenv("USERPROFILE", str(default_home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(isolated_home))
    return default_home, isolated_home


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["json", "json-trace"])
async def test_headless_json_output_creates_and_loads_new_session_from_amplifier_home(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    """A successful response persists and is loadable from AMPLIFIER_HOME.

    This deliberately starts with no session directory.  Before the fix,
    execute_single() called get_metadata() first, which raised
    FileNotFoundError and exited 1 after the model response had completed.
    """
    from amplifier_app_cli.main import execute_single

    default_home, isolated_home = split_homes
    initialized = _initialized_session()

    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
    ):
        await execute_single(
            prompt="persist this",
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=_SESSION_ID,
            bundle_name="test-bundle",
            output_format=output_format,
        )

    output = json.loads(capfd.readouterr().out)
    assert output["status"] == "success"
    assert output["session_id"] == _SESSION_ID
    if output_format == "json-trace":
        assert output["execution_trace"] == []

    # This is a real second lookup, not an assertion about a mocked constructor.
    transcript, metadata = SessionStore().load(_SESSION_ID)
    assert transcript[0]["content"] == "persist this"
    assert metadata["session_id"] == _SESSION_ID
    assert metadata["session_visibility"] == "internal"
    assert metadata["session_purpose"] == "memory.suggestion"
    assert SessionStore().base_dir.is_relative_to(isolated_home)
    assert not (default_home / ".amplifier").exists()


def test_session_store_default_home_fallback_and_explicit_base_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver honors AMPLIFIER_HOME, falls back to HOME, and never wins over base_dir."""
    default_home = tmp_path / "default-home"
    isolated_home = tmp_path / "isolated-home"
    explicit_base = tmp_path / "explicit-sessions"
    monkeypatch.setenv("HOME", str(default_home))
    monkeypatch.setenv("USERPROFILE", str(default_home))

    monkeypatch.delenv("AMPLIFIER_HOME", raising=False)
    fallback_store = SessionStore()
    assert fallback_store.base_dir.is_relative_to(default_home / ".amplifier")

    monkeypatch.setenv("AMPLIFIER_HOME", str(isolated_home))
    configured_store = SessionStore()
    configured_store.save("configured-session", [], {"source": "configured"})
    assert configured_store.load("configured-session")[1]["source"] == "configured"
    assert configured_store.base_dir.is_relative_to(isolated_home)
    configured_store.save("named-session", [], {"name": "preserved"})
    assert configured_store.get_metadata_if_exists("named-session") == {
        "name": "preserved"
    }
    with pytest.raises(FileNotFoundError, match="Session 'missing' not found"):
        configured_store.get_metadata("missing")
    assert configured_store.get_metadata_if_exists("missing") == {}
    with pytest.raises(ValueError, match="Invalid session_id"):
        configured_store.get_metadata_if_exists("../invalid")
    corrupt_dir = configured_store.base_dir / "corrupt-session"
    corrupt_dir.mkdir()
    (corrupt_dir / "metadata.json").write_text("{not json", encoding="utf-8")
    from amplifier_foundation.session.history import SessionHistoryError

    with pytest.raises(SessionHistoryError, match="metadata"):
        configured_store.get_metadata_if_exists("corrupt-session")

    explicit_store = SessionStore(base_dir=explicit_base)
    explicit_store.save("explicit-session", [], {"source": "explicit"})
    assert explicit_store.load("explicit-session")[1]["source"] == "explicit"
    assert explicit_store.base_dir == explicit_base


@pytest.mark.asyncio
async def test_headless_json_trace_still_exits_nonzero_for_execution_failure(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The new-session case is narrow: genuine execution failures still fail loud."""
    from amplifier_app_cli.main import execute_single

    initialized = _initialized_session()
    initialized.session.execute = AsyncMock(side_effect=RuntimeError("provider failed"))

    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        pytest.raises(SystemExit) as exit_info,
    ):
        await execute_single(
            prompt="persist this",
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=_SESSION_ID,
            bundle_name="test-bundle",
            output_format="json-trace",
        )

    assert exit_info.value.code == 1
    output = json.loads(capfd.readouterr().out)
    assert output["status"] == "error"
    assert output["error"] == "provider failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json", "json-trace"])
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("context length exceeded"), id="generic"),
        pytest.param("llm", id="context-length-error"),
    ],
)
async def test_headless_failed_turn_still_persists_transcript(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    output_format: str,
    failure: object,
) -> None:
    """Persist canonical tool results before cleanup, even after an overflow."""
    from amplifier_app_cli.main import execute_single

    if failure == "llm":
        from amplifier_core.llm_errors import ContextLengthError

        failure = ContextLengthError("prompt is too long")

    # Context holds the user turn plus two tool round-trips at raise time --
    # the shape a resumed session must be able to see.
    accumulated = [
        {"role": "user", "content": "read the four big files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "t1",
                    "name": "read_file",
                    "arguments": {"file_path": "first.txt"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "x" * 5000},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "t2",
                    "name": "read_file",
                    "arguments": {"file_path": "second.txt"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "t2", "content": "y" * 5000},
    ]
    initialized = _initialized_session()
    initialized.session.execute = AsyncMock(side_effect=failure)
    initialized.session.coordinator.get("context").get_messages = AsyncMock(
        return_value=accumulated
    )

    async def cleanup():
        # Prove durability BEFORE teardown, not merely after execute_single returns.
        assert SessionStore().load(_SESSION_ID)[0] == accumulated

    initialized.cleanup.side_effect = cleanup

    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        pytest.raises(SystemExit) as exit_info,
    ):
        await execute_single(
            prompt="read the four big files",
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=_SESSION_ID,
            bundle_name="test-bundle",
            output_format=output_format,
        )

    # Still fails loud -- persistence must not turn an error into a success.
    assert exit_info.value.code == 1
    if output_format in ("json", "json-trace"):
        assert json.loads(capfd.readouterr().out)["status"] == "error"

    # The whole accumulated turn is on disk, tool results included.
    transcript, metadata = SessionStore().load(_SESSION_ID)
    assert transcript == accumulated
    assert metadata["session_id"] == _SESSION_ID
    assert metadata["session_visibility"] == "internal"
    assert metadata["session_purpose"] == "memory.suggestion"
    assert metadata["turn_count"] == 1

    # cleanup still ran exactly once after the save.
    initialized.cleanup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["json", "json-trace"])
async def test_headless_failed_turn_save_failure_does_not_mask_original_error(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    output_format: str,
) -> None:
    """If the fallback save itself blows up, the user still sees the turn's error."""
    from amplifier_app_cli.main import execute_single

    initialized = _initialized_session()
    initialized.session.execute = AsyncMock(
        side_effect=RuntimeError("the real failure")
    )

    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        patch(f"{_MAIN}.SessionStore") as store_cls,
        pytest.raises(SystemExit) as exit_info,
    ):
        store_cls.return_value.get_metadata_if_exists.return_value = {}
        store_cls.return_value.save.side_effect = OSError("disk full")
        await execute_single(
            prompt="persist this",
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=_SESSION_ID,
            bundle_name="test-bundle",
            output_format=output_format,
        )

    assert exit_info.value.code == 1
    output = json.loads(capfd.readouterr().out)
    assert output["status"] == "error"
    assert output["error"] == "the real failure"  # not "disk full"
    assert "transcript save after failed turn did not complete" in caplog.text
    store_cls.return_value.save.assert_called_once()
    initialized.cleanup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_fails", [False, True])
async def test_failed_turn_keeps_root_writer_ownership(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    checkpoint_fails: bool,
) -> None:
    """The failure save must use the root writer, never bypass its refusal."""
    from amplifier_app_cli.main import execute_single
    from amplifier_app_cli.session_handoff import CLIHandoff

    store = SessionStore()
    store.save(_SESSION_ID, [], {"name": "existing name", "description": "keep me"})
    initialized = _initialized_session()
    initialized.session.execute.side_effect = RuntimeError("execution failed")
    root = MagicMock()
    initialized.root_state = root

    def checkpoint(target_store, messages, *, bundle, metadata):
        assert metadata["name"] == "existing name"
        assert metadata["description"] == "keep me"
        assert bundle == "test-bundle"
        if checkpoint_fails:
            raise RuntimeError("writer refused")
        target_store.save(_SESSION_ID, messages, metadata)

    root.checkpoint.side_effect = checkpoint

    async def cleanup(**kwargs):
        assert root.checkpoint.call_count == 1
        assert kwargs == {"release_ownership": False}
        transcript, _ = store.load(_SESSION_ID)
        assert len(transcript) == (0 if checkpoint_fails else 2)

    initialized.cleanup.side_effect = cleanup
    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        # No takeover transport is needed to exercise the existing writer path.
        patch.object(CLIHandoff, "start", new=AsyncMock()),
        pytest.raises(SystemExit) as exit_info,
    ):
        await execute_single(
            "persist this",
            {},
            [tmp_path],
            False,
            session_id=_SESSION_ID,
            bundle_name="test-bundle",
            output_format="json",
        )

    assert exit_info.value.code == 1
    assert json.loads(capfd.readouterr().out)["error"] == "execution failed"
    root.checkpoint.assert_called_once()
    root.release.assert_called_once()


@pytest.mark.asyncio
async def test_headless_cancellation_saves_before_cleanup(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    from amplifier_app_cli.main import execute_single

    initialized = _initialized_session()
    initialized.session.execute.side_effect = asyncio.CancelledError()

    async def cleanup():
        assert SessionStore().load(_SESSION_ID)[0][0]["content"] == "persist this"

    initialized.cleanup.side_effect = cleanup
    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        pytest.raises(asyncio.CancelledError),
    ):
        await execute_single(
            "persist this",
            {},
            [tmp_path],
            False,
            session_id=_SESSION_ID,
            output_format="json",
        )
    assert capfd.readouterr().out == ""
    initialized.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_turn_save_is_not_gated_on_store_hooks(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    from amplifier_app_cli.main import execute_single

    initialized = _initialized_session()
    initialized.session.execute.side_effect = RuntimeError("execution failed")
    original_get = initialized.session.coordinator.get
    hooks = MagicMock()

    async def emit(event, data):
        if event.startswith("cleanup:store_"):
            raise RuntimeError("store observer failed")
        if event == "cleanup:finally_begin":
            assert SessionStore().load(_SESSION_ID)[0]

    hooks.emit = AsyncMock(side_effect=emit)
    initialized.session.coordinator.get = lambda key: (
        hooks if key == "hooks" else original_get(key)
    )
    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        pytest.raises(SystemExit) as exit_info,
    ):
        await execute_single(
            "persist this", {}, [tmp_path], False, session_id=_SESSION_ID
        )
    assert exit_info.value.code == 1
    assert SessionStore().load(_SESSION_ID)[0]


@pytest.mark.asyncio
async def test_failure_before_execution_does_not_save(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    from amplifier_app_cli.main import execute_single

    initialized = _initialized_session()
    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        patch(
            f"{_MAIN}.process_runtime_mentions",
            new=AsyncMock(side_effect=ValueError("bad mention")),
        ),
        patch.object(SessionStore, "save") as save,
        pytest.raises(SystemExit),
    ):
        await execute_single(
            "bad mention", {}, [tmp_path], False, session_id=_SESSION_ID
        )
    initialized.session.execute.assert_not_awaited()
    save.assert_not_called()
    initialized.cleanup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["json", "json-trace"])
async def test_headless_json_reports_one_error_when_final_session_save_fails(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    """A failed final save fails loud without appending stale success JSON."""
    from amplifier_app_cli.main import execute_single

    initialized = _initialized_session()

    with (
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch(f"{_MAIN}.console", new=_private_console()),
        patch.object(
            SessionStore, "save", side_effect=OSError("session save failed")
        ) as save,
        pytest.raises(SystemExit) as exit_info,
    ):
        await execute_single(
            prompt="persist this",
            config={},
            search_paths=[tmp_path],
            verbose=False,
            session_id=_SESSION_ID,
            bundle_name="test-bundle",
            output_format=output_format,
        )

    assert exit_info.value.code == 1
    stdout = capfd.readouterr().out
    assert stdout.count('"status"') == 1
    output = json.loads(stdout)
    assert output["status"] == "error"
    assert output["error"] == "session save failed"
    save.assert_called_once()


@pytest.mark.parametrize(
    ("output_format", "marker_stream"),
    [("json", "stderr"), ("json-trace", "stderr"), ("text", "stdout")],
)
def test_run_routes_preparation_output_away_from_json_payload(
    split_homes: tuple[Path, Path],
    output_format: str,
    marker_stream: str,
) -> None:
    """Preparation diagnostics precede execute_single but not JSON stdout."""
    import click

    from amplifier_app_cli.commands.run import register_run_command
    from amplifier_app_cli.main import execute_single

    cli = click.Group()
    initialized = _initialized_session()
    prepared_bundle = MagicMock()
    prepared_bundle.mount_plan = {}
    test_console = Console()

    def _prepare(**_kwargs):
        print("PREPARATION MARKER")
        test_console.print("PREPARATION CONSOLE MARKER")
        return {}, prepared_bundle

    startup_console = Console()

    def _startup_check() -> None:
        print("STARTUP MARKER")
        startup_console.print("STARTUP CONSOLE MARKER")

    register_run_command(
        cli,
        interactive_chat=AsyncMock(),
        execute_single=execute_single,
        get_module_search_paths=list,
        check_first_run=lambda: False,
        prompt_first_run_init=lambda _console: False,
    )

    with (
        patch(
            "amplifier_app_cli.commands.run._resolve_config_interruptibly",
            side_effect=_prepare,
        ),
        patch(
            "amplifier_app_cli.commands.run.create_config_manager",
            return_value=MagicMock(get_merged_settings=dict),
        ),
        patch(
            "amplifier_app_cli.commands.run._run_startup_update_check",
            side_effect=_startup_check,
        ),
        patch(
            f"{_MAIN}.create_initialized_session",
            new=AsyncMock(return_value=initialized),
        ),
        patch("amplifier_app_cli.commands.run.console", new=test_console),
        patch(f"{_MAIN}.console", new=test_console),
    ):
        result = CliRunner().invoke(
            cli, ["run", "--output-format", output_format, "persist this"]
        )

    assert result.exit_code == 0, result.output
    stream = result.stderr if marker_stream == "stderr" else result.stdout
    assert "PREPARATION MARKER" in stream
    assert "PREPARATION CONSOLE MARKER" in stream
    assert "STARTUP MARKER" in stream
    assert "STARTUP CONSOLE MARKER" in stream

    if output_format in {"json", "json-trace"}:
        assert "PREPARATION MARKER" not in result.stdout
        assert "PREPARATION CONSOLE MARKER" not in result.stdout
        assert "STARTUP MARKER" not in result.stdout
        assert "STARTUP CONSOLE MARKER" not in result.stdout
        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["session_id"] == _SESSION_ID
    else:
        assert "saved response" in result.stdout

    assert SessionStore().load(_SESSION_ID)[1]["session_id"] == _SESSION_ID


def test_run_json_preparation_failure_remains_nonzero(
    split_homes: tuple[Path, Path],
) -> None:
    """Redirecting diagnostics must not convert a failed preparation to success."""
    import click

    from amplifier_app_cli.commands.run import register_run_command

    cli = click.Group()
    test_console = Console()
    register_run_command(
        cli,
        interactive_chat=AsyncMock(),
        execute_single=AsyncMock(),
        get_module_search_paths=list,
        check_first_run=lambda: False,
        prompt_first_run_init=lambda _console: False,
    )

    with (
        patch(
            "amplifier_app_cli.commands.run._resolve_config_interruptibly",
            side_effect=FileNotFoundError("bundle missing"),
        ),
        patch(
            "amplifier_app_cli.commands.run.create_config_manager",
            return_value=MagicMock(get_merged_settings=dict),
        ),
        patch("amplifier_app_cli.commands.run.console", new=test_console),
    ):
        result = CliRunner().invoke(
            cli, ["run", "--output-format", "json", "persist this"]
        )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "bundle missing" in result.stderr


@pytest.mark.parametrize("output_format", ["json", "json-trace"])
def test_run_json_restores_dynamic_console_file_after_execution(
    split_homes: tuple[Path, Path],
    tmp_path: Path,
    output_format: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless output must not retain CliRunner's closed stream in the console."""
    import asyncio

    import click

    from amplifier_app_cli.console import console
    from amplifier_app_cli.main import execute_single

    monkeypatch.setattr(console, "_file", None)
    cli = click.Group()

    @cli.command()
    def run_headless() -> None:
        asyncio.run(
            execute_single(
                prompt="persist this",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                session_id=_SESSION_ID,
                bundle_name="test-bundle",
                output_format=output_format,
            )
        )

    @cli.command()
    def print_again() -> None:
        console.print("SECOND INVOCATION")

    assert console._file is None
    with patch(
        f"{_MAIN}.create_initialized_session",
        new=AsyncMock(return_value=_initialized_session()),
    ):
        first = CliRunner().invoke(cli, ["run-headless"])

    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["status"] == "success"
    assert console._file is None

    second = CliRunner().invoke(cli, ["print-again"])
    assert second.exit_code == 0, second.output
    assert "SECOND INVOCATION" in second.stdout
