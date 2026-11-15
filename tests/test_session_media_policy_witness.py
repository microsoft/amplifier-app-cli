"""Operation-local policy reuse, with byte/namespace checks on every guard."""

import json
from pathlib import Path
import tracemalloc

import pytest
import yaml

from amplifier_app_cli import session_media_policy as policy
from amplifier_app_cli import session_storage_owner as owners
from amplifier_app_cli.runtime import config as runtime
from amplifier_foundation.session.metadata import SessionMetadataStore
from test_session_media_policy import (  # noqa: F401
    configuration, external_requests_forbidden, prospective, registered, invoke,
    require_linux_custody,
)


@pytest.fixture
def counted(monkeypatch):
    calls = []
    actual = runtime.resolve_bundle_config

    async def resolve(*args, **kwargs):
        calls.append(True)
        return await actual(*args, **kwargs)

    monkeypatch.setattr(runtime, "resolve_bundle_config", resolve)
    return calls


def test_repeated_checks_one_composition_bounded_allocation(configuration, counted):
    directory, _, _, _, _ = prospective(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known
        assert owner._policy_witness is not None
        assert len(counted) == 1
        tracemalloc.start()
        try:
            for _ in range(128):
                owner.check()
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(counted) == 1
        assert peak < 256 * 1024, (current, peak)
        print(json.dumps({"checks": 128, "compositions": len(counted),
                          "current": current, "peak": peak}))


@pytest.mark.parametrize("change", [
    "global", "project", "local", "session", "environment", "root-content",
    "include-content", "registration", "legacy", "legacy-marker", "cache-binding",
    "restriction-metadata", "same-byte-replacement", "source-alias", "higher-priority",
])
def test_changed_input_next_guard_held(configuration, counted, tmp_path, monkeypatch, change):
    home, root, settings_path, sources = configuration
    directory, _, _, _, _ = prospective(configuration)
    included = root / "included.yaml"
    included.write_text("bundle: {name: included}\n")
    manifest = root / "bundle.md"
    if change == "include-content":
        manifest.write_text("---\nbundle: {name: owned}\nincludes:\n- bundle: "
                            + included.as_uri() + "\n---\n")
    if change == "source-alias":
        alias = tmp_path / "aliased-root"
        alias.symlink_to(root, target_is_directory=True)
        settings = yaml.safe_load(settings_path.read_text())
        settings["bundle"]["added"]["owned"] = alias.as_uri()
        settings_path.write_text(yaml.safe_dump(settings))
    if change == "higher-priority":
        # Remove explicit registration; normal discovery selects user root.
        settings = yaml.safe_load(settings_path.read_text())
        settings["bundle"]["added"].pop("owned")
        settings_path.write_text(yaml.safe_dump(settings))
        (home / "bundles").mkdir()
        (home / "bundles" / "owned.yaml").write_text("bundle: {name: owned}\n")
        # Fixture HOME == cwd/.amplifier; use a separate cwd for distinct scopes.
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        # Owner is selected explicitly only for this discovery-input test;
        # the positive count test uses the unpatched normal factory.
    with owners.SessionStorageOwner(directory, session_id="root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known and owner._policy_witness is not None
        initial_calls = len(counted)
        if change == "global":
            settings_path.write_text(settings_path.read_text() + "\n# changed bytes\n")
        elif change in {"project", "local", "session"}:
            target = {
                "project": Path.cwd() / ".amplifier/settings.yaml",
                "local": Path.cwd() / ".amplifier/settings.local.yaml",
                "session": directory / "settings.yaml",
            }[change]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("config:\n  session_media: {max_pixels: 1}\n")
        elif change == "environment":
            monkeypatch.setenv("CLI_POLICY_WITNESS_TEST", "changed-no-value-logging")
        elif change == "root-content":
            manifest.write_text(manifest.read_text() + "\nchanged\n")
        elif change == "include-content":
            included.write_text("bundle: {name: included}\nsession: {marker: changed}\n")
        elif change == "registration":
            settings = yaml.safe_load(settings_path.read_text())
            settings["bundle"]["added"]["owned"] = included.as_uri()
            settings_path.write_text(yaml.safe_dump(settings))
        elif change == "legacy":
            (home / "bundle-registry.yaml").write_text("bundles: {}\n")
        elif change == "legacy-marker":
            (home / "bundle-registry.yaml.migrated").write_text("marker")
        elif change == "cache-binding":
            registry = home / "registry.json"
            registry.write_bytes(registry.read_bytes() + b"\n")
        elif change == "restriction-metadata":
            metadata = SessionMetadataStore(directory).read()
            metadata[policy.RESTRICTIONS_KEY]["restrictions"]["max_pixels"] = 1
            SessionMetadataStore(directory).update(metadata)
        elif change == "same-byte-replacement":
            replacement = root / "replacement.md"
            replacement.write_bytes(manifest.read_bytes())
            replacement.replace(manifest)
        elif change == "source-alias":
            alternate = tmp_path / "alternate"
            alternate.mkdir()
            (alternate / "bundle.md").write_bytes(manifest.read_bytes())
            alias.unlink()
            alias.symlink_to(alternate, target_is_directory=True)
        elif change == "higher-priority":
            candidate = Path.cwd() / ".amplifier/bundles/owned.yaml"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("bundle: {name: owned}\n")
        with pytest.raises(owners.StorageOwnerHeld) as raised:
            owner.check()
        assert raised.value.reason == "policy"
        assert len(counted) == initial_calls  # No stale reuse OR hidden recomposition.


def test_no_namespace_witness_uses_fresh_composition(configuration, counted):
    _, root, _, _ = configuration
    prospective(configuration)
    included = root / "included.yaml"
    included.write_text("bundle: {name: included}\n")
    (root / "bundle.md").write_text(
        "---\nbundle: {name: owned}\nincludes: [{bundle: 'owned:included.yaml'}]\n---\n"
    )
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known and owner._policy_witness is None
        before = len(counted)
        owner.check()
        assert len(counted) == before + 1


def test_alternate_prefix_uses_same_owner_local_witness(configuration, counted):
    directory, _, _, _, expected = prospective(configuration)

    class Alternate(owners.SessionStorageOwner):
        def _policy_projection(self):
            from amplifier_foundation.session.media import MediaPolicy
            try:
                config = policy.closed_policy_config(self.directory, self.session_id, expected)
                return owners.project_policy(self.session_id, resolved_config=config)
            except Exception:
                return owners.PolicyProjection(MediaPolicy(retention="drop"), False)

    with Alternate(directory, session_id="root", purpose="apply") as owner:
        # Subclass's selected-prefix path is exercised by inherited check().
        owner.prepare_media()
        owner.check()
        before = len(counted)
        for _ in range(32):
            owner.check()
        assert len(counted) == before
        _, _, settings_path, _ = configuration
        settings_path.write_text(settings_path.read_text() + "\n# narrower input\n")
        with pytest.raises(owners.StorageOwnerHeld):
            owner.check()


def test_stream_callback_rechecks_bytes_before_next_pixels(configuration, counted):
    directory, ref, _, _, _ = prospective(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        _, _, settings_path, _ = configuration
        original = settings_path.read_bytes()
        with pytest.raises(owners.StorageOwnerHeld):
            with owner.reader(ref) as stream:
                stream.read(1)
                settings_path.write_text("config:\n  session_media: {retention: drop}\n")
                with pytest.raises(owners.StorageOwnerHeld):
                    owner.check()
                settings_path.write_bytes(original)
                # Even a subsequently restored file cannot clear this owner's
                # observed failure. Reader exit uses the same actual guard.


def test_actual_registered_snapshot_retains_fences(configuration, counted, tmp_path, monkeypatch):
    prospective(configuration)
    failures = []
    actual = policy.PolicyReadWitness.check

    def observe(self):
        result = actual(self)
        if not result:
            failures.append([
                path for path, content, fact in self._entries
                if policy._path_fact(path, content) != fact
            ])
        return result

    monkeypatch.setattr(policy.PolicyReadWitness, "check", observe)
    result = invoke(registered(), "fork-snapshot", "root", "snapshot", tmp_path / "snapshot-op",
                    "--operation-id", "snapshot-op")
    assert result.exit_code == 0, (result.output, failures, len(counted))
    assert json.loads(result.output)["checkpoint_acknowledged"]


def test_home_alias_retarget_is_not_hidden_by_canonical_settings_paths(configuration, tmp_path, monkeypatch):
    home, _, _, _ = configuration
    prospective(configuration)
    alias = tmp_path / "home-alias"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("AMPLIFIER_HOME", str(alias))
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner._policy_witness is not None
        alternate = tmp_path / "different-home"
        alternate.mkdir()
        alias.unlink()
        alias.symlink_to(alternate, target_is_directory=True)
        with pytest.raises(owners.StorageOwnerHeld):
            owner.check()


def test_plain_home_guard_adds_no_entries_to_existing_benchmark_readset(configuration):
    """The final lexical-home hardening is redundant for a nonaliased home."""
    directory, _, _, _, expected = prospective(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        actual = owner._policy_witness
        comparison = policy._PolicyReadCapture()
        # Reproduce only the pre-hardening capture initialization, then run
        # the same real public resolution and closure checks. No guard/effect
        # uses this diagnostic object and no benchmark threshold is changed.
        comparison.entries.clear()
        token = policy._POLICY_READS.set(comparison)
        try:
            policy.closed_policy_config(directory, "root", expected)
            previous_readset = comparison.freeze()
        finally:
            policy._POLICY_READS.reset(token)
        assert previous_readset is not None
        assert set(actual._entries) == set(previous_readset._entries)


def test_uncertain_dotdot_source_keeps_full_resolution(configuration, counted):
    _, root, settings_path, _ = configuration
    prospective(configuration)
    (root.parent / "separator").mkdir()
    settings = yaml.safe_load(settings_path.read_text())
    settings["bundle"]["added"]["owned"] = (root.parent / "separator" / ".." / "root").as_uri()
    settings_path.write_text(yaml.safe_dump(settings))
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known and owner._policy_witness is None
        before = len(counted)
        owner.check()
        assert len(counted) == before + 1


def test_owner_selected_prefix_is_part_of_the_witness_join(configuration):
    from dataclasses import replace

    prospective(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        owner.current = replace(owner.current, operation_id="different-selector")
        with pytest.raises(owners.StorageOwnerHeld):
            owner.check()


def test_prevalidated_missing_cache_path_cannot_leave_stale_known_witness(configuration, tmp_path, counted):
    """Only public register/load/save constructs the persisted stale binding."""
    import asyncio
    import hashlib
    from amplifier_foundation.registry import BundleRegistry
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources.policy import source_resolution_policy

    home, _, settings_path, _ = configuration
    directory, _, _, _, expected = prospective(configuration)
    old = tmp_path / "formerly-cached"
    active = tmp_path / "current-source"
    parked = tmp_path / "parked-original"
    alias = tmp_path / "root-alias"
    old.mkdir()
    active.mkdir()
    (old / "bundle.yaml").write_text(
        "bundle: {name: owned}\ntools: [{module: old-source-marker}]\n"
    )
    (active / "bundle.yaml").write_text(
        "bundle: {name: owned}\ntools: [{module: current-source-marker}]\n"
    )
    alias.symlink_to(old, target_is_directory=True)
    uri = alias.as_uri()
    settings = yaml.safe_load(settings_path.read_text())
    settings["bundle"]["added"]["owned"] = uri
    settings_path.write_text(yaml.safe_dump(settings))

    async def seed_public_binding():
        registry = BundleRegistry(home=home, strict=True, persist=False)
        registry.register({"owned": uri})

        def admit(source, *, base_path):
            assert source == uri
            path = alias.resolve(strict=True)
            return ResolvedSource(active_path=path, source_root=path)

        with source_resolution_policy(admit):
            await registry.load("owned")
        assert registry.get_state("owned").local_path == str(old)
        # Save through the public writer using a persistent registry populated
        # by the same public load, not a hand-written registry JSON fixture.
        persistent = BundleRegistry(home=home, strict=True)
        persistent.register({"owned": uri})
        with source_resolution_policy(admit):
            await persistent.load("owned")
        persistent.save()
        assert persistent.get_state("owned").local_path == str(old)

    asyncio.run(seed_public_binding())
    registry_path = home / "registry.json"
    registry_before = registry_path.read_bytes()
    old.rename(parked)
    alias.unlink()
    alias.symlink_to(active, target_is_directory=True)
    (parked / "bundle.yaml").write_text("bundle: [\n")
    assert not old.exists()
    config, witness = policy.capture_closed_policy_config(directory, "root", expected)
    assert config["session_media"]["retention"] == "durable"
    assert any(row["module"] == "current-source-marker" for row in config["tools"])
    assert witness is None, "Constructor-cleared cache-path absence is not publicly observable"

    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known and owner._policy_witness is None
        before = {
            "registry": hashlib.sha256(registry_before).hexdigest(),
            "settings": settings_path.read_bytes(),
            "active": (active / "bundle.yaml").read_bytes(),
            "alias": alias.readlink(),
        }
        parked.rename(old)  # Sole measured change restores malformed old cache.
        assert registry_path.read_bytes() == registry_before
        assert settings_path.read_bytes() == before["settings"]
        assert (active / "bundle.yaml").read_bytes() == before["active"]
        assert alias.readlink() == before["alias"]
        calls_before = len(counted)
        with pytest.raises(owners.StorageOwnerHeld):
            owner.check()
        assert len(counted) == calls_before + 1  # Actual fresh composition refused.
        with pytest.raises(policy.CurrentPolicyIncomplete):
            policy.capture_closed_policy_config(directory, "root", expected)
        assert registry_path.read_bytes() == registry_before


def test_same_prefix_tighter_recovery_publication_transition(configuration, tmp_path, monkeypatch):
    """Keep the original full registered recovery oracle; report safe cause tags."""
    import traceback
    from test_session_storage_transfer import test_normal_snapshot_real_binary_policy_and_cold_recovery

    failures = []
    original = policy.PolicyReadWitness.check
    publish = owners.SessionStorageOwner.publish_restriction_metadata

    def observe_publication(self, *args, **kwargs):
        try:
            return publish(self, *args, **kwargs)
        except BaseException as error:
            chain = []
            while error is not None and len(chain) < 4:
                chain.append({
                    "type": type(error).__name__,
                    "frames": [(Path(f.filename).name, f.name, f.lineno)
                               for f in traceback.extract_tb(error.__traceback__)],
                })
                error = error.__cause__ or error.__context__
            failures.append({"publication_failure_types_frames": chain})
            raise

    def observe(self):
        result = original(self)
        if not result:
            changed = [
                Path(path).name for path, content, fact in self._entries
                if policy._path_fact(path, content) != fact
            ]
            failures.append({
                "changed_basenames": sorted(set(changed)),
                "frames": [frame.name for frame in traceback.extract_stack(limit=8)],
            })
        return result

    monkeypatch.setattr(policy.PolicyReadWitness, "check", observe)
    monkeypatch.setattr(owners.SessionStorageOwner, "publish_restriction_metadata", observe_publication)
    try:
        test_normal_snapshot_real_binary_policy_and_cold_recovery(
            configuration, tmp_path, monkeypatch, "PNG",
        )
    finally:
        print(json.dumps({"same_prefix_recovery_witness_failures": failures}))


def _tightened_owner_inputs(configuration):
    directory, _, _, _, prefix = prospective(configuration)
    settings_path = configuration[2]
    settings = yaml.safe_load(settings_path.read_text())
    settings["config"]["session_media"]["max_pixels"] = 8
    settings_path.write_text(yaml.safe_dump(settings))
    return directory, prefix


def _restriction_publication_fields(owner, prefix):
    from dataclasses import asdict

    metadata = SessionMetadataStore(owner.directory).read()
    original = policy.restriction_record(
        metadata[policy.RESTRICTIONS_KEY], session_id=owner.session_id, expected=prefix,
    )
    return {policy.RESTRICTIONS_KEY: {
        "version": 1, "session_id": owner.session_id,
        "prefix": asdict(owner.binding.expected),
        "restrictions": policy.meet(original, {"enabled": True, **asdict(owner.media.policy)}),
    }}


def test_explicit_same_prefix_ack_publication_only_tightens(configuration):
    directory, prefix = _tightened_owner_inputs(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        before = SessionMetadataStore(directory).read()
        assert before[policy.RESTRICTIONS_KEY]["restrictions"]["max_pixels"] is None
        assert owner.media.policy.max_pixels == 8
        receipt = owner.binding.last_receipt
        fields = _restriction_publication_fields(owner, prefix)
        owner.publish_restriction_metadata(receipt, fields, expected_prefix=prefix)
        owner.check()
        after = SessionMetadataStore(directory).read()
        assert after == {**before, **fields}
        assert owner.current == owner.binding.expected == prefix
        assert after[policy.RESTRICTIONS_KEY]["restrictions"]["max_pixels"] == 8
        with pytest.raises(owners.StorageOwnerHeld):
            owner.publish_restriction_metadata(receipt, fields, expected_prefix=prefix)


@pytest.mark.parametrize("invalid", ["serialized-ack", "stale-ack", "wider-floor", "external-edit"])
def test_publication_helper_never_admits_external_or_unissued_change(configuration, invalid):
    from copy import deepcopy

    directory, prefix = _tightened_owner_inputs(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        receipt = owner.binding.last_receipt
        fields = _restriction_publication_fields(owner, prefix)
        if invalid == "serialized-ack":
            receipt = receipt.verify()
        elif invalid == "stale-ack":
            with owner.binding.publication():
                owner.binding.checkpoint(expected=prefix, media_store=owner.media)
        elif invalid == "wider-floor":
            fields = deepcopy(fields)
            fields[policy.RESTRICTIONS_KEY]["restrictions"]["max_pixels"] = 100
        else:
            metadata = SessionMetadataStore(directory).read()
            metadata[policy.RESTRICTIONS_KEY]["restrictions"]["max_pixels"] = 7
            SessionMetadataStore(directory).update(metadata)
        before = (directory / "metadata.json").read_bytes()
        with pytest.raises(owners.StorageOwnerHeld):
            owner.publish_restriction_metadata(receipt, fields, expected_prefix=prefix)
        assert (directory / "metadata.json").read_bytes() == before


def test_unknown_auxiliary_reply_latches_exact_failure_without_replay(configuration, monkeypatch):
    directory, prefix = _tightened_owner_inputs(configuration)
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        fields = _restriction_publication_fields(owner, prefix)
        original = SessionMetadataStore.update
        error = OSError("fixture lost auxiliary reply")
        calls = []

        def lose_reply(store, incoming):
            calls.append(True)
            original(store, incoming)
            raise error

        monkeypatch.setattr(SessionMetadataStore, "update", lose_reply)
        with pytest.raises(OSError) as raised:
            owner.publish_restriction_metadata(
                owner.binding.last_receipt, fields, expected_prefix=prefix,
            )
        assert raised.value is owner.native.versioned_publication_failure is error
        with pytest.raises(owners.StorageOwnerHeld):
            owner.publish_restriction_metadata(
                owner.binding.last_receipt, fields, expected_prefix=prefix,
            )
        assert len(calls) == 1


@pytest.mark.parametrize("home_input", ["unset", "empty"])
def test_default_home_public_resolution_has_no_reusable_witness(configuration, counted, monkeypatch, home_input):
    from amplifier_foundation.paths import resolution

    home, _, _, _ = configuration
    directory, _, _, _, expected = prospective(configuration)
    if home_input == "unset":
        monkeypatch.delenv("AMPLIFIER_HOME")
    else:
        monkeypatch.setenv("AMPLIFIER_HOME", "")
    original = resolution.get_amplifier_home
    public_calls = []

    def observe():
        selected = original()
        public_calls.append(selected)
        return selected

    monkeypatch.setattr(resolution, "get_amplifier_home", observe)
    config, witness = policy.capture_closed_policy_config(directory, "root", expected)
    assert public_calls and all(path == home for path in public_calls)
    assert config["session_media"]["retention"] == "durable"
    assert config["session_media"]["max_pixels"] == 100
    assert witness is None
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known and owner._policy_witness is None
        before = len(counted)
        owner.check()
        owner.check()
        assert len(counted) == before + 2


@pytest.mark.parametrize("home_input", ["unset", "empty"])
def test_default_home_alias_retarget_gets_fresh_public_policy(configuration, tmp_path, monkeypatch, home_input):
    from amplifier_foundation.paths.resolution import get_amplifier_home

    require_linux_custody()
    home, _, _, _ = configuration
    original_home = tmp_path / "original-default-home"
    home.rename(original_home)
    home.symlink_to(original_home, target_is_directory=True)
    directory, _, _, _, expected = prospective(configuration)
    if home_input == "unset":
        monkeypatch.delenv("AMPLIFIER_HOME")
    else:
        monkeypatch.setenv("AMPLIFIER_HOME", "")
    assert get_amplifier_home() == original_home
    replacement = tmp_path / "replacement-default-home"
    replacement.mkdir()
    settings = yaml.safe_load((original_home / "settings.yaml").read_text())
    settings["config"]["session_media"]["max_pixels"] = 8
    (replacement / "settings.yaml").write_text(yaml.safe_dump(settings))
    (replacement / "registry.json").write_bytes((original_home / "registry.json").read_bytes())
    with owners.default_owner_factory("root", purpose="apply") as owner:
        owner.prepare_media()
        assert owner.projection.known and owner.projection.policy.max_pixels == 100
        assert owner._policy_witness is None
        home.unlink()
        home.symlink_to(replacement, target_is_directory=True)
        assert get_amplifier_home() == replacement
        fresh, witness = policy.capture_closed_policy_config(directory, "root", expected)
        assert fresh["session_media"]["max_pixels"] == 8 and witness is None
        with pytest.raises(owners.StorageOwnerHeld):
            owner.check()


def test_explicit_public_home_does_not_observe_decoy_os_home(configuration, tmp_path, monkeypatch):
    import asyncio
    from amplifier_app_cli.lib.settings import AppSettings
    from amplifier_foundation.paths import resolution

    home, _, _, _ = configuration
    decoy = tmp_path / "decoy-os-home"
    (decoy / ".amplifier").mkdir(parents=True)
    decoy_settings = decoy / ".amplifier" / "settings.yaml"
    decoy_registry = decoy / ".amplifier" / "registry.json"
    decoy_settings.write_text("config: [invalid-decoy\n")
    decoy_registry.write_text("{invalid-decoy")
    monkeypatch.setenv("HOME", str(decoy))
    monkeypatch.setenv("USERPROFILE", str(decoy))
    original_public = resolution.get_amplifier_home
    public_calls = []

    def observe():
        selected = original_public()
        public_calls.append(selected)
        return selected

    original_read = Path.read_text

    def reject_decoy(path, *args, **kwargs):
        if path in (decoy_settings, decoy_registry):
            pytest.fail("Explicit home resolution read decoy OS-home settings")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(resolution, "get_amplifier_home", observe)
    monkeypatch.setattr(Path, "read_text", reject_decoy)
    # Pure capture/read-set check remains portable; no prospective custody.
    capture = policy._PolicyReadCapture()
    assert public_calls == [home]
    assert capture.complete
    assert all(not Path(path).is_relative_to(decoy)
               for path, _content in capture.entries)
    witness = capture.freeze()
    assert witness is not None and witness.check()
    decoy_settings.write_text("another invalid decoy\n")
    decoy_registry.write_text("another invalid decoy\n")
    assert witness.check()
    current = asyncio.run(policy.resolve_declared_config_async(
        "owned", AppSettings(), session_id="root", project_slug="fixture",
    ))
    assert current["session_media"]["max_pixels"] == 100
    assert public_calls and all(path == home for path in public_calls)