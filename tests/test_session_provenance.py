"""Explicit internal creation labels survive persistence without hiding human roots."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.incremental_save import IncrementalSaveHook
from amplifier_app_cli.session_provenance import creation_metadata
from amplifier_app_cli.session_store import SessionStore


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, {"session_visibility": "chat"}),
        (
            {"AMPLIFIER_SESSION_ORIGIN": "agent", "AMPLIFIER_EXECUTION_MODE": "single"},
            {"session_visibility": "chat"},
        ),
        ({"AMPLIFIER_SESSION_VISIBILITY": "unknown"}, {"session_visibility": "chat"}),
        (
            {"AMPLIFIER_SESSION_PURPOSE": "memory.suggestion"},
            {"session_visibility": "chat"},
        ),
        (
            {
                "AMPLIFIER_SESSION_VISIBILITY": "internal",
                "AMPLIFIER_SESSION_PURPOSE": "memory.suggestion",
            },
            {"session_visibility": "internal", "session_purpose": "memory.suggestion"},
        ),
        (
            {
                "AMPLIFIER_SESSION_VISIBILITY": "internal",
                "AMPLIFIER_SESSION_PURPOSE": "private\ntext",
            },
            {"session_visibility": "internal"},
        ),
        (
            {
                "AMPLIFIER_SESSION_VISIBILITY": "internal",
                "AMPLIFIER_SESSION_PURPOSE": "a" * 81,
            },
            {"session_visibility": "internal"},
        ),
    ],
)
def test_only_explicit_internal_creation_is_hidden(env, expected):
    assert creation_metadata(is_resume=False, env=env) == expected
    assert creation_metadata(is_resume=True, env=env) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing",
    [
        None,
        {},
        {"session_visibility": "chat"},
        {"session_visibility": "internal", "session_purpose": "original.job"},
    ],
)
async def test_incremental_save_preserves_creation_across_resume(tmp_path, existing):
    store = SessionStore(base_dir=tmp_path / "sessions")
    messages = [{"role": "user", "content": "synthetic"}]
    if existing is not None:
        store.save("root", messages, existing)
    context = MagicMock(get_messages=AsyncMock(return_value=messages))
    session = MagicMock()
    session.coordinator.get.return_value = context
    new = creation_metadata(
        is_resume=existing is not None,
        env={
            "AMPLIFIER_SESSION_VISIBILITY": "internal",
            "AMPLIFIER_SESSION_PURPOSE": "memory.suggestion",
        },
    )
    hook = IncrementalSaveHook(
        session, store, "root", "test", {}, creation_metadata=new
    )
    await hook.on_tool_post("tool:post", {})
    transcript, metadata = store.load("root")
    assert transcript == messages
    assert {
        key: metadata[key]
        for key in ("session_visibility", "session_purpose")
        if key in metadata
    } == (existing if existing is not None else new)


@pytest.mark.anyio
@pytest.mark.parametrize("resume", [False, True])
async def test_initializer_marks_before_start_without_relabelling_resume(
    tmp_path, monkeypatch, resume
):
    from amplifier_app_cli import session_runner
    from tests.test_session_runner import (
        _configurator_patches,
        _make_mock_session,
        _make_session_config,
    )

    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_SESSION_VISIBILITY", "internal")
    monkeypatch.setenv("AMPLIFIER_SESSION_PURPOSE", "memory.suggestion")
    native = SessionStore(base_dir=tmp_path / "sessions")
    if resume:
        native.save("root", [], {"session_visibility": "chat"})
    session = _make_mock_session()
    config = _make_session_config(
        session_id="root", initial_transcript=[] if resume else None
    )

    async def construct(**_kwargs):
        metadata = native.get_metadata("root")
        assert metadata["session_visibility"] == ("chat" if resume else "internal")
        return session

    with ExitStack() as stack:
        for patcher in _configurator_patches(session):
            stack.enter_context(patcher)
        stack.enter_context(
            patch.object(session_runner, "SessionStore", return_value=native)
        )
        stack.enter_context(
            patch.object(
                session_runner, "_create_bundle_session", side_effect=construct
            )
        )
        initialized = await session_runner.create_initialized_session(
            config, MagicMock()
        )
    assert initialized.creation_metadata == (
        {}
        if resume
        else {"session_visibility": "internal", "session_purpose": "memory.suggestion"}
    )


@pytest.mark.anyio
async def test_initialization_failure_keeps_internal_marker_and_releases_owner(
    tmp_path, monkeypatch
):
    from amplifier_app_cli import session_runner
    from tests.test_session_runner import (
        _configurator_patches,
        _make_mock_session,
        _make_session_config,
    )

    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_SESSION_VISIBILITY", "internal")
    native = SessionStore(base_dir=tmp_path / "sessions")
    root = MagicMock()
    config = _make_session_config(session_id="root", root_state=root)
    with ExitStack() as stack:
        for patcher in _configurator_patches(_make_mock_session()):
            stack.enter_context(patcher)
        stack.enter_context(
            patch.object(session_runner, "SessionStore", return_value=native)
        )
        stack.enter_context(
            patch.object(
                session_runner,
                "_create_bundle_session",
                side_effect=RuntimeError("synthetic init failure"),
            )
        )
        with pytest.raises(RuntimeError, match="synthetic init failure"):
            await session_runner.create_initialized_session(config, MagicMock())
    assert native.load("root")[1]["session_visibility"] == "internal"
    root.release.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["legacy", "chat", "child"])
async def test_existing_or_child_identity_is_not_relabelled_before_creation(
    tmp_path, monkeypatch, kind
):
    from amplifier_app_cli import session_runner
    from tests.test_session_runner import (
        _configurator_patches,
        _make_mock_session,
        _make_session_config,
    )

    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_SESSION_VISIBILITY", "internal")
    native = SessionStore(base_dir=tmp_path / "sessions")
    if kind != "child":
        native.save(
            "root",
            [{"role": "user", "content": "original"}],
            {"session_visibility": "chat"} if kind == "chat" else {},
        )
    before = {
        path: path.read_bytes() for path in native.base_dir.rglob("*") if path.is_file()
    }
    config = _make_session_config(
        session_id="root",
        session_config_initial={"root_session_id": "parent"} if kind == "child" else {},
    )
    with ExitStack() as stack:
        for patcher in _configurator_patches(_make_mock_session()):
            stack.enter_context(patcher)
        stack.enter_context(
            patch.object(session_runner, "SessionStore", return_value=native)
        )
        stack.enter_context(
            patch.object(
                session_runner,
                "_create_bundle_session",
                side_effect=RuntimeError("stop before execution"),
            )
        )
        with pytest.raises(RuntimeError, match="stop before execution"):
            await session_runner.create_initialized_session(config, MagicMock())
    assert before == {
        path: path.read_bytes() for path in native.base_dir.rglob("*") if path.is_file()
    }
