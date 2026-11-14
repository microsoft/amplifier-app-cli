"""Real Click/installed Foundation integration, exclusively owned synthetic files.

The fixture's flock is established BEFORE namespace creation or any factory/FD.
It owns every writer in this fixture process; this is NOT proof of quiescence of
live historical writers, nor an enrollment mechanism for old sessions.
"""

import base64
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
from contextlib import contextmanager
from unittest.mock import AsyncMock

import click
import pytest
from click.testing import CliRunner
from PIL import Image
from amplifier_foundation.session.checkpoint import verified_checkpoint_refs
from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.media import MediaPolicy, MediaRefusal, SessionMediaStore

from amplifier_app_cli.commands.session import register_session_commands
from amplifier_app_cli.commands import session_storage
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX format owner")


def cli_for(tmp_path, monkeypatch):
    native = SessionStore(tmp_path / "sessions")
    cli = click.Group()
    register_session_commands(cli, interactive_chat=AsyncMock(), execute_single=AsyncMock(), get_module_search_paths=list)
    import amplifier_app_cli.commands.session as commands
    monkeypatch.setattr(commands, "SessionStore", lambda: native)
    return cli, native


def test_storage_preview_exact_primary_no_recovery_or_content_write(tmp_path, monkeypatch):
    cli, native = cli_for(tmp_path, monkeypatch)
    native.save("root", [{"role": "user", "content": "private synthetic body"}], {})
    folder = native.base_dir / "root"
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    events = []

    class Held:
        def check(self): events.append("check")
    class Root:
        held = Held()
        def release(self): events.append("release")
    monkeypatch.setattr(session_storage.SharedRootSession, "acquire", lambda identity: Root())
    result = CliRunner().invoke(cli, ["session", "storage", "preview", "root"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["state"] == "preview-only-not-applied"
    assert report["plan"]["records"] == 1 and report["plan"]["inline_images"] == 0
    assert "private synthetic body" not in result.output
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert events == ["check", "check", "release"]


def test_preview_refuses_pending_without_read_repair(tmp_path, monkeypatch):
    cli, native = cli_for(tmp_path, monkeypatch)
    native.save("root", [{"role": "user", "content": "safe"}], {})
    marker = native.base_dir / "root/transcript.jsonl.append-pending"
    marker.write_text("unsettled")
    result = CliRunner().invoke(cli, ["session", "storage", "preview", "root"])
    assert result.exit_code == 1 and "Pending" in result.output
    assert marker.read_text() == "unsettled"


def test_storage_surface_has_held_workflow_and_read_controls(tmp_path, monkeypatch):
    cli, _ = cli_for(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli, ["session", "storage", "--help"])
    assert result.exit_code == 0
    assert "preview" in result.output and "verify-export" in result.output
    assert "\n  apply " in result.output and "\n  resume " in result.output
    failed = CliRunner().invoke(cli, ["session", "storage", "reconcile", str(tmp_path / "unknown")])
    assert failed.exit_code == 0
    assert json.loads(failed.output)["state"] == "interrupted-preserve-and-inspect-journal"
    assert not (tmp_path / "unknown").exists()


class FixtureMaintenance:
    """Actual fixture custody; no production/live-old-writer claims."""

    def __init__(self, tmp_path):
        import fcntl

        self.directory = tmp_path / "sessions/root"
        self.session_id = "root"
        self.path = tmp_path / "fixture-writer-lease"
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.pid = os.getpid()
        self.closed = False
        self.checks = 0

    def check(self):
        import fcntl

        if self.closed or self.pid != os.getpid():
            raise ValueError("Fixture custody is not live in this process")
        named, opened = self.path.stat(), os.fstat(self.fd)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Fixture lease was replaced")
        # A competing factory cannot open this fixture writer cohort.
        probe = os.open(self.path, os.O_RDWR)
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError("Fixture maintenance exclusion was lost")
        finally:
            os.close(probe)
        self.checks += 1

    def close(self):
        self.check()
        os.close(self.fd)
        self.closed = True


class NativeFixture:
    def __init__(self, tmp_path):
        self.maintenance = FixtureMaintenance(tmp_path)
        self.maintenance.check()  # Before namespaces, native/media or primary FDs.
        self.directory = self.maintenance.directory
        self.directory.parent.mkdir(mode=0o700)
        self.directory.mkdir(mode=0o700)
        self.native = SessionStore(self.directory.parent)
        self.source = self.directory / "transcript.jsonl"
        self.originals = {}
        self.permissions = {"preview", "prepare", "apply", "resume", "export"}
        self.permission_checks = []
        self.revoke_after = None
        self.fail_ack = False
        self.store_calls = 0
        self.open_media()
        self.cli = click.Group()
        self.register()

    def open_media(self, *, policy=None):
        self.maintenance.check()

        def verifier(receipt):
            receipt.verify()  # The actual source-bound owner receipt, never JSON.
            if self.fail_ack:
                raise RuntimeError("Fixture ACK unavailable; preserve installed bytes")
            yield from verified_checkpoint_refs(receipt)

        self.media = SessionMediaStore(
            self.directory / "media", "root",
            policy=policy or MediaPolicy(1024 * 1024, 10000, 10),
            checkpoint_verifier=verifier,
        )
        self.native.bind_media_store("root", self.media)

    def reopen(self):
        policy = self.media.policy
        self.native.unbind_media_store("root", self.media)
        self.media.close()
        self.open_media(policy=policy)

    def store_factory(self):
        self.maintenance.check()
        self.store_calls += 1
        return self.native

    def permission(self, purpose):
        self.maintenance.check()
        self.permission_checks.append(purpose)
        if purpose not in self.permissions:
            raise ValueError("Fixture selected-source/purpose permission revoked")
        if self.revoke_after is not None:
            self.revoke_after -= 1
            if self.revoke_after == 0:
                self.permissions.discard(purpose)
                raise ValueError("Fixture selected-source/purpose permission revoked")

    def origin_for(self, record, block):
        if self.media.policy.retention != "durable":
            raise ValueError("Fixture request-only observation is not durable permission")
        try:
            return self.originals[record, block].copy()
        except KeyError:
            raise ValueError("HELD: unknown original provenance/retention") from None

    @contextmanager
    def asset_reader(self, ref):
        self.maintenance.check()
        # Public owner journal, not a hash/path-only file reader or fabricated
        # retention bit. This fixture cohort is tiny; production hosts own their
        # own indexed selected-source/retention authority.
        records = [r for r in self.media.journal()
                   if r.get("ref") == ref and r.get("state") in {"published", "committed"}]
        if not records or any(r.get("retention") != "durable" for r in records):
            raise ValueError("Fixture request-only or unknown retained publication")
        with self.media.resolve_published_ref(ref) as stream:
            yield stream

    @contextmanager
    def admission_factory(self, native, *, session_id, source, purpose):
        self.maintenance.check()
        self.permission(purpose)
        yield session_storage._StorageAdmission(
            native=native, session_id=session_id, source=source,
            maintenance=self.maintenance, media_store=self.media,
            check_permission=self.permission, origin_for=self.origin_for,
            asset_reader=self.asset_reader,
        )
        self.maintenance.check()

    def register(self, admission_factory="fixture"):
        self.cli = click.Group()
        session_storage.register_session_storage_commands(
            self.cli, store_factory=self.store_factory,
            admission_factory=self.admission_factory if admission_factory == "fixture" else admission_factory,
        )

    def write(self, rows):
        self.maintenance.check()
        SessionHistoryStore(self.directory).save_messages(rows)

    def images(self, formats=("PNG", "JPEG"), *, attested=True):
        blocks, pixels = [], []
        for index, format in enumerate(formats):
            output = io.BytesIO()
            Image.new("RGB", (3 + index, 2), "purple").save(output, format=format)
            data = output.getvalue()
            pixels.append(data)
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": f"image/{'jpeg' if format == 'JPEG' else 'png'}",
                "data": base64.b64encode(data).decode(),
            }, "visibility": ["assistant"]})
            if attested:
                self.originals[0, index] = {
                    "kind": "upload", "session_id": "root", "id": f"actual-original-{index}",
                    "created_at": "2024-02-03T04:05:06+00:00", "expires_at": None,
                }
        self.write([{"role": "user", "content": blocks, "metadata": {"opaque": "keep"}}])
        return pixels

    def invoke(self, *args, ok=True):
        result = CliRunner().invoke(self.cli, ["storage", *map(str, args)])
        if ok:
            assert result.exit_code == 0, (result.output, result.exception)
            return json.loads(result.output)
        assert result.exit_code != 0, result.output
        return result

    def expected(self, tmp_path, name="expected.json"):
        report = self.invoke("preview", "root")
        path = tmp_path / name
        path.write_text(json.dumps(report))
        return path

    def close(self):
        self.native.unbind_media_store("root", self.media)
        self.media.close()
        self.maintenance.close()


