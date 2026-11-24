"""Synthetic native forks: selected closure and actual checkpoint, no inference."""

import io
import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from amplifier_foundation.session.media import MEDIA_NAMESPACE, MediaPolicy, SessionMediaStore, MediaRefusal
from amplifier_foundation.session.checkpoint import verified_checkpoint_refs

from amplifier_app_cli.session_media_fork import fork_native_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "posix", reason="POSIX media owner")]
POLICY = MediaPolicy(1000000, 10000, 10)


async def origin_ref(assets, identity, color, *, expired=False):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), color).save(buffer, "PNG")
    data = buffer.getvalue()
    origin = {"kind": "tool", "session_id": "parent", "id": identity}
    if expired:
        origin.update(created_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
                      expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    ref = (await assets.ingest(data, source_format="image-bytes", origin=origin, policy=None))["value"]
    await assets.publish([ref], origin=origin, disposition="retain")
    return ref, data


def message(ref):
    return {"role": "user", "content": "Observe image", "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}}


async def source(tmp_path):
    native = SessionStore(tmp_path)
    assets = SessionMediaStore(tmp_path / "parent/media", "parent", policy=POLICY,
                               checkpoint_verifier=verified_checkpoint_refs)
    native.bind_media_store("parent", assets)
    first, pixels = await origin_ref(assets, "first", "red")
    later, other = await origin_ref(assets, "later", "blue")
    rows = [message(first), {"role": "assistant", "content": "First reply"}, message(later)]
    native.save("parent", rows, {"bundle": "chosen", "model": "exact-model"})
    native.unbind_media_store("parent", assets)
    assets.close()
    return native, rows, pixels, other


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


async def test_dormant_filtered_fork_is_independent_after_parent_erased(tmp_path):
    native, rows, pixels, other = await source(tmp_path)
    before = snapshot(tmp_path / "parent")
    result = fork_native_session(native, "parent", rows, {"bundle": "chosen"},
                                 turn=1, new_session_id="child", fenced=True)
    copied, metadata = native.load("child")
    ref = copied[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]
    assert ref == {**rows[0]["metadata"][MEDIA_NAMESPACE]["refs"][0], "session_id": "child"}
    assert result.session_id == "child" and len(copied) == 2
    assert metadata["bundle"] == "chosen"
    assert snapshot(tmp_path / "parent") == before
    assert len(list((tmp_path / "child/media/assets").iterdir())) == 1
    assert not (tmp_path / "child/events.jsonl").exists()
    shutil.rmtree(tmp_path / "parent")
    reopened = SessionMediaStore(tmp_path / "child/media", "child", policy=POLICY,
                                 checkpoint_verifier=verified_checkpoint_refs)
    try:
        native.bind_media_store("child", reopened)
        native.acknowledge_saved("child", copied)
        with reopened.resolve_ref(ref) as stream:
            assert stream.read() == pixels and pixels != other
    finally:
        native.unbind_media_store("child", reopened)
        reopened.close()


async def test_fork_collision_and_missing_fence_have_no_target_effect(tmp_path):
    native, rows, _, _ = await source(tmp_path)
    native.save_new("existing", [{"role": "user", "content": "unchanged"}], {})
    before = (tmp_path / "existing/transcript.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        fork_native_session(native, "parent", rows, {}, turn=1, new_session_id="existing", fenced=True)
    assert (tmp_path / "existing/transcript.jsonl").read_bytes() == before
    with pytest.raises(RuntimeError, match="writer ownership"):
        fork_native_session(native, "parent", rows, {}, turn=1, new_session_id="unfenced")
    assert not (tmp_path / "unfenced").exists()


async def test_forged_source_never_reports_fork_success(tmp_path):
    native, rows, _, _ = await source(tmp_path)
    rows[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]["bytes"] += 1
    with pytest.raises((MediaRefusal, ValueError)):
        fork_native_session(native, "parent", rows, {}, turn=1, new_session_id="forged", fenced=True)
    assert not (tmp_path / "forged/transcript.jsonl").exists()


async def test_live_fork_preserves_group_and_expiry_without_new_source_authority(tmp_path):
    native = SessionStore(tmp_path)
    assets = SessionMediaStore(tmp_path / "parent/media", "parent", policy=POLICY,
                               checkpoint_verifier=verified_checkpoint_refs)
    native.bind_media_store("parent", assets)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "yellow").save(buffer, "PNG")
    origin = {"kind": "tool", "session_id": "parent", "id": "same-call"}
    values = assets.ingest_group_sync(
        [buffer.getvalue(), buffer.getvalue()],
        origin=origin, policy=None,
    )["value"]
    await assets.publish(values, origin=origin, disposition="retain")
    rows = [{"role": "user", "content": "Two observations", "metadata": {
        MEDIA_NAMESPACE: {"refs": values},
    }}]
    try:
        native.save("parent", rows, {})
        result = fork_native_session(native, "parent", rows, {}, turn=1,
                                     new_session_id="group-child", fenced=True, source_media=assets)
        refs = result.messages[0]["metadata"][MEDIA_NAMESPACE]["refs"]
        assert refs == [{**ref, "session_id": "group-child"} for ref in values]
        assert all(ref["provenance"] == values[i]["provenance"] for i, ref in enumerate(refs))
        assert len(list((tmp_path / "group-child/media/assets").iterdir())) == 1
    finally:
        native.unbind_media_store("parent", assets)
        assets.close()


async def test_expired_fork_is_warning_only_no_asset_copy_or_renewal(tmp_path, monkeypatch):
    native = SessionStore(tmp_path)
    assets = SessionMediaStore(tmp_path / "parent/media", "parent", policy=POLICY,
                               checkpoint_verifier=verified_checkpoint_refs)
    ref, _ = await origin_ref(assets, "expired", "black", expired=True)
    native.bind_media_store("parent", assets)
    rows = [message(ref)]
    native.save("parent", rows, {})
    native.unbind_media_store("parent", assets)
    assets.close()
    import amplifier_foundation.session.media_conversion as conversion
    import amplifier_foundation.session.media as media
    future = datetime.now(UTC) + timedelta(days=2)
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future
    monkeypatch.setattr(conversion, "datetime", FutureClock)
    monkeypatch.setattr(media, "datetime", FutureClock)
    result = fork_native_session(native, "parent", rows, {}, turn=1,
                                 new_session_id="expired-child", fenced=True)
    row = result.messages[0]
    assert row["metadata"][MEDIA_NAMESPACE]["refs"] == []
    assert "expired" in row["content"]
    assert list((tmp_path / "expired-child/media/assets").iterdir()) == []
    assert native.load("parent")[0][0]["metadata"][MEDIA_NAMESPACE]["refs"] == [ref]


async def test_actual_click_fork_copies_selected_closure_under_real_writer(tmp_path, monkeypatch):
    import click
    from click.testing import CliRunner
    from unittest.mock import AsyncMock
    from amplifier_app_cli.commands import session as commands
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "home/.amplifier"))
    monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", raising=False)
    native, rows, _, _ = await source(tmp_path)
    cli = click.Group()
    commands.register_session_commands(
        cli, interactive_chat=AsyncMock(), execute_single=AsyncMock(), get_module_search_paths=list,
    )
    monkeypatch.setattr(commands, "SessionStore", lambda: native)
    result = CliRunner().invoke(cli, [
        "session", "fork", "parent", "--at-turn", "1", "--name", "actual-child",
    ])
    assert result.exit_code == 0, result.output
    copied, _ = native.load("actual-child")
    assert copied[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]["session_id"] == "actual-child"
    assert len(list((tmp_path / "actual-child/media/assets").iterdir())) == 1
