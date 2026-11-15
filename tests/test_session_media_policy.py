"""Normal registered owned-ref maintenance: real composition, custody and ACK.

Synthetic homes/manifests only; no execution preparation, credentials, network,
neighboring dependency overlays or host-policy/factory injection on positives.
"""

import asyncio
from dataclasses import asdict
import hashlib
import io
import json
import socket
import sys
from unittest.mock import AsyncMock

import click
from click.testing import CliRunner
from PIL import Image
import pytest
import yaml

from amplifier_foundation import Bundle
from amplifier_foundation.registry import BundleRegistry
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.sources.policy import source_resolution_policy
from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
from amplifier_foundation.session.versioned_checkpoint import verified_versioned_checkpoint_refs

from amplifier_app_cli import session_media_policy as policy
from amplifier_app_cli.commands.session import register_session_commands
from amplifier_app_cli.lib.settings import AppSettings
from amplifier_app_cli.runtime import config as runtime
from amplifier_app_cli.session_storage_owner import default_session_directory
from amplifier_app_cli.session_store import SessionStore


def digest_tree(path):
    return {str(p.relative_to(path)): (p.stat().st_mode, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in path.rglob("*") if p.is_file() and not p.is_symlink()}


@pytest.fixture
def external_requests_forbidden(monkeypatch):
    """Watch external clients/handlers, not asyncio's local self-pipe sockets."""
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        pytest.fail("Configuration-only path attempted an external request")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("httpx.Client.request", forbidden)
    monkeypatch.setattr("httpx.AsyncClient.request", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("amplifier_foundation.sources.http.urlopen", forbidden)
    monkeypatch.setattr("amplifier_foundation.sources.zip.urlopen", forbidden)
    monkeypatch.setattr("amplifier_foundation.sources.git.GitSourceHandler.resolve", forbidden)
    monkeypatch.setattr("amplifier_foundation.sources.http.HttpSourceHandler.resolve", forbidden)
    monkeypatch.setattr("amplifier_foundation.sources.zip.ZipSourceHandler.resolve", forbidden)
    yield attempts
    assert attempts == [], "Configuration-only path attempted an external request"


@pytest.fixture
def configuration(tmp_path, monkeypatch, isolated_home, external_requests_forbidden):
    home = tmp_path / ".amplifier"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "bundle.md").write_text(
        "---\nbundle:\n  name: owned\nsession:\n  orchestrator: {module: never-import-orchestrator}\n"
        "providers:\n  - module: never-import-provider\n    config: {api_key: '${UNSET_TEST_CREDENTIAL}'}\n"
        "---\n@owned:context/system.md\n"
    )
    settings = {
        "bundle": {"added": {"owned": root.as_uri()}, "active": "must-not-substitute"},
        "config": {"session_media": {"retention": "durable", "max_pixels": 100}},
    }
    settings_path = home / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(settings))
    uris = [
        *runtime._build_imagegen_behaviors(), *runtime._build_modes_behaviors(),
        *runtime._build_skills_behaviors(), *runtime._build_routing_behaviors(AppSettings()),
        *runtime._build_wayfinder_behaviors(),
    ]
    # Populate a genuine public registry's exact source bindings from local
    # synthetic manifests. This is cache setup, never an owner/factory toggle.
    sources = {}
    for index, uri in enumerate(uris):
        directory = tmp_path / f"cached-{index}"
        directory.mkdir()
        manifest = directory / "bundle.yaml"
        manifest.write_text(f"bundle:\n  name: fixture-{index}\n")
        sources[uri] = manifest

    async def cache():
        registry = BundleRegistry(home=home, strict=True, read_persisted=False)
        def admit(uri, *, base_path):
            path = sources[uri]
            return ResolvedSource(active_path=path, source_root=path.parent)
        with source_resolution_policy(admit):
            for uri in uris:
                await registry.load(uri)
        registry.save()
    asyncio.run(cache())

    def forbidden(*args, **kwargs):
        pytest.fail("Configuration-only path crossed execution/network boundary")
    monkeypatch.setattr(Bundle, "prepare", forbidden)
    monkeypatch.setattr("amplifier_foundation.bundle.PreparedBundle.create_session", forbidden)
    monkeypatch.setattr(runtime, "_validate_provider_credentials", forbidden)
    monkeypatch.setattr("amplifier_app_cli.commands.init.check_first_run", forbidden)
    monkeypatch.setattr("amplifier_foundation.session.history.SessionHistoryStore.load_messages", forbidden)
    monkeypatch.setattr("amplifier_app_cli.lib.bundle_loader.discovery.importlib.import_module", forbidden)
    return home, root, settings_path, sources