@pytest.fixture
def owned(tmp_path):
    fixture = NativeFixture(tmp_path)
    try:
        yield fixture
    finally:
        fixture.close()


def snapshots(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_native_v1_png_jpeg_prepare_apply_resume_export_real_ack(owned, tmp_path):
    pixels = owned.images()
    source_before = owned.source.read_bytes()
    original_stamp = owned.source.stat()
    expected = owned.expected(tmp_path)
    operation = tmp_path / "stable-operation"
    prepared = owned.invoke("prepare", "root", operation, "--expected-plan", expected, "--export-assets")
    assert prepared["state"] == "verified-candidate-not-applied"
    assert prepared["optimization"] == "inline-images-externalized"
    assert prepared["images"] == 2 and prepared["inline_removed_bytes"] > 0
    assert prepared["checkpoint_acknowledged"] is False
    assert owned.source.read_bytes() == source_before
    assert (operation / "original-0").read_bytes() == source_before
    candidate = json.loads((operation / "candidate.jsonl").read_text())
    refs = candidate["metadata"]["experimental.media.v1"]["refs"]
    assert candidate["metadata"]["opaque"] == "keep"
    assert [r["provenance"] for r in refs] == [owned.originals[0, i] for i in range(2)]
    for ref, data in zip(refs, pixels):
        with pytest.raises(MediaRefusal):
            owned.media.resolve_ref(ref)  # Assets-only is not canonical ACK.
        with owned.media.resolve_published_ref(ref) as stream:
            assert stream.read() == data

    exported = owned.invoke("export", "root", operation)
    assert Path(exported["portable"]) == operation / "portable"
    assert exported["checkpoint_acknowledged"] is False
    assert set(p.name for p in (operation / "portable").iterdir()) == {
        "candidate.jsonl", "assets", "manifest.sqlite", "export.json"}
    before_status = snapshots(tmp_path)
    state = owned.invoke("status", "root", operation)
    assert state["activation"]["state"] == "not-started"
    assert snapshots(tmp_path) == before_status

    applied = owned.invoke("apply", "root", operation, "--expected-plan", expected)
    assert applied["state"] == "acknowledged"
    assert applied["checkpoint"]["kind"] == "legacy-checkpoint-backed-v1"
    assert applied["checkpoint"]["sha256"] == prepared["sha256"]
    assert applied["checkpoint"]["message_count"] == 1
    assert not any(k.startswith("_") for k in applied["checkpoint"])
    assert owned.source.read_bytes() == (operation / "candidate.jsonl").read_bytes()
    assert owned.source.stat().st_mtime_ns == original_stamp.st_mtime_ns
    for ref, data in zip(refs, pixels):
        with owned.media.resolve_ref(ref) as stream:
            assert stream.read() == data

    installed = owned.source.stat()
    owned.reopen()
    for ref in refs:
        with pytest.raises(MediaRefusal):
            owned.media.resolve_ref(ref)
    fresh = owned.expected(tmp_path, "installed-plan.json")
    state = owned.invoke("status", "root", operation)
    assert state["conversion"] == "installed-bytes-match-checkpoint-unconfirmed"
    assert state["activation"]["state"] == "acknowledged-on-disk-reverify-required"
    assert state["activation"]["checkpoint"] is None
    resumed = owned.invoke("resume", "root", operation, "--expected-plan", fresh)
    assert resumed["state"] == "acknowledged"
    assert owned.source.stat().st_ino == installed.st_ino
    for ref, data in zip(refs, pixels):
        with owned.media.resolve_ref(ref) as stream:
            assert stream.read() == data
    assert owned.maintenance.checks > 10 and owned.store_calls > 5


def test_ack_failure_status_and_resume_do_not_replay_replace(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected)
    owned.fail_ack = True
    failure = owned.invoke("apply", "root", operation, "--expected-plan", expected, ok=False)
    _assert_command_failure(failure, "apply", RuntimeError,
                            "Fixture ACK unavailable; preserve installed bytes")
    stamp, body = owned.source.stat(), owned.source.read_bytes()
    state = owned.invoke("status", "root", operation)
    assert state["activation"]["state"] == "installed-awaiting-ack"
    owned.reopen()
    owned.fail_ack = False
    fresh = owned.expected(tmp_path, "fresh.json")
    result = owned.invoke("resume", "root", operation, "--expected-plan", fresh)
    assert result["state"] == "acknowledged"
    assert owned.source.stat().st_ino == stamp.st_ino and owned.source.read_bytes() == body
    assert len(list(owned.directory.glob("activation-stage-*"))) == 0
    assert len(list(operation.glob("activation-stage-*"))) == 1


@pytest.mark.parametrize("command", ["prepare", "apply", "resume", "export"])
def test_default_admission_keeps_workflow_held(owned, tmp_path, command):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    owned.register(None)
    before = snapshots(tmp_path)
    args = [command, "root", tmp_path / "unknown"]
    if command != "export":
        args += ["--expected-plan", expected]
    result = owned.invoke(*args, ok=False)
    assert "HELD: private host" in result.output
    assert snapshots(tmp_path) == before


@pytest.mark.parametrize("value", [True, {"admitted": True}])
def test_boolean_json_cannot_be_private_admission(owned, tmp_path, value):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)

    @contextmanager
    def fake(*args, **kwargs):
        yield value

    owned.register(fake)
    before = snapshots(tmp_path)
    result = owned.invoke("prepare", "root", tmp_path / "operation", "--expected-plan", expected, ok=False)
    assert "actual private StorageAdmission" in result.output
    assert snapshots(tmp_path) == before


