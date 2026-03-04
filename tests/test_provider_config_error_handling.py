"""Tests for provider configuration error handling (Sections 1 & 2).

Verifies that _prompt_model_selection() catches non-connectivity exceptions
and that _manage_add_provider() / provider_add have safety nets around
configure_provider().
"""

import click
import pytest
from unittest.mock import MagicMock, patch


# ============================================================
# Task 1: _prompt_model_selection() error handling
# ============================================================


class TestPromptModelSelectionErrorHandling:
    """Tests for widened exception handling in _prompt_model_selection()."""

    def test_generic_exception_prints_error_and_raises_click_exception(self):
        """When get_provider_models() raises a generic Exception,
        _prompt_model_selection() should print the error and raise click.ClickException."""
        from amplifier_app_cli.provider_config_utils import _prompt_model_selection

        mock_console = MagicMock()

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                side_effect=Exception("Token expired. Run `gh auth login` to fix."),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.console",
                mock_console,
            ),
        ):
            with pytest.raises(click.ClickException):
                _prompt_model_selection("test-provider")

        # Verify the error message was printed (at least one call contains the error text)
        printed_texts = [str(call) for call in mock_console.print.call_args_list]
        joined = " ".join(printed_texts)
        assert "Token expired" in joined, (
            f"Expected error message in console output, got: {printed_texts}"
        )

    def test_connection_error_falls_through_to_manual_entry(self):
        """When get_provider_models() raises ConnectionError,
        existing behavior is preserved: falls through to manual model entry."""
        from amplifier_app_cli.provider_config_utils import _prompt_model_selection

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                side_effect=ConnectionError("Connection refused"),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="my-model",
            ) as mock_prompt,
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            result = _prompt_model_selection("test-provider")

        # Should have fallen through to manual entry and returned what user typed
        assert result == "my-model", f"Expected 'my-model', got '{result}'"
        mock_prompt.assert_called_once()

    def test_os_error_falls_through_to_manual_entry(self):
        """When get_provider_models() raises OSError,
        existing behavior is preserved: falls through to manual model entry."""
        from amplifier_app_cli.provider_config_utils import _prompt_model_selection

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                side_effect=OSError("Network unreachable"),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="fallback-model",
            ) as mock_prompt,
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            result = _prompt_model_selection("test-provider")

        assert result == "fallback-model", f"Expected 'fallback-model', got '{result}'"
        mock_prompt.assert_called_once()


# ============================================================
# Task 2: Safety net in _manage_add_provider() and provider_add
# ============================================================


def _make_settings(tmp_path):
    """Create AppSettings with isolated paths for testing."""
    from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths

    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


class TestManageAddProviderSafetyNet:
    """Tests for the safety net around configure_provider() in _manage_add_provider()."""

    def test_exception_prints_error_and_returns(self, tmp_path):
        """When configure_provider() raises an arbitrary Exception,
        _manage_add_provider() should print a friendly error and return
        (not crash with a traceback)."""
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)
        mock_console = MagicMock()

        with (
            patch(
                "amplifier_app_cli.commands.provider._ensure_providers_ready",
            ),
            patch(
                "amplifier_app_cli.commands.provider.ProviderManager",
            ) as MockPM,
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="1",
            ),
            patch(
                "amplifier_app_cli.commands.provider.KeyManager",
            ),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=Exception("Unexpected kaboom during config"),
            ),
            patch(
                "amplifier_app_cli.commands.provider.console",
                mock_console,
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            # Should NOT raise — should print error and return
            _manage_add_provider(settings)

        # Verify a friendly error was printed
        printed_texts = [str(call) for call in mock_console.print.call_args_list]
        joined = " ".join(printed_texts)
        assert "Unexpected kaboom" in joined, (
            f"Expected error message in console output, got: {printed_texts}"
        )

    def test_click_abort_propagates(self, tmp_path):
        """When configure_provider() raises click.Abort,
        it should propagate (not be caught by the safety net)."""
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)

        with (
            patch(
                "amplifier_app_cli.commands.provider._ensure_providers_ready",
            ),
            patch(
                "amplifier_app_cli.commands.provider.ProviderManager",
            ) as MockPM,
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="1",
            ),
            patch(
                "amplifier_app_cli.commands.provider.KeyManager",
            ),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=click.Abort(),
            ),
            patch("amplifier_app_cli.commands.provider.console"),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            with pytest.raises(click.Abort):
                _manage_add_provider(settings)

    def test_click_exception_propagates(self, tmp_path):
        """When configure_provider() raises click.ClickException,
        it should propagate (not be caught by the safety net)."""
        from amplifier_app_cli.commands.provider import _manage_add_provider

        settings = _make_settings(tmp_path)

        with (
            patch(
                "amplifier_app_cli.commands.provider._ensure_providers_ready",
            ),
            patch(
                "amplifier_app_cli.commands.provider.ProviderManager",
            ) as MockPM,
            patch(
                "amplifier_app_cli.commands.provider.Prompt.ask",
                return_value="1",
            ),
            patch(
                "amplifier_app_cli.commands.provider.KeyManager",
            ),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=click.ClickException("Auth failed"),
            ),
            patch("amplifier_app_cli.commands.provider.console"),
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            with pytest.raises(click.ClickException):
                _manage_add_provider(settings)


