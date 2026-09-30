"""Configured discovery must use the same settings as login and runtime."""

from copy import deepcopy
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_app_cli import provider_config_utils as wizard
from amplifier_app_cli import provider_loader as loader
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


@pytest.fixture(autouse=True)
def isolated_settings_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / ".amplifier"))


class StandardProvider:
    def __init__(self, api_key=None, config=None):
        self.api_key, self.config = api_key, config


class EndpointProvider:
    def __init__(self, *, base_url=None, api_key=None, config=None):
        self.base_url, self.api_key, self.config = base_url, api_key, config


class HostProvider:
    def __init__(self, host=None, config=None, api_key=None):
        self.host, self.api_key, self.config = host, api_key, config


class ConfigProvider:
    def __init__(self, config=None):
        self.config = config


@pytest.mark.parametrize(
    "provider_class", [StandardProvider, EndpointProvider, HostProvider, ConfigProvider]
)
def test_config_reaches_each_constructor_and_is_independent(
    provider_class, monkeypatch
):
    monkeypatch.setenv("FIXTURE_KEY", "fixture-key")
    config = {
        "auth_mode": "alternate",
        "api_key": "${FIXTURE_KEY}",
        "base_url": "https://endpoint.invalid/v1",
        "host": "https://host.invalid",
        "token_file_path": "/fixture/account.json",
        "options": {"values": [1]},
    }
    original = deepcopy(config)
    provider = loader._try_instantiate_provider(provider_class, config)
    assert provider.config == config
    if hasattr(provider, "api_key"):
        assert provider.api_key == "fixture-key"
    if hasattr(provider, "base_url"):
        assert provider.base_url == config["base_url"]
    if hasattr(provider, "host"):
        assert provider.host == config["host"]
    provider.config["options"]["values"].append(2)
    assert config == original


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, TypeError])
def test_constructor_validation_never_retries_using_defaults(error_type):
    attempts = []

    class RejectingProvider:
        def __init__(self, config=None):
            attempts.append(config)
            if config:
                raise error_type("Invalid configured account")

    with pytest.raises(error_type, match="Invalid configured account"):
        loader._try_instantiate_provider(RejectingProvider, {"auth_mode": "invalid"})
    assert attempts == [{"auth_mode": "invalid"}]


def test_no_arg_metadata_discovery_remains_supported_without_discarding_config():
    class MetadataOnlyProvider:
        pass

    assert isinstance(
        loader._try_instantiate_provider(MetadataOnlyProvider), MetadataOnlyProvider
    )
    assert (
        loader._try_instantiate_provider(
            MetadataOnlyProvider, {"auth_mode": "alternate"}
        )
        is None
    )


def test_unsupported_required_coordinator_does_not_run_constructor():
    class MountedOnlyProvider:
        def __init__(self, config, coordinator):
            pytest.fail("Lightweight discovery cannot invent a coordinator")

    assert (
        loader._try_instantiate_provider(MountedOnlyProvider, {"account": "test"})
        is None
    )


def test_kwargs_provider_receives_config():
    class FlexibleProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    assert loader._try_instantiate_provider(
        FlexibleProvider, {"account": "test"}
    ).kwargs == {"config": {"account": "test"}}


def test_catalog_uses_the_supplied_config(monkeypatch):
    class CatalogProvider(ConfigProvider):
        async def list_models(self):
            return [SimpleNamespace(id=self.config["catalog"])]

    monkeypatch.setattr(loader, "load_provider_class", lambda _: CatalogProvider)
    models = loader.get_provider_models(
        "fixture", collected_config={"catalog": "selected-catalog"}
    )
    assert [m.id for m in models] == ["selected-catalog"]


def test_azure_endpoint_alias_is_resolved(monkeypatch):
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://azure.invalid")
    provider = loader._try_instantiate_provider(
        EndpointProvider, {"azure_endpoint": "${FIXTURE_ENDPOINT}"}
    )
    assert provider.base_url == "https://azure.invalid"


class OAuthProvider(ConfigProvider):
    events: ClassVar[list] = []

    def get_info(self):
        return SimpleNamespace(
            display_name="Fixture OAuth",
            capabilities=["auth:browser"],
            config_fields=[
                {
                    "id": "auth_mode",
                    "display_name": "Connection",
                    "field_type": "choice",
                    "prompt": "Select connection",
                    "choices": ["default", "alternate"],
                    "default": "default",
                },
                {
                    "id": "token_file_path",
                    "display_name": "Account path",
                    "field_type": "text",
                    "prompt": "Account path",
                    "required": False,
                },
            ],
        )

    def auth_status(self):
        self.events.append(("status", deepcopy(self.config)))
        return "unauthenticated"

    async def login(self, print_fn=None):
        self.events.append(("login", deepcopy(self.config)))
        return True

    async def list_models(self):
        self.events.append(("models", deepcopy(self.config)))
        return [SimpleNamespace(id="chosen-model", display_name="Chosen model")]


