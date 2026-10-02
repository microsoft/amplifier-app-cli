"""Configured discovery must use the same settings as login and runtime."""

import os
from copy import deepcopy
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_app_cli import provider_config_utils as wizard
from amplifier_app_cli import provider_loader as loader
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.runtime.config import expand_env_vars


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
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://example.invalid/v1")
    monkeypatch.setenv("FIXTURE_HOST", "https://host.example.invalid")
    monkeypatch.setenv("FIXTURE_OPTION", "selected-option")
    config = {
        "auth_mode": "alternate",
        "api_key": "${FIXTURE_KEY}",
        "base_url": "${FIXTURE_ENDPOINT}",
        "host": "${FIXTURE_HOST}",
        "token_file_path": "/fixture/account.json",
        "options": {"values": [1, "${FIXTURE_OPTION}"]},
    }
    original = deepcopy(config)
    expected = expand_env_vars(deepcopy(original))
    provider = loader._try_instantiate_provider(provider_class, config)
    assert provider.config == expected
    if hasattr(provider, "api_key"):
        assert provider.api_key == "fixture-key"
    if hasattr(provider, "base_url"):
        assert provider.base_url == expected["base_url"]
    if hasattr(provider, "host"):
        assert provider.host == expected["host"]
    provider.config["options"]["values"].append(2)
    assert config == original


@pytest.mark.parametrize(
    "endpoint",
    ["${FIXTURE_ENDPOINT}", "https://${FIXTURE_HOST}/v1"],
    ids=["whole-reference", "interpolated"],
)
def test_openai_shaped_constructor_expands_config_and_preserves_mutable_values(
    monkeypatch, endpoint
):
    monkeypatch.setenv("FIXTURE_KEY", "fixture-key")
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://example.invalid/v1")
    monkeypatch.setenv("FIXTURE_HOST", "example.invalid")
    monkeypatch.setenv("FIXTURE_OPTION", "selected-option")
    config = {
        "api_key": "${FIXTURE_KEY}",
        "base_url": endpoint,
        "interpolated_endpoint": "https://${FIXTURE_HOST}/models",
        "options": {
            "values": ["${FIXTURE_OPTION}", {"endpoint": "${FIXTURE_ENDPOINT}"}]
        },
        # Expansion intentionally does not walk tuples; deepcopy must still
        # protect the mutable list contained in this unsupported container.
        "opaque": (["${FIXTURE_OPTION}"],),
    }
    original = deepcopy(config)
    expected = expand_env_vars(deepcopy(original))
    attempts = []

    class OpenAIShapedProvider:
        def __init__(self, api_key=None, config=None):
            self.api_key = api_key
            self.base_url = config["base_url"]
            self.received = deepcopy(config)
            attempts.append(self.received)
            config["options"]["values"].append("constructor-added")
            config["options"]["values"][1]["endpoint"] = "constructor-changed"
            config["opaque"][0].append("constructor-added")

    provider = loader._try_instantiate_provider(OpenAIShapedProvider, config)
    assert config == original
    assert len(attempts) == 1
    assert provider.base_url == "https://example.invalid/v1"
    assert provider.api_key == "fixture-key"
    assert provider.received == expected
    assert provider.received["opaque"] == (["${FIXTURE_OPTION}"],)


@pytest.mark.parametrize("provider_class", [EndpointProvider, HostProvider])
def test_connection_arguments_and_config_share_single_pass_expansion(
    provider_class, monkeypatch
):
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://example.invalid/${SECOND}")
    monkeypatch.setenv("FIXTURE_HOST", "https://host.example.invalid/${SECOND}")
    # An entire placeholder returned by getenv catches the legacy standalone
    # resolver being applied a second time after shared config expansion.
    monkeypatch.setenv("FIXTURE_KEY", "${SECOND}")
    monkeypatch.setenv("SECOND", "must-not-be-substituted-again")
    monkeypatch.delenv("FIXTURE_MISSING", raising=False)
    monkeypatch.setenv("FIXTURE_EMPTY", "")
    config = {
        "base_url": "${FIXTURE_ENDPOINT}",
        "host": "${FIXTURE_HOST}",
        "api_key": "${FIXTURE_KEY}",
        "options": {
            "missing": "${FIXTURE_MISSING}",
            "default": "${FIXTURE_MISSING:default}",
            "empty": "${FIXTURE_EMPTY:default}",
        },
    }
    original = deepcopy(config)
    expected = expand_env_vars(deepcopy(original))
    provider = loader._try_instantiate_provider(provider_class, config)
    assert config == original
    if hasattr(provider, "base_url"):
        assert provider.base_url == "https://example.invalid/${SECOND}"
        assert provider.base_url == expected["base_url"]
    if hasattr(provider, "host"):
        assert provider.host == "https://host.example.invalid/${SECOND}"
        assert provider.host == expected["host"]
    assert provider.api_key == "${SECOND}"
    assert provider.api_key == expected["api_key"]
    assert provider.config == expected
    assert provider.config["options"] == {
        "missing": "",
        "default": "default",
        "empty": "",
    }