def require_linux_custody():
    if sys.platform != "linux":
        pytest.skip("Actual prospective Foundation custody is qualified on Linux only")


def declare(configuration, **limits):
    _, _, path, _ = configuration
    settings = yaml.safe_load(path.read_text())
    settings["config"]["session_media"] = limits
    path.write_text(yaml.safe_dump(settings))


def prospective(configuration, *, sid="root", actual=None, classification=None, image_format="PNG"):
    require_linux_custody()  # Before selector, mkdir, media or binding effects.
    directory = default_session_directory(sid)
    directory.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    binding = VersionedSessionBinding.create_new(
        directory, session_id=sid, operation_id="allocate", admit=lambda: None,
    )
    media = SessionMediaStore(
        directory / "media", sid, policy=actual or MediaPolicy(max_images=2),
        checkpoint_verifier=verified_versioned_checkpoint_refs,
    )
    native = SessionStore(directory.parent, canonical_binding=binding)
    native.bind_media_store(sid, media)
    output = io.BytesIO()
    Image.new("RGB", (3, 2), "purple").save(output, format=image_format)
    raw = output.getvalue()
    origin = {"kind": "upload", "session_id": sid, "id": "original-upload",
              "created_at": "2024-01-02T03:04:05+00:00", "expires_at": None}
    ref = media.ingest_sync(raw, source_format="image-bytes", origin=origin, policy=None)["value"]
    rows = [{"role": "user", "content": [{"type": "text", "text": f"[Session image {ref['sha256']}]"}],
             "metadata": {"experimental.media.v1": {"refs": [ref]}}}]
    try:
        def admit():
            media.publish_sync([ref], origin=origin, disposition="retain")
            if classification is not None:
                media.classify_origin(ref, classification=classification)
        receipt = native.save_versioned(
            sid, rows, {"bundle": "bundle:owned"}, expected=binding.expected,
            operation_id="original-publication", admit=admit,
        )
        receipt.verify()
        return directory, ref, raw, rows, binding.expected
    finally:
        media.close()
        binding.close()


def registered():
    cli = click.Group()
    register_session_commands(cli, interactive_chat=AsyncMock(), execute_single=AsyncMock(),
                              get_module_search_paths=list)
    return cli


def invoke(cli, *args):
    return CliRunner().invoke(cli, ["session", "storage", *map(str, args)])


def saved_preview(cli, tmp_path, sid="root"):
    preview = invoke(cli, "preview", sid)
    assert preview.exit_code == 0, (preview.output, preview.exception)
    path = tmp_path / "expected.json"
    path.write_text(preview.output)
    return path


def test_configonly_complete_normal_composition_and_once_expansion(configuration, monkeypatch):
    home, root, _, _ = configuration
    monkeypatch.setenv("POLICY_ONCE", "${NEVER_EXPAND_TWICE}")
    settings = yaml.safe_load((home / "settings.yaml").read_text())
    settings["overrides"] = {"never-import-orchestrator": {"config": {"marker": "${POLICY_ONCE}"}}}
    (home / "settings.yaml").write_text(yaml.safe_dump(settings))
    before = digest_tree(home)
    config = asyncio.run(policy.resolve_declared_config_async(
        "owned", AppSettings(), session_id="root", project_slug="fixture",
    ))
    assert config["session"]["orchestrator"]["config"]["marker"] == "${NEVER_EXPAND_TWICE}"
    assert config["session_media"]["max_pixels"] == 100
    assert config["session_media"]["retention"] == "durable"
    assert digest_tree(home) == before
    assert config["providers"][0]["module"] == "never-import-provider"