class TestProviderAddSafetyNet:
    """Tests for the safety net around configure_provider() in the provider_add command."""

    def test_exception_exits_with_code_1(self, tmp_path):
        """When configure_provider() raises an arbitrary Exception,
        provider_add should show a friendly error and exit with code 1."""
        from click.testing import CliRunner

        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        runner = CliRunner()

        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=Exception("Auth token invalid"),
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            result = runner.invoke(provider, ["add", "anthropic"])

        assert result.exit_code == 1, (
            f"Expected exit code 1, got {result.exit_code}. Output: {result.output}"
        )
        assert "Auth token invalid" in result.output, (
            f"Expected error message in output, got: {result.output}"
        )

    def test_click_abort_propagates_cleanly(self, tmp_path):
        """When configure_provider() raises click.Abort,
        provider_add should let Click handle it (exit code 1, no traceback)."""
        from click.testing import CliRunner

        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        runner = CliRunner()

        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=click.Abort(),
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            result = runner.invoke(provider, ["add", "anthropic"])

        # click.Abort produces exit code 1 and prints "Aborted!" by default
        assert result.exit_code == 1, (
            f"Expected exit code 1, got {result.exit_code}. Output: {result.output}"
        )
        # Should NOT contain a Python traceback
        assert "Traceback" not in result.output, (
            f"Expected no traceback, got: {result.output}"
        )

    def test_click_exception_shows_error_message(self, tmp_path):
        """When configure_provider() raises click.ClickException (from _prompt_model_selection),
        provider_add should let Click render it (exit code 1, error message shown, no traceback)."""
        from click.testing import CliRunner

        from amplifier_app_cli.commands.provider import provider

        settings = _make_settings(tmp_path)
        runner = CliRunner()

        with (
            patch(
                "amplifier_app_cli.commands.provider._get_settings",
                return_value=settings,
            ),
            patch("amplifier_app_cli.commands.provider._ensure_providers_ready"),
            patch(
                "amplifier_app_cli.commands.provider.configure_provider",
                side_effect=click.ClickException("Auth failed: run gh auth login"),
            ),
            patch("amplifier_app_cli.commands.provider.KeyManager"),
            patch("amplifier_app_cli.commands.provider.ProviderManager") as MockPM,
        ):
            mock_pm = MagicMock()
            mock_pm.list_providers.return_value = [
                ("provider-anthropic", "Anthropic", "Anthropic provider"),
            ]
            MockPM.return_value = mock_pm

            result = runner.invoke(provider, ["add", "anthropic"])

        assert result.exit_code == 1, (
            f"Expected exit code 1, got {result.exit_code}. Output: {result.output}"
        )
        assert "Auth failed" in result.output, (
            f"Expected error message in output, got: {result.output}"
        )
        assert "Traceback" not in result.output, (
            f"Expected no traceback, got: {result.output}"
        )
