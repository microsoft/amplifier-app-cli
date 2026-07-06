import yaml

from amplifier_app_cli.paths import create_config_manager
from amplifier_app_cli.provider_manager import ProviderManager


def _write_local_providers(tmp_path, providers: list[dict]) -> None:
    """Seed the local-scope provider list directly (bypasses use_provider's
    priority auto-assignment so tests can control priority explicitly)."""
    settings_path = tmp_path / ".amplifier" / "settings.local.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        yaml.dump({"config": {"providers": providers}}), encoding="utf-8"
    )


def _make_manager(tmp_path, monkeypatch) -> ProviderManager:
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(
        "amplifier_app_cli.paths.Path.home", classmethod(lambda cls: fake_home)
    )
    config_manager = create_config_manager()
    return ProviderManager(config_manager)


def test_provider_override_persists_under_config_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(
        "amplifier_app_cli.paths.Path.home", classmethod(lambda cls: fake_home)
    )

    config_manager = create_config_manager()
    manager = ProviderManager(config_manager)

    manager.use_provider(
        "provider-anthropic",
        scope="local",
        config={"default_model": "claude-sonnet-4-5"},
    )

    settings_path = tmp_path / ".amplifier" / "settings.local.yaml"
    assert settings_path.exists()

    file_contents = yaml.safe_load(settings_path.read_text())
    provider_entry = file_contents["config"]["providers"][0]
    assert provider_entry["module"] == "provider-anthropic"
    assert provider_entry["source"].startswith(
        "git+https://github.com/microsoft/amplifier-module-provider-anthropic"
    )

    provider = manager.get_current_provider()
    assert provider is not None
    assert provider.module_id == "provider-anthropic"
    assert provider.source == "local"

    manager.reset_provider("local")

    cleared_contents = yaml.safe_load(settings_path.read_text()) or {}
    config_section = cleared_contents.get("config") or {}
    assert "providers" not in config_section
    assert manager.get_current_provider() is None


class TestGetProviderConfigPrioritySelection:
    """BUG 1 regression: get_provider_config() matched only on 'module' and
    returned the FIRST list-order match, ignoring priority, whenever 2+
    instances of the same module are configured (e.g. two provider-anthropic
    entries with distinct ids/priorities). This silently served the wrong
    instance's config to any caller resolving by bare module id --
    concretely, ``amplifier provider models anthropic`` via
    commands/provider.py:744.
    """

    def test_selects_highest_priority_instance_not_first_in_list(
        self, tmp_path, monkeypatch
    ):
        """Two provider-anthropic instances, priority 2 listed BEFORE
        priority 1 (lower number = higher precedence). Must return the
        priority=1 instance's config, not whichever is first in the list.
        """
        manager = _make_manager(tmp_path, monkeypatch)
        _write_local_providers(
            tmp_path,
            [
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-secondary",
                    "config": {"priority": 2, "default_model": "wrong-instance"},
                },
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-primary",
                    "config": {"priority": 1, "default_model": "correct-instance"},
                },
            ],
        )

        config = manager.get_provider_config("provider-anthropic")

        assert config is not None, "Expected a config dict, got None"
        assert config.get("default_model") == "correct-instance", (
            "get_provider_config() must resolve to the highest-priority "
            f"(lowest priority number) instance, got: {config}"
        )

    def test_returns_none_when_no_instance_of_module_exists(
        self, tmp_path, monkeypatch
    ):
        """Regression guard: the 'genuinely not found' contract must be
        preserved as None -- this must NOT change to raising or returning {}.
        """
        manager = _make_manager(tmp_path, monkeypatch)
        _write_local_providers(
            tmp_path,
            [
                {
                    "module": "provider-openai",
                    "config": {"priority": 1, "default_model": "gpt-5"},
                },
            ],
        )

        config = manager.get_provider_config("provider-anthropic")

        assert config is None, f"Expected None for unconfigured module, got: {config}"


# ============================================================
# DRY consolidation: resolve_provider_entry() shared resolver
# ============================================================
#
# Consolidates the matching predicate that was independently duplicated (and
# independently bugfixed for the same priority-tiebreak issue in PR #214 and
# #215) across:
#   - ProviderManager.get_provider_config()            (this file, above)
#   - commands/routing.py::_get_provider_config()
#   - commands/provider.py::_find_provider_entry()
#
# These tests exercise resolve_provider_entry() directly -- the union of all
# 3 predicates -- independent of any particular call site's return-value
# adaptation.


