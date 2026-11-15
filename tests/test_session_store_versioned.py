"""Thin CLI publication against the installed, actual Foundation lifetime owner.

No neighboring source imports, fake ownership, inferred admission or fallback
receipts. A missing transcript_lifetime dependency is a collection failure, not
skipped qualification. All physical namespaces are private pytest temporaries.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import io
import os
import shutil
import sys
import threading

import pytest
from PIL import Image

from amplifier_foundation import sanitize_message
from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
from amplifier_foundation.session.transcript_lifetime import (
    VersionedSessionBinding,
    VersionedSessionConflict,
)
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.transcript_v2 import (
    CommittedPrefix,
    UnsupportedTranscript,
)
from amplifier_foundation.session.transcript_writer import CommitAck
from amplifier_foundation.session.versioned_checkpoint import (
    VersionedCheckpointReceipt,
    VersionedCheckpointRefused,
    verified_versioned_checkpoint_refs,
)

from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Prospective Foundation namespace custody is Linux-only"
)


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def prefix(directory):
    with open_committed_source(directory) as source:
        result = source.commit
    return result


@pytest.fixture
def owner(tmp_path):
    parent = tmp_path / "sessions"
    parent.mkdir(mode=0o700)
    binding = VersionedSessionBinding.create_new(
        parent / "root", session_id="root", operation_id="allocate", admit=lambda: None,
    )
    try:
        yield binding
    finally:
        binding.close()


@contextmanager
def owned_media(binding):
    media = SessionMediaStore(
        binding.directory / "media", binding.session_id,
        checkpoint_verifier=verified_versioned_checkpoint_refs,
    )
    try:
        yield media
    finally:
        media.close()


def staged_image(media, image_format):
    payload = io.BytesIO()
    Image.new("RGB", (3, 2), (12, 31, 87)).save(payload, format=image_format)
    raw = payload.getvalue()
    origin = {"kind": "upload", "session_id": media.session_id, "id": "actual-upload"}
    ref = media.ingest_sync(
        raw, source_format="image-bytes", origin=origin, policy=None,
    )["value"]
    return raw, ref, origin


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_actual_publish_save_typed_ack_resolve_and_cold_reopen(owner, image_format):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    initial = owner.expected
    with owned_media(owner) as media:
        store.bind_media_store("root", media)
        raw, ref, origin = staged_image(media, image_format)
        rows = [{"role": "user", "content": "inspect", "metadata": {
            "experimental.media.v1": {"refs": [ref]},
        }}]
        untouched = deepcopy(rows)
        admission = []

        def admit():
            # The actual caller supplies origin and publishes BEFORE descriptor
            # reachability. SessionStore must neither infer nor read that origin.
            assert prefix(owner.directory) == initial
            result = media.publish_sync([ref], origin=origin, disposition="retain")
            assert result["publication"]["durability"] == "assets-only"
            admission.append(result)

        receipt = store.save_versioned(
            "root", rows, {"session_id": "root", "api_key": "not-on-disk"},
            expected=initial, operation_id="image-save", admit=admit,
        )
        assert type(receipt) is VersionedCheckpointReceipt
        assert receipt is owner.last_receipt
        assert receipt.verify()["operation_id"] == "image-save"
        assert len(admission) == 1
        assert rows == untouched
        with media.resolve_ref(ref) as stream:
            assert hashlib.sha256(stream.read()).digest() == hashlib.sha256(raw).digest()
        loaded, metadata = store.load("root")
        assert loaded[0]["metadata"] == rows[0]["metadata"]
        assert metadata["api_key"] == "[REDACTED]"
        current = owner.expected
        assert current == prefix(owner.directory) != initial
    directory = owner.directory
    owner.close()

    reopened = VersionedSessionBinding.resume(directory, session_id="root", expected=current)
    try:
        resumed = SessionStore(directory.parent, canonical_binding=reopened)
        with owned_media(reopened) as fresh_media:
            resumed.bind_media_store("root", fresh_media)
            with pytest.raises(MediaRefusal):
                fresh_media.verify_checkpoint(ref)  # Persisted facts are not authority.
            before = snapshot(directory / "transcript.v2")
            fresh_receipt = resumed.acknowledge_versioned("root", expected=current)
            assert type(fresh_receipt) is VersionedCheckpointReceipt
            assert fresh_receipt is not receipt
            assert fresh_receipt is reopened.last_receipt
            assert before == snapshot(directory / "transcript.v2")
            assert reopened.expected == current
            with fresh_media.resolve_ref(ref) as stream:
                assert stream.read() == raw
            assert resumed.load("root")[0] == loaded
    finally:
        reopened.close()


def test_save_returns_own_receipt_when_successor_publishes_after_real_release(owner, monkeypatch):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    with owned_media(owner) as media:
        store.bind_media_store("root", media)
        _, ref, origin = staged_image(media, "PNG")
        rows = [{"role": "user", "content": "first", "metadata": {
            "experimental.media.v1": {"refs": [ref]},
        }}]
        initial = owner.expected
        actual_publication = owner.publication
        first_released = threading.Event()
        successor_finished = threading.Event()
        first_issued, results, errors = [], [], []

        @contextmanager
        def pause_only_after_real_release():
            # Delegate every ownership check, lock and exit to the real binding.
            with actual_publication() as check:
                yield check
                if threading.current_thread() is first:
                    first_issued.append(owner.last_receipt)
            if threading.current_thread() is first:
                # Only widen the unlocked scheduling window. The successor
                # must acquire and publish through the same real owner itself.
                first_released.set()
                assert successor_finished.wait(10), "successor did not finish"

        def save_first():
            try:
                results.append(store.save_versioned(
                    "root", rows, {}, expected=initial, operation_id="first",
                    admit=lambda: media.publish_sync([ref], origin=origin, disposition="retain"),
                ))
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=save_first, name="first-publication")
        with monkeypatch.context() as patch:
            patch.setattr(owner, "publication", pause_only_after_real_release)
            first.start()
            try:
                assert first_released.wait(10), "first did not release real publication"
                assert first_issued[0].verify()["operation_id"] == "first"
                second = store.save_versioned(
                    "root", [*rows, {"role": "assistant", "content": "second"}], {},
                    expected=owner.expected, operation_id="second",
                    admit=lambda: media.validate_published_group([ref], origin=origin),
                )
                assert second.verify()["operation_id"] == "second"
            finally:
                successor_finished.set()
                first.join(10)
        assert not first.is_alive()
        assert errors == []
        assert len(results) == 1
        assert results[0] is first_issued[0]
        assert results[0] is not second
        assert results[0].operation_id == "first"
        assert second is owner.last_receipt
        assert second.sequence == results[0].sequence + 1
        assert prefix(owner.directory).operation_id == "second"


def test_nonmedia_save_returns_actual_commit_ack_and_exact_effective_rows(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    rows = [
        {"role": "system", "content": "private system"},
        {"role": "developer", "content": "private developer"},
        {"role": "user", "content": "hello", "timestamp": "fixed-user"},
        {"role": "assistant", "content": "answer", "timestamp": "fixed-assistant",
         "thinking_block": {"text": "retained thinking"}},
    ]
    original = deepcopy(rows)
    ack = store.save_versioned(
        "root", rows, {"turn_count": 1}, expected=owner.expected,
        operation_id="full-rewrite", admit=lambda: None,
    )
    assert type(ack) is CommitAck and ack is owner.last_result
    assert ack.commit == owner.expected == prefix(owner.directory)
    assert store.load("root")[0] == [sanitize_message(row) for row in rows[2:]]
    assert owner.load_messages() == store.load("root")[0]
    assert rows == original
    assert store.has_transcript("root")
    assert store.list_sessions() == ["root"]


def test_metadata_updates_and_stale_save_preserve_canonical_name(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    rows = [{"role": "user", "content": "hello"}]
    store.save_versioned("root", rows, {}, expected=owner.expected,
                         operation_id="first", admit=lambda: None)
    saved = snapshot(owner.directory / "transcript.v2")
    store.rename("root", "Chosen by user")
    store.update_metadata("root", {"turn_count": 4})
    assert snapshot(owner.directory / "transcript.v2") == saved
    store.save_versioned("root", rows, {"name": "stale", "turn_count": 5},
                         expected=owner.expected, operation_id="second", admit=lambda: None)
    metadata = store.get_metadata("root")
    assert metadata["name"] == "Chosen by user"
    assert metadata["turn_count"] == 5


@pytest.mark.parametrize("entry", ["save", "save_new", "_save_transcript", "reserve_session", "acknowledge_saved"])
def test_bound_store_refuses_every_legacy_path_before_any_write(owner, entry):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    before = snapshot(owner.directory.parent)
    absent = owner.directory.parent / "must-not-appear"
    if entry == "_save_transcript":
        args = (absent, [])
    elif entry == "reserve_session":
        args = ("must-not-appear",)
    elif entry == "acknowledge_saved":
        args = ("root", [])
    else:
        args = ("must-not-appear", [], {})
    with pytest.raises(ValueError, match="explicit versioned"):
        getattr(store, entry)(*args)
    assert snapshot(owner.directory.parent) == before
    assert not absent.exists()


@pytest.mark.parametrize("impostor", [True, {}, object()])
def test_constructor_refuses_nonbinding_before_mkdir(tmp_path, impostor):
    absent = tmp_path / "not-created"
    with pytest.raises(TypeError, match="actual live"):
        SessionStore(absent, canonical_binding=impostor)
    assert not absent.exists()


def test_constructor_exact_path_and_live_process_checked_before_writes(owner, tmp_path, monkeypatch):
    absent = tmp_path / "wrong-parent"
    with pytest.raises(ValueError, match="base_dir"):
        SessionStore(absent, canonical_binding=owner)
    assert not absent.exists()
    with monkeypatch.context() as patch:
        patch.setattr(owner, "_pid", os.getpid() + 1)
        with pytest.raises(VersionedSessionConflict, match="another process"):
            SessionStore(absent, canonical_binding=owner)
        assert not absent.exists()
    owner.close()
    with pytest.raises(VersionedSessionConflict, match="closed"):
        SessionStore(absent, canonical_binding=owner)
    assert not absent.exists()


@pytest.mark.parametrize("missing", ["expected", "operation_id", "admit"])
def test_explicit_save_arguments_are_required(owner, missing):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    options = {"expected": owner.expected, "operation_id": "save", "admit": lambda: None}
    del options[missing]
    before = snapshot(owner.directory)
    with pytest.raises(TypeError):
        store.save_versioned("root", [], {}, **options)
    assert snapshot(owner.directory) == before


@pytest.mark.parametrize("selector", [True, {}, "receipt"])
def test_data_is_not_selector_or_receipt_authority(owner, selector):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    before = snapshot(owner.directory)
    with pytest.raises(TypeError, match="CommittedPrefix"):
        store.save_versioned("root", [], {}, expected=selector,
                             operation_id="invalid", admit=lambda: pytest.fail("admission ran"))
    assert snapshot(owner.directory) == before


def test_exact_cas_and_session_identity_refuse_before_admission(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    original = owner.expected
    before = snapshot(owner.directory)
    with pytest.raises(ValueError, match="identity"):
        store.save_versioned("other", [], {}, expected=owner.expected,
                             operation_id="other", admit=lambda: pytest.fail("admission ran"))
    stale = replace(owner.expected, sequence=owner.expected.sequence + 1)
    with pytest.raises(VersionedSessionConflict, match="commit changed"):
        store.save_versioned("root", [], {}, expected=stale,
                             operation_id="stale", admit=lambda: pytest.fail("admission ran"))
    assert snapshot(owner.directory) == before
    store.save_versioned("root", [], {}, expected=original,
                         operation_id="actual-successor", admit=lambda: None)
    after = snapshot(owner.directory)
    with pytest.raises(VersionedSessionConflict, match="commit changed"):
        store.save_versioned("root", [], {}, expected=original,
                             operation_id="actual-stale", admit=lambda: pytest.fail("admission ran"))
    assert snapshot(owner.directory) == after


def test_closed_owner_refuses_save_before_admission_or_metadata(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    selected = owner.expected
    owner.close()
    before = snapshot(owner.directory)
    with pytest.raises(VersionedSessionConflict, match="closed"):
        store.save_versioned("root", [], {}, expected=selected, operation_id="closed",
                             admit=lambda: pytest.fail("admission ran"))
    assert snapshot(owner.directory) == before


def test_standalone_ack_requires_selector_and_actual_bound_media(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    before = snapshot(owner.directory)
    with pytest.raises(TypeError):
        store.acknowledge_versioned("root")
    with pytest.raises(ValueError, match="bound media"):
        store.acknowledge_versioned("root", expected=owner.expected)
    assert snapshot(owner.directory) == before


@pytest.mark.parametrize("admit", [None, True, {}])
def test_admission_must_be_actual_callback(owner, admit):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    before = snapshot(owner.directory)
    with pytest.raises(TypeError, match="admission"):
        store.save_versioned("root", [], {}, expected=owner.expected,
                             operation_id="bad-admit", admit=admit)
    assert snapshot(owner.directory) == before


def test_wrong_actual_media_namespace_fails_before_descriptor_reachability(owner, tmp_path):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    wrong = SessionMediaStore(tmp_path / "detached-media", "root",
                              checkpoint_verifier=verified_versioned_checkpoint_refs)
    try:
        store.bind_media_store("root", wrong)
        initial = owner.expected
        with pytest.raises(VersionedSessionConflict, match="source-bound"):
            store.save_versioned("root", [], {}, expected=initial,
                                 operation_id="wrong-media", admit=lambda: pytest.fail("admission ran"))
        assert prefix(owner.directory) == initial
        assert not (owner.directory / "metadata.json").exists()
    finally:
        wrong.close()


def test_unpublished_assets_are_not_admitted_by_inspecting_records(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    with owned_media(owner) as media:
        store.bind_media_store("root", media)
        _, ref, _ = staged_image(media, "PNG")
        initial = owner.expected
        with pytest.raises(MediaRefusal):
            store.save_versioned(
                "root", [{"role": "user", "content": "not published", "metadata": {
                    "experimental.media.v1": {"refs": [ref]},
                }}], {}, expected=initial, operation_id="unpublished", admit=lambda: None,
            )
        assert prefix(owner.directory) == initial
        assert not (owner.directory / "metadata.json").exists()


def test_forged_receipts_are_not_checkpoint_authority(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    with owned_media(owner) as media:
        store.bind_media_store("root", media)
        receipt = store.save_versioned("root", [], {}, expected=owner.expected,
                                       operation_id="empty", admit=lambda: None)
        before = snapshot(owner.directory)
        for forged in (receipt.verify(), replace(receipt), asdict(owner.expected), True):
            with pytest.raises((VersionedCheckpointRefused, MediaRefusal)):
                media.acknowledge_checkpoint(forged)
        with pytest.raises(TypeError, match="CommittedPrefix"):
            store.acknowledge_versioned("root", expected=receipt)
        assert before == snapshot(owner.directory)


def test_metadata_failure_preserves_exact_new_prefix_and_blocks_replay(owner, monkeypatch):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    initial = owner.expected
    error = OSError("synthetic metadata failure")
    calls = []

    def fail_metadata(*args):
        assert prefix(owner.directory) == owner.expected != initial
        raise error

    monkeypatch.setattr(store, "_save_metadata", fail_metadata)
    with pytest.raises(OSError) as failed:
        store.save_versioned("root", [{"role": "user", "content": "settled"}], {},
                             expected=initial, operation_id="settled", admit=lambda: calls.append("admit"))
    assert failed.value is error and store.versioned_publication_failure is error
    committed = owner.expected
    assert type(committed) is CommittedPrefix and committed == prefix(owner.directory)
    before = snapshot(owner.directory)
    for operation, selected in (("settled", initial), ("new-operation", committed)):
        with pytest.raises(RuntimeError, match="owner reconciliation") as retained:
            store.save_versioned("root", [], {}, expected=selected,
                                 operation_id=operation, admit=lambda: calls.append("REPLAY"))
        assert retained.value.__cause__ is error
    assert calls == ["admit"]
    assert before == snapshot(owner.directory)
    assert store.load("root")[0][0]["content"] == "settled"


def test_late_checkpoint_failure_returns_no_receipt_and_does_not_replay(owner, monkeypatch):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    with owned_media(owner) as media:
        store.bind_media_store("root", media)
        raw, ref, origin = staged_image(media, "PNG")
        rows = [{"role": "user", "content": "image", "metadata": {
            "experimental.media.v1": {"refs": [ref]},
        }}]
        initial = owner.expected
        receipts, admissions = [], []
        actual_ack = media.acknowledge_checkpoint
        actual_check = owner.check
        error = OSError("synthetic late checkpoint fence failure")
        armed = False

        def acknowledge_then_arm(receipt):
            nonlocal armed
            result = actual_ack(receipt)
            receipts.append(receipt)
            armed = True
            return result

        def late_check():
            actual_check()
            if armed:
                raise error

        def admit():
            admissions.append("publish")
            media.publish_sync([ref], origin=origin, disposition="retain")

        with monkeypatch.context() as patch:
            patch.setattr(media, "acknowledge_checkpoint", acknowledge_then_arm)
            patch.setattr(owner, "check", late_check)
            with pytest.raises((OSError, VersionedCheckpointRefused)):
                store.save_versioned("root", rows, {}, expected=initial,
                                     operation_id="late", admit=admit)
        assert len(receipts) == 1 and admissions == ["publish"]
        with pytest.raises(VersionedCheckpointRefused):
            receipts[0].verify()
        actual = prefix(owner.directory)
        assert actual != initial and actual.operation_id == "late"
        assert owner.expected == initial  # Owner must explicitly reconcile intent.
        assert owner.last_receipt is None
        assert not (owner.directory / "metadata.json").exists()
        before = snapshot(owner.directory)
        with pytest.raises(VersionedSessionConflict, match="reconciliation"):
            store.save_versioned("root", rows, {}, expected=initial,
                                 operation_id="replacement", admit=admit)
        assert before == snapshot(owner.directory) and admissions == ["publish"]
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]


def test_committed_reader_ignores_uncommitted_tail_without_repair(owner):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    ack = store.save_versioned("root", [{"role": "user", "content": "committed"}], {},
                               expected=owner.expected, operation_id="prefix", admit=lambda: None)
    body = owner.directory / "transcript.v2" / ack.commit.body
    with body.open("ab") as stream:
        stream.write(b'{"role":"user","content":"uncommitted"}\nnot-json\x00')
    before = snapshot(owner.directory)
    assert store.has_transcript("root")
    assert [row["content"] for row in store.load("root")[0]] == ["committed"]
    assert before == snapshot(owner.directory)


@pytest.mark.parametrize("damage", ["descriptor", "body", "missing-descriptor"])
def test_damaged_v2_never_falls_back_to_complete_legacy_backup(owner, damage):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    ack = store.save_versioned("root", [{"role": "user", "content": "committed"}], {},
                               expected=owner.expected, operation_id="prefix", admit=lambda: None)
    directory = owner.directory
    owner.close()
    backup = directory / "transcript.jsonl.backup"
    shutil.rmtree(backup)
    backup.write_bytes(b'{"role":"user","content":"stale legacy"}\n')
    if damage == "descriptor":
        (directory / "transcript.v2" / "current.json").write_bytes(b"broken")
    elif damage == "missing-descriptor":
        (directory / "transcript.v2" / "current.json").unlink()
    else:
        (directory / "transcript.v2" / ack.commit.body).write_bytes(b"broken")
    before = snapshot(directory)
    reader = SessionStore(directory.parent)
    assert reader.has_transcript("root")
    with pytest.raises(UnsupportedTranscript):
        reader.load("root")
    assert snapshot(directory) == before


def test_v2_namespace_without_legacy_filenames_is_still_not_absence(tmp_path):
    store = SessionStore(tmp_path)
    directory = tmp_path / "damaged"
    directory.mkdir()
    (directory / "transcript.v2").symlink_to(directory / "missing", target_is_directory=True)
    assert store.has_transcript("damaged")
    with pytest.raises(UnsupportedTranscript):
        store.load("damaged")


def test_ordinary_legacy_saves_remain_unbound_and_unchanged(tmp_path):
    store = SessionStore(tmp_path)
    assert store.save("legacy", [{"role": "user", "content": "ordinary"}], {}) is None
    assert store.save_new("new", [], {"name": "new"}) is None
    store._save_transcript(tmp_path / "legacy", [{"role": "assistant", "content": "updated"}])
    assert store.load("legacy")[0][0]["content"] == "updated"
    assert store.load("new") == ([], {"name": "new"})
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="No live"):
        store.save_versioned("legacy", [], {}, expected=None,
                             operation_id="no-owner", admit=lambda: None)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("maintenance", ["cleanup", "policy-cleanup", "config-snapshot"])
def test_bound_store_refuses_legacy_maintenance_before_effects(owner, maintenance, monkeypatch):
    store = SessionStore(owner.directory.parent, canonical_binding=owner)
    store._save_metadata(owner.directory, {"session_id": "root", "name": "retained"})
    sibling = owner.directory.parent / "independent"
    sibling.mkdir()
    (sibling / "kept").write_text("independent source", encoding="utf-8")
    before = snapshot(owner.directory.parent)
    calls = []
    monkeypatch.setattr(
        "amplifier_app_cli.session_store.shutil.rmtree",
        lambda path: calls.append(path),
    )
    with pytest.raises(ValueError, match="explicit versioned publication"):
        if maintenance == "config-snapshot":
            store.save_config_snapshot("root", {"model": "not-published"})
        else:
            store.cleanup_old_sessions(
                days=0,
                remove_session=(lambda path: calls.append(path) or True)
                if maintenance == "policy-cleanup" else None,
            )
    assert calls == []
    assert snapshot(owner.directory.parent) == before
    owner.check()
