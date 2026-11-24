"""Actual registered full-source snapshots; synthetic enrollment and policy only."""

import asyncio
from dataclasses import asdict
import hashlib
import json
import os
import sys

import pytest
from amplifier_foundation.session import media_activation, media_conversion
from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.versioned_checkpoint import verified_versioned_checkpoint_refs

from amplifier_app_cli import session_storage_transfer as transfer
from amplifier_app_cli.session_binding_custody import binding_custody
from amplifier_app_cli.session_media_policy import RESTRICTIONS_KEY
from amplifier_app_cli.session_storage_owner import default_session_directory
from amplifier_app_cli.session_store import SessionStore

# Reuse maintained real offline composition/normal registration fixtures, not a
# fake admission factory. No source graph overlay or user configuration is read.
from test_session_media_policy import (  # noqa: F401
    configuration, external_requests_forbidden, invoke, prospective, registered,
)


def linux():
    if sys.platform != "linux":
        pytest.skip("Actual prospective source custody is Linux qualified")


def current(directory):
    with open_committed_source(directory) as source:
        return source.commit


def preserved(directory):
    roots = [directory / "metadata.json", directory / "transcript.v2", directory / "media/assets"]
    result = {"root": directory.stat().st_ino}
    for root in roots:
        for path in ([root] if root.is_file() else root.rglob("*")):
            if path.is_file():
                info = path.stat()
                result[str(path.relative_to(directory))] = (
                    info.st_ino, info.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest(),
                )
    return result


def call(cli, tmp_path, *, source="root", target="snapshot", identity="snapshot-op", operation=None):
    return invoke(cli, "fork-snapshot", source, target, operation or tmp_path / "snapshot-op",
                  "--operation-id", identity)


def check_success(result):
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.output)


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_normal_snapshot_real_binary_policy_and_cold_recovery(configuration, tmp_path, monkeypatch, image_format):
    import amplifier_app_cli.commands.session as commands

    directory, ref, raw, _, expected = prospective(configuration, image_format=image_format)
    baseline = preserved(directory)
    monkeypatch.setattr(commands, "SessionStore", lambda: pytest.fail("Unbound command store"))
    cli = registered()
    receipts = []
    actual = media_activation.apply_committed_candidate

    def observe(*args, **kwargs):
        plan = media_conversion.load_candidate_plan(args[0])
        target = kwargs["binding"]
        record = SessionMetadataStore(target.directory).read()[RESTRICTIONS_KEY]
        assert record["prefix"] == plan.target_prefix == asdict(kwargs["expected"])
        assert record["restrictions"]["max_images"] == 2
        assert plan.target_prefix != plan.committed_prefix == asdict(expected)
        result = actual(*args, **kwargs)
        receipts.append(result.checkpoint)
        return result

    monkeypatch.setattr(media_activation, "apply_committed_candidate", observe)
    report = check_success(call(cli, tmp_path))
    assert report["state"] == "acknowledged" and report["checkpoint_acknowledged"]
    assert report["conversation_fork"] is False
    assert report["cost_boundary"] == "incomplete-not-computed"
    assert "amplifier session resume" not in json.dumps(report)
    assert receipts[0].verify() == report["checkpoint"]
    target = default_session_directory("snapshot")
    with open_committed_source(target) as source:
        row = source.window(0, 1).messages[0]
        accepted = source.commit
    copied = row["metadata"]["experimental.media.v1"]["refs"][0]
    assert copied == {**ref, "session_id": "snapshot"}
    metadata = SessionMetadataStore(target).read()
    assert metadata[RESTRICTIONS_KEY]["prefix"] == asdict(accepted)
    assert metadata[RESTRICTIONS_KEY]["restrictions"]["max_images"] == 2
    assert metadata["bundle"] == "bundle:owned"
    assert "forked_from_turn" not in metadata and "fork_cost_boundary" not in metadata
    assert metadata["storage_snapshot"]["source_prefix"] == asdict(expected)
    with transfer.SnapshotTarget(target, session_id="snapshot", operation=tmp_path / "snapshot-op",
                                 operation_id="snapshot-op") as owner:
        owner.prepare_media()
        with owner.media.resolve_ref(copied) as stream:
            assert stream.read() == raw
    assert preserved(directory) == baseline
    assert not list(target.glob("events*")) and not (target / "context-intelligence").exists()

    def forbidden(*args, **kwargs):
        pytest.fail("Recovery repeated creation/adoption/transfer")
    monkeypatch.setattr(media_conversion, "transfer_committed_prefix", forbidden)
    monkeypatch.setattr(VersionedSessionBinding, "create_new", forbidden)
    monkeypatch.setattr(SessionMediaStore, "adopt", forbidden)
    status = check_success(invoke(cli, "snapshot-status", "snapshot", tmp_path / "snapshot-op",
                                  "--operation-id", "snapshot-op"))
    assert status["effect"] == "read-only" and not status["checkpoint_acknowledged"]
    # A tighter current policy used for a real recovery ACK must become part of
    # the target's retained floor, not be lost when its prefix is associated.
    import yaml
    settings_path = configuration[2]
    settings = yaml.safe_load(settings_path.read_text())
    settings["config"]["session_media"]["max_pixels"] = 8  # Image has six pixels.
    settings_path.write_text(yaml.safe_dump(settings))
    resumed = check_success(invoke(cli, "snapshot-resume", "snapshot", tmp_path / "snapshot-op",
                                   "--operation-id", "snapshot-op"))
    assert resumed["checkpoint_acknowledged"] and resumed["commit"] == asdict(accepted)
    assert SessionMetadataStore(target).read()[RESTRICTIONS_KEY]["restrictions"]["max_pixels"] == 8


