"""Tests for config command helpers in CommandProcessor."""

import sys
import os

import pytest
from unittest.mock import MagicMock

# Add tests directory to sys.path so helpers can be imported
sys.path.insert(0, os.path.dirname(__file__))

from amplifier_app_cli.main import CommandProcessor
from helpers import _make_command_processor


class TestRedactValue:
    """Tests for CommandProcessor._redact_value static method."""

    def test_redacts_api_key(self):
        """Sensitive key 'api_key' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "api_key", "sk-ant-1234567890abcdef1234567890"
        )
        assert result == "sk-a...redacted"

    def test_redacts_token(self):
        """Key containing 'token' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "access_token", "ghp_1234567890abcdefghijklmnop"
        )
        assert result == "ghp_...redacted"

    def test_redacts_secret(self):
        """Key containing 'secret' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "client_secret", "abcdefghijklmnopqrstuvwxyz"
        )
        assert result == "abcd...redacted"

    def test_redacts_password(self):
        """Key containing 'password' with long value should be redacted."""
        result = CommandProcessor._redact_value(
            "db_password", "supersecretpassword12345"
        )
        assert result == "supe...redacted"

    def test_does_not_redact_short_value(self):
        """Sensitive key with value <= 20 chars should NOT be redacted."""
        result = CommandProcessor._redact_value("api_key", "short")
        assert result == "short"

    def test_does_not_redact_non_sensitive_key(self):
        """Non-sensitive key with long value should NOT be redacted."""
        long_value = "a_very_long_value_that_exceeds_twenty_characters"
        result = CommandProcessor._redact_value("model", long_value)
        assert result == long_value

    def test_handles_non_string_value(self):
        """Non-string value (e.g. integer) should be returned as-is."""
        result = CommandProcessor._redact_value("api_key", 12345)
        assert result == 12345

    def test_redacts_key_containing_keyword(self):
        """Key containing a sensitive keyword (e.g. 'encryption_key') should be redacted."""
        long_value = "a_very_long_encryption_value_here_12345"
        result = CommandProcessor._redact_value("encryption_key", long_value)
        assert result == "a_ve...redacted"


def _make_mock_configurator():
    """Create a MagicMock configurator with realistic list data for testing."""
    mock_configurator = MagicMock()

    # context_list: 3 items (2 enabled foundation, 1 disabled caveman)
    mock_configurator.context_list.return_value = [
        {"name": "foundation-base", "enabled": True, "source": "foundation"},
        {"name": "foundation-tools-context", "enabled": True, "source": "foundation"},
        {"name": "caveman-context", "enabled": False, "source": "caveman"},
    ]

    # tools_list: 3 items (2 enabled foundation, 1 disabled caveman)
    mock_configurator.tools_list.return_value = [
        {"name": "foundation-tool-fs", "enabled": True, "source": "foundation"},
        {"name": "foundation-tool-web", "enabled": True, "source": "foundation"},
        {"name": "caveman-tool", "enabled": False, "source": "caveman"},
    ]

    # hooks_list: 1 item (enabled modes hook)
    mock_configurator.hooks_list.return_value = [
        {"name": "modes-hook", "enabled": True, "source": "foundation"},
    ]

    # providers_list: 1 item (enabled anthropic with api_key in config)
    mock_configurator.providers_list.return_value = [
        {
            "name": "anthropic",
            "enabled": True,
            "source": "foundation",
            "config": {
                "api_key": "sk-ant-1234567890abcdef1234567890",
                "model": "claude-3-5-sonnet",
            },
        },
    ]

    # agents_list: 1 item (enabled foundation:explorer)
    mock_configurator.agents_list.return_value = [
        {"name": "foundation:explorer", "enabled": True, "source": "foundation"},
    ]

    # behaviors_list: 2 items (enabled foundation, disabled caveman with contribution counts)
    mock_configurator.behaviors_list.return_value = [
        {
            "name": "foundation",
            "enabled": True,
            "contributions": {
                "context": 2,
                "tools": 2,
                "hooks": 1,
                "providers": 1,
                "agents": 1,
            },
        },
        {
            "name": "caveman",
            "enabled": False,
            "contributions": {"context": 1, "tools": 1},
        },
    ]

    # diff_from_original: 1 change (caveman context disabled)
    mock_configurator.diff_from_original.return_value = [
        {"category": "context", "name": "caveman-context", "action": "disabled"},
    ]

    return mock_configurator


class TestConfigDashboard:
    """Tests for the /config live dashboard rendering via SessionConfigurator."""

    @pytest.mark.asyncio
    async def test_config_no_args_renders_dashboard(self):
        """All 6 list methods should be called on the configurator when rendering dashboard."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)

        await cp._get_config_display()

        mock_configurator.context_list.assert_called_once()
        mock_configurator.tools_list.assert_called_once()
        mock_configurator.hooks_list.assert_called_once()
        mock_configurator.providers_list.assert_called_once()
        mock_configurator.agents_list.assert_called_once()
        mock_configurator.behaviors_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_dashboard_returns_empty_string(self):
        """handle_command returns '' because output goes to console, not return value."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)

        result = await cp.handle_command("show_config", {})

        assert result == ""

    @pytest.mark.asyncio
    async def test_config_falls_back_without_configurator(self):
        """When no configurator is set, falls back to legacy display and returns a string."""
        cp = _make_command_processor()  # No configurator set

        result = await cp._get_config_display()

        assert isinstance(result, str)