class TestResolveProviderEntry:
    """Tests for the shared resolve_provider_entry() resolver."""

    def test_id_match_returns_immediately_ignoring_priority(self):
        """An 'id' match is unambiguous by convention and must win even when
        a *different* entry would otherwise be the higher-priority module
        match."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "id": "openai-named",
                "module": "provider-openai",
                "config": {"priority": 99, "default_model": "named-instance"},
            },
            {
                "module": "provider-openai",
                "config": {"priority": 1, "default_model": "unnamed-instance"},
            },
        ]

        entry = resolve_provider_entry(providers, "openai-named")

        assert entry is not None
        assert entry.get("id") == "openai-named"
        assert entry["config"].get("default_model") == "named-instance", (
            f"id match must win regardless of priority, got: {entry}"
        )

    def test_module_full_match(self):
        """Caller-supplied name matches the 'module' field verbatim."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude"},
            },
        ]

        entry = resolve_provider_entry(providers, "provider-anthropic")

        assert entry is not None
        assert entry["config"].get("default_model") == "claude"

    def test_module_bare_name_matches_prefixed_module(self):
        """The common real-world case: caller passes the bare type name
        (e.g. 'anthropic') and the stored module has the 'provider-' prefix
        (e.g. 'provider-anthropic'). Exercises both the
        `module == f"provider-{name}"` and `module.removeprefix("provider-")
        == name` predicates simultaneously -- they coincide for any module id
        that has 'provider-' as a genuine prefix."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "module": "provider-anthropic",
                "config": {"default_model": "claude"},
            },
        ]

        entry = resolve_provider_entry(providers, "anthropic")

        assert entry is not None
        assert entry["config"].get("default_model") == "claude"

    def test_module_display_name_match(self):
        """Display-name matching (module.replace('provider-', '') -- mirrors
        commands/provider.py::_display_name()) uses str.replace(), which
        strips ALL occurrences of the substring, not just a leading prefix.
        This distinguishes it from the removeprefix()-based predicate: a
        module id with 'provider-' embedded (not as a strict prefix) only
        matches via the display-name predicate."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "module": "meta-provider-custom",
                "config": {"default_model": "custom-model"},
            },
        ]

        entry = resolve_provider_entry(providers, "meta-custom")

        assert entry is not None, (
            "Expected display-name predicate (module.replace('provider-', '')) "
            "to match 'meta-provider-custom' -> 'meta-custom'"
        )
        assert entry["config"].get("default_model") == "custom-model"

    def test_ambiguous_module_match_resolves_to_highest_priority(self):
        """When 2+ entries match on the module predicate (no distinguishing
        id), resolve deterministically to the highest-priority (lowest
        config.priority, default 100) instance -- not whichever is first in
        list order."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "module": "provider-openai",
                "config": {"priority": 5, "default_model": "wrong-instance"},
            },
            {
                "module": "provider-openai",
                "config": {"priority": 1, "default_model": "correct-instance"},
            },
        ]

        entry = resolve_provider_entry(providers, "openai")

        assert entry is not None
        assert entry["config"].get("default_model") == "correct-instance", (
            "resolve_provider_entry() must resolve to the highest-priority "
            f"(lowest priority number) instance, got: {entry}"
        )

    def test_ambiguous_module_match_defaults_missing_priority_to_100(self):
        """An entry with no explicit config.priority defaults to 100 (lowest
        precedence) when compared against an entry with an explicit,
        higher-precedence priority."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "module": "provider-openai",
                "config": {"default_model": "no-priority-set"},
            },
            {
                "module": "provider-openai",
                "config": {"priority": 2, "default_model": "explicit-priority"},
            },
        ]

        entry = resolve_provider_entry(providers, "openai")

        assert entry is not None
        assert entry["config"].get("default_model") == "explicit-priority", (
            f"Missing priority must default to 100, got: {entry}"
        )

    def test_returns_none_when_no_match(self):
        """Regression guard: the 'genuinely not found' contract must be
        preserved as None."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        providers = [
            {
                "module": "provider-openai",
                "config": {"priority": 1, "default_model": "gpt-5"},
            },
        ]

        entry = resolve_provider_entry(providers, "anthropic")

        assert entry is None, f"Expected None for unconfigured module, got: {entry}"

    def test_returns_none_for_empty_providers_list(self):
        """Edge case: an empty providers list must return None, not raise."""
        from amplifier_app_cli.provider_manager import resolve_provider_entry

        entry = resolve_provider_entry([], "anthropic")

        assert entry is None