def test_configured_environment_bindings_win_over_ambient_sdk_defaults(monkeypatch):
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://selected.example.invalid/v1")
    monkeypatch.setenv("FIXTURE_KEY", "selected-fixture-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ambient.example.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-fixture-key")
    config = {"base_url": "${FIXTURE_ENDPOINT}", "api_key": "${FIXTURE_KEY}"}
    original = deepcopy(config)
    attempts = []

    class SDKShapedProvider:
        def __init__(self, api_key=None, config=None):
            self.config = config
            self.base_url = config.get("base_url") or os.environ["OPENAI_BASE_URL"]
            self.api_key = api_key or os.environ["OPENAI_API_KEY"]
            attempts.append(deepcopy(config))

    provider = loader._try_instantiate_provider(SDKShapedProvider, config)
    assert config == original
    assert len(attempts) == 1
    assert provider.base_url == "https://selected.example.invalid/v1"
    assert provider.api_key == "selected-fixture-key"
    assert provider.config == expand_env_vars(deepcopy(original))


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
    monkeypatch.setenv("FIXTURE_CATALOG", "selected-catalog")
    events = []

    class CatalogProvider(ConfigProvider):
        async def list_models(self):
            events.append(("models", deepcopy(self.config)))
            return [SimpleNamespace(id=self.config["catalog"])]

        async def close(self):
            events.append(("close", deepcopy(self.config)))

    monkeypatch.setattr(loader, "load_provider_class", lambda _: CatalogProvider)
    config = {"catalog": "${FIXTURE_CATALOG}"}
    original = deepcopy(config)
    models = loader.get_provider_models("fixture", collected_config=config)
    assert config == original
    assert [m.id for m in models] == ["selected-catalog"]
    assert events == [
        ("models", {"catalog": "selected-catalog"}),
        ("close", {"catalog": "selected-catalog"}),
    ]


def test_azure_endpoint_alias_is_resolved(monkeypatch):
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://azure.invalid")
    provider = loader._try_instantiate_provider(
        EndpointProvider, {"azure_endpoint": "${FIXTURE_ENDPOINT}"}
    )
    assert provider.base_url == "https://azure.invalid"


@pytest.mark.parametrize("env_value", [None, ""])
def test_absent_connection_bindings_follow_runtime_expansion(monkeypatch, env_value):
    for name in ("FIXTURE_KEY", "FIXTURE_ENDPOINT", "FIXTURE_HOST"):
        monkeypatch.delenv(name, raising=False)
        if env_value is not None:
            monkeypatch.setenv(name, env_value)
    config = {
        "api_key": "${FIXTURE_KEY}",
        "base_url": "${FIXTURE_ENDPOINT}",
        "host": "${FIXTURE_HOST}",
    }
    original = deepcopy(config)
    provider = loader._try_instantiate_provider(EndpointProvider, config)
    assert provider.config == expand_env_vars(original)
    assert provider.config == {"api_key": "", "base_url": "", "host": ""}
    assert provider.api_key == ""
    assert provider.base_url == "http://placeholder"
    assert config == original
    # This pins normalization only. Session credential validation and a
    # provider/SDK's treatment of empty credentials remain separate boundaries.


