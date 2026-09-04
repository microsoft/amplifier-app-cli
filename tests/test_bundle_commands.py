"""Focused regressions for bundle add/remove and global update source handling."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from amplifier_app_cli.utils.source_status import UpdateReport
from amplifier_app_cli.utils.update_executor import ExecutionResult


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

    _invoke_bundle_add(_URI, name_override="new-alias")

    assert loaded_uris == [_URI]
    assert settings.get_added_bundles() == {"existing-alias": _URI}
    settings.add_bundle.assert_not_called()


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


def test_global_update_loads_an_app_target_by_source_uri(monkeypatch):
    """Applying a global update must load an app bundle from its URI, not an alias."""
    import amplifier_foundation

    status = SimpleNamespace(has_updates=True, bundle_source=_FRAGMENT_URI)
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
