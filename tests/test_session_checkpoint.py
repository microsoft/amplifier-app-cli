"""Settled canonical checkpoints are not the earlier best-effort autosave."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.session_checkpoint import install_session_checkpoint
from amplifier_app_cli.session_store import SessionStore


def make_session(messages):
    context = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
    capabilities = {}
    coordinator = SimpleNamespace(
        get=lambda name: context if name == "context" else None,
        register_capability=capabilities.__setitem__,
    )
    return SimpleNamespace(coordinator=coordinator), context, capabilities


@pytest.mark.asyncio
async def test_checkpoint_saves_settled_rows_and_current_metadata(tmp_path):
    store = SessionStore(tmp_path)
    rows = [{"role": "user", "content": "first"}]
    session, context, capabilities = make_session(rows)
    install_session_checkpoint(session, store, session_id="owner", bundle="chosen")
    store.save("owner", [], {"name": "new name", "other-owner": {"keep": True}})

    await capabilities["session.durable_checkpoint"]()
    saved, metadata = store.load("owner")
    assert saved == rows
    assert metadata["name"] == "new name"
    assert metadata["other-owner"] == {"keep": True}
    assert metadata["bundle"] == "chosen"

    # Equal length is not a content/prefix witness; explicit checkpoints cannot
    # debounce away a changed canonical result.
    context.get_messages.return_value = [{"role": "user", "content": "changed"}]
    await capabilities["session.durable_checkpoint"]()
    assert store.load("owner")[0][0]["content"] == "changed"


@pytest.mark.asyncio
async def test_checkpoint_uses_actual_root_writer_and_returns_its_receipt():
    session, context, capabilities = make_session([{"role": "user", "content": "x"}])
    store = MagicMock()
    store.get_metadata_if_exists.return_value = {"name": "current"}
    receipt = object()
    root = SimpleNamespace(checkpoint=MagicMock(return_value=receipt))
    install_session_checkpoint(
        session, store, session_id="owner", root_state=root, bundle="selected"
    )
    assert await capabilities["session.durable_checkpoint"]() is receipt
    root.checkpoint.assert_called_once()
    assert root.checkpoint.call_args.args == (store, context.get_messages.return_value)
    assert root.checkpoint.call_args.kwargs["metadata"]["name"] == "current"
    store.save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("fixture"), asyncio.CancelledError()])
async def test_checkpoint_failure_propagates_before_dispatch(failure):
    session, _, capabilities = make_session([])
    store = MagicMock()
    store.get_metadata_if_exists.return_value = {}
    store.save.side_effect = failure
    install_session_checkpoint(session, store, session_id="owner")
    sent = False
    with pytest.raises(type(failure)):
        await capabilities["session.durable_checkpoint"]()
        sent = True
    assert not sent


@pytest.mark.asyncio
async def test_child_checkpoint_is_independently_bound(tmp_path):
    store = SessionStore(tmp_path)
    parent, _, parent_caps = make_session([{"role": "user", "content": "parent"}])
    child, _, child_caps = make_session([{"role": "user", "content": "child"}])
    install_session_checkpoint(parent, store, session_id="parent")
    install_session_checkpoint(child, store, session_id="child")
    assert parent_caps["session.durable_checkpoint"] is not child_caps["session.durable_checkpoint"]
    await child_caps["session.durable_checkpoint"]()
    assert not store.exists("parent")
    assert store.load("child")[0][0]["content"] == "child"


@pytest.mark.asyncio
async def test_checkpoint_without_canonical_context_refuses():
    session, _, capabilities = make_session([])
    session.coordinator.get = lambda name: None
    store = MagicMock()
    install_session_checkpoint(session, store, session_id="owner")
    with pytest.raises(RuntimeError, match="Canonical context"):
        await capabilities["session.durable_checkpoint"]()
    store.save.assert_not_called()