def test_plaintext_snapshot_has_no_pixel_copy(configuration, tmp_path, monkeypatch):
    directory, *_ = prospective(configuration)
    with transfer.owners.SessionStorageOwner(directory, session_id="root", purpose="prepare") as owner:
        owner.prepare_media()
        owner.native.save_versioned(
            "root", [{"role": "user", "content": "plain"}], {}, expected=owner.current,
            operation_id="plain", admit=lambda: None,
        )
    monkeypatch.setattr(SessionMediaStore, "resolve_ref", lambda *_a, **_k: pytest.fail("Text copied pixels"))
    report = check_success(call(registered(), tmp_path))
    assert report["commit"]["message_count"] == 1
    record = SessionMetadataStore(default_session_directory("snapshot")).read()[RESTRICTIONS_KEY]
    assert record["restrictions"]["max_images"] == 2


@pytest.mark.parametrize("phase", ["rewrite-reply", "late-ack", "metadata"])
def test_lost_effect_cold_resume_original_target_without_transfer(configuration, tmp_path, monkeypatch, phase):
    prospective(configuration)
    cli = registered()
    operation = tmp_path / "snapshot-op"
    rewrites = []
    original_rewrite = VersionedSessionBinding.rewrite_serialized
    original_update = SessionMetadataStore.update
    original_ack = SessionMediaStore.acknowledge_checkpoint

    def lost_rewrite(self, *args, **kwargs):
        if self.session_id == "snapshot":
            rewrites.append(1)
        result = original_rewrite(self, *args, **kwargs)
        if self.session_id == "snapshot":
            if phase == "rewrite-reply":
                raise OSError("private lost reply")
        return result

    def lost_metadata(self, fields):
        if "storage_snapshot" in fields:
            raise OSError("private metadata failure")
        return original_update(self, fields)

    def late_ack(self, receipt):
        result = original_ack(self, receipt)
        if self.session_id == "snapshot" and receipt.verify()["message_count"] and phase == "late-ack":
            raise OSError("private late ACK failure")
        return result

    monkeypatch.setattr(VersionedSessionBinding, "rewrite_serialized", lost_rewrite)
    monkeypatch.setattr(SessionMetadataStore, "update", lost_metadata)
    monkeypatch.setattr(SessionMediaStore, "acknowledge_checkpoint", late_ack)
    result = call(cli, tmp_path)
    assert result.exit_code != 0 and "private" not in result.output
    assert len(rewrites) == 1
    installed = current(default_session_directory("snapshot"))
    plan = media_conversion.load_candidate_plan(operation)
    assert plan.target_prefix != asdict(installed)
    first_record = SessionMetadataStore(default_session_directory("snapshot")).read()[RESTRICTIONS_KEY]
    assert first_record["prefix"] == plan.target_prefix

    # Neither a repeated creation nor wrong-ID recovery can replay effects.
    repeat = call(cli, tmp_path)
    assert repeat.exit_code != 0 and len(rewrites) == 1
    wrong = invoke(cli, "snapshot-resume", "snapshot", operation, "--operation-id", "wrong")
    assert wrong.exit_code != 0 and len(rewrites) == 1
    monkeypatch.setattr(SessionMetadataStore, "update", original_update)
    monkeypatch.setattr(SessionMediaStore, "acknowledge_checkpoint", original_ack)
    def forbidden(*args, **kwargs):
        pytest.fail("Recovery repeated transfer or serialized rewrite")
    monkeypatch.setattr(media_conversion, "transfer_committed_prefix", forbidden)
    monkeypatch.setattr(VersionedSessionBinding, "rewrite_serialized", forbidden)
    report = check_success(invoke(cli, "snapshot-resume", "snapshot", operation,
                                  "--operation-id", "snapshot-op"))
    assert report["state"] == "acknowledged" and report["commit"] == asdict(installed)
    assert SessionMetadataStore(default_session_directory("snapshot")).read()[RESTRICTIONS_KEY]["prefix"] == asdict(installed)


