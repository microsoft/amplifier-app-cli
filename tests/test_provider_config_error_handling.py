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

    def test_generic_exception_prints_error_and_aborts(self):
        """When get_provider_models() raises a generic Exception,
        _prompt_model_selection() should print the error and raise click.Abort."""
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
            with pytest.raises(click.Abort):
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