@pytest.mark.parametrize("sid,image_format", [("root", "PNG"), ("root_child", "JPEG")])
def test_normal_registered_owned_refs_prepare_apply_resume_export(configuration, monkeypatch, tmp_path, sid, image_format):
    from amplifier_foundation.session import media_activation
    from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
    import amplifier_app_cli.commands.session as commands

    directory, ref, raw, rows, original = prospective(configuration, sid=sid, image_format=image_format)
    record = SessionMetadataStore(directory).read()[policy.RESTRICTIONS_KEY]
    assert record["prefix"] == asdict(original)
    assert record["restrictions"]["max_images"] == 2
    monkeypatch.setattr(commands, "SessionStore", lambda: pytest.fail("Unbound history factory"))
    cli = registered()
    operation = tmp_path / "operation"
    baseline = digest_tree(directory / "transcript.v2")
    plan = saved_preview(cli, tmp_path, sid)
    prepared = invoke(cli, "prepare", sid, operation,
                      "--operation-id", "ordinary-refs", "--expected-plan", plan, "--export-assets")
    assert prepared.exit_code == 0, (prepared.output, prepared.exception)
    assert json.loads(prepared.output)["optimization"] == "already-externalized-no-inline-removal"
    receipts = []
    actual = media_activation.apply_committed_candidate
    def observe(*args, **kwargs):
        result = actual(*args, **kwargs)
        receipts.append(result.checkpoint)
        return result
    monkeypatch.setattr(media_activation, "apply_committed_candidate", observe)
    for command in ("apply", "resume"):
        result = invoke(cli, command, sid, operation, "--expected-plan", plan, "--operation-id", "ordinary-refs")
        assert result.exit_code == 0, (result.output, result.exception)
        report = json.loads(result.output)
        assert report["checkpoint_acknowledged"] is True
        assert report["inline_removed_bytes"] == 0
        assert report["commit"] == asdict(original)
    assert receipts and type(receipts[0]) is VersionedCheckpointReceipt
    receipts[0].verify()
    result = invoke(cli, "export", sid, operation, "--operation-id", "ordinary-refs")
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.output)["state"] == "portable-integrity-verified"
    assert digest_tree(directory / "transcript.v2") == baseline
    assert SessionMetadataStore(directory).read()[policy.RESTRICTIONS_KEY] == record
    # Original clock/provenance and bytes remain exactly unchanged.
    binding = VersionedSessionBinding.resume(directory, session_id=sid, expected=original)
    try:
        assert binding.load_messages() == rows
        reopened = SessionMediaStore(
            directory / "media", sid, checkpoint_verifier=verified_versioned_checkpoint_refs,
        )
        try:
            with binding.publication():
                binding.checkpoint(expected=original, media_store=reopened)
            with reopened.resolve_ref(ref) as stream:
                assert stream.read() == raw
        finally:
            reopened.close()
    finally:
        binding.close()


@pytest.mark.parametrize("restriction", [
    {"ttl_seconds": 1}, {"retention": "drop"}, {"retention": "request-only"},
    {"enabled": False}, {"max_pixels": 1}, {"max_bytes": 1},
])
def test_current_restrictions_hold_before_pixels_or_ack(configuration, monkeypatch, tmp_path, restriction):
    directory, _, _, _, _ = prospective(configuration)
    declare(configuration, **restriction)
    def forbidden(*args, **kwargs):
        pytest.fail("Restricted ref reached pixels/ACK")
    monkeypatch.setattr(SessionMediaStore, "resolve_ref", forbidden)
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", forbidden)
    before = digest_tree(directory)
    cli = registered()
    plan = saved_preview(cli, tmp_path)
    result = invoke(cli, "prepare", "root", tmp_path / "held",
                    "--operation-id", "held", "--expected-plan",
                    plan)
    assert result.exit_code != 0 and ("STORAGE_T2_POLICY_HELD" in result.output or "STORAGE_T2_SOURCE_HELD" in result.output)
    assert digest_tree(directory) == before


