"""Focused WARM contract coverage for CLI's Foundation shared-state adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.incremental_save import IncrementalSaveHook
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.shared_root_state import (
    SharedRootSession,
    load_root_resume,
    resolve_root_session_id,
)


class _Held:
    def __init__(self, records: dict[str, dict], session_id: str) -> None:
        self.records = records
        self.session_id = session_id
        self.released = False
        self.writes: list[dict] = []

    def read(self) -> dict | None:
        return self.records.get(self.session_id)

    def write(self, messages, *, bundle, metadata) -> None:
        checkpoint = {
            "version": 1,
            "workspace": "set by Foundation",
            "session_id": self.session_id,
            "bundle": bundle,
            "messages": messages,
            "metadata": metadata,
        }
        self.writes.append(checkpoint)
        self.records[self.session_id] = checkpoint

    def release(self) -> None:
        self.released = True


class _SharedStore:
    records: dict[str, dict] = {}
    acquired: list[_Held] = []

    def __init__(self, workspace: Path, session_id: str) -> None:
        self.workspace = workspace
        self.session_id = session_id

    def acquire(self, *, app: str, **_diagnostics) -> _Held:
        assert app == "amplifier-cli"
        held = _Held(self.records, self.session_id)
        self.acquired.append(held)
        return held

    def read(self) -> dict | None:
        return self.records.get(self.session_id)

    @classmethod
    def list_ids(cls, _workspace: Path) -> list[str]:
        return list(cls.records)


@pytest.fixture
def shared_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, dict]:
    from amplifier_app_cli import shared_root_state

    _SharedStore.records = {}
    _SharedStore.acquired = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shared_root_state, "_shared_api", lambda: (_SharedStore, _Held))
    return _SharedStore.records


def test_root_checkpoint_writes_common_authority_before_native_projection(
    shared_api: dict[str, dict], tmp_path: Path
) -> None:
    native = SessionStore(base_dir=tmp_path / "native")
    root = SharedRootSession.acquire("web-root")
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1"}]},
        {"role": "user", "content": {"type": "tool_result", "tool_use_id": "call-1"}},
    ]
    root.checkpoint(
        native,
        messages,
        bundle="bundle:portable-bundle",
        metadata={"session_id": "web-root", "api_key": "secret"},
    )

    assert shared_api["web-root"]["bundle"] == "portable-bundle"
    assert shared_api["web-root"]["messages"] == messages
    assert shared_api["web-root"]["metadata"]["api_key"] != "secret"
    assert native.load("web-root")[0] == messages
    root.release()
    assert _SharedStore.acquired[0].released is True


def test_common_checkpoint_wins_over_stale_native_and_shared_only_id_resolves(
    shared_api: dict[str, dict], tmp_path: Path
) -> None:
    native = SessionStore(base_dir=tmp_path / "native")
    native.save(
        "web-root",
        [{"role": "user", "content": "stale"}],
        {"session_id": "web-root", "bundle": "bundle:stale"},
    )
    shared_api["web-root"] = {
        "messages": [{"role": "user", "content": "authoritative"}],
        "metadata": {"session_id": "web-root"},
        "bundle": "portable-bundle",
    }
    shared_api["web-only"] = {
        "messages": [],
        "metadata": {"session_id": "web-only"},
        "bundle": "portable-bundle",
    }

    transcript, metadata = load_root_resume(native, "web-root")
    assert transcript[0]["content"] == "authoritative"
    assert metadata["bundle"] == "bundle:portable-bundle"
    assert resolve_root_session_id(native, "web-o") == "web-only"


def test_invalid_common_checkpoint_is_not_replaced_by_native_projection(
    shared_api: dict[str, dict], tmp_path: Path
) -> None:
    """A damaged authority is an error, never permission to use stale native data."""

    native = SessionStore(base_dir=tmp_path / "native")
    native.save(
        "root",
        [{"role": "user", "content": "stale"}],
        {"session_id": "root", "bundle": "bundle:stale"},
    )
    shared_api["root"] = {
        "messages": "not a provider-context list",
        "metadata": {"session_id": "root"},
        "bundle": "portable-bundle",
    }

    with pytest.raises(RuntimeError, match="invalid messages"):
        load_root_resume(native, "root")


@pytest.mark.asyncio
async def test_incremental_root_save_uses_live_held_state_not_native_store() -> None:
    context = MagicMock()
    context.get_messages = AsyncMock(
        return_value=[{"role": "user", "content": "keep tools"}]
    )
    session = MagicMock()
    session.coordinator.get.return_value = context
    native = MagicMock()
    native.get_metadata_if_exists.return_value = {}
    root = MagicMock()
    hook = IncrementalSaveHook(session, native, "root", "bundle:anchors", {}, root)

    await hook.on_tool_post("tool:post", {"tool_name": "read_file"})

    root.checkpoint.assert_called_once()
    native.save.assert_not_called()


@pytest.mark.asyncio
async def test_initializer_acquires_before_context_and_restores_common_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real initializer boundary with a deterministic core fixture."""

    from amplifier_app_cli import session_runner

    order: list[str] = []
    root = MagicMock()
    root.read.return_value = (
        [{"role": "user", "content": "from shared authority"}],
        {"session_id": "root", "bundle": "bundle:anchors"},
    )
    context = MagicMock()
    context.set_messages = AsyncMock()
    context.get_messages = AsyncMock(return_value=[])
    session = MagicMock()
    session.config = {}
    session.coordinator.get.side_effect = lambda key: context if key == "context" else None

    def acquire(session_id: str):
        assert session_id == "root"
        order.append("acquire")
        return root

    async def create_bundle(**_kwargs):
        assert order == ["acquire"]
        order.append("context")
        return session

    config = session_runner.SessionConfig(
        config={},
        search_paths=[],
        verbose=False,
        session_id="root",
        initial_transcript=[{"role": "user", "content": "stale projection"}],
        shared_root=True,
    )
    monkeypatch.setattr(
        "amplifier_app_cli.shared_root_state.SharedRootSession.acquire", acquire
    )
    monkeypatch.setattr(session_runner, "_create_bundle_session", create_bundle)
    with (
        patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
    ):
        initialized = await session_runner.create_initialized_session(config, MagicMock())

    assert order == ["acquire", "context"]
    context.set_messages.assert_awaited_once_with(
        [{"role": "user", "content": "from shared authority"}]
    )
    assert initialized.root_state is root
    session.coordinator.register_capability.assert_any_call("cli.shared_root_state", root)