def test_later_successor_never_borrows_current_receipt(configuration, tmp_path, monkeypatch):
    prospective(configuration)
    cli = registered()
    first = check_success(call(cli, tmp_path))
    directory = default_session_directory("snapshot")
    # A genuine later host/native publication carries restrictions forward.
    with transfer.SnapshotTarget(directory, session_id="snapshot", operation=tmp_path / "snapshot-op",
                                 operation_id="snapshot-op") as owner:
        owner.prepare_media()
        with open_committed_source(directory) as source:
            rows = source.window(0, 4).messages
        owner.native.save_versioned(
            "snapshot", [*rows, {"role": "assistant", "content": "later"}], {},
            expected=owner.current, operation_id="later", admit=lambda: None,
        )
    successor = current(directory)
    report = check_success(invoke(cli, "snapshot-resume", "snapshot", tmp_path / "snapshot-op",
                                  "--operation-id", "snapshot-op"))
    assert report["state"] == "superseded-original-operation"
    assert not report["checkpoint_acknowledged"] and report["checkpoint"] is None
    assert report["commit"] == first["commit"] and report["commit"] != asdict(successor)
    assert current(directory) == successor


@pytest.mark.parametrize("which", ["source", "parent", "operation-parent"])
def test_private_path_refusal_no_target(configuration, tmp_path, which):
    directory, *_ = prospective(configuration)
    path = directory if which == "source" else directory.parent if which == "parent" else tmp_path / "public"
    if not path.exists():
        path.mkdir()
    path.chmod(0o755)
    operation = path / "op" if which == "operation-parent" else tmp_path / "snapshot-op"
    try:
        result = call(registered(), tmp_path, operation=operation)
        assert result.exit_code != 0 and "STORAGE_T2_PATH_HELD" in result.output
        assert not os.path.lexists(default_session_directory("snapshot"))
        assert not operation.exists()
    finally:
        path.chmod(0o700)  # Only test fixture teardown.


def test_busy_source_never_allocates_target(configuration, tmp_path):
    directory, *_, expected = prospective(configuration)
    source = VersionedSessionBinding.resume(directory, session_id="root", expected=expected)
    try:
        result = call(registered(), tmp_path)
        assert result.exit_code != 0 and not default_session_directory("snapshot").exists()
        source.check()
    finally:
        source.close()


def test_tighter_target_policy_prevents_adoption_before_target_exists(configuration, tmp_path, monkeypatch):
    prospective(configuration)
    # Actual target-scoped declarations at the existing settings seam; not a
    # controller/factory policy injection or a policy file inside target space.
    from amplifier_app_cli.lib.settings import AppSettings
    actual = AppSettings.with_session
    target_settings = tmp_path / "target-settings.yaml"
    target_settings.write_text("config:\n  session_media:\n    max_pixels: 1\n")

    def scoped(self, sid, slug):
        result = actual(self, sid, slug)
        if sid == "snapshot":
            result.paths.session_settings = target_settings
        return result
    monkeypatch.setattr(AppSettings, "with_session", scoped)
    result = call(registered(), tmp_path)
    assert result.exit_code != 0 and "STORAGE_T2_SOURCE_HELD" in result.output
    assert not default_session_directory("snapshot").exists()