@pytest.mark.parametrize("damage", [
    "missing-record", "malformed-record", "mismatch", "fake-known", "cache-miss",
    "include-miss", "app-miss", "missing-saved-selection",
])
def test_unavailable_facts_never_grant_normal_refs(configuration, monkeypatch, tmp_path, damage):
    directory, _, _, _, _ = prospective(configuration)
    metadata = SessionMetadataStore(directory).read()
    home, root, settings_path, sources = configuration
    if damage == "missing-record":
        metadata.pop(policy.RESTRICTIONS_KEY)
    elif damage == "malformed-record":
        metadata[policy.RESTRICTIONS_KEY]["restrictions"]["ttl_seconds"] = "invalid"
    elif damage == "mismatch":
        metadata[policy.RESTRICTIONS_KEY]["prefix"]["sequence"] += 1
    elif damage == "fake-known":
        metadata[policy.RESTRICTIONS_KEY] = {"known": True, "allow": True}
    elif damage == "cache-miss":
        next(iter(sources.values())).unlink()
    elif damage == "include-miss":
        with (root / "bundle.md").open("w") as stream:
            stream.write("---\nbundle: {name: owned}\nincludes: [{bundle: 'https://unavailable.invalid/include'}]\n---\n")
    elif damage == "app-miss":
        settings = yaml.safe_load(settings_path.read_text())
        settings["bundle"]["app"] = ["https://unavailable.invalid/app"]
        settings_path.write_text(yaml.safe_dump(settings))
    elif damage == "missing-saved-selection":
        metadata.pop("bundle")
        settings = yaml.safe_load(settings_path.read_text())
        settings["bundle"]["active"] = "owned"
        settings_path.write_text(yaml.safe_dump(settings))
    (directory / "metadata.json").write_text(json.dumps(metadata))
    before = digest_tree(directory)
    monkeypatch.setattr(SessionMediaStore, "resolve_ref", lambda *a, **k: pytest.fail("Pixels before facts"))
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", lambda *a, **k: pytest.fail("ACK before facts"))
    cli = registered()
    plan = saved_preview(cli, tmp_path)
    result = invoke(cli, "prepare", "root", tmp_path / "held",
                    "--operation-id", "held", "--expected-plan",
                    plan)
    assert result.exit_code != 0 and "STORAGE_T2_POLICY_HELD" in result.output, (result.output, result.exception)
    assert digest_tree(directory) == before


