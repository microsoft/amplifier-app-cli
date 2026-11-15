"""Standalone registered-command custody on actual installed c2 prospective APIs.

No source overlay, fake future API, SDK/provider import or alternate parser.
Normal commands still refuse refs with unavailable effective runtime policy;
actual resolved-host-input tests are labeled separately, not normal-user proof.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import click
from click.testing import CliRunner
from PIL import Image
import pytest
from amplifier_foundation.session import media_conversion, versioned_checkpoint
from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding, VersionedSessionConflict
from amplifier_foundation.session.transcript_source import open_committed_source

from amplifier_app_cli import session_storage_owner as owners
from amplifier_app_cli.commands.session import register_session_commands
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.session_binding_custody import binding_custody


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Actual enrolled lease is Linux-only")


def files(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file() and not p.is_symlink()}


@pytest.fixture
def closed(tmp_path, monkeypatch):
    parent = tmp_path / "sessions"
    parent.mkdir(mode=0o700)
    directory = parent / "root"
    binding = VersionedSessionBinding.create_new(
        directory, session_id="root", operation_id="allocate", admit=lambda: None,
    )
    binding.rewrite([{"role": "user", "content": "fixture text"}], expected=binding.expected,
                    operation_id="seed", admit=lambda: None)
    binding.close()
    monkeypatch.setattr(owners, "default_session_directory", lambda sid: parent / sid)
    # Exact existing public settings-data seam, redirected wholly into fixture.
    paths = SettingsPaths(
        global_settings=tmp_path / "global.yaml", project_settings=tmp_path / "project.yaml",
        local_settings=tmp_path / "local.yaml", session_settings=directory / "settings.yaml",
    )
    monkeypatch.setattr(AppSettings, "with_session", lambda self, sid, slug: AppSettings(paths))
    return directory


def registered():
    cli = click.Group()
    register_session_commands(cli, interactive_chat=AsyncMock(), execute_single=AsyncMock(),
                              get_module_search_paths=list)
    return cli


def invoke(cli, *args):
    return CliRunner().invoke(cli, ["session", "storage", *map(str, args)])


def current(directory):
    with open_committed_source(directory) as source:
        return source.commit


def test_normal_registration_acquires_closed_enrollment_without_unbound_factory(closed, monkeypatch):
    import amplifier_app_cli.commands.session as commands

    monkeypatch.setattr(commands, "SessionStore", lambda: pytest.fail("Unbound native factory called"))
    before = files(closed)
    result = invoke(registered(), "preview", "root")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["state"] in {"committed-source-only", "preview-only-not-applied"}
    assert not (closed / "media").exists()
    assert files(closed) == before
    # Command released its standalone owner; physical resume now succeeds.
    binding = VersionedSessionBinding.resume(closed, session_id="root", expected=current(closed))
    binding.close()


def test_standalone_claim_and_release_same_actual_binding(closed):
    with owners.SessionStorageOwner(closed, session_id="root", purpose="preview") as owner:
        binding = owner.binding
        assert type(binding) is VersionedSessionBinding
        assert binding_custody(binding).owner is owner
        with pytest.raises(VersionedSessionConflict):
            VersionedSessionBinding.resume(closed, session_id="root", expected=owner.current)
        assert owner.native is None and owner.media is None
    assert binding_custody(binding) is None and owner.closed


@pytest.mark.parametrize("which", ["parent", "session"])
def test_normal_0755_refuses_before_native_media_or_candidate(closed, tmp_path, monkeypatch, which):
    import amplifier_app_cli.commands.session as commands

    monkeypatch.setattr(commands, "SessionStore", lambda: pytest.fail("Native before admission"))
    path = closed.parent if which == "parent" else closed
    path.chmod(0o755)
    before = files(tmp_path)
    result = invoke(registered(), "preview", "root")
    assert result.exit_code != 0 and "STORAGE_T2_PATH_HELD" in result.output
    assert files(tmp_path) == before and not (closed / "media").exists()
    assert path.stat().st_mode & 0o777 == 0o755
    path.chmod(0o700)  # Restore only fixture mode for cleanup, not production repair.


def test_busy_owner_normal_command_refuses_without_mkdir_or_factory(closed, monkeypatch):
    import amplifier_app_cli.commands.session as commands

    monkeypatch.setattr(commands, "SessionStore", lambda: pytest.fail("Native before lease"))
    binding = VersionedSessionBinding.resume(closed, session_id="root", expected=current(closed))
    try:
        before = files(closed)
        result = invoke(registered(), "preview", "root")
        assert result.exit_code != 0 and "STORAGE_PREVIEW_FAILED" in result.output
        assert files(closed) == before and not (closed / "media").exists()
        binding.check()
    finally:
        binding.close()


@pytest.mark.parametrize("damage", ["enrollment", "dangling", "alias"])
def test_damaged_or_aliased_enrollment_is_never_adopted(closed, tmp_path, damage):
    if damage == "enrollment":
        (closed / ".versioned-enrollment.json").write_bytes(b"{}")
    elif damage == "dangling":
        (closed / "transcript.v2").rename(closed / "retained-v2")
        (closed / "transcript.v2").symlink_to(tmp_path / "absent")
    else:
        target = closed.parent / "retained-root"
        closed.rename(target)
        closed.symlink_to(target, target_is_directory=True)
    before = files(tmp_path)
    result = invoke(registered(), "preview", "root")
    assert result.exit_code != 0
    assert files(tmp_path) == before
    assert not (closed / "media").exists()


def test_running_loop_refuses_before_acquiring_controller(closed):
    async def run():
        result = invoke(registered(), "preview", "root")
        assert result.exit_code != 0 and "STORAGE_PREVIEW_FAILED" in result.output
        assert not (closed / "media").exists()
    asyncio.run(run())
    binding = VersionedSessionBinding.resume(closed, session_id="root", expected=current(closed))
    binding.close()


def test_range_view_uses_public_ranges_and_exact_prefix_eof(closed):
    with open_committed_source(closed) as source:
        view = owners.CommittedRangeView(source)
        digest = hashlib.sha256()
        while raw := view.read(65536):
            digest.update(raw)
        assert digest.hexdigest() == source.commit.sha256
        assert view.tell() == source.commit.committed_bytes
        assert view.read(1) == b""
        view.seek(0)
        assert view.read(1) == b"{"
        view.seek(-1, 2)
        assert len(view.read(65536)) == 1
        for size in (-1, 65537, True):
            with pytest.raises(ValueError):
                view.read(size)
        with pytest.raises(ValueError):
            view.seek(source.commit.committed_bytes + 1)


def test_installed_public_scanner_available_without_overlay_or_private_fallback(closed):
    assert callable(media_conversion.preview_committed)
    assert callable(versioned_checkpoint.scan_serialized_refs)
    before = files(closed)
    with owners.SessionStorageOwner(closed, session_id="root", purpose="preview") as owner:
        with open_committed_source(closed, expected=owner.current) as source:
            assert list(versioned_checkpoint.scan_serialized_refs(
                owners.CommittedRangeView(source), prefix=owner.current, check=owner.check,
            )) == []
    assert files(closed) == before and not (closed / "media").exists()


def test_policy_projection_settings_only_narrow_never_grant(closed, tmp_path):
    (tmp_path / "project.yaml").write_text(
        "session_media:\n  max_bytes: 128\n  ttl_seconds: 10\n  retention: durable\n")
    unknown = owners.project_policy("root")
    assert unknown.known is False and unknown.policy.retention == "drop"
    known = owners.project_policy("root", resolved_config={
        "session_media": {"max_bytes": 512, "max_pixels": 100, "ttl_seconds": 60},
    })
    assert known.known and known.policy.max_bytes == 128 and known.policy.ttl_seconds == 10
    (tmp_path / "local.yaml").write_text("session_media:\n  retention: request-only\n")
    narrowed = owners.project_policy("root", resolved_config={"session_media": {"retention": "durable"}})
    assert narrowed.policy.retention == "request-only"
    (tmp_path / "local.yaml").write_text(
        "config:\n  session_media:\n    retention: request-only\n    max_bytes: 64\n")
    narrowed = owners.project_policy("root", resolved_config={"session_media": {"max_bytes": 512}})
    assert narrowed.policy.retention == "request-only" and narrowed.policy.max_bytes == 64


def seed_ref(directory, *, classification=None, retention="durable", expires_at=None):
    """Actual admitted publication + typed canonical ACK, not serialized authority."""
    binding = VersionedSessionBinding.resume(directory, session_id="root", expected=current(directory))
    media = SessionMediaStore(
        directory / "media", "root", policy=MediaPolicy(retention=retention),
        checkpoint_verifier=versioned_checkpoint.verified_versioned_checkpoint_refs,
    )
    try:
        output = io.BytesIO()
        Image.new("RGB", (3, 2), "purple").save(output, format="PNG")
        data = output.getvalue()
        origin = {"kind": "upload", "session_id": "root", "id": "fixture-original",
                  "created_at": "2024-01-02T03:04:05+00:00", "expires_at": expires_at}
        ref = media.ingest_sync(data, source_format="image-bytes", origin=origin, policy=None)["value"]
        media.publish_sync([ref], origin=origin, disposition="retain")
        if classification is not None:
            media.classify_origin(ref, classification=classification)
        binding.rewrite([{
            "role": "user", "content": [{"type": "text", "text": f"[Session image {ref['sha256']}]"}],
            "metadata": {"experimental.media.v1": {"refs": [ref]}},
        }], expected=binding.expected, operation_id="seed-ref", admit=lambda: None, media_store=media)
        return ref, data
    finally:
        media.close()
        binding.close()


@contextmanager
def attached_owner(directory, *, resolved_config):
    """Exercise source checks on real reopened fd32 objects, without fake scanner."""
    from amplifier_app_cli.session_store import SessionStore

    with owners.SessionStorageOwner(
        directory, session_id="root", purpose="export", resolved_config=resolved_config,
    ) as owner:
        owner.projection = owners.project_policy("root", resolved_config=resolved_config)
        owner.media = SessionMediaStore(
            directory / "media", "root", policy=owner.projection.policy,
            checkpoint_verifier=versioned_checkpoint.verified_versioned_checkpoint_refs,
            policy_verifier=owner.source_policy,
        )
        owner.native = SessionStore(directory.parent, canonical_binding=owner.binding)
        owner.native.bind_media_store("root", owner.media)
        owner.epoch = owner.media.epoch
        yield owner


@pytest.mark.parametrize("restriction", ["computer-use", "unknown-class", "unknown-policy", "ttl", "retention"])
def test_real_reopened_source_policy_refuses_before_physical_read_or_ack(
    closed, monkeypatch, restriction,
):
    classification = restriction if restriction in {"computer-use", "unknown-class"} else None
    ref, _ = seed_ref(closed, classification=classification)
    config = {"session_media": {"retention": "durable"}}
    if restriction == "unknown-policy":
        config = None
    elif restriction == "ttl":
        config["session_media"]["ttl_seconds"] = 1
    elif restriction == "retention":
        config["session_media"]["retention"] = "request-only"
    with attached_owner(closed, resolved_config=config) as owner:
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(True)
            pytest.fail("Physical read/ACK occurred before source preflight")
        monkeypatch.setattr(owner.binding, "checkpoint", forbidden)
        monkeypatch.setattr(owner.media, "resolve_ref", forbidden)
        monkeypatch.setattr(owner.media, "resolve_published_ref", forbidden)
        with pytest.raises(owners.StorageOwnerHeld):
            owner.source_policy(ref, purpose="export")
        assert calls == []


def test_owned_plain_ref_needs_real_fresh_receipt_after_policy_check(closed):
    from amplifier_foundation.session.media import MediaRefusal

    ref, data = seed_ref(closed)
    with attached_owner(closed, resolved_config={"session_media": {"retention": "durable"}}) as owner:
        assert owner.source_policy(ref, purpose="export") is True
        with pytest.raises(MediaRefusal):
            owner.media.resolve_ref(ref)  # Old on-disk ACK is not a current receipt.
        with owner.binding.publication():
            receipt = owner.binding.checkpoint(expected=owner.current, media_store=owner.media)
            receipt.verify()
        with owner.reader(ref) as stream:
            assert stream.read() == data
        owner.media.revoke()
        with pytest.raises(owners.StorageOwnerHeld):
            owner.source_policy(ref, purpose="export")


def test_cleanup_failure_retains_actual_binding_and_does_not_retry(closed, monkeypatch):
    owner = owners.SessionStorageOwner(closed, session_id="root", purpose="preview")
    owner.__enter__()
    binding = owner.binding
    original = binding.close
    failure = RuntimeError("fixture close failure")
    calls = []

    def refuse():
        calls.append(True)
        raise failure
    monkeypatch.setattr(binding, "close", refuse)
    with pytest.raises(RuntimeError) as captured:
        owner.close()
    assert captured.value is failure
    assert binding_custody(binding).error is failure and binding_custody(binding).owner is owner
    with pytest.raises(owners.StorageOwnerHeld):
        owner.close()
    assert calls == [True]
    # Fixture-only physical teardown; production custody deliberately stays
    # retained with its error, never cleared by the controller.
    original()


def test_media_close_failure_keeps_lease_and_same_cleanup_owner(closed, monkeypatch):
    manager = attached_owner(closed, resolved_config={"session_media": {}})
    owner = manager.__enter__()
    media_close = owner.media.close
    binding_close = owner.binding.close
    error = RuntimeError("fixture media cleanup failed")
    calls = []
    def refuse():
        calls.append("media")
        raise error
    def forbidden():
        calls.append("binding")
        pytest.fail("Lease release preceded successful media settlement")
    monkeypatch.setattr(owner.media, "close", refuse)
    monkeypatch.setattr(owner.binding, "close", forbidden)
    with pytest.raises(RuntimeError) as captured:
        manager.__exit__(None, None, None)
    assert captured.value is error
    assert calls == ["media"]
    assert binding_custody(owner.binding).owner is owner
    assert binding_custody(owner.binding).error is error
    with pytest.raises(VersionedSessionConflict):
        VersionedSessionBinding.resume(closed, session_id="root", expected=current(closed))
    # Bounded fixture teardown only. The production custody record deliberately
    # retains the actual failed owner; there is no success/clear/retry path.
    media_close()
    binding_close()


def test_async_host_cancellation_releases_standalone_custody(closed):
    held_binding = []
    async def run():
        entered = asyncio.Event()
        async def work():
            with owners.SessionStorageOwner(closed, session_id="root", purpose="preview") as owner:
                held_binding.append(owner.binding)
                report = await owner.run()
                assert report["state"] in {"committed-source-only", "preview-only-not-applied"}
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(work())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert binding_custody(held_binding[0]) is None
    binding = VersionedSessionBinding.resume(closed, session_id="root", expected=current(closed))
    binding.close()


@pytest.mark.parametrize("seam", ["enter", "exit"])
def test_normal_registered_failures_do_not_format_private_diagnostics(closed, monkeypatch, seam):
    calls = []
    class PrivateError(LookupError):
        def __str__(self):
            calls.append("str")
            raise AssertionError("fixture-private-text")
        def __repr__(self):
            calls.append("repr")
            raise AssertionError("fixture-private-repr")
    error = PrivateError()
    error.__cause__ = ValueError("fixture-private-cause")
    error.add_note("fixture-private-note")
    original = owners.SessionStorageOwner.__enter__ if seam == "enter" else owners.SessionStorageOwner.__exit__
    def refuse(self, *args):
        if seam == "exit":
            original(self, *args)
        raise error
    monkeypatch.setattr(owners.SessionStorageOwner, "__enter__" if seam == "enter" else "__exit__", refuse)
    result = invoke(registered(), "preview", "root")
    if result.exit_code == 0 or result.output != (
        "Error: STORAGE_PREVIEW_FAILED: Admitted preview not confirmed; preserve source files and inspect storage.\n"
    ):
        pytest.fail("Normal controller emitted nonfixed diagnostics", pytrace=False)
    assert result.exception.__context__.__cause__ is error
    assert calls == []


def test_normal_text_prepare_apply_resume_status_noop(closed, tmp_path):
    cli = registered()
    preview = invoke(cli, "preview", "root")
    assert preview.exit_code == 0, preview.output
    plan = json.loads(preview.output)
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(plan))
    operation = tmp_path / "operation"
    args = ["root", operation, "--expected-plan", expected, "--operation-id", "stable-intent"]
    prepared = invoke(cli, "prepare", *args, "--export-assets")
    assert prepared.exit_code == 0, prepared.output
    before = current(closed)
    applied = invoke(cli, "apply", *args)
    assert applied.exit_code == 0, applied.output
    report = json.loads(applied.output)
    assert report["checkpoint_acknowledged"] and report["inline_removed_bytes"] == 0
    assert current(closed) == before
    status = invoke(cli, "status", "root", operation, "--operation-id", "stable-intent")
    assert status.exit_code == 0 and not json.loads(status.output)["checkpoint_acknowledged"]
    resumed = invoke(cli, "resume", *args)
    assert resumed.exit_code == 0 and json.loads(resumed.output)["checkpoint_acknowledged"]
    wrong = invoke(cli, "resume", "root", operation, "--expected-plan", expected, "--operation-id", "wrong")
    assert wrong.exit_code != 0
    exported = invoke(cli, "export", "root", operation, "--operation-id", "stable-intent")
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["portable"] == str(operation / "portable")


def test_actual_refs_first_controller_known_policy_and_normal_unknown_policy_held(closed, tmp_path):
    ref, data = seed_ref(closed)
    cli = registered()
    preview = invoke(cli, "preview", "root")
    expected = json.loads(preview.output)["plan"]
    operation = tmp_path / "operation"
    before = current(closed)
    async def prepare():
        with owners.SessionStorageOwner(
            closed, session_id="root", purpose="prepare", operation=operation,
            operation_id="actual-host-intent", resolved_config={"session_media": {"retention": "durable"}},
        ) as owner:
            candidate = await owner.run(
                expected_plan=json.dumps(expected, sort_keys=True), expected_json=lambda v: json.dumps(v, sort_keys=True),
                export_assets=True,
            )
            assert candidate["inline_removed_bytes"] == 0
            with owner.reader(ref) as stream:
                assert stream.read() == data
    asyncio.run(prepare())
    with owners.SessionStorageOwner(
        closed, session_id="root", purpose="apply", operation=operation,
        operation_id="actual-host-intent", resolved_config={"session_media": {"retention": "durable"}},
    ) as owner:
        result = asyncio.run(owner.run())
        assert result["checkpoint_acknowledged"] and result["inline_removed_bytes"] == 0
        assert owner.binding.expected == before
    # Normal standalone settings cannot silently substitute for missing actual
    # bundle/child runtime restrictions. Read-only preview remains available.
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(preview.output)
    result = invoke(cli, "prepare", "root", tmp_path / "unknown-policy",
                    "--expected-plan", expected_file, "--operation-id", "different-intent")
    assert result.exit_code != 0 and "STORAGE_T2_POLICY_HELD" in result.output
    assert not (tmp_path / "unknown-policy").exists()


def test_normal_help_distinguishes_policy_hold_from_api_support(closed):
    result = invoke(registered(), "--help")
    assert result.exit_code == 0
    assert "bundle/child policy" in result.output and "HELD" in result.output
    assert "Stable --operation-id" in result.output


def test_normal_explicit_settings_do_not_claim_missing_bundle_or_child_policy(closed, tmp_path, monkeypatch):
    ref, _ = seed_ref(closed)
    (closed / "settings.yaml").write_text(
        "config:\n  session_media:\n    enabled: true\n    retention: durable\n")
    cli = registered()
    preview = invoke(cli, "preview", "root")
    assert preview.exit_code == 0
    expected = tmp_path / "expected.json"
    expected.write_text(preview.output)
    before = files(closed)
    reads = []
    def forbidden(*args, **kwargs):
        reads.append(True)
        pytest.fail("Unknown effective policy allowed physical pixel read or checkpoint")
    monkeypatch.setattr(SessionMediaStore, "_open_verified", forbidden)
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", forbidden)
    result = invoke(cli, "prepare", "root", tmp_path / "held-policy",
                    "--expected-plan", expected, "--operation-id", "plain-local-intent")
    assert result.exit_code == 1 and "STORAGE_T2_POLICY_HELD" in result.output
    assert "API_HELD" not in result.output and "STORAGE_PREPARE_FAILED" not in result.output
    assert reads == [] and files(closed) == before and not (tmp_path / "held-policy").exists()


@pytest.mark.parametrize("kind", ["computer-use", "unrecognized-origin"])
def test_restricted_ref_preflight_never_reaches_checkpoint_or_pixels(closed, monkeypatch, kind):
    seed_ref(closed, classification=kind)
    with owners.SessionStorageOwner(
        closed, session_id="root", purpose="prepare", resolved_config={"session_media": {"retention": "durable"}},
    ) as owner:
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(True)
            pytest.fail("Restricted-source policy reached bootstrap ACK or physical pixels")
        monkeypatch.setattr(owner.binding, "checkpoint", forbidden)
        monkeypatch.setattr(SessionMediaStore, "_open_verified", forbidden)
        before = files(closed)
        with pytest.raises(owners.StorageOwnerHeld) as captured:
            owner.prepare_media()
        assert owners.public_held(captured.value).startswith("STORAGE_T2_SOURCE_HELD")
        assert calls == [] and files(closed) == before


@pytest.mark.parametrize("purpose", ["apply", "resume", "export"])
def test_normal_ref_workflow_missing_policy_refuses_before_bootstrap_ack(
    closed, tmp_path, monkeypatch, purpose,
):
    seed_ref(closed)
    cli = registered()
    preview = invoke(cli, "preview", "root")
    expected = tmp_path / "expected.json"
    expected.write_text(preview.output)
    operation = tmp_path / "known-candidate"
    async def prepare():
        with owners.SessionStorageOwner(
            closed, session_id="root", purpose="prepare", operation=operation,
            operation_id="stable-original", resolved_config={"session_media": {}},
        ) as owner:
            await owner.run(expected_plan=json.dumps(json.loads(preview.output)["plan"], sort_keys=True),
                            expected_json=lambda v: json.dumps(v, sort_keys=True), export_assets=True)
    asyncio.run(prepare())
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("Normal unknown-policy workflow reached physical read or bootstrap ACK")
    monkeypatch.setattr(SessionMediaStore, "_open_verified", forbidden)
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", forbidden)
    before = files(tmp_path)
    # No injected admission/controller/policy factory on the genuine command.
    args = [purpose, "root", operation, "--operation-id", "stable-original"]
    if purpose != "export":
        args += ["--expected-plan", expected]
    result = invoke(cli, *args)
    assert result.exit_code == 1 and "STORAGE_T2_POLICY_HELD" in result.output
    assert calls == [] and files(tmp_path) == before
    # Status is read-only and useful even though no new pixel policy is present.
    status = invoke(cli, "status", "root", operation, "--operation-id", "stable-original")
    assert status.exit_code == 0 and not json.loads(status.output)["checkpoint_acknowledged"]
    assert calls == [] and files(tmp_path) == before


def test_wrong_stable_id_refused_before_any_bootstrap_checkpoint(closed, tmp_path, monkeypatch):
    cli = registered()
    preview = invoke(cli, "preview", "root")
    expected = tmp_path / "expected.json"
    expected.write_text(preview.output)
    operation = tmp_path / "operation"
    args = ["root", operation, "--expected-plan", expected, "--operation-id", "original-intent"]
    assert invoke(cli, "prepare", *args).exit_code == 0
    assert invoke(cli, "apply", *args).exit_code == 0
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("Wrong operation ID caused bootstrap ACK")
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", forbidden)
    before = files(tmp_path)
    result = invoke(cli, "resume", "root", operation, "--expected-plan", expected, "--operation-id", "new-id")
    assert result.exit_code != 0
    assert calls == [] and files(tmp_path) == before


def test_expired_refs_preserve_original_metadata_without_ttl_or_pixel_copy(closed, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from amplifier_foundation.session import media as media_module, media_activation, transcript_lifetime

    expiry = datetime.now(UTC) + timedelta(days=1)
    ref, _ = seed_ref(closed, expires_at=expiry.isoformat())
    original = ref["provenance"].copy()
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return (expiry + timedelta(days=1)).astimezone(tz)
    # Deterministic fixture clock advancement only; original record bytes/TTL
    # remain untouched. No private retention record is read or fabricated.
    for module in (owners, media_module, media_conversion, media_activation, transcript_lifetime):
        monkeypatch.setattr(module, "datetime", Later, raising=False)
    cli = registered()
    preview = invoke(cli, "preview", "root")
    expected = json.loads(preview.output)["plan"]
    operation = tmp_path / "expired-operation"
    reads = []
    def forbidden(*args, **kwargs):
        reads.append(True)
        pytest.fail("Expired metadata-only maintenance read or copied pixels")
    monkeypatch.setattr(SessionMediaStore, "_open_verified", forbidden)
    async def prepare():
        with owners.SessionStorageOwner(
            closed, session_id="root", purpose="prepare", operation=operation, operation_id="expired-intent",
            resolved_config={"session_media": {"retention": "durable"}},
        ) as owner:
            result = await owner.run(expected_plan=json.dumps(expected, sort_keys=True),
                                     expected_json=lambda v: json.dumps(v, sort_keys=True), export_assets=True)
            assert result["inline_removed_bytes"] == 0 and not result["checkpoint_acknowledged"]
    asyncio.run(prepare())
    for purpose in ("apply", "resume", "export"):
        with owners.SessionStorageOwner(
            closed, session_id="root", purpose=purpose, operation=operation, operation_id="expired-intent",
            resolved_config={"session_media": {"retention": "durable"}},
        ) as owner:
            result = asyncio.run(owner.run())
            assert result["state"] in {"acknowledged", "portable-integrity-verified"}
        owner.verify_export_after_close()
    assert reads == []
    assert list((operation / "portable/assets").iterdir()) == []
    # Strict public scanner reads metadata DATA, never a pixel or whole row.
    with open_committed_source(closed) as source:
        refs = list(versioned_checkpoint.scan_serialized_refs(
            owners.CommittedRangeView(source), prefix=source.commit, check=lambda: None))
    assert refs == [ref] and refs[0]["provenance"] == original


def test_operation_inside_native_namespace_refused_before_owner_or_mkdir(closed):
    before = files(closed)
    result = invoke(registered(), "prepare", "root", closed / "transcript.v2/extra-operation",
                    "--expected-plan", closed / "absent-plan", "--operation-id", "bad-placement")
    # Expected-plan file is an input-only read; direct controller proves the
    # path boundary independently of which Click input validation fails first.
    assert result.exit_code != 0
    with pytest.raises(owners.StorageOwnerHeld) as captured:
        with owners.SessionStorageOwner(
            closed, session_id="root", purpose="prepare", operation=closed / "transcript.v2/extra-operation",
        ):
            pytest.fail("Operation adopted an owned native namespace")
    assert owners.public_held(captured.value).startswith("STORAGE_T2_PATH_HELD")
    assert files(closed) == before


def test_screenshot_preflight_precedes_bootstrap_checkpoint(closed, tmp_path, monkeypatch):
    seed_ref(closed, classification="computer-use")
    with owners.SessionStorageOwner(
        closed, session_id="root", purpose="prepare", resolved_config={"session_media": {"retention": "durable"}},
    ) as owner:
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(True)
            pytest.fail("Screenshot checkpoint bypassed source preflight")
        monkeypatch.setattr(owner.binding, "checkpoint", forbidden)
        with pytest.raises(owners.StorageOwnerHeld):
            owner.prepare_media()
        assert calls == []


def test_normal_unknown_inline_held_without_media_or_candidate(closed, tmp_path):
    binding = VersionedSessionBinding.resume(closed, session_id="root", expected=current(closed))
    try:
        import base64
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "purple").save(output, format="PNG")
        binding.rewrite([{"role": "user", "content": [{"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": base64.b64encode(output.getvalue()).decode(),
        }}]}], expected=binding.expected, operation_id="inline", admit=lambda: None)
    finally:
        binding.close()
    cli = registered()
    preview = invoke(cli, "preview", "root")
    expected = tmp_path / "expected.json"
    expected.write_text(preview.output)
    before = files(closed)
    result = invoke(cli, "prepare", "root", tmp_path / "operation", "--expected-plan", expected,
                    "--operation-id", "stable-inline")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["state"] == "held-original-policy"
    assert files(closed) == before and not (tmp_path / "operation").exists()