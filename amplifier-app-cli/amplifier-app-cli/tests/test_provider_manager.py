import yaml

from amplifier_app_cli.paths import create_config_manager
from amplifier_app_cli.provider_manager import ProviderManager


def test_provider_override_persists_under_config_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("amplifier_app_cli.paths.Path.home", classmethod(lambda cls: fake_home))

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
    assert provider_entry["source"].startswith("git+https://github.com/microsoft/amplifier-module-provider-anthropic")

    provider = manager.get_current_provider()
    assert provider is not None
    assert provider.module_id == "provider-anthropic"
    assert provider.source == "local"

    manager.reset_provider("local")

    cleared_contents = yaml.safe_load(settings_path.read_text()) or {}
    config_section = cleared_contents.get("config") or {}
    assert "providers" not in config_section
    assert manager.get_current_provider() is None