@pytest.mark.parametrize("classification", ["computer-use", "unknown-owner"])
def test_normal_restricted_classification_still_needs_actual_validator(configuration, monkeypatch, tmp_path, classification):
    directory, _, _, _, _ = prospective(configuration, classification=classification)
    monkeypatch.setattr(SessionMediaStore, "resolve_ref", lambda *a, **k: pytest.fail("Pixels before validator"))
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", lambda *a, **k: pytest.fail("ACK before validator"))
    cli = registered()
    plan = saved_preview(cli, tmp_path)
    result = invoke(cli, "prepare", "root", tmp_path / "held",
                    "--operation-id", "held", "--expected-plan",
                    plan)
    assert result.exit_code != 0 and "STORAGE_T2_SOURCE_HELD" in result.output


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Actual prospective Foundation custody is qualified on Linux only")
async def test_first_root_child_capture_uses_actual_store_and_resume_only_tightens(tmp_path):
    require_linux_custody()
    from amplifier_core import AmplifierSession
    from amplifier_app_cli.session_media import install_session_media, restrict_child_media

    sessions = tmp_path / "sessions"
    sessions.mkdir(mode=0o700)
    bindings, hosts = [], []
    try:
        def allocate(sid, config, actual=None):
            binding = VersionedSessionBinding.create_new(
                sessions / sid, session_id=sid, operation_id="allocate", admit=lambda: None,
            )
            bindings.append(binding)
            native = SessionStore(sessions, canonical_binding=binding)
            session = AmplifierSession(
                config={"session": {"orchestrator": "never-mount", "context": "never-mount"}, **config},
                session_id=sid,
            )
            host = install_session_media(session, native, config=actual)
            hosts.append(host)
            return binding, native, session, host

        parent, parent_native, parent_session, parent_host = allocate(
            "parent", {"session_media": {"max_pixels": 1000, "ttl_seconds": 600}},
            {"session_media": {"max_pixels": 100, "ttl_seconds": 60}},
        )
        parent_native.save_versioned(
            "parent", [], {}, expected=parent.expected, operation_id="first",
            admit=lambda: None,
        )
        assert SessionMetadataStore(parent.directory).read()[policy.RESTRICTIONS_KEY]["restrictions"]["ttl_seconds"] == 60
        child_config = restrict_child_media(
            parent_session, {"session_media": {"max_pixels": 500, "ttl_seconds": 300}},
        )
        child, native, _, host = allocate("parent_child", child_config)
        assert host.store.policy.max_pixels == 100 and host.store.policy.ttl_seconds == 60
        native.save_versioned(
            "parent_child", [{"role": "user", "content": "first"}],
            {"config": {"session_media": {"max_pixels": 9999}},
             policy.RESTRICTIONS_KEY: {"known": True, "allow": True}},
            expected=child.expected, operation_id="first-child", admit=lambda: None,
        )
        first = SessionMetadataStore(child.directory).read()[policy.RESTRICTIONS_KEY]
        assert first["restrictions"]["max_pixels"] == 100 and first["restrictions"]["ttl_seconds"] == 60
        assert first["prefix"] == asdict(child.expected)
        await host.close()
        child.close()
        reopened = VersionedSessionBinding.resume(
            child.directory, session_id="parent_child", expected=child.expected,
        )
        bindings.append(reopened)
        native = SessionStore(sessions, canonical_binding=reopened)
        for index, maximum in enumerate((9000, 50, 9999)):
            media = SessionMediaStore(
                child.directory / "media", "parent_child", policy=MediaPolicy(max_pixels=maximum),
                checkpoint_verifier=verified_versioned_checkpoint_refs,
            )
            native.bind_media_store("parent_child", media)
            try:
                native.save_versioned(
                    "parent_child", [{"role": "user", "content": f"turn {index}"}],
                    {policy.RESTRICTIONS_KEY: first},
                    expected=reopened.expected, operation_id=f"later-{index}", admit=lambda: None,
                )
                record = SessionMetadataStore(child.directory).read()[policy.RESTRICTIONS_KEY]
                assert record["restrictions"]["max_pixels"] == (100 if index == 0 else 50)
                assert record["restrictions"]["ttl_seconds"] == 60
                assert record["prefix"] == asdict(reopened.expected)
            finally:
                native.unbind_media_store("parent_child", media)
                media.close()
    finally:
        for host in reversed(hosts):
            await host.close()
        for binding in reversed(bindings):
            binding.close()


def test_no_retrospective_capture_and_postcommit_metadata_failure_latches(configuration, monkeypatch):
    directory, _, _, rows, expected = prospective(configuration)
    metadata = SessionMetadataStore(directory).read()
    metadata.pop(policy.RESTRICTIONS_KEY)
    (directory / "metadata.json").write_text(json.dumps(metadata))
    binding = VersionedSessionBinding.resume(directory, session_id="root", expected=expected)
    media = SessionMediaStore(
        directory / "media", "root", checkpoint_verifier=verified_versioned_checkpoint_refs,
    )
    native = SessionStore(directory.parent, canonical_binding=binding)
    native.bind_media_store("root", media)
    try:
        native.save_versioned(
            "root", rows, {policy.RESTRICTIONS_KEY: {"known": True, "allow": True}},
            expected=binding.expected, operation_id="old-no-capture", admit=lambda: None,
        )
        assert policy.RESTRICTIONS_KEY not in SessionMetadataStore(directory).read()
        before = binding.expected
        error = OSError("fixture metadata publication failed")
        def fail(*args):
            raise error
        monkeypatch.setattr(native, "_save_metadata", fail)
        with pytest.raises(OSError) as raised:
            native.save_versioned(
                "root", rows, {}, expected=before, operation_id="metadata-fails", admit=lambda: None,
            )
        assert raised.value is error
        assert binding.expected != before and native.versioned_publication_failure is error
        with pytest.raises(RuntimeError, match="owner reconciliation"):
            native.save_versioned(
                "root", rows, {}, expected=binding.expected, operation_id="never-replay", admit=lambda: None,
            )
    finally:
        media.close()
        binding.close()