def test_inline_source_held_before_target_or_media(configuration, tmp_path):
    linux()
    directory = default_session_directory("root")
    directory.parent.mkdir(mode=0o700, parents=True)
    binding = VersionedSessionBinding.create_new(
        directory, session_id="root", operation_id="init", admit=lambda: None,
    )
    try:
        binding.rewrite([{"role": "user", "content": [{"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "aW1hZ2U=",
        }}]}], expected=binding.expected, operation_id="inline", admit=lambda: None)
    finally:
        binding.close()
    baseline = preserved(directory)
    result = call(registered(), tmp_path)
    assert result.exit_code != 0 and "STORAGE_T2_INLINE_HELD" in result.output
    assert preserved(directory) == baseline and not (directory / "media").exists()
    assert not default_session_directory("snapshot").exists()


def test_cancelled_after_adoption_preserves_target_and_never_retries(configuration, tmp_path, monkeypatch):
    prospective(configuration)
    stopped = [False]
    actual = SessionMediaStore.adopt

    async def stop_after(self, *args, **kwargs):
        result = await actual(self, *args, **kwargs)
        stopped[0] = True
        return result
    monkeypatch.setattr(SessionMediaStore, "adopt", stop_after)
    with pytest.raises(InterruptedError):
        asyncio.run(transfer.fork_snapshot(
            "root", "snapshot", tmp_path / "snapshot-op", "snapshot-op", cancelled=lambda: stopped[0],
        ))
    target = default_session_directory("snapshot")
    assert target.exists() and current(target).message_count == 0
    assert not (tmp_path / "snapshot-op/verified.json").exists()
    assert call(registered(), tmp_path).exit_code != 0
    assert list((target / "media/assets").iterdir())  # Retained adopted assets, not deletion.
    assert not SessionMetadataStore(target).read().get("storage_snapshot")


@pytest.mark.parametrize("phase", ["media-open", "empty-save", "cleanup"])
def test_setup_and_cleanup_failures_retain_evidence_and_custody(configuration, tmp_path, monkeypatch, phase):
    prospective(configuration)
    targets = []
    actual_create = transfer.SnapshotTarget.create
    actual_close = SessionMediaStore.close
    actual_save = SessionStore.save_versioned
    original_init = SessionMediaStore.__init__

    def capture(self, *args):
        targets.append(self)
        return actual_create(self, *args)
    monkeypatch.setattr(transfer.SnapshotTarget, "create", capture)
    def media_init(self, root, sid, **kwargs):
        if sid == "snapshot" and phase == "media-open":
            raise LookupError("private constructor failure")
        return original_init(self, root, sid, **kwargs)
    monkeypatch.setattr(SessionMediaStore, "__init__", media_init)
    def empty_save(self, sid, *args, **kwargs):
        if sid == "snapshot" and phase == "empty-save":
            raise LookupError("private first publication failure")
        return actual_save(self, sid, *args, **kwargs)
    monkeypatch.setattr(SessionStore, "save_versioned", empty_save)
    def fail_close(self):
        if self.session_id == "snapshot" and phase == "cleanup":
            raise LookupError("private close failure")
        return actual_close(self)
    monkeypatch.setattr(SessionMediaStore, "close", fail_close)
    try:
        result = call(registered(), tmp_path)
        assert result.exit_code != 0 and "private" not in result.output
        assert targets and targets[0].directory.exists()
        if phase == "cleanup":
            target = targets[0]
            custody = binding_custody(target.binding)
            assert custody.owner is target and custody.error is not None
            with pytest.raises(Exception):
                target.close()
        else:
            assert binding_custody(targets[0].binding) is None
        assert call(registered(), tmp_path).exit_code != 0
    finally:
        # Explicitly settle TEST resources only; never exercise a production
        # retry/clear of retained cleanup state.
        if phase == "cleanup" and targets:
            target = targets[0]
            actual_close(target.media)
            target.binding.close()

