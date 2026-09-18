"""Lifecycle and real-lock regressions for Foundation shared root sessions."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from amplifier_app_cli.commands import session as session_commands
from amplifier_app_cli.commands.session import register_session_commands
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.shared_root_state import (
    SharedRootSession,
    read_shared_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Foundation shared-session locking is intentionally POSIX-only.",
)


def _messages(content: str) -> list[dict]:
    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": f"answer to {content}"},
    ]


def _session_cli(native: SessionStore, monkeypatch) -> click.Group:
    """Register the actual Click lifecycle commands over one isolated store."""

    cli = click.Group()
    register_session_commands(
        cli,
        interactive_chat=AsyncMock(),
        execute_single=AsyncMock(),
        get_module_search_paths=list,
    )
    monkeypatch.setattr(session_commands, "SessionStore", lambda: native)
    return cli


def _write_shared_checkpoint(
    native: SessionStore, session_id: str, messages: list[dict]
) -> None:
    root = SharedRootSession.acquire(session_id)
    try:
        root.checkpoint(
            native,
            messages,
            bundle="bundle:anchors",
            metadata={"session_id": session_id, "bundle": "bundle:anchors"},
        )
    finally:
        root.release()


def _isolate_state(tmp_path: Path, monkeypatch) -> SessionStore:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "shared-state"))
    return SessionStore(base_dir=tmp_path / "native-state")


def test_session_fork_reads_shared_authority_not_stale_projection(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    _write_shared_checkpoint(native, "shared-root", _messages("authoritative"))
    native.save(
        "shared-root",
        _messages("stale projection"),
        {"session_id": "shared-root", "bundle": "bundle:stale"},
    )

    result = CliRunner().invoke(
        _session_cli(native, monkeypatch),
        ["session", "fork", "shared-root", "--at-turn", "1", "--name", "forked"],
    )

    assert result.exit_code == 0, result.output
    transcript, metadata = native.load("forked")
    assert transcript[0]["content"] == "authoritative"
    assert metadata["parent_id"] == "shared-root"
    assert metadata["forked_from_turn"] == 1


def test_real_foundation_checkpoint_strips_forbidden_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    root = SharedRootSession.acquire("shared-root")
    try:
        root.checkpoint(
            native,
            _messages("safe metadata"),
            bundle="bundle:anchors",
            metadata={
                "session_id": "shared-root",
                "api_key": "never write this",
                "nested": {"token": "also never write this"},
            },
        )
    finally:
        root.release()

    _messages_after, metadata = read_shared_root("shared-root") or ([], {})
    assert "api_key" not in metadata
    assert "token" not in metadata.get("nested", {})


def test_session_fork_rejects_existing_shared_target_id(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    _write_shared_checkpoint(native, "shared-root", _messages("authoritative"))

    result = CliRunner().invoke(
        _session_cli(native, monkeypatch),
        ["session", "fork", "shared-root", "--at-turn", "1", "--name", "shared-root"],
    )

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert read_shared_root("shared-root") is not None


def test_new_session_save_never_overwrites_an_existing_target(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    native.save("existing-child", _messages("original"), {"session_id": "existing-child"})

    with pytest.raises(FileExistsError):
        native.save_new(
            "existing-child",
            _messages("replacement"),
            {"session_id": "existing-child"},
        )

    assert native.load("existing-child")[0][0]["content"] == "original"


def test_session_cleanup_skips_busy_root_before_first_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    native.save(
        "active-root",
        _messages("do not remove"),
        {"session_id": "active-root", "bundle": "bundle:anchors"},
    )
    os.utime(native.base_dir / "active-root", (1, 1))
    root = SharedRootSession.acquire("active-root")
    try:
        result = CliRunner().invoke(
            _session_cli(native, monkeypatch),
            ["session", "cleanup", "--days", "0", "--force"],
        )
    finally:
        root.release()

    assert result.exit_code == 0, result.output
    assert native.exists("active-root")
    assert "Skipped 1 busy session" in result.output


def test_session_delete_removes_only_held_checkpoint_and_native_projection(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    _write_shared_checkpoint(native, "shared-root", _messages("delete me"))

    result = CliRunner().invoke(
        _session_cli(native, monkeypatch),
        ["session", "delete", "shared-root", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert not native.exists("shared-root")
    assert read_shared_root("shared-root") is None

    from amplifier_foundation.session.shared_state import SharedSessionStore

    store = SharedSessionStore(tmp_path, "shared-root")
    assert not store.checkpoint_path.exists()
    assert store.checkpoint_path.parent.exists()
    assert (store.checkpoint_path.parent / "session.lock").exists()


def test_session_cleanup_skips_shared_projection_and_removes_ordinary_session(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    _write_shared_checkpoint(native, "shared-root", _messages("keep me"))
    native.save(
        "ordinary-root",
        _messages("remove me"),
        {"session_id": "ordinary-root", "bundle": "bundle:anchors"},
    )
    old = 1
    for session_id in ("shared-root", "ordinary-root"):
        session_dir = native.base_dir / session_id
        os.utime(session_dir, (old, old))

    result = CliRunner().invoke(
        _session_cli(native, monkeypatch),
        ["session", "cleanup", "--days", "0", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert native.exists("shared-root")
    assert not native.exists("ordinary-root")
    assert "Skipped 1 shared-root session" in result.output


@pytest.mark.asyncio
async def test_live_fork_uses_current_context_without_reacquiring_shared_lock(
    tmp_path: Path, monkeypatch
) -> None:
    """`/fork` must work before a native projection exists for a held root."""

    native = _isolate_state(tmp_path, monkeypatch)
    context = type(
        "Context",
        (),
        {"get_messages": AsyncMock(return_value=_messages("live authority"))},
    )()
    root_handle = MagicMock()
    root_handle.read.return_value = (
        _messages("held metadata authority"),
        {"bundle": "bundle:held", "model": "held-model"},
    )

    def get_capability(name: str):
        if name == "cli.shared_root_state":
            return root_handle
        return None

    session = type("Session", (), {})()
    session.coordinator = type(
        "Coordinator",
        (),
        {
            "session_id": "shared-root",
            "session_state": {},
            "get": staticmethod(lambda name: context if name == "context" else None),
            "get_capability": staticmethod(get_capability),
        },
    )()

    main_module = import_module("amplifier_app_cli.main")
    store_module = import_module("amplifier_app_cli.session_store")

    monkeypatch.setattr(store_module, "SessionStore", lambda: native)
    processor = main_module.CommandProcessor(session, "bundle:anchors")
    result = await processor._fork_session("1 live-fork")

    assert not result.startswith("Error"), result
    transcript, metadata = native.load("live-fork")
    assert "Forked session created: live-fork" in result
    assert transcript[0]["content"] == "live authority"
    assert metadata["parent_id"] == "shared-root"
    assert metadata["bundle"] == "bundle:held"
    root_handle.read.assert_called_once_with()

    native.save(
        "shared-root_worker",
        _messages("existing child"),
        {"session_id": "shared-root_worker"},
    )
    assert (await processor._fork_session("1 shared-root")).startswith("Error:")
    assert (await processor._fork_session("1 shared-root_worker")).startswith("Error:")


def test_busy_shared_root_is_a_click_error_with_parseable_json_stdout(
    tmp_path: Path, monkeypatch
) -> None:
    """A separate process owns the real Foundation lock during Click startup."""

    _isolate_state(tmp_path, monkeypatch)
    env = {
        **os.environ,
        "AMPLIFIER_SESSION_STATE_HOME": str(tmp_path / "shared-state"),
    }
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from amplifier_app_cli.shared_root_state import SharedRootSession; "
                "root = SharedRootSession.acquire('busy-root'); "
                "root.held.write([], bundle='anchors', metadata={'session_id': 'busy-root'}); "
                "print('ready', flush=True); "
                "input(); "
                "root.release()"
            ),
        ],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"

    try:
        main_module = import_module("amplifier_app_cli.main")
        prepared_bundle = type("PreparedBundle", (), {"mount_plan": {}})()
        with (
            patch(
                "amplifier_app_cli.commands.run._resolve_config_interruptibly",
                return_value=({}, prepared_bundle),
            ),
            patch(
                "amplifier_app_cli.commands.run.create_config_manager",
                return_value=MagicMock(get_merged_settings=dict),
            ),
            patch("amplifier_app_cli.commands.run._run_startup_update_check"),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
        ):
            result = CliRunner().invoke(
                main_module.cli,
                ["run", "--output-format", "json", "--resume", "busy-root", "blocked"],
            )
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)

    assert result.exit_code == 1
    payload = __import__("json").loads(result.stdout)
    assert payload["error_type"] == "SharedRootSessionBusyError"
    assert "finish/exit that owner then retry" in payload["error"].lower()
    assert "process=" in payload["error"]
    assert str(tmp_path / "shared-state") in payload["error"]
    assert "Shared root session is busy" in result.stderr