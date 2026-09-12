"""Focused regressions for bundle add/remove and global update source handling."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from amplifier_app_cli.utils.source_status import UpdateReport
from amplifier_app_cli.utils.update_executor import ExecutionResult
from amplifier_foundation.sources.protocol import SourceStatus
from amplifier_foundation.updates import BundleStatus


bundle_module = importlib.import_module("amplifier_app_cli.commands.bundle")
update_module = importlib.import_module("amplifier_app_cli.commands.update")

_URI = "git+https://github.com/example/amplifier-bundle-team@main"
_OTHER_URI = "git+https://github.com/example/amplifier-bundle-team@release"
_FRAGMENT_URI = f"{_URI}#subdirectory=behaviors/team.yaml"


class _FakeSettings:
    """In-memory AppSettings double with a global settings scope."""

    def __init__(self, *, app_bundles=None, added_bundles=None):
        self.app_bundles = list(app_bundles or [])
        self.global_settings = {
            "bundle": {
                "added": dict(added_bundles or {}),
            }
        }
        self.add_app_bundle = MagicMock(side_effect=self._add_app_bundle)
        self.add_bundle = MagicMock(side_effect=self._add_bundle)

    def get_app_bundles(self):
        return list(self.app_bundles)

    def get_added_bundles(self):
        return dict(self.global_settings.get("bundle", {}).get("added", {}))

    def _read_scope(self, scope):
        assert scope == "global"
        return self.global_settings

    def _add_app_bundle(self, uri):
        if uri not in self.app_bundles:
            self.app_bundles.append(uri)

    def _add_bundle(self, name, uri):
        self.global_settings.setdefault("bundle", {}).setdefault("added", {})[name] = (
            uri
        )

    def remove_added_bundle(self, name, scope="global"):
        assert scope == "global"
        added = self.global_settings.get("bundle", {}).get("added", {})
        if name not in added:
            return False
        del added[name]
        if not added:
            self.global_settings["bundle"].pop("added")
        return True

    def remove_app_bundle(self, uri):
        if uri not in self.app_bundles:
            return False
        self.app_bundles.remove(uri)
        return True


def _stub_bundle_load(monkeypatch, bundle_name="team"):
    import amplifier_foundation

    loaded_uris = []

    async def fake_load_bundle(uri, *, auto_include):
        assert auto_include is False
        loaded_uris.append(uri)
        return SimpleNamespace(name=bundle_name, version="1.2.3")

    monkeypatch.setattr(amplifier_foundation, "load_bundle", fake_load_bundle)
    return loaded_uris


def _invoke_bundle_add(uri, *, name_override=None, app=False):
    return bundle_module.bundle_add.callback(uri, name_override, app)


def test_app_add_persists_only_app_source(monkeypatch):
    """An app add must never create a selectable bundle.added registration."""
    settings = _FakeSettings()
    loaded_uris = _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    _invoke_bundle_add(_URI, app=True)

    assert loaded_uris == [_URI]
    assert settings.app_bundles == [_URI]
    settings.add_app_bundle.assert_called_once_with(_URI)
    settings.add_bundle.assert_not_called()
    assert settings.get_added_bundles() == {}


def test_repeated_app_add_loads_again_without_writing_settings(monkeypatch):
    """A retry reloads its source but does not write either bundle setting."""
    settings = _FakeSettings(app_bundles=[_URI])
    loaded_uris = _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    _invoke_bundle_add(_URI, app=True)

    assert loaded_uris == [_URI]
    assert settings.app_bundles == [_URI]
    settings.add_app_bundle.assert_not_called()
    settings.add_bundle.assert_not_called()


def test_app_add_preserves_same_name_standard_registration(monkeypatch):
    """Adding an app source must not remove an indistinguishable normal mapping."""
    settings = _FakeSettings(
        added_bundles={"team": _URI},
    )
    _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    _invoke_bundle_add(_URI, app=True)

    assert settings.app_bundles == [_URI]
    assert settings.get_added_bundles() == {"team": _URI}
    settings.add_bundle.assert_not_called()


def test_app_remove_preserves_same_name_standard_registration(monkeypatch):
    """Removing an app source must not remove an indistinguishable normal mapping."""
    settings = _FakeSettings(
        app_bundles=[_URI],
        added_bundles={"team": _URI},
    )
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    bundle_module.bundle_remove.callback("team", app=True)

    assert settings.app_bundles == []
    assert settings.get_added_bundles() == {"team": _URI}


def test_non_app_add_rejects_uri_already_registered_under_alias(monkeypatch):
    """The same exact URI cannot be persisted under another selectable name."""
    settings = _FakeSettings(added_bundles={"existing-alias": _URI})
    loaded_uris = _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    with pytest.raises(SystemExit) as excinfo:
        _invoke_bundle_add(_URI, name_override="new-alias")

    # A refusal must be distinguishable from a successful registration by a
    # scripted caller -- exit 0 here would read as "added under new-alias".
    assert excinfo.value.code == 1
    assert loaded_uris == [_URI]
    assert settings.get_added_bundles() == {"existing-alias": _URI}
    settings.add_bundle.assert_not_called()


def test_non_app_add_alias_conflict_exits_non_zero_through_cli(monkeypatch):
    """The refusal surfaces as a non-zero process exit, not just an exception."""
    settings = _FakeSettings(added_bundles={"existing-alias": _URI})
    _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    result = CliRunner().invoke(bundle_module.bundle_add, [_URI, "--name", "new-alias"])

    assert result.exit_code == 1, result.output
    assert "already registered as 'existing-alias'" in result.output
    settings.add_bundle.assert_not_called()


def test_repeated_non_app_add_same_name_same_uri_still_succeeds(monkeypatch):
    """Re-adding an identical name+URI pair stays an idempotent success."""
    settings = _FakeSettings(added_bundles={"team": _URI})
    _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    result = CliRunner().invoke(bundle_module.bundle_add, [_URI])

    assert result.exit_code == 0, result.output
    assert settings.get_added_bundles() == {"team": _URI}


def test_non_app_add_updates_existing_name_to_new_uri(monkeypatch):
    """A supplied existing name still updates its mapping to a new URI."""
    settings = _FakeSettings(added_bundles={"team": _URI})
    loaded_uris = _stub_bundle_load(monkeypatch)
    monkeypatch.setattr(bundle_module, "AppSettings", lambda: settings)

    _invoke_bundle_add(_OTHER_URI, name_override="team")

    assert loaded_uris == [_OTHER_URI]
    assert settings.get_added_bundles() == {"team": _OTHER_URI}
    settings.add_bundle.assert_called_once_with("team", _OTHER_URI)


@pytest.mark.asyncio
async def test_global_update_checks_app_only_sources_by_exact_uri(monkeypatch):
    """Distinct app refs/fragments remain independent global-update targets."""
    from amplifier_foundation.sources.git import GitSourceHandler

    app_uris = [_URI, _OTHER_URI, _FRAGMENT_URI]
    monkeypatch.setattr(
        update_module,
        "AppBundleDiscovery",
        lambda: SimpleNamespace(list_cached_root_bundles=lambda: []),
    )
    monkeypatch.setattr(update_module, "create_bundle_registry", MagicMock())
    monkeypatch.setattr(
        update_module,
        "AppSettings",
        lambda: SimpleNamespace(get_app_bundles=lambda: app_uris),
    )

    async def fake_get_status(self, parsed, cache_dir):
        return SimpleNamespace(source_uri="checked", has_update=False)

    monkeypatch.setattr(GitSourceHandler, "get_status", fake_get_status)

    results = await update_module._check_all_bundle_status()

    assert set(results) == set(app_uris)
    assert {status.bundle_source for status in results.values()} == set(app_uris)


@pytest.mark.asyncio
async def test_global_update_reports_registry_failure_and_checks_later_bundles(monkeypatch):
    """A failed registry lookup remains visible and does not stop later checks."""
    from amplifier_foundation.sources.git import GitSourceHandler

    working_bundle = "working"
    working_uri = "git+https://github.com/example/amplifier-bundle-working@main"
    registry = MagicMock()

    def find_bundle(bundle_name):
        if bundle_name == "broken":
            raise RuntimeError("registry unavailable")
        assert bundle_name == working_bundle
        return working_uri

    registry.find.side_effect = find_bundle
    monkeypatch.setattr(
        update_module,
        "AppBundleDiscovery",
        lambda: SimpleNamespace(
            list_cached_root_bundles=lambda: ["broken", working_bundle]
        ),
    )
    monkeypatch.setattr(update_module, "create_bundle_registry", lambda: registry)
    monkeypatch.setattr(
        update_module,
        "AppSettings",
        lambda: SimpleNamespace(get_app_bundles=lambda: []),
    )

    async def fake_get_status(self, parsed, cache_dir):
        return SimpleNamespace(source_uri=working_uri, has_update=False)

    monkeypatch.setattr(GitSourceHandler, "get_status", fake_get_status)

    results = await update_module._check_all_bundle_status()

    assert set(results) == {"broken", working_bundle}
    broken = results["broken"]
    assert broken.bundle_name == "broken"
    assert broken.bundle_source == ""
    assert broken.sources[0].error == "status check failed"
    assert results[working_bundle].bundle_source == working_uri
    assert registry.find.call_args_list == [(("broken",),), ((working_bundle,),)]


@pytest.mark.asyncio
async def test_global_update_reports_direct_app_check_failure_by_exact_uri(monkeypatch):
    """A configured app URI remains a row when its direct status request fails."""

    from amplifier_foundation.sources.git import GitSourceHandler

    failing_uri = (
        "git+https://person:secret@example.invalid/org/amplifier-bundle-failing@main"
    )
    monkeypatch.setattr(
        update_module,
        "AppBundleDiscovery",
        lambda: SimpleNamespace(list_cached_root_bundles=lambda: []),
    )
    monkeypatch.setattr(update_module, "create_bundle_registry", MagicMock())
    monkeypatch.setattr(
        update_module,
        "AppSettings",
        lambda: SimpleNamespace(get_app_bundles=lambda: [failing_uri]),
    )

    async def failing_get_status(self, parsed, cache_dir):
        raise TimeoutError("token in https://person:secret@example.invalid")

    async def no_transitives(*args, **kwargs):
        return {}

    monkeypatch.setattr(GitSourceHandler, "get_status", failing_get_status)
    monkeypatch.setattr(update_module, "_check_transitive_bundle_status", no_transitives)

    results = await update_module._check_all_bundle_status()

    assert set(results) == {failing_uri}
    assert results[failing_uri].bundle_source == failing_uri
    assert results[failing_uri].sources[0].source_uri == ""
    assert results[failing_uri].sources[0].error == "timeout"


def test_global_update_loads_an_app_target_by_source_uri(monkeypatch):
    """Applying a global update must load an app bundle from its URI, not an alias."""
    import amplifier_foundation

    status = BundleStatus(
        bundle_name="app target",
        bundle_source=_FRAGMENT_URI,
        sources=[
            SourceStatus(
                source_uri=_FRAGMENT_URI,
                is_cached=True,
                cached_commit="a" * 40,
                remote_commit="b" * 40,
                has_update=True,
            )
        ],
    )
    loaded_uris = []
    updated_bundles = []

    async def fake_check_all_sources(**kwargs):
        return UpdateReport(local_file_sources=[], cached_git_sources=[])

    async def fake_execute_updates(*args, **kwargs):
        return ExecutionResult(
            success=True, updated=[], failed=[], errors={}, messages=[]
        )

    async def fake_load_bundle(uri, *, auto_include):
        assert auto_include is False
        loaded_uris.append(uri)
        return SimpleNamespace()

    async def fake_update_bundle(bundle):
        updated_bundles.append(bundle)

    monkeypatch.setattr(update_module, "check_all_sources", fake_check_all_sources)

    async def fake_check_all_bundle_status():
        return {"app target": status}

    monkeypatch.setattr(
        update_module, "_check_all_bundle_status", fake_check_all_bundle_status
    )
    monkeypatch.setattr(
        update_module,
        "_show_concise_report",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(update_module, "execute_updates", fake_execute_updates)
    monkeypatch.setattr(update_module, "_refresh_skills_cache", lambda console: None)
    monkeypatch.setattr(update_module, "save_update_last_check", lambda value: None)
    monkeypatch.setattr(
        "amplifier_app_cli.utils.umbrella_discovery.discover_umbrella_source",
        lambda: None,
    )
    monkeypatch.setattr(amplifier_foundation, "load_bundle", fake_load_bundle)
    monkeypatch.setattr(amplifier_foundation, "update_bundle", fake_update_bundle)

    result = CliRunner().invoke(update_module.update, ["--yes"])

    assert result.exit_code == 0, result.output
    assert loaded_uris == [_FRAGMENT_URI]
    assert len(updated_bundles) == 1


@pytest.mark.asyncio
async def test_global_update_collapses_an_app_subdirectory_into_its_root_bundle(
    monkeypatch,
):
    """A behavior YAML added as an app bundle is not a second row for its repo.

    `amplifier bundle add --app <repo>@main#subdirectory=behaviors/x.yaml`
    records the fragment URI in settings. When the same repo is already a
    tracked root bundle, the two are the same repo at the same ref and one
    status check covers both -- the rule list_cached_root_bundles() applies to
    registry sub-bundles. Comparing the exact strings can never match, so
    without a fragment-stripped comparison every such app bundle rendered a
    duplicate row alongside its root.
    """
    from amplifier_foundation.sources.git import GitSourceHandler

    registry = MagicMock()
    registry.find.return_value = _URI
    monkeypatch.setattr(
        update_module,
        "AppBundleDiscovery",
        lambda: SimpleNamespace(list_cached_root_bundles=lambda: ["team"]),
    )
    monkeypatch.setattr(update_module, "create_bundle_registry", lambda: registry)
    monkeypatch.setattr(
        update_module,
        "AppSettings",
        lambda: SimpleNamespace(get_app_bundles=lambda: [_FRAGMENT_URI]),
    )

    async def fake_get_status(self, parsed, cache_dir):
        return SimpleNamespace(source_uri=_URI, has_update=False)

    monkeypatch.setattr(GitSourceHandler, "get_status", fake_get_status)

    results = await update_module._check_all_bundle_status()

    assert set(results) == {"team"}, (
        "the fragment URI must not appear as a row of its own next to the "
        "root bundle it shares a repo and ref with"
    )


@pytest.mark.asyncio
async def test_global_update_keeps_an_app_subdirectory_on_a_different_ref(monkeypatch):
    """Only the fragment is redundant with the root - the ref is not."""
    from amplifier_foundation.sources.git import GitSourceHandler

    other_ref_uri = (
        "git+https://github.com/example/amplifier-bundle-team@v2"
        "#subdirectory=behaviors/team.yaml"
    )
    registry = MagicMock()
    registry.find.return_value = _URI
    monkeypatch.setattr(
        update_module,
        "AppBundleDiscovery",
        lambda: SimpleNamespace(list_cached_root_bundles=lambda: ["team"]),
    )
    monkeypatch.setattr(update_module, "create_bundle_registry", lambda: registry)
    monkeypatch.setattr(
        update_module,
        "AppSettings",
        lambda: SimpleNamespace(get_app_bundles=lambda: [other_ref_uri]),
    )

    async def fake_get_status(self, parsed, cache_dir):
        return SimpleNamespace(source_uri=_URI, has_update=False)

    monkeypatch.setattr(GitSourceHandler, "get_status", fake_get_status)

    results = await update_module._check_all_bundle_status()

    assert set(results) == {"team", other_ref_uri}


def test_strip_uri_fragment_keeps_the_ref():
    assert (
        update_module._strip_uri_fragment(_FRAGMENT_URI) == _URI
    ), "only the #fragment is redundant with the root bundle"
    assert update_module._strip_uri_fragment(_URI) == _URI


def test_app_bundle_rows_display_a_friendly_name():
    """An app bundle keyed by URI must not print the URI as its Name.

    The key stays the URI -- it is the update target's identity and has to
    round-trip into update_bundle -- so only the label changes.
    """
    labels = update_module._bundle_display_names(["modes", _FRAGMENT_URI])

    assert labels["modes"] == "modes"
    assert labels[_FRAGMENT_URI] == "team"


def test_colliding_app_bundle_labels_use_compact_repo_qualifiers():
    """Colliding app rows remain identifiable without exposing raw URIs."""
    first = "git+https://github.com/a/repo-one@main#subdirectory=behaviors/main.yaml"
    second = "git+https://github.com/b/repo-two@main#subdirectory=behaviors/main.yaml"

    labels = update_module._bundle_display_names([first, second])

    assert labels[first] == "main (a/repo-one)"
    assert labels[second] == "main (b/repo-two)"


def test_an_app_bundle_label_never_shadows_a_registry_alias():
    collides = (
        "git+https://github.com/x/amplifier-bundle-modes@main"
        "#subdirectory=behaviors/modes.yaml"
    )

    labels = update_module._bundle_display_names(["modes", collides])

    assert labels["modes"] == "modes", "the registry alias keeps its name"
    assert labels[collides] == "modes (app source)"


def test_colliding_app_bundle_labels_distinguish_refs_and_ignore_credentials():
    """Ref is added only when needed, and userinfo/query never reach the table."""

    main = (
        "git+https://user:secret@example.invalid/org/amplifier-bundle-team@main"
        "#subdirectory=behaviors/team.yaml"
    )
    release = (
        "git+https://user:secret@example.invalid/org/amplifier-bundle-team@release"
        "#subdirectory=behaviors/team.yaml"
    )

    labels = update_module._bundle_display_names([release, main])

    assert labels[main] == "team (org/amplifier-bundle-team @main)"
    assert labels[release] == "team (org/amplifier-bundle-team @release)"
    assert all("secret" not in label for label in labels.values())
    assert labels == update_module._bundle_display_names([main, release])


def test_bundle_labels_are_globally_unique_without_changing_registry_aliases():
    """Generated labels yield to literal aliases, then use source-only context."""

    uri = (
        "git+https://example.invalid/org/amplifier-bundle-team@main"
        "#subdirectory=behaviors/team.yaml"
    )
    keys = ["team", "team (app source)", uri]

    labels = update_module._bundle_display_names(keys)

    assert labels["team"] == "team"
    assert labels["team (app source)"] == "team (app source)"
    assert len(set(labels.values())) == len(labels)
    assert labels == update_module._bundle_display_names(list(reversed(keys)))


def test_bundle_labels_distinguish_local_hosts_schemes_and_equivalent_uri_shapes():
    """Readable parts come first; opaque hashes resolve only safe-part ties."""

    local_one = "file:///work/one/behaviors/team.yaml"
    local_two = "file:///work/two/behaviors/team.yaml"
    host_one = (
        "git+https://one.invalid/org/amplifier-bundle-team@feature/next"
        "#subdirectory=behaviors/team.yaml"
    )
    host_two = (
        "https://two.invalid/org/amplifier-bundle-team@feature/next"
        "#subdirectory=behaviors/team.yaml"
    )
    exact_one = (
        "git+https://same.invalid/org/amplifier-bundle-team@main"
        "?mirror=one#subdirectory=behaviors/team.yaml"
    )
    exact_two = (
        "git+https://same.invalid/org/amplifier-bundle-team@main"
        "?mirror=two#subdirectory=behaviors/team.yaml"
    )
    keys = [local_one, local_two, host_one, host_two, exact_one, exact_two]

    labels = update_module._bundle_display_names(keys)

    assert len(set(labels.values())) == len(labels)
    assert labels[local_one] != labels[local_two]
    assert "one.invalid" in labels[host_one]
    assert "two.invalid" in labels[host_two]
    assert labels[exact_one].endswith("]")
    assert labels[exact_two].endswith("]")
    assert all("mirror=" not in label for label in labels.values())
    assert labels == update_module._bundle_display_names(list(reversed(keys)))


def test_bundle_labels_remain_unique_when_an_alias_matches_a_hash_candidate():
    """A literal alias also wins over a source label introduced by final hashing."""

    first = (
        "git+https://same.invalid/org/amplifier-bundle-team@main"
        "?mirror=one#subdirectory=behaviors/team.yaml"
    )
    second = (
        "git+https://same.invalid/org/amplifier-bundle-team@main"
        "?mirror=two#subdirectory=behaviors/team.yaml"
    )
    generated_alias = update_module._bundle_display_names([first, second])[first]

    labels = update_module._bundle_display_names([generated_alias, first, second])

    assert labels[generated_alias] == generated_alias
    assert len(set(labels.values())) == len(labels)


def test_malformed_uri_never_breaks_or_leaks_through_bundle_labels():
    malformed = "git+https://user:token@[broken/team"

    labels = update_module._bundle_display_names([malformed])

    assert labels[malformed] == "bundle"
    assert "token" not in labels[malformed]
