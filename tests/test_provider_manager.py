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