def test_wizard_collects_then_reuses_configuration_for_login_and_models(monkeypatch):
    OAuthProvider.events = []
    monkeypatch.setattr(loader, "load_provider_class", lambda _: OAuthProvider)
    monkeypatch.setattr(wizard, "load_provider_class", lambda _: OAuthProvider)
    choices = iter(["2", "/fixture/alternate.json", "1"])
    monkeypatch.setattr(wizard.Prompt, "ask", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(wizard.Confirm, "ask", lambda *args, **kwargs: True)
    result = wizard.configure_provider("fixture", MagicMock())
    expected = {"auth_mode": "alternate", "token_file_path": "/fixture/alternate.json"}
    assert OAuthProvider.events == [
        ("status", expected),
        ("login", expected),
        ("models", expected),
    ]
    assert result == {**expected, "default_model": "chosen-model"}


def test_login_command_passes_saved_configuration_through_real_loader(
    tmp_path, monkeypatch
):
    from amplifier_app_cli.commands.provider import provider

    settings = AppSettings(
        paths=SettingsPaths(
            global_settings=tmp_path / "global.yaml",
            project_settings=tmp_path / "project.yaml",
            local_settings=tmp_path / "local.yaml",
        )
    )
    config = {
        "auth_mode": "alternate",
        "token_file_path": str(tmp_path / "account.json"),
    }
    monkeypatch.setattr(loader, "load_provider_class", lambda _: OAuthProvider)
    monkeypatch.setattr(wizard, "load_provider_class", lambda _: OAuthProvider)
    settings.set_provider_override(
        {"module": "provider-fixture", "config": config}, scope="global"
    )
    OAuthProvider.events = []
    with (
        patch(
            "amplifier_app_cli.commands.provider._get_settings", return_value=settings
        ),
        patch(
            "amplifier_app_cli.commands.provider.is_provider_module_installed",
            return_value=True,
        ),
        patch(
            "amplifier_app_cli.commands.provider.load_provider_class",
            return_value=OAuthProvider,
        ),
    ):
        result = CliRunner().invoke(provider, ["login", "fixture"])
    assert result.exit_code == 0, result.output
    assert ("login", config) in OAuthProvider.events


@pytest.mark.parametrize("name", ["first-account", "second-account"])
def test_login_uses_named_instance_even_when_module_has_multiple_accounts(
    tmp_path, monkeypatch, name
):
    from amplifier_app_cli.commands.provider import provider

    settings = AppSettings(
        paths=SettingsPaths(
            global_settings=tmp_path / "global.yaml",
            project_settings=tmp_path / "project.yaml",
            local_settings=tmp_path / "local.yaml",
        )
    )
    monkeypatch.setattr(loader, "load_provider_class", lambda _: OAuthProvider)
    settings._write_scope(
        "global",
        {
            "config": {
                "providers": [
                    {
                        "id": account,
                        "module": "provider-fixture",
                        "config": {"account": account},
                    }
                    for account in ("first-account", "second-account")
                ]
            }
        },
    )
    OAuthProvider.events = []
    with (
        patch(
            "amplifier_app_cli.commands.provider._get_settings", return_value=settings
        ),
        patch(
            "amplifier_app_cli.commands.provider.is_provider_module_installed",
            return_value=True,
        ) as installed,
        patch(
            "amplifier_app_cli.commands.provider.load_provider_class",
            return_value=OAuthProvider,
        ) as load,
    ):
        result = CliRunner().invoke(provider, ["login", name])
    assert result.exit_code == 0, result.output
    installed.assert_called_once_with("provider-fixture")
    load.assert_called_once_with("provider-fixture")
    assert ("login", {"account": name}) in OAuthProvider.events
    assert all(config == {"account": name} for _, config in OAuthProvider.events)


@pytest.mark.parametrize("name", ["fixture", "provider-fixture"])
def test_login_rejects_ambiguous_module_without_loading_or_logging_in(
    tmp_path, monkeypatch, name
):
    from amplifier_app_cli.commands.provider import provider

    settings = AppSettings(
        paths=SettingsPaths(
            global_settings=tmp_path / "global.yaml",
            project_settings=tmp_path / "project.yaml",
            local_settings=tmp_path / "local.yaml",
        )
    )
    monkeypatch.setattr(loader, "load_provider_class", lambda _: OAuthProvider)
    settings._write_scope(
        "global",
        {
            "config": {
                "providers": [
                    {
                        "id": account,
                        "module": "provider-fixture",
                        "config": {"account": account, "priority": priority},
                    }
                    for account, priority in (
                        ("first-account", 1),
                        ("second-account", 99),
                    )
                ]
            }
        },
    )
    with (
        patch(
            "amplifier_app_cli.commands.provider._get_settings", return_value=settings
        ),
        patch(
            "amplifier_app_cli.commands.provider.is_provider_module_installed"
        ) as installed,
        patch("amplifier_app_cli.commands.provider.load_provider_class") as load,
    ):
        result = CliRunner().invoke(provider, ["login", name])
    assert result.exit_code == 1
    assert "Choose an exact instance ID" in result.output
    installed.assert_not_called()
    load.assert_not_called()