@pytest.mark.parametrize("classification", ["computer-use", "unknown-owner"])
def test_restricted_sources_held_before_target(configuration, tmp_path, classification):
    prospective(configuration, classification=classification)
    result = call(registered(), tmp_path)
    assert result.exit_code != 0 and "STORAGE_T2_SOURCE_HELD" in result.output
    assert not os.path.lexists(default_session_directory("snapshot"))
    assert not (tmp_path / "snapshot-op").exists()


@pytest.mark.parametrize("occupied", ["directory", "file", "dangling", "symlink"])
def test_target_occupied_even_damaged_or_alias_has_no_effect(configuration, tmp_path, occupied):
    directory, *_ = prospective(configuration)
    target = default_session_directory("snapshot")
    if occupied == "directory":
        target.mkdir()
    elif occupied == "file":
        target.write_bytes(b"retained")
    else:
        target.symlink_to(directory if occupied == "symlink" else tmp_path / "absent")
    before = preserved(directory)
    result = call(registered(), tmp_path)
    assert result.exit_code != 0 and "STORAGE_T2_OPERATION_HELD" in result.output
    assert preserved(directory) == before and not (tmp_path / "snapshot-op").exists()


def test_help_and_invalid_ids_do_not_enroll_or_select_records(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer.owners, "default_session_directory", lambda *_: pytest.fail("Bad ID selected path"))
    result = invoke(registered(), "fork-snapshot", "root", "../bad", tmp_path / "op",
                    "--operation-id", "operation")
    assert result.exit_code != 0
    help_result = invoke(registered(), "fork-snapshot", "--help")
    assert "ALL source assets" in help_result.output and "not a chat-turn fork" in help_result.output
    assert "--records" not in help_result.output


def test_platform_guard_precedes_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer.sys, "platform", "win32")
    monkeypatch.setattr(transfer.owners, "default_session_directory", lambda *_: pytest.fail("Platform selected path"))
    result = call(registered(), tmp_path)
    assert result.exit_code != 0 and "STORAGE_T2_PATH_HELD" in result.output


def test_ordered_png_jpeg_group_preserves_finite_original_expiry(configuration, tmp_path):
    import io
    from PIL import Image

    linux()
    directory = default_session_directory("root")
    directory.parent.mkdir(mode=0o700, parents=True)
    binding = VersionedSessionBinding.create_new(
        directory, session_id="root", operation_id="init", admit=lambda: None,
    )
    media = SessionMediaStore(directory / "media", "root", policy=MediaPolicy(max_images=2),
                              checkpoint_verifier=verified_versioned_checkpoint_refs)
    native = SessionStore(directory.parent, canonical_binding=binding)
    native.bind_media_store("root", media)
    origin = {"kind": "tool", "session_id": "root", "id": "actual-tool-result",
              "created_at": "2024-01-02T03:04:05+00:00", "expires_at": "2035-01-02T03:04:05+00:00"}
    pixels = []
    for format_ in ("PNG", "JPEG"):
        stream = io.BytesIO()
        Image.new("RGB", (3, 2), "purple").save(stream, format=format_)
        pixels.append(stream.getvalue())
    try:
        refs = media.ingest_group_sync(
            [{"stream": io.BytesIO(raw), "mime": mime}
             for raw, mime in zip(pixels, ("image/png", "image/jpeg"))],
            origin=origin, policy=None,
        )["staged_refs"]
        native.save_versioned("root", [{"role": "tool", "tool_call_id": "original-call",
                                       "content": "unchanged original", "status": "complete",
                                       "metadata": {"experimental.media.v1": {"refs": refs}}}],
                              {"bundle": "bundle:owned"}, expected=binding.expected,
                              operation_id="seed", admit=lambda: media.publish_sync(
                                  refs, origin=origin, disposition="retain"))
    finally:
        media.close()
        binding.close()
    report = check_success(call(registered(), tmp_path))
    target = default_session_directory("snapshot")
    with transfer.SnapshotTarget(target, session_id="snapshot", operation=tmp_path / "snapshot-op",
                                 operation_id="snapshot-op") as owner:
        owner.prepare_media()
        with open_committed_source(target) as source:
            row = source.window(0, 1).messages[0]
        copied = row["metadata"]["experimental.media.v1"]["refs"]
        assert copied == [{**ref, "session_id": "snapshot"} for ref in refs]
        assert row["tool_call_id"] == "original-call" and row["status"] == "complete"
        for ref, original in zip(copied, pixels):
            with owner.media.resolve_ref(ref) as stream:
                assert stream.read() == original
        assert report["checkpoint_acknowledged"]