@pytest.mark.asyncio
async def test_initializer_does_not_create_shared_writer_for_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Children retain their independent persistence and cannot take root locks."""

    from amplifier_app_cli import session_runner

    session = MagicMock()
    session.config = {}
    session.coordinator.get.return_value = None
    config = session_runner.SessionConfig(
        config={"root_session_id": "root"},
        search_paths=[],
        verbose=False,
        session_id="root_delegate",
        shared_root=True,
    )
    acquire = MagicMock()
    monkeypatch.setattr(
        "amplifier_app_cli.shared_root_state.SharedRootSession.acquire", acquire
    )
    monkeypatch.setattr(
        session_runner, "_create_bundle_session", AsyncMock(return_value=session)
    )
    with (
        patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
    ):
        initialized = await session_runner.create_initialized_session(config, MagicMock())

    acquire.assert_not_called()
    assert initialized.root_state is None


@pytest.mark.asyncio
async def test_initialized_cleanup_releases_shared_handle_after_session_cleanup() -> None:
    """Final root cleanup preserves the held lock until normal runtime cleanup ends."""

    from amplifier_app_cli.session_runner import InitializedSession, SessionConfig

    order: list[str] = []
    session = MagicMock()
    session.cleanup = AsyncMock(side_effect=lambda: order.append("cleanup"))
    root = MagicMock()
    root.release.side_effect = lambda: order.append("release")
    initialized = InitializedSession(
        session=session,
        session_id="root",
        config=SessionConfig(config={}, search_paths=[], verbose=False),
        root_state=root,
    )

    await initialized.cleanup()

    assert order == ["cleanup", "release"]


@pytest.mark.asyncio
async def test_failed_runtime_cleanup_retains_shared_handle() -> None:
    """A cleanup failure must not hand an active root to another writer."""

    from amplifier_app_cli.session_runner import InitializedSession, SessionConfig

    session = MagicMock()
    session.cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    root = MagicMock()
    initialized = InitializedSession(
        session=session,
        session_id="root",
        config=SessionConfig(config={}, search_paths=[], verbose=False),
        root_state=root,
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await initialized.cleanup()
    root.release.assert_not_called()