def test_admission_exact_native_session_source_and_bound_store(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    original = owned.admission_factory
    for field, value in [
        ("native", object()), ("session_id", "other"),
        ("source", owned.directory / "backup.jsonl"), ("media_store", object()),
    ]:
        @contextmanager
        def wrong(*args, **kwargs):
            with original(*args, **kwargs) as admission:
                yield replace(admission, **{field: value})

        owned.register(wrong)
        before = snapshots(tmp_path)
        owned.invoke("prepare", "root", tmp_path / field, "--expected-plan", expected, ok=False)
        assert snapshots(tmp_path) == before


def test_source_changed_since_preview_refuses_before_operation(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    with owned.source.open("ab") as stream:
        stream.write(b'{"role":"assistant","content":"new activity"}\n')
    before = snapshots(tmp_path)
    result = owned.invoke("prepare", "root", tmp_path / "operation", "--expected-plan", expected, ok=False)
    assert "Fresh typed plan differs" in result.output
    assert snapshots(tmp_path) == before


def test_expected_plan_boolean_count_is_not_integer_fact(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    value = json.loads(expected.read_text())
    value["plan"]["records"] = True
    expected.write_text(json.dumps(value))
    before = snapshots(tmp_path)
    result = owned.invoke("prepare", "root", tmp_path / "operation", "--expected-plan", expected, ok=False)
    assert "Fresh typed plan differs" in result.output
    assert snapshots(tmp_path) == before


@pytest.mark.parametrize("midflight", [False, True])
def test_permission_revocation_refuses_before_source_replacement(owned, tmp_path, midflight):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected)
    body, inode = owned.source.read_bytes(), owned.source.stat().st_ino
    if midflight:
        owned.revoke_after = 8
    else:
        owned.permissions.discard("apply")
    result = owned.invoke("apply", "root", operation, "--expected-plan", expected, ok=False)
    _assert_command_failure(result, "apply", ValueError, "Fixture selected-source/purpose permission revoked")
    assert owned.source.read_bytes() == body and owned.source.stat().st_ino == inode


def test_unknown_original_stays_held_without_migration_clock(owned, tmp_path):
    owned.images(("PNG",), attested=False)
    expected = owned.expected(tmp_path)
    original = owned.source.read_bytes()
    operation = tmp_path / "operation"
    result = owned.invoke("prepare", "root", operation, "--expected-plan", expected, ok=False)
    _assert_command_failure(result, "prepare", ValueError, "HELD: unknown original provenance/retention")
    assert owned.source.read_bytes() == original
    assert not (operation / "verified.json").exists()
    assert not list(owned.media.journal())
    state = owned.invoke("status", "root", operation)
    assert state["conversion"] == "interrupted-preserve-and-inspect-journal"
    assert state["activation"] is None


def test_three_field_origin_cannot_synthesize_original_time_or_retention(owned, tmp_path):
    owned.images(("PNG",))
    owned.originals[0, 0] = {"kind": "upload", "session_id": "root", "id": "original"}
    expected = owned.expected(tmp_path)
    result = owned.invoke("prepare", "root", tmp_path / "operation", "--expected-plan", expected, ok=False)
    assert "original five-field provenance/retention required" in result.output
    assert not list(owned.media.journal())


def test_shared_root_alone_does_not_admit_legacy_descriptors(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    original = owned.admission_factory

    @contextmanager
    def weak(*args, **kwargs):
        with original(*args, **kwargs) as admission:
            yield replace(admission, maintenance=session_storage.SharedRootSession(
                "root", owned.native.base_dir, object(),
            ))

    owned.register(weak)
    before = snapshots(tmp_path)
    result = owned.invoke("prepare", "root", tmp_path / "operation", "--expected-plan", expected, ok=False)
    assert "alone cannot quiesce old native descriptors" in result.output
    assert snapshots(tmp_path) == before


def test_unknown_operation_never_rebuilt_and_status_never_replays(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "unknown"
    state = owned.invoke("status", "root", operation)
    assert state["activation"] is None and not operation.exists()
    owned.invoke("apply", "root", operation, "--expected-plan", expected, ok=False)
    owned.invoke("resume", "root", operation, "--expected-plan", expected, ok=False)
    assert not operation.exists()
    operation.mkdir()
    (operation / "unknown-effect").write_bytes(b"preserve")
    before = snapshots(tmp_path)
    result = owned.invoke("prepare", "root", operation, "--expected-plan", expected, ok=False)
    assert "never rebuild" in result.output
    assert owned.invoke("status", "root", operation)["activation"] is None
    assert snapshots(tmp_path) == before


def test_source_operation_mismatch_refuses_without_activation_journal(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected)
    owned.source.rename(owned.directory / "old-primary")
    owned.images(("JPEG",))
    fresh = owned.expected(tmp_path, "fresh.json")
    body = owned.source.read_bytes()
    result = owned.invoke("apply", "root", operation, "--expected-plan", fresh, ok=False)
    _assert_command_failure(result, "apply", ValueError, "Source identity/hash changed since preview")
    assert owned.source.read_bytes() == body
    assert not (operation / "activation.json").exists()


def test_refs_only_is_truthfully_no_inline_optimization_and_no_replacement(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    first = tmp_path / "first"
    owned.invoke("prepare", "root", first, "--expected-plan", expected)
    owned.invoke("apply", "root", first, "--expected-plan", expected)
    fresh = owned.expected(tmp_path, "fresh.json")
    assert json.loads(fresh.read_text())["plan"]["existing_refs"] == 1
    second = tmp_path / "second"
    result = owned.invoke("prepare", "root", second, "--expected-plan", fresh)
    assert result["optimization"] == "already-externalized-no-inline-images"
    assert result["images"] == 0 and result["inline_removed_bytes"] == 0
    inode = owned.source.stat().st_ino
    assert owned.invoke("apply", "root", second, "--expected-plan", fresh)["state"] == "acknowledged"
    assert owned.source.stat().st_ino == inode


def test_deferred_export_is_prepare_needed_no_private_builder(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected)
    before = snapshots(tmp_path)
    result = owned.invoke("export", "root", operation, ok=False)
    assert "Prepare needed" in result.output and "--export-assets" in result.output
    assert snapshots(tmp_path) == before


def test_saved_apply_is_never_replayed_and_status_is_not_live_ack(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected)
    owned.invoke("apply", "root", operation, "--expected-plan", expected)
    fresh = owned.expected(tmp_path, "fresh.json")
    before = snapshots(tmp_path)
    result = owned.invoke("apply", "root", operation, "--expected-plan", fresh, ok=False)
    _assert_command_failure(result, "apply", ValueError,
                            "Saved activation exists; reconcile/resume, never replay apply")
    assert owned.invoke("status", "root", operation)["activation"]["checkpoint"] is None
    assert snapshots(tmp_path) == before


def test_export_permission_and_source_conflict_refuse_without_writes(owned, tmp_path):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected, "--export-assets")
    owned.permissions.discard("export")
    before = snapshots(tmp_path)
    _assert_command_failure(owned.invoke("export", "root", operation, ok=False),
                            "export", ValueError, "Fixture selected-source/purpose permission revoked")
    assert snapshots(tmp_path) == before
    owned.permissions.add("export")
    owned.write([{"role": "user", "content": "different selected source"}])
    before = snapshots(tmp_path)
    result = owned.invoke("export", "root", operation, ok=False)
    assert "source conflicts with this exact operation" in result.output
    assert snapshots(tmp_path) == before


def test_export_cannot_substitute_another_valid_portable_closure(owned, tmp_path):
    from amplifier_foundation.session.media_conversion import verify_export

    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected, "--export-assets")
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = NativeFixture(other_root)
    try:
        other.images(("JPEG",))
        other_expected = other.expected(other_root)
        other_operation = other_root / "operation"
        other.invoke("prepare", "root", other_operation, "--expected-plan", other_expected, "--export-assets")
        (operation / "portable").rename(operation / "original-portable")
        shutil.copytree(other_operation / "portable", operation / "portable")
        verify_export(operation / "portable")  # Individually valid is insufficient.
        before = snapshots(tmp_path)
        result = owned.invoke("export", "root", operation, ok=False)
        _assert_command_failure(result, "export", ValueError)
        assert snapshots(tmp_path) == before
    finally:
        other.close()


@pytest.mark.parametrize("extra", ["unexpected", "assets/unexpected"])
def test_export_extra_file_refuses_never_exposes_recovery_parent(owned, tmp_path, extra):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected, "--export-assets")
    (operation / "portable" / extra).write_bytes(b"foreign")
    before = snapshots(tmp_path)
    result = owned.invoke("export", "root", operation, ok=False)
    _assert_command_failure(result, "export", ValueError)
    assert '"portable":' not in result.output and str(operation) not in result.output
    assert snapshots(tmp_path) == before


@pytest.mark.parametrize("when", ["asset-reader", "admission-exit"])
def test_export_reverifies_portable_after_callbacks_and_admission_exit(owned, tmp_path, when):
    """Finite reentrant mutations, not a universal concurrent TOCTOU claim."""
    from amplifier_foundation.session.media_conversion import verify_export

    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    owned.invoke("prepare", "root", operation, "--expected-plan", expected, "--export-assets")
    portable = operation / "portable"
    verify_export(portable)
    before = snapshots(tmp_path)
    asset = next((portable / "assets").iterdir())
    relative = str(asset.relative_to(tmp_path))
    mutations = []
    original = owned.admission_factory

    def mutate():
        asset.write_bytes(b"fixture-corrupted-export-after-initial-verification")
        mutations.append(when)

    @contextmanager
    def reader(ref):
        with owned.asset_reader(ref) as stream:
            yield stream
        mutate()

    @contextmanager
    def admission(*args, **kwargs):
        with original(*args, **kwargs) as admitted:
            yield replace(admitted, asset_reader=reader) if when == "asset-reader" else admitted
        if when == "admission-exit":
            mutate()

    owned.register(admission)
    result = CliRunner().invoke(owned.cli, ["storage", "export", "root", str(operation)])
    after = snapshots(tmp_path)
    if result.exit_code == 0 or "portable-integrity-verified" in result.output:
        pytest.fail("Export declared verified after participating callback mutation", pytrace=False)
    assert mutations == [when]
    assert after[relative] != before[relative]
    assert {k: v for k, v in after.items() if k != relative} == {
        k: v for k, v in before.items() if k != relative}
    with pytest.raises(ValueError):
        verify_export(portable)  # Corruption/evidence retained; never silently rebuilt.
    owned.register()
    owned.invoke("prepare", "root", operation, "--expected-plan", expected, ok=False)
    assert snapshots(tmp_path) == after


@pytest.mark.parametrize("replacement", ["portable-only", "operation-and-portable"])
def test_export_rejects_valid_substitution_after_admission_exit(owned, tmp_path, replacement):
    """Both bundles really verify; final selection must remain admitted PNG A."""
    from amplifier_foundation.session.media_conversion import reconcile, verify_export

    # Build B first at the SAME native/session/source path, then prepare A with
    # a distinct actual fixture observation. This avoids a foreign source-path
    # refusal masking the operation+portable substitution counterexample.
    owned.images(("JPEG",))
    expected_b = owned.expected(tmp_path, "expected-b.json")
    operation_b = tmp_path / "operation-b"
    candidate_b = owned.invoke(
        "prepare", "root", operation_b, "--expected-plan", expected_b, "--export-assets")
    owned.images(("PNG",))
    owned.originals[0, 0]["id"] = "actual-original-png-a"
    expected_a = owned.expected(tmp_path, "expected-a.json")
    operation_a = tmp_path / "operation-a"
    candidate_a = owned.invoke(
        "prepare", "root", operation_a, "--expected-plan", expected_a, "--export-assets")
    assert candidate_a["sha256"] != candidate_b["sha256"]
    portable = operation_a / "portable"
    verify_export(portable)
    original = owned.admission_factory
    snapshots_at_exit = []
    checks_at_exit = []

    @contextmanager
    def substitute(*args, **kwargs):
        with original(*args, **kwargs) as admitted:
            yield admitted
        if replacement == "portable-only":
            portable.rename(operation_a / "retained-admitted-portable-a")
            shutil.copytree(operation_b / "portable", portable)
        else:
            operation_a.rename(tmp_path / "retained-admitted-operation-a")
            shutil.copytree(operation_b, operation_a)
        # A valid binary closure is not proof of the previously admitted choice.
        verify_export(portable)
        if replacement == "operation-and-portable":
            assert reconcile(operation_a, installed=portable / "candidate.jsonl") == (
                "installed-bytes-match-checkpoint-unconfirmed")
        snapshots_at_exit.append(snapshots(tmp_path))
        checks_at_exit.append(len(owned.permission_checks))

    owned.register(substitute)
    result = CliRunner().invoke(owned.cli, ["storage", "export", "root", str(operation_a)])
    _private_check(result.exit_code != 0 and "portable-integrity-verified" not in result.output,
                   "Export declared a substituted valid bundle as the admitted candidate")
    _assert_command_failure(
        result, "export", ValueError,
        "Portable export no longer matches the admitted operation" if replacement == "portable-only" else
        "Portable export differs from admitted candidate facts",
    )
    assert snapshots(tmp_path) == snapshots_at_exit[0]
    assert len(owned.permission_checks) == checks_at_exit[0]  # No re-admission/callback replay.
    verify_export(portable)  # Retained B remains valid, not repaired or rebuilt.
    owned.register()
    owned.invoke("prepare", "root", operation_a, "--expected-plan", expected_a, ok=False)
    assert snapshots(tmp_path) == snapshots_at_exit[0]


_PRIVATE_DIAGNOSTIC = "fixture-private-provider-origin-cleanup-payload"
_SAFE_FAILURES = {
    "prepare": "STORAGE_PREPARE_FAILED: Preparation not confirmed; preserve operation files and inspect status.",
    "apply": "STORAGE_APPLY_FAILED: Activation not confirmed; preserve operation files and inspect status.",
    "resume": "STORAGE_RESUME_FAILED: Resume not confirmed; preserve operation files and inspect status.",
    "export": "STORAGE_EXPORT_FAILED: Portable export not verified; preserve operation files and inspect status.",
}


def _assert_command_failure(result, command, error_type, message=None):
    _private_check(result.output == "Error: " + _SAFE_FAILURES[command] + "\n",
                   "Command emitted non-fixed diagnostic output")
    error = result.exception.__context__.__cause__
    _private_check(isinstance(error, error_type), "Original diagnostic type was not retained")
    if message is not None:
        _private_check(error.args == (message,), "Original diagnostic facts were not retained privately")
    return error


def _private_error(flavor):
    """Construct hostile diagnostics without printing/formatting any of them."""
    str_calls = []

    class Hostile(ValueError):
        def __str__(self):
            str_calls.append(True)
            raise AssertionError(_PRIVATE_DIAGNOSTIC)

    error = Hostile() if flavor == "hostile-str" else ValueError("fixture generic failure")
    if flavor == "message":
        error = ValueError(_PRIVATE_DIAGNOSTIC)
    elif flavor == "cause":
        error.__cause__ = OSError(_PRIVATE_DIAGNOSTIC)
    elif flavor == "context":
        error.__context__ = RuntimeError(_PRIVATE_DIAGNOSTIC)
    elif flavor == "notes":
        error.add_note(_PRIVATE_DIAGNOSTIC)
    elif flavor == "lookup":
        error = LookupError(_PRIVATE_DIAGNOSTIC)
    elif flavor == "click-exception":
        error = click.ClickException(_PRIVATE_DIAGNOSTIC)
    return error, str_calls


def _private_check(condition, safe_failure):
    # Avoid assertion introspection or exception-chain rendering in RED receipts.
    if not condition:
        pytest.fail(safe_failure, pytrace=False)


@pytest.mark.parametrize("command", ["prepare", "apply", "resume", "export"])
@pytest.mark.parametrize("seam", ["entry", "callback", "admission-exit"])
@pytest.mark.parametrize("flavor", [
    "message", "cause", "context", "notes", "hostile-str", "lookup", "click-exception",
])
@pytest.mark.parametrize("delivery", ["entrypoint", "click"])
def test_mutating_lane_diagnostics_are_fixed_and_original_stays_private(
    owned, tmp_path, capsys, command, seam, flavor, delivery,
):
    owned.images(("PNG",))
    expected = owned.expected(tmp_path)
    operation = tmp_path / "operation"
    if command != "prepare":
        owned.invoke("prepare", "root", operation, "--expected-plan", expected, "--export-assets")
    if command == "resume":
        owned.invoke("apply", "root", operation, "--expected-plan", expected)
        expected = owned.expected(tmp_path, "installed.json")
    error, str_calls = _private_error(flavor)
    original_cause, original_context = error.__cause__, error.__context__
    original_notes = getattr(error, "__notes__", None)

    def refuse(*args, **kwargs):
        raise error

    @contextmanager
    def refuse_reader(ref):
        raise error
        yield  # Context-manager shape, not a returned boolean or serialized grant.

    original = owned.admission_factory

    @contextmanager
    def admission(*args, **kwargs):
        with original(*args, **kwargs) as admitted:
            if seam == "callback":
                admitted = replace(admitted, **(
                    {"origin_for": refuse} if command == "prepare" else {"asset_reader": refuse_reader}
                ))
            yield admitted
        if seam == "admission-exit":
            raise error

    cli = click.Group()
    session_storage.register_session_storage_commands(
        cli, store_factory=refuse if seam == "entry" else owned.store_factory,
        admission_factory=admission,
    )
    capsys.readouterr()
    caught = None
    if delivery == "entrypoint":
        kwargs = {"session_id": "root", "operation": operation}
        if command != "export":
            kwargs["expected_plan"] = expected
        if command == "prepare":
            kwargs["export_assets"] = False
        try:
            cli.commands["storage"].commands[command].callback(**kwargs)
        except BaseException as observed:
            caught = observed
        captured = capsys.readouterr()
        _private_check(captured.out == "" and captured.err == "", "Entrypoint emitted diagnostic output")
        _private_check(type(caught) is click.ClickException, "Entrypoint did not raise fixed Click refusal")
        _private_check(caught.message == _SAFE_FAILURES[command], "Entrypoint retained arbitrary diagnostic text")
    else:
        args = ["storage", command, "root", str(operation)]
        if command != "export":
            args += ["--expected-plan", str(expected)]
        result = CliRunner().invoke(cli, args)
        _private_check(result.exit_code != 0, "Click reported success after injected failure")
        _private_check(result.output == "Error: " + _SAFE_FAILURES[command] + "\n",
                       "Click emitted non-fixed diagnostic output")
        caught = result.exception.__context__
        _private_check(type(caught) is click.ClickException, "Click did not retain fixed refusal")
        captured = capsys.readouterr()
        _private_check(captured.out == "" and captured.err == "", "Click leaked outside its output boundary")
    _private_check(caught.__cause__ is error, "Original exception was not retained privately as cause")
    _private_check(error.__cause__ is original_cause and error.__context__ is original_context,
                   "Original diagnostic chain was rewritten")
    _private_check(getattr(error, "__notes__", None) is original_notes, "Original notes were rewritten")
    _private_check(not str_calls, "Hostile exception string conversion executed")


def test_request_only_real_publication_is_not_candidate_authority(owned, tmp_path):
    owned.images(("PNG",))
    owned.native.unbind_media_store("root", owned.media)
    owned.media.close()
    owned.open_media(policy=MediaPolicy(1024 * 1024, 10000, 10, retention="request-only"))
    data = io.BytesIO()
    Image.new("RGB", (3, 2), "purple").save(data, format="PNG")
    origin = owned.originals[0, 0]
    ref = owned.media.ingest_sync(
        data.getvalue(), source_format="image-bytes", origin=origin, policy=None,
    )["value"]
    owned.media.publish_sync([ref], origin=origin, disposition="retain")
    # Real owner publication exists, but its request-only retention is not
    # promoted by portable closure possession or a JSON expected plan.
    assert owned.media.validate_published_ref(ref) == ref
    owned.write([{"role": "user", "content": [
        {"type": "text", "text": f"[Session image {ref['sha256']}]"}],
        "metadata": {"experimental.media.v1": {"refs": [ref]}}}])
    expected = owned.expected(tmp_path)
    before = owned.source.read_bytes()
    operation = tmp_path / "operation"
    result = owned.invoke("prepare", "root", operation, "--expected-plan", expected, ok=False)
    _assert_command_failure(result, "prepare", ValueError, "Fixture request-only or unknown retained publication")
    assert owned.source.read_bytes() == before and not (operation / "verified.json").exists()


@pytest.mark.skipif(os.sys.platform != "linux", reason="Actual prospective binding is Linux-only")
def test_actual_versioned_binding_is_explicitly_held_not_header_stripped(owned, tmp_path):
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding

    owned.maintenance.check()
    binding = VersionedSessionBinding.create_new(
        owned.directory.parent / "t2", session_id="t2", operation_id="allocate",
        admit=lambda *args: None,
    )
    try:
        native = SessionStore(owned.directory.parent, canonical_binding=binding)
        cli = click.Group()
        session_storage.register_session_storage_commands(cli, store_factory=lambda: native)
        before = snapshots(binding.directory)
        expected = tmp_path / "not-authority.json"
        expected.write_text("{}")
        for command in [
            ["preview", "t2"],
            ["prepare", "t2", str(tmp_path / "operation"), "--expected-plan", str(expected)],
        ]:
            result = CliRunner().invoke(cli, ["storage", *command])
            assert result.exit_code == 1 and "T2 API pending" in result.output
        assert snapshots(binding.directory) == before
        binding.check()
    finally:
        binding.close()
