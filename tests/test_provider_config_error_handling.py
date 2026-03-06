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

    def test_generic_exception_prints_warning_and_falls_back_to_manual_entry(self):
        """When get_provider_models() raises a generic Exception,
        _prompt_model_selection() should print a warning and fall back to manual entry
        (not raise click.ClickException)."""
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
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="my-model",
            ),
        ):
            result = _prompt_model_selection("test-provider")

        # Should return the manually-entered model, not raise
        assert result == "my-model", f"Expected 'my-model', got '{result}'"

        # Verify the warning message was printed
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


# ============================================================
# Task 3: Spinner wraps model fetching
# ============================================================


class TestModelFetchingSpinner:
    """Tests that _prompt_model_selection() wraps model fetching in a spinner."""

    def test_spinner_context_manager_entered_during_fetch(self):
        """console.status() should be entered as a context manager during model fetching."""
        from amplifier_app_cli.provider_config_utils import _prompt_model_selection

        mock_console = MagicMock()
        mock_status_ctx = MagicMock()
        mock_console.status.return_value = mock_status_ctx

        mock_model = MagicMock()
        mock_model.id = "test-model"
        mock_model.display_name = "Test Model"
        mock_model.capabilities = []

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                return_value=[mock_model],
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.console",
                mock_console,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="1",
            ),
        ):
            result = _prompt_model_selection("test-provider")

        assert result == "test-model", f"Expected 'test-model', got '{result}'"
        mock_console.status.assert_called_once()
        mock_status_ctx.__enter__.assert_called_once()
        mock_status_ctx.__exit__.assert_called_once()

    def test_spinner_exits_on_connection_error(self):
        """Spinner should exit cleanly when get_provider_models() raises ConnectionError."""
        from amplifier_app_cli.provider_config_utils import _prompt_model_selection

        mock_console = MagicMock()
        mock_status_ctx = MagicMock()
        mock_console.status.return_value = mock_status_ctx

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                side_effect=ConnectionError("refused"),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.console",
                mock_console,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="fallback-model",
            ),
        ):
            result = _prompt_model_selection("test-provider")

        assert result == "fallback-model", f"Expected 'fallback-model', got '{result}'"
        mock_status_ctx.__enter__.assert_called_once()
        mock_status_ctx.__exit__.assert_called_once()

    def test_spinner_exits_on_generic_exception(self):
        """Spinner should exit cleanly when get_provider_models() raises a generic Exception,
        then fall back to manual entry (not raise click.ClickException)."""
        from amplifier_app_cli.provider_config_utils import _prompt_model_selection

        mock_console = MagicMock()
        mock_status_ctx = MagicMock()
        mock_console.status.return_value = mock_status_ctx

        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_models",
                side_effect=Exception("Token expired"),
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.console",
                mock_console,
            ),
            patch(
                "amplifier_app_cli.provider_config_utils.Prompt.ask",
                return_value="my-model",
            ),
        ):
            result = _prompt_model_selection("test-provider")

        # Should return the manually-entered model, not raise
        assert result == "my-model", f"Expected 'my-model', got '{result}'"
        mock_status_ctx.__enter__.assert_called_once()
        mock_status_ctx.__exit__.assert_called_once()


# ============================================================
# Task 7: _manage_test_providers() spinner
# ============================================================


class TestProviderTestSpinner:
    """Tests for spinner display during provider connection testing."""

    def test_spinner_shown_during_provider_testing(self):
        """Spinner should be shown while providers are being tested."""
        from amplifier_app_cli.commands.provider import _manage_test_providers

        mock_console = MagicMock()
        mock_status_ctx = MagicMock()
        mock_console.status.return_value = mock_status_ctx

        provider = {"module": "test-mod", "id": "test-provider", "config": {}}

        with (
            patch(
                "amplifier_app_cli.commands.provider.console",
                mock_console,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_models",
                return_value=["model-a"],
            ),
        ):
            _manage_test_providers(MagicMock(), [provider])

        mock_console.status.assert_called_once()
        mock_status_ctx.__enter__.assert_called_once()
        mock_status_ctx.__exit__.assert_called_once()

    def test_spinner_not_shown_when_no_providers(self):
        """Spinner should NOT be shown when provider list is empty."""
        from amplifier_app_cli.commands.provider import _manage_test_providers

        mock_console = MagicMock()

        with patch(
            "amplifier_app_cli.commands.provider.console",
            mock_console,
        ):
            _manage_test_providers(MagicMock(), [])

        mock_console.status.assert_not_called()

    def test_spinner_exits_on_provider_test_failure(self):
        """Spinner should exit cleanly even when a provider test fails."""
        from amplifier_app_cli.commands.provider import _manage_test_providers

        mock_console = MagicMock()
        mock_status_ctx = MagicMock()
        mock_console.status.return_value = mock_status_ctx

        provider = {"module": "bad-mod", "id": "bad-provider", "config": {}}

        with (
            patch(
                "amplifier_app_cli.commands.provider.console",
                mock_console,
            ),
            patch(
                "amplifier_app_cli.commands.provider.get_provider_models",
                side_effect=ConnectionError("Connection refused"),
            ),
        ):
            _manage_test_providers(MagicMock(), [provider])

        mock_status_ctx.__enter__.assert_called_once()
        mock_status_ctx.__exit__.assert_called_once()
