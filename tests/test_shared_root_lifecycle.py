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
from amplifier_foundation.session import slice_to_turn

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
        # Fixture for a checkpoint left by the preceding release. Production
        # save paths no longer call HeldSession.write().
        root.held.write(messages, bundle="anchors", metadata={"session_id": session_id})
        native.save(session_id, messages, {"session_id": session_id, "bundle": "bundle:anchors"})
    finally:
        root.release()


def _isolate_state(tmp_path: Path, monkeypatch) -> SessionStore:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "shared-state"))
    return SessionStore(base_dir=tmp_path / "native-state")


def test_session_fork_reads_latest_native_history_over_legacy_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    _write_shared_checkpoint(native, "shared-root", _messages("authoritative"))
    native.save(
        "shared-root",
        _messages("stale projection"),
        {"session_id": "shared-root", "bundle": "bundle:stale",
         "session_visibility": "internal", "session_purpose": "memory.suggestion"},
    )
    parent_capture = native.base_dir / "shared-root" / "context-intelligence" / "events.jsonl"
    parent_capture.parent.mkdir()
    parent_capture.write_text(
        '{"event":"prompt:submit","data":{"session_id":"shared-root","prompt":"stale projection"}}\n'
        '{"event":"llm:response","data":{"session_id":"shared-root","usage":{"cost_usd":"0.10"}}}\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        _session_cli(native, monkeypatch),
        ["session", "fork", "shared-root", "--at-turn", "1", "--name", "forked"],
    )

    assert result.exit_code == 0, result.output
    transcript, metadata = native.load("forked")
    assert transcript[0]["content"] == "stale projection"
    assert metadata["bundle"] == "bundle:stale"
    assert metadata["parent_id"] == "shared-root"
    assert metadata["forked_from_turn"] == 1
    assert metadata["session_visibility"] == "chat"
    assert "session_purpose" not in metadata
    assert metadata["fork_cost_boundary"]["status"] == "verified"
    assert metadata["fork_cost_boundary"]["cumulative_cost_usd_by_turn"] == ["0.10"]


def test_session_fork_uses_native_reminder_turns_for_verified_cost_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    messages = [
        {"role": "user", "content": "human one"},
        {"role": "assistant", "content": "answer one"},
        {
            "role": "user",
            "content": "<system-reminder source=\"hook\">remember</system-reminder>",
            "metadata": {"ephemeral": True, "persisted": True},
        },
        {"role": "assistant", "content": "hook output"},
        {"role": "user", "content": "human two"},
        {"role": "assistant", "content": "answer two"},
    ]
    native.save("mixed-root", messages, {"session_id": "mixed-root"})
    capture = native.base_dir / "mixed-root" / "context-intelligence" / "events.jsonl"
    capture.parent.mkdir()
    capture.write_text(
        '{"event":"prompt:submit","data":{"session_id":"mixed-root","prompt":"human one"}}\n'
        '{"event":"llm:response","data":{"session_id":"mixed-root","usage":{"cost_usd":"0.10"}}}\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        _session_cli(native, monkeypatch),
        ["session", "fork", "mixed-root", "--at-turn", "2", "--name", "mixed-child"],
    )

    assert result.exit_code == 0, result.output
    transcript, metadata = native.load("mixed-child")
    assert transcript == slice_to_turn(messages, 2, handle_orphaned_tools="complete")
    assert metadata["forked_from_turn"] == 2
    assert metadata["fork_cost_boundary"]["status"] == "verified"
    assert metadata["fork_cost_boundary"]["cumulative_cost_usd_by_turn"] == ["0.10", "0.10"]


