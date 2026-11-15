"""Candidate Foundation integration; synthetic images and native files only."""

import io
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

media = pytest.importorskip("amplifier_foundation.session.media")
checkpoint = pytest.importorskip("amplifier_foundation.session.checkpoint")
Image = pytest.importorskip("PIL.Image")

from amplifier_app_cli.session_checkpoint import install_session_checkpoint
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.shared_root_state import SharedRootSession

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.name != "posix", reason="Foundation legacy ACK is POSIX-only"),
]


@pytest.fixture
def stores(tmp_path):
    native = SessionStore(tmp_path)
    assets = media.SessionMediaStore(
        tmp_path / "root" / "media",
        "root",
        policy=media.MediaPolicy(1024 * 1024, 10000, 10),
        checkpoint_verifier=checkpoint.verified_checkpoint_refs,
    )
    native.bind_media_store("root", assets)
    yield native, assets
    native.unbind_media_store("root", assets)
    assets.close()


async def published(assets):
    output = io.BytesIO()
    Image.new("RGB", (3, 2), "purple").save(output, format="PNG")
    data = output.getvalue()
    origin = {"kind": "tool", "session_id": "root", "id": "actual-tool-call"}
    ref = (await assets.ingest(
        data, source_format="image-bytes", origin=origin, policy=None
    ))["value"]
    await assets.publish([ref], origin=origin, disposition="retain")
    return ref, data


def rows_for(ref):
    return [{
        "role": "tool", "tool_call_id": "actual-tool-call",
        "content": "Saved observation; pixels are not in canonical history",
        "metadata": {
            media.MEDIA_NAMESPACE: {"refs": [ref]},
            "provider-owned": {"signature": "opaque-preserved"},
        },
    }]


async def test_assets_first_then_actual_checkpoint_and_resume(stores):
    native, assets = stores
    ref, data = await published(assets)
    with pytest.raises(media.MediaRefusal):
        assets.resolve_ref(ref)
    rows = rows_for(ref)
    held = SimpleNamespace(check=MagicMock())
    root = SharedRootSession("root", native.base_dir, held)
    receipt = root.checkpoint(native, rows, bundle="chosen", metadata={})
    assert receipt.kind == "legacy-checkpoint-backed-v1"
    held.check.assert_called_once()
    with assets.resolve_ref(ref) as stream:
        assert stream.read() == data
    assert native.load("root")[0] == rows
    # No image bytes in canonical rows or backups/metadata/journals.
    for path in (native.base_dir / "root").rglob("*.json*"):
        assert data not in path.read_bytes()
    assert native.load("root")[0][0]["metadata"]["provider-owned"]["signature"] == "opaque-preserved"

    assets.close()
    reopened = media.SessionMediaStore(
        native.base_dir / "root" / "media", "root", policy=assets.policy,
        checkpoint_verifier=checkpoint.verified_checkpoint_refs,
    )
    native.unbind_media_store("root", assets)
    native.bind_media_store("root", reopened)
    try:
        with pytest.raises(media.MediaRefusal):
            reopened.resolve_ref(ref)
        # Resume refreshes current actual primary; no serialized receipt becomes
        # authority and no tool/image read is repeated.
        native.acknowledge_saved("root", rows)
        with reopened.resolve_ref(ref) as stream:
            assert stream.read() == data
    finally:
        native.unbind_media_store("root", reopened)
        reopened.close()


async def test_every_bound_save_refreshes_exact_source_receipt(stores):
    native, assets = stores
    ref, data = await published(assets)
    rows = rows_for(ref)
    first = native.save("root", rows, {})
    rows.append({"role": "assistant", "content": "settled"})
    second = native.save("root", rows, {})
    assert first.sha256 != second.sha256
    with pytest.raises(checkpoint.CheckpointRefused):
        first.verify()
    with assets.resolve_ref(ref) as stream:
        assert stream.read() == data
    # A metadata-only save must not invalidate the canonical source.
    native.update_metadata("root", {"name": "renamed"})
    second.verify()
    with assets.resolve_ref(ref) as stream:
        assert stream.read() == data


async def test_failed_ack_prevents_send_without_tool_reexecution(stores, monkeypatch):
    native, assets = stores
    ref, _ = await published(assets)
    rows = rows_for(ref)
    calls = {"tool": 1, "provider": 0}

    async def messages():
        return rows

    caps = {}
    session = SimpleNamespace(coordinator=SimpleNamespace(
        get=lambda name: SimpleNamespace(get_messages=messages) if name == "context" else None,
        register_capability=caps.__setitem__,
    ))
    install_session_checkpoint(session, native, session_id="root")

    def fail(*args, **kwargs):
        raise checkpoint.CheckpointRefused("synthetic flush failure")

    monkeypatch.setattr(checkpoint, "acknowledge_legacy_checkpoint", fail)
    with pytest.raises(checkpoint.CheckpointRefused):
        await caps["session.durable_checkpoint"]()
        calls["provider"] += 1
    assert calls == {"tool": 1, "provider": 0}
    # The settled row may be present after failed flush; that is not an ACK.
    assert native.load("root")[0] == rows
    with pytest.raises(media.MediaRefusal):
        assets.resolve_ref(ref)


async def test_ack_matches_effective_sanitized_role_filtered_rows(stores):
    native, assets = stores
    ref, _ = await published(assets)
    rows = [{"role": "system", "content": "ephemeral host prompt"}, *rows_for(ref)]
    receipt = native.save("root", rows, {})
    assert receipt.message_count == 1
    assert list(checkpoint.verified_checkpoint_refs(receipt)) == [ref]
    altered = json.loads(json.dumps(rows_for(ref)))
    altered[0]["metadata"][media.MEDIA_NAMESPACE]["refs"][0]["v"] = True
    with pytest.raises(checkpoint.CheckpointRefused):
        native.acknowledge_saved("root", altered)