def test_offline_discovery_preserves_legacy_precedence_without_migration(configuration):
    home, root, path, _ = configuration
    (home / "bundle-registry.yaml").write_text(yaml.safe_dump({
        "bundles": {"owned": {"uri": (root / "legacy.yaml").as_uri()}},
    }))
    before = digest_tree(home)
    discovery = policy.offline_discovery(AppSettings())
    assert discovery.find("owned") == (root / "legacy.yaml").as_uri()
    assert digest_tree(home) == before
    (root.parent / ".amplifier/settings.local.yaml").write_text(yaml.safe_dump({
        "bundle": {"added": {"owned": root.as_uri()}},
    }))
    assert policy.offline_discovery(AppSettings()).find("owned") == root.as_uri()


def test_file_fragment_exact_containment_and_unsupported_bundle_policy_not_inferred(configuration):
    _, root, _, _ = configuration
    included = root / "included.yaml"
    included.write_text("bundle: {name: included}\nsession_media: {enabled: true, known: true}\n")
    discovery = policy.offline_discovery(AppSettings())
    resolved = discovery.admit_source(root.as_uri() + "#subdirectory=included.yaml", base_path=root)
    assert resolved.active_path == included and resolved.source_root == root
    for suffix in ("#other=included.yaml", "#subdirectory=../root", "#subdirectory=/etc/passwd"):
        with pytest.raises(Exception):
            discovery.admit_source(root.as_uri() + suffix, base_path=root)
    manifest = root / "bundle.md"
    manifest.write_text(
        "---\nbundle: {name: owned}\nsession_media: {retention: drop}\n---\n"
        "@owned:context/system.md\n"
    )
    config = asyncio.run(policy.resolve_declared_config_async(
        "owned", AppSettings(), session_id="root", project_slug="fixture",
    ))
    # Unsupported raw Bundle YAML never becomes a second policy contract.
    assert config["session_media"]["retention"] == "durable"


def test_normal_owner_rechecks_current_policy_at_effect_fence(configuration):
    from amplifier_app_cli.session_storage_owner import default_owner_factory, StorageOwnerHeld

    directory, _, _, _, _ = prospective(configuration)
    with default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known
        before = digest_tree(directory)
        declare(configuration, retention="drop")
        with pytest.raises(StorageOwnerHeld) as raised:
            owner.check()
        assert raised.value.reason == "policy"
        assert digest_tree(directory) == before


@pytest.mark.parametrize("missing", ["remote", "namespace"])
def test_transitive_include_failure_is_not_current_policy(configuration, monkeypatch, tmp_path, missing):
    """c2 can skip A's dependency error as a cycle despite strict=True."""
    directory, _, _, _, _ = prospective(configuration)
    home, root, _, _ = configuration
    child = root / "child.yaml"
    target = "https://unavailable.invalid/nested.yaml" if missing == "remote" else "absent:behaviors/missing"
    child.write_text(yaml.safe_dump({
        "bundle": {"name": "nested-child"}, "includes": [{"bundle": target}],
    }))
    (root / "bundle.md").write_text(
        "---\nbundle: {name: owned}\nincludes:\n  - bundle: " + child.as_uri() + "\n---\n"
    )
    before = digest_tree(home)
    with pytest.raises(ValueError, match="Current policy composition is incomplete"):
        asyncio.run(policy.resolve_declared_config_async(
            "owned", AppSettings(), session_id="root", project_slug="fixture",
        ))
    assert digest_tree(home) == before
    monkeypatch.setattr(SessionMediaStore, "resolve_ref", lambda *a, **k: pytest.fail("Pixels before closure"))
    monkeypatch.setattr(VersionedSessionBinding, "checkpoint", lambda *a, **k: pytest.fail("ACK before closure"))
    cli = registered()
    plan = saved_preview(cli, tmp_path)
    result = invoke(cli, "prepare", "root", tmp_path / "incomplete",
                    "--operation-id", "incomplete", "--expected-plan", plan)
    assert result.exit_code != 0 and "STORAGE_T2_POLICY_HELD" in result.output
    assert digest_tree(home) == before