def test_real_foundation_lock_writes_native_metadata_without_checkpoint(
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

    _messages_after, metadata = native.load("shared-root")
    assert metadata["api_key"] == "[REDACTED]"
    assert metadata["nested"]["token"] == "[REDACTED]"
    assert read_shared_root("shared-root") is None
    from amplifier_foundation.session.shared_state import SharedSessionStore
    assert not SharedSessionStore(tmp_path, "shared-root").checkpoint_path.exists()


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
        {"bundle": "bundle:held", "model": "held-model",
         "session_visibility": "internal", "session_purpose": "memory.suggestion"},
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
    assert metadata["session_visibility"] == "chat"
    assert "session_purpose" not in metadata
    assert metadata["fork_cost_boundary"]["status"] == "unavailable"
    root_handle.read.assert_called_once_with(native)

    native.save(
        "shared-root_worker",
        _messages("existing child"),
        {"session_id": "shared-root_worker"},
    )
    assert (await processor._fork_session("1 shared-root")).startswith("Error:")
    assert (await processor._fork_session("1 shared-root_worker")).startswith("Error:")


@pytest.mark.asyncio
async def test_interactive_fork_persists_verified_boundary_from_live_context(
    tmp_path: Path, monkeypatch
) -> None:
    native = _isolate_state(tmp_path, monkeypatch)
    messages = _messages("interactive cost")
    native.save("interactive-root", messages, {"session_id": "interactive-root"})
    capture = native.base_dir / "interactive-root" / "context-intelligence" / "events.jsonl"
    capture.parent.mkdir()
    capture.write_text(
        '{"event":"prompt:submit","data":{"session_id":"interactive-root","prompt":"interactive cost"}}\n'
        '{"event":"llm:response","data":{"session_id":"interactive-root","usage":{"cost_usd":"0.10"}}}\n',
        encoding="utf-8",
    )
    context = type("Context", (), {"get_messages": AsyncMock(return_value=messages)})()
    root_handle = MagicMock()
    root_handle.read.return_value = (messages, {"session_id": "interactive-root"})
    session = type("Session", (), {})()
    session.coordinator = type(
        "Coordinator",
        (),
        {
            "session_id": "interactive-root",
            "session_state": {},
            "get": staticmethod(lambda name: context if name == "context" else None),
            "get_capability": staticmethod(
                lambda name: root_handle if name == "cli.shared_root_state" else None
            ),
        },
    )()
    main_module = import_module("amplifier_app_cli.main")
    store_module = import_module("amplifier_app_cli.session_store")
    monkeypatch.setattr(store_module, "SessionStore", lambda: native)

    result = await main_module.CommandProcessor(session, "bundle:anchors")._fork_session(
        "1 interactive-child"
    )

    assert not result.startswith("Error"), result
    boundary = native.get_metadata("interactive-child")["fork_cost_boundary"]
    assert boundary["status"] == "verified"
    assert boundary["cumulative_cost_usd_by_turn"] == ["0.10"]


@pytest.mark.asyncio
async def test_interactive_legacy_fork_never_includes_parent_event_logs(
    tmp_path: Path, monkeypatch
) -> None:
    """The non-shared `/fork` branch writes boundary metadata without copying logs."""
    native = _isolate_state(tmp_path, monkeypatch)
    messages = _messages("legacy authority")
    native.save("legacy-root", messages, {"session_id": "legacy-root"})
    parent = native.base_dir / "legacy-root"
    (parent / "events.jsonl").write_text('{"event":"llm:response"}\n', encoding="utf-8")
    capture = parent / "context-intelligence"
    capture.mkdir()
    capture_events = capture / "events.jsonl"
    capture_events.write_text('{"event":"llm:response"}\n', encoding="utf-8")
    before = (parent / "events.jsonl").read_bytes(), capture_events.read_bytes()

    context = type("Context", (), {"get_messages": AsyncMock(return_value=messages)})()
    session = type("Session", (), {})()
    session.coordinator = type(
        "Coordinator",
        (),
        {
            "session_id": "legacy-root",
            "session_state": {},
            "get": staticmethod(lambda name: context if name == "context" else None),
            "get_capability": staticmethod(lambda _name: None),
        },
    )()
    main_module = import_module("amplifier_app_cli.main")
    store_module = import_module("amplifier_app_cli.session_store")
    monkeypatch.setattr(store_module, "SessionStore", lambda: native)
    result = await main_module.CommandProcessor(session, "bundle:anchors")._fork_session(
        "1 legacy-child"
    )

    assert not result.startswith("Error"), result
    child = native.base_dir / "legacy-child"
    assert not (child / "events.jsonl").exists()
    assert not (child / "context-intelligence").exists()
    assert (parent / "events.jsonl").read_bytes() == before[0]
    assert capture_events.read_bytes() == before[1]
    assert native.get_metadata("legacy-child")["fork_cost_boundary"]["status"] == "unavailable"
    assert "Event history remains with its original owners." in result


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
        from amplifier_app_cli.commands.run import register_run_command
        run_cli = click.Group()
        register_run_command(
            run_cli,
            interactive_chat=AsyncMock(),
            execute_single=main_module.execute_single,
            get_module_search_paths=list,
            check_first_run=lambda: False,
            prompt_first_run_init=lambda _console: None,
        )
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
                run_cli,
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
    # Process-start identity is best effort (/proc on Linux); PID is portable.
    assert f"pid={holder.pid}" in payload["error"]
    assert str(tmp_path / "shared-state") in payload["error"]
    assert "Shared root session is busy" in result.stderr

@pytest.mark.parametrize("skip_events", [False, True])
@pytest.mark.parametrize("shared_lock", [False, True])
def test_native_fork_leaves_all_event_logs_with_original_owners(
    tmp_path, monkeypatch, skip_events, shared_lock
):
    native = _isolate_state(tmp_path, monkeypatch)
    messages = [
        {"role": "user", "content": "first", "timestamp": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "content": "answer", "timestamp": "2026-01-01T00:00:01Z"},
        {"role": "user", "content": "second", "timestamp": "2026-01-01T00:00:02Z"},
    ]
    native.save("root", messages, {"bundle": "bundle:anchors"})
    parent = native.base_dir / "root"
    legacy = parent / "events.jsonl"
    legacy.write_text(
        '{"event":"prompt:submit","session_id":"root","ts":"2026-01-01T00:00:00Z","data":{}}\n'
    )
    capture = parent / "context-intelligence"
    capture.mkdir()
    (capture / "events.jsonl").write_text('{"event":"prompt:submit","data":{"session_id":"root"}}\n')
    before = (capture / "events.jsonl").read_bytes()
    args = ["session", "fork", "root", "--at-turn", "1", "--name", "forked"]
    if skip_events:
        args.append("--no-events")
    monkeypatch.setattr(
        session_commands, "_shared_root_platform_supported", lambda: shared_lock
    )
    result = CliRunner().invoke(_session_cli(native, monkeypatch), args)
    assert result.exit_code == 0, result.output
    child = native.base_dir / "forked"
    assert native.load("forked")[0] == messages[:2]
    assert native.load("forked")[1]["fork_cost_boundary"]["status"] == "unavailable"
    assert not (child / "context-intelligence").exists()
    assert (capture / "events.jsonl").read_bytes() == before
    assert not (child / "events.jsonl").exists()
    assert "Event history remains with its original owners." in result.output
    if skip_events:
        assert "--no-events accepted for compatibility" in result.output


@pytest.mark.parametrize("primary", ["missing", "corrupt"])
@pytest.mark.parametrize("explicit_turn", [False, True])
@pytest.mark.parametrize("shared_lock", [False, True])
def test_fork_previews_and_saves_recovered_native_history(
    tmp_path, monkeypatch, primary, explicit_turn, shared_lock
):
    native = _isolate_state(tmp_path, monkeypatch)
    expected = _messages("first") + _messages("second")
    native.save("root", expected, {"bundle": "bundle:anchors"})
    native.save("root", expected, {"bundle": "bundle:anchors"})
    transcript = native.base_dir / "root" / "transcript.jsonl"
    if primary == "missing":
        transcript.unlink()
    else:
        # A syntactically valid first row followed by damage used to produce a
        # silently shortened preview, even though resume recovered all rows.
        transcript.write_text('{"role":"user","content":"partial"}\n{broken')
    monkeypatch.setattr(session_commands, "_shared_root_platform_supported", lambda: shared_lock)
    args = ["session", "fork", "root", "--name", "forked", "--no-events"]
    if explicit_turn:
        args += ["--at-turn", "2"]
    result = CliRunner().invoke(_session_cli(native, monkeypatch), args, input="\n")
    assert result.exit_code == 0, result.output
    assert "Fork at turn: 2 of 2" in result.output
    if not explicit_turn:
        assert "(2 turns)" in result.output
    assert native.load("forked")[0] == expected
    assert native.load("forked")[1]["forked_from_turn"] == 2
    # Recovery is read-only for the parent's damaged/missing primary file.
    if primary == "missing":
        assert not transcript.exists()
    else:
        assert transcript.read_text().endswith("{broken")
