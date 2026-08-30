"""Tests for `amplifier provider login <provider-id>`.

Duck-types auth_status()/login() the same way the wizard's login step
does, so this command works safely independent of the parallel
provider-module PR that's adding those methods to provider-openai-chatgpt.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


def _make_settings(tmp_path: Path) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def _seed_provider(settings: AppSettings, module: str, config: dict) -> None:
    entry = {"module": module, "config": {**config, "priority": 1}}
    with patch(
        "amplifier_app_cli.provider_config_utils.get_provider_info",
        return_value=None,
    ):
        settings.set_provider_override(entry, scope="global")


def _oauth_info(display_name: str = "OpenAI ChatGPT") -> dict:
    return {
        "display_name": display_name,
        "capabilities": ["streaming", "auth:oauth-device-code"],
        "config_fields": [],
    }


def _api_key_info() -> dict:
    return {
        "display_name": "Anthropic",
        "capabilities": ["streaming"],
        "config_fields": [
            {"id": "api_key", "field_type": "secret", "env_var": "ANTHROPIC_API_KEY"}
        ],
    }


class TestProviderLoginCommandRegistered:
    def test_login_command_exists(self):
        from amplifier_app_cli.commands.provider import provider

        command_names = [c.name for c in provider.commands.values()]
        assert "login" in command_names


class TestProviderLoginNotInstalled:
    def test_helpful_error_names_provider_install(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=False,
            ),
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code != 0
        assert "not installed" in result.output.lower()
        assert "provider install" in result.output


class TestProviderLoginNoAuthCapability:
    def test_clean_error_for_api_key_provider(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_api_key_info(),
            ),
        ):
            result = runner.invoke(provider, ["login", "anthropic"])

        assert result.exit_code == 0
        assert "API-key configuration" in result.output
        assert "provider edit" in result.output


class TestProviderLoginDuckTypeMissingMethods:
    def test_clean_error_when_methods_missing_despite_capability(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        provider_instance = MagicMock(spec=[])  # no auth_status/login

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_oauth_info(),
            ),
            patch(
                "amplifier_app_cli.commands.provider.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.commands.provider._try_instantiate_provider",
                return_value=provider_instance,
            ),
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code == 0
        assert "API-key configuration" in result.output


class TestProviderLoginAlreadyAuthenticated:
    def test_reports_already_logged_in(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        provider_instance = MagicMock()
        provider_instance.auth_status.return_value = "authenticated"

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_oauth_info(),
            ),
            patch(
                "amplifier_app_cli.commands.provider.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.commands.provider._try_instantiate_provider",
                return_value=provider_instance,
            ),
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code == 0
        assert "Already logged in" in result.output
        provider_instance.login.assert_not_called()


class TestProviderLoginUsesSavedConfig:
    def test_instantiates_with_saved_config_for_id(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        _seed_provider(
            settings, "provider-openai-chatgpt", {"some_field": "saved-value"}
        )
        provider_instance = MagicMock()
        provider_instance.auth_status.return_value = "authenticated"

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_oauth_info(),
            ),
            patch(
                "amplifier_app_cli.commands.provider.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.commands.provider._try_instantiate_provider",
                return_value=provider_instance,
            ) as mock_instantiate,
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code == 0
        args, _ = mock_instantiate.call_args
        assert args[1]["some_field"] == "saved-value"

    def test_instantiates_with_empty_config_when_never_configured(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)  # no seeded provider
        provider_instance = MagicMock()
        provider_instance.auth_status.return_value = "authenticated"

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_oauth_info(),
            ),
            patch(
                "amplifier_app_cli.commands.provider.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.commands.provider._try_instantiate_provider",
                return_value=provider_instance,
            ) as mock_instantiate,
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code == 0
        args, _ = mock_instantiate.call_args
        assert args[1] == {}


class TestProviderLoginRunsLoginFlow:
    def test_successful_login_reports_success(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        provider_instance = MagicMock()
        provider_instance.auth_status.side_effect = ["unauthenticated", "authenticated"]
        provider_instance.login = AsyncMock(return_value=True)

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_oauth_info(),
            ),
            patch(
                "amplifier_app_cli.commands.provider.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.commands.provider._try_instantiate_provider",
                return_value=provider_instance,
            ),
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code == 0
        provider_instance.login.assert_awaited_once()
        assert "Logged in to" in result.output
        assert "authenticated" in result.output

    def test_failed_login_reports_failure_and_nonzero_exit(self, tmp_path):
        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        provider_instance = MagicMock()
        provider_instance.auth_status.return_value = "unauthenticated"
        provider_instance.login = AsyncMock(return_value=False)

        runner = CliRunner()
        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch(
                "amplifier_app_cli.commands.provider.is_provider_module_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_info",
                return_value=_oauth_info(),
            ),
            patch(
                "amplifier_app_cli.commands.provider.load_provider_class",
                return_value=MagicMock,
            ),
            patch(
                "amplifier_app_cli.commands.provider._try_instantiate_provider",
                return_value=provider_instance,
            ),
        ):
            result = runner.invoke(provider, ["login", "openai-chatgpt"])

        assert result.exit_code != 0
        assert "Login failed" in result.output