@pytest.mark.parametrize("sid", ["root", "root_child"])
def test_configonly_does_not_read_or_log_private_agent_frontmatter(configuration, monkeypatch, tmp_path, caplog, sid):
    """Assert on raw LogRecords, not only sanitized Click diagnostics."""
    directory, _, _, _, _ = prospective(configuration, sid=sid)
    home, root, _, _ = configuration
    agents = root / "agents"
    agents.mkdir()
    sentinel = "opaque-private-agent-payload-8c13"
    (agents / "private.md").write_text("---\nmeta:\n  description: [" + sentinel + "\n---\n")
    (root / "bundle.md").write_text(
        "---\nbundle: {name: owned}\nagents:\n  include: [owned:private]\n---\n"
        "@owned:context/system.md\n"
    )
    caplog.clear()
    config = asyncio.run(policy.resolve_declared_config_async(
        "owned", AppSettings(), session_id=sid, project_slug="fixture",
    ))
    assert config["session_media"]["retention"] == "durable"
    # Metadata loading is unnecessary for this top-level host declaration
    # projection. This is NOT a claim of fresh child agent-overlay policy.
    raw = [(record.msg, record.args, record.exc_info) for record in caplog.records]
    assert all(sentinel not in str(row) for row in raw), "Raw logger received private frontmatter"
    assert all("Failed to load metadata for agent" not in record.getMessage()
               for record in caplog.records), "Agent metadata loading was attempted"

    original_read = type(root).read_text
    def no_agent_read(path, *args, **kwargs):
        if path == agents / "private.md":
            pytest.fail("Policy projection read private agent frontmatter")
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(type(root), "read_text", no_agent_read)
    cli = registered()
    plan = saved_preview(cli, tmp_path, sid)
    result = invoke(cli, "prepare", sid, tmp_path / "safe-agent-omission",
                    "--operation-id", "safe-agent-omission", "--expected-plan", plan)
    assert result.exit_code == 0, (result.output, result.exception)


def test_configonly_malformed_root_has_fixed_public_error_without_raw_log(configuration, caplog):
    _, root, _, _ = configuration
    sentinel = "opaque-root-yaml-fragment-2bc7"
    (root / "bundle.md").write_text("---\nbundle:\n  name: [" + sentinel + "\n---\n")
    caplog.clear()
    with pytest.raises(policy.CurrentPolicyIncomplete) as raised:
        asyncio.run(policy.resolve_declared_config_async(
            "owned", AppSettings(), session_id="root", project_slug="fixture",
        ))
    assert str(raised.value) == "Current policy composition is incomplete"
    assert raised.value.__suppress_context__ is True
    assert raised.value.__cause__ is None
    assert all(sentinel not in str((r.msg, r.args, r.exc_info)) for r in caplog.records), "Raw root logger leaked"


def test_configonly_failure_message_and_notes_are_not_formatted(configuration, monkeypatch, caplog):
    class OpaqueFailure(Exception):
        def __str__(self):
            pytest.fail("Private exception was formatted")

    fault = OpaqueFailure("opaque-message-894d")
    fault.add_note("opaque-note-273e")
    def fail(_):
        raise fault
    monkeypatch.setattr(Bundle, "to_mount_plan", fail)
    caplog.clear()
    with pytest.raises(policy.CurrentPolicyIncomplete) as raised:
        asyncio.run(policy.resolve_declared_config_async(
            "owned", AppSettings(), session_id="root", project_slug="fixture",
        ))
    assert raised.value.args == ("Current policy composition is incomplete",)
    assert raised.value.__suppress_context__ is True and raised.value.__cause__ is None
    assert raised.value.__context__ is fault  # Retained privately, not public text.
    assert not getattr(raised.value, "__notes__", [])
    assert all(r.exc_info is None for r in caplog.records)
    assert all("opaque-" not in str((r.msg, r.args)) for r in caplog.records)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_prospective_fixture_skips_before_selector_or_filesystem(tmp_path, monkeypatch, platform):
    def unexpected(*args):
        pytest.fail("Unsupported fixture reached selector/filesystem")
    before = digest_tree(tmp_path)
    monkeypatch.setitem(globals(), "default_session_directory", unexpected)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", platform)
        with pytest.raises(pytest.skip.Exception, match="qualified on Linux only"):
            prospective(None)
    assert digest_tree(tmp_path) == before


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_actual_foundation_unsupported_platform_refuses_before_filesystem(tmp_path, monkeypatch, platform):
    """Simulation exercises the installed public refusal, not native parity."""
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionConflict

    before = digest_tree(tmp_path)
    def unexpected():
        pytest.fail("Unsupported Foundation binding reached asset admission")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", platform)
        with pytest.raises(VersionedSessionConflict, match="qualified on Linux only"):
            VersionedSessionBinding.create_new(
                tmp_path / "must-not-create" / "root", session_id="root",
                operation_id="unsupported", admit=unexpected,
            )
    assert digest_tree(tmp_path) == before


