"""Regression tests: normalize_provider_secrets() must not raise a
false-alarm "Could not resolve provider module" warning for a provider that
resolves fine but simply has no secret ConfigField (e.g. an OAuth-based
provider like openai-chatgpt). That warning is reserved for a genuine
resolution failure (get_provider_info() returns None).

See the owner's broken-onboarding transcript: after a Ctrl-C during
`provider add openai-chatgpt`, the save path still ran
normalize_provider_secrets() and printed a spurious
"warning: Could not resolve provider module ... skipping plaintext-secret scan"
warning for a provider that was never actually unresolvable.
"""

from unittest.mock import MagicMock, patch

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.provider_config_utils import normalize_provider_secrets


def _make_settings(tmp_path) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def _oauth_provider_info() -> dict:
    """A resolvable provider with zero secret ConfigFields -- the normal
    shape for an OAuth-based provider (e.g. openai-chatgpt, github-copilot)
    that has nothing to scan for a plaintext api_key."""
    return {
        "display_name": "OpenAI ChatGPT",
        "config_fields": [
            {
                "id": "some_non_secret_field",
                "display_name": "Something",
                "field_type": "text",
            }
        ],
    }


def _api_key_provider_info() -> dict:
    return {
        "display_name": "Test Provider",
        "config_fields": [
            {
                "id": "api_key",
                "display_name": "API Key",
                "field_type": "secret",
                "env_var": "TEST_PROVIDER_API_KEY",
            }
        ],
    }


class TestNormalizeProviderSecretsUnresolvableModule:
    """Genuine resolution failure -- must keep the loud, user-facing warning."""

    def test_warns_when_provider_module_unresolvable(self, tmp_path):
        settings = _make_settings(tmp_path)
        scope_settings = {
            "config": {
                "providers": [
                    {"module": "provider-does-not-exist", "config": {}},
                ]
            }
        }

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=None,
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            normalize_provider_secrets(settings, scope_settings, "global")

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Could not resolve provider module" in printed


class TestNormalizeProviderSecretsOAuthProviderNoWarning:
    """Resolved-but-no-secret-field is the NORMAL case for an OAuth
    provider -- must never print the loud warning, only a debug log."""

    def test_no_console_warning_for_provider_with_no_secret_field(self, tmp_path):
        settings = _make_settings(tmp_path)
        scope_settings = {
            "config": {
                "providers": [
                    {"module": "provider-openai-chatgpt", "config": {}},
                ]
            }
        }

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_oauth_provider_info(),
            ),
            patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
        ):
            normalize_provider_secrets(settings, scope_settings, "global")

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Could not resolve provider module" not in printed, (
            f"Expected no false-alarm warning, got console output: {printed}"
        )

    def test_debug_logged_for_provider_with_no_secret_field(self, tmp_path, caplog):
        import logging

        settings = _make_settings(tmp_path)
        scope_settings = {
            "config": {
                "providers": [
                    {"module": "provider-openai-chatgpt", "config": {}},
                ]
            }
        }

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_oauth_provider_info(),
            ),
            patch("amplifier_app_cli.provider_config_utils.console"),
            caplog.at_level(
                logging.DEBUG, logger="amplifier_app_cli.provider_config_utils"
            ),
        ):
            normalize_provider_secrets(settings, scope_settings, "global")

        assert any(
            "no secret ConfigField" in record.message for record in caplog.records
        ), f"Expected a debug log, got: {[r.message for r in caplog.records]}"

    def test_get_provider_info_called_once_per_entry(self, tmp_path):
        """Hoisting must not cost a second get_provider_info() call for the
        same entry (previously implied by _secret_field_id_for() calling it
        again internally)."""
        settings = _make_settings(tmp_path)
        scope_settings = {
            "config": {
                "providers": [
                    {"module": "provider-openai-chatgpt", "config": {}},
                ]
            }
        }

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_oauth_provider_info(),
            ) as mock_get_info,
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            normalize_provider_secrets(settings, scope_settings, "global")

        assert mock_get_info.call_count == 1, (
            f"Expected get_provider_info() called once, got "
            f"{mock_get_info.call_count} calls"
        )


class TestNormalizeProviderSecretsStillMovesLiteralSecrets:
    """Regression guard: the split must not break the actual
    plaintext-to-keys.env normalization for providers that DO have a
    secret ConfigField."""

    def test_literal_secret_still_moved_to_placeholder(self, tmp_path):
        settings = _make_settings(tmp_path)
        scope_settings = {
            "config": {
                "providers": [
                    {
                        "module": "provider-test",
                        "config": {"api_key": "sk-literal-secret-value"},
                    },
                ]
            }
        }

        # Patch KeyManager so this test never touches the real, shared
        # ~/.amplifier/keys.env on the host running the suite.
        mock_key_manager = MagicMock()
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=_api_key_provider_info(),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.KeyManager",
                return_value=mock_key_manager,
            ),
        ):
            normalize_provider_secrets(settings, scope_settings, "global")

        entry_config = scope_settings["config"]["providers"][0]["config"]
        assert entry_config["api_key"].startswith("${"), (
            f"Expected literal secret rewritten to a placeholder, got: {entry_config}"
        )
        mock_key_manager.save_key.assert_called_once()
