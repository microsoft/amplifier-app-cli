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
    assert (
        configured_store.get_metadata_if_exists("corrupt-session")["recovered"] is True
    )

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
        patch.object(SessionStore, "save", side_effect=OSError("session save failed")),
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