def test_expanded_empty_primary_endpoint_uses_supplied_azure_alias(monkeypatch):
    monkeypatch.delenv("FIXTURE_PRIMARY", raising=False)
    monkeypatch.setenv("FIXTURE_ALIAS", "https://azure.example.invalid")
    config = {
        "base_url": "${FIXTURE_PRIMARY}",
        "azure_endpoint": "${FIXTURE_ALIAS}",
    }
    original = deepcopy(config)
    provider = loader._try_instantiate_provider(EndpointProvider, config)
    assert provider.base_url == "https://azure.example.invalid"
    assert provider.config == expand_env_vars(original)
    assert config == original


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
    monkeypatch.setenv("FIXTURE_ACCOUNT_PATH", str(tmp_path / f"{name}.json"))
    monkeypatch.setenv("FIXTURE_OTHER_ACCOUNT_PATH", str(tmp_path / "other.json"))
    monkeypatch.setattr(loader, "load_provider_class", lambda _: OAuthProvider)
    settings._write_scope(
        "global",
        {
            "config": {
                "providers": [
                    {
                        "id": account,
                        "module": "provider-fixture",
                        "config": {
                            "account": account,
                            "token_file_path": (
                                "${FIXTURE_ACCOUNT_PATH}"
                                if account == name
                                else "${FIXTURE_OTHER_ACCOUNT_PATH}"
                            ),
                        },
                    }
                    for account in ("first-account", "second-account")
                ]
            }
        },
    )
    original = deepcopy(settings.get_provider_overrides())
    original_bytes = settings.paths.global_settings.read_bytes()
    selected_config = next(entry["config"] for entry in original if entry["id"] == name)
    expected = expand_env_vars(deepcopy(selected_config))
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
    assert settings.paths.global_settings.read_bytes() == original_bytes
    assert settings.get_provider_overrides() == original
    assert selected_config["token_file_path"] == "${FIXTURE_ACCOUNT_PATH}"
    assert OAuthProvider.events == [
        ("status", expected),
        ("login", expected),
        ("status", expected),
    ]


def test_models_command_uses_exact_instance_and_expanded_config_through_real_loader(
    tmp_path, monkeypatch
):
    from amplifier_app_cli.commands.provider import provider

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FIXTURE_ENDPOINT", "https://selected.example.invalid/v1")
    monkeypatch.setenv("FIXTURE_KEY", "selected-fixture-key")
    monkeypatch.setenv("FIXTURE_CATALOG", "selected-model")
    monkeypatch.setenv("FIXTURE_OTHER_KEY", "other-fixture-key")
    events = []
    loaded_modules = []

    class OpenAIShapedProvider:
        def __init__(self, api_key=None, config=None):
            self.api_key = api_key
            self.config = config
            self.base_url = config.get("base_url")
            events.append(("construct", api_key, self.base_url, deepcopy(config)))

        def get_info(self):
            return SimpleNamespace(display_name="Fixture OpenAI", config_fields=[])

        async def list_models(self):
            events.append(
                ("models", self.api_key, self.base_url, deepcopy(self.config))
            )
            return [
                SimpleNamespace(
                    id=self.config["catalog"],
                    display_name="Fixture model",
                    context_window=1024,
                    max_output_tokens=128,
                    capabilities=[],
                )
            ]

        async def close(self):
            events.append(
                ("close", self.api_key, self.base_url, deepcopy(self.config))
            )

    def load_provider_class(module_id):
        loaded_modules.append(module_id)
        return OpenAIShapedProvider

    monkeypatch.setattr(loader, "load_provider_class", load_provider_class)
    settings = AppSettings()
    selected_config = {
        "base_url": "${FIXTURE_ENDPOINT}",
        "api_key": "${FIXTURE_KEY}",
        "catalog": "${FIXTURE_CATALOG}",
        "priority": 99,
    }
    entries = [
        {
            "id": "other-openai",
            "module": "provider-openai",
            "config": {
                "base_url": "https://other.example.invalid/v1",
                "api_key": "${FIXTURE_OTHER_KEY}",
                "catalog": "other-model",
                "priority": 1,
            },
        },
        {
            "id": "selected-openai",
            "module": "provider-openai",
            "config": selected_config,
        },
    ]
    original = deepcopy(entries)
    settings._write_scope("global", {"config": {"providers": entries}})
    original_bytes = settings.paths.global_settings.read_bytes()
    expected = expand_env_vars(deepcopy(selected_config))
    events.clear()
    loaded_modules.clear()

    # Keep settings resolution, routing and get_provider_models real; suppress
    # only first-run installation. Click exports a provider group, so patch the
    # module boundary rather than traversing package attributes.
    with patch(
        "amplifier_app_cli.commands.provider._ensure_providers_ready", lambda: None
    ):
        result = CliRunner().invoke(provider, ["models", "selected-openai"])

    assert result.exit_code == 0, result.output
    assert loaded_modules == ["provider-openai"]
    assert entries == original
    assert settings.get_provider_overrides() == original
    assert settings.paths.global_settings.read_bytes() == original_bytes
    assert events == [
        (action, "selected-fixture-key", "https://selected.example.invalid/v1", expected)
        for action in ("construct", "models", "close")
    ]
    assert "Models for selected-openai" in result.output
    assert "selected-model" in result.output
    assert "other-model" not in result.output


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