@pytest.mark.parametrize("mebibytes", [0, 2, 8])
def test_huge_full_registered_snapshot_bounded(configuration, tmp_path, monkeypatch, mebibytes):
    import tracemalloc

    directory, *_ = prospective(configuration)
    with transfer.owners.SessionStorageOwner(directory, session_id="root", purpose="prepare") as owner:
        owner.prepare_media()
        with open_committed_source(directory) as source:
            rows = source.window(0, 1).messages
        rows[0]["provider_state"] = {"opaque": "x" * (mebibytes * 1024 * 1024)}
        owner.native.save_versioned("root", rows, {}, expected=owner.current,
                                   operation_id="huge", admit=lambda: None)
        del rows
    monkeypatch.setattr(VersionedSessionBinding, "load_messages",
                        lambda *_a, **_k: pytest.fail("Snapshot materialized messages"))
    cli = registered()
    tracemalloc.start()
    try:
        result = call(cli, tmp_path)
        report = check_success(result)
        _, peak = tracemalloc.get_traced_memory()
        if peak >= 6 * 1024 * 1024:
            print(json.dumps({
                "memory_diagnostics": [
                    str(stat) for stat in tracemalloc.take_snapshot().statistics("lineno")[:12]
                ],
            }))
    finally:
        tracemalloc.stop()
    assert report["checkpoint_acknowledged"] and report["commit"]["message_count"] == 1
    with open_committed_source(default_session_directory("snapshot")) as source:
        assert sum(map(len, source.iter_bytes())) > mebibytes * 1024 * 1024
    print(json.dumps({"opaque_mib": mebibytes, "python_peak_bytes": peak}))
    assert peak < 6 * 1024 * 1024, peak


def test_small_policy_recheck_allocation_diagnostic(configuration, monkeypatch):
    """Diagnostic only: tiny source, repeated REAL checks, no memory waiver."""
    import functools
    import tracemalloc
    import yaml
    from amplifier_app_cli import session_media_policy

    directory, *_ = prospective(configuration)
    calls = []
    actual = session_media_policy.resolve_declared_config_async

    @functools.wraps(actual)
    async def observed(*args, **kwargs):
        calls.append(1)
        return await actual(*args, **kwargs)

    monkeypatch.setattr(session_media_policy, "resolve_declared_config_async", observed)
    with transfer.owners.SessionStorageOwner(directory, session_id="root", purpose="prepare") as owner:
        owner.prepare_media()
        before_calls = len(calls)
        # This attributes incremental checks, NOT the cold-command memory bound.
        tracemalloc.start(8)
        try:
            metrics = []
            for index in range(32):
                owner.check()
                if index in (0, 7, 31):
                    memory = tracemalloc.get_traced_memory()
                    metrics.append({"checks": index + 1, "current": memory[0], "peak": memory[1],
                                    "compositions": len(calls) - before_calls})
            top = tracemalloc.take_snapshot().statistics("traceback")[:3]
            print(json.dumps({
                "diagnostic": "tiny-source-real-policy-rechecks-not-memory-qualification",
                "metrics": metrics,
                "allocation_traces": [{"bytes": item.size, "count": item.count,
                                       "traceback": list(item.traceback.format())} for item in top],
            }))
        finally:
            tracemalloc.stop()
        # Actual declaration edits must still invalidate; caching a known bool
        # merely to improve memory would violate the effect boundary.
        settings_path = configuration[2]
        settings = yaml.safe_load(settings_path.read_text())
        settings["config"]["session_media"]["max_pixels"] = 1
        settings_path.write_text(yaml.safe_dump(settings))
        with pytest.raises(transfer.owners.StorageOwnerHeld):
            owner.check()