def test_external_request_tripwires_allow_local_selfpipe_and_event_loop(external_requests_forbidden):
    # Windows ProactorEventLoop needs a socketpair, whose stdlib fallback
    # connects to its own local listener. That is not a manifest/client fetch.
    left, right = socket.socketpair()
    try:
        left.sendall(b"x")
        assert right.recv(1) == b"x"
    finally:
        left.close()
        right.close()
    assert asyncio.run(asyncio.sleep(0, result="loop-ready")) == "loop-ready"
    assert external_requests_forbidden == []
    assert socket.create_connection.__name__ == "forbidden"


@pytest.mark.parametrize("uri,expected", [
    ("file:///D:/fixture/root", r"D:\fixture\root"),
    ("file:///D:/fixture/space%20and%23hash.yaml", r"D:\fixture\space and#hash.yaml"),
    ("file:///D:/fixture/literal%2520.yaml", r"D:\fixture\literal%20.yaml"),
    ("file:///D:/fixture/caf%C3%A9.yaml", "D:\\fixture\\café.yaml"),
])
def test_windows_file_uri_uses_native_conversion_once(monkeypatch, uri, expected):
    """Run the Windows stdlib conversion on any OS; no Windows I/O claim."""
    import nturl2path
    from pathlib import PureWindowsPath
    from urllib.parse import urlsplit

    monkeypatch.setattr(policy, "url2pathname", nturl2path.url2pathname)
    monkeypatch.setattr(policy, "Path", PureWindowsPath)
    selected = policy._native_file_uri_path(urlsplit(uri).path)
    assert selected == PureWindowsPath(expected)
    assert selected.drive == "D:" and selected.is_absolute()


@pytest.mark.parametrize("encoded_path", ["//remote/share/file", "/%2Fremote/share/file", "/%5C%5Cremote/share/file"])
def test_windows_native_conversion_does_not_admit_unc(monkeypatch, encoded_path):
    import nturl2path
    from pathlib import PureWindowsPath
    from amplifier_foundation.sources.policy import SourceResolutionDenied

    monkeypatch.setattr(policy, "url2pathname", nturl2path.url2pathname)
    monkeypatch.setattr(policy, "Path", PureWindowsPath)
    with pytest.raises(SourceResolutionDenied, match="Nonlocal manifest"):
        policy._native_file_uri_path(encoded_path)


def test_native_file_uri_preserves_encoded_names_and_refuses_nonlocal_forms(configuration):
    from amplifier_foundation.sources.policy import SourceResolutionDenied

    _, root, _, _ = configuration
    manifest = root / "space # literal%20.yaml"
    manifest.write_text("bundle: {name: encoded}\n")
    discovery = policy.offline_discovery(AppSettings())
    uri = manifest.as_uri()
    assert "%20" in uri and "%23" in uri and "%2520" in uri
    assert discovery.admit_source(uri, base_path=root).active_path == manifest
    for bad_uri in (
        uri + "?query=yes", uri + "#unexpected=yes",
        "file://foreign-host" + uri[len("file://"):],
        "file://user@localhost" + uri[len("file://"):],
    ):
        with pytest.raises(SourceResolutionDenied):
            discovery.admit_source(bad_uri, base_path=root)


def test_local_fragment_cannot_escape_through_symlink(configuration, tmp_path):
    from amplifier_foundation.sources.policy import SourceResolutionDenied

    _, root, _, _ = configuration
    outside = tmp_path / "outside.yaml"
    outside.write_text("bundle: {name: outside}\n")
    link = root / "escape.yaml"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This host does not permit creating the symlink test fixture")
    discovery = policy.offline_discovery(AppSettings())
    with pytest.raises(SourceResolutionDenied):
        discovery.admit_source(root.as_uri() + "#subdirectory=escape.yaml", base_path=root)