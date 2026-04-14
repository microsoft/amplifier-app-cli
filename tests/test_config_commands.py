"""Tests for config command helpers in CommandProcessor."""

import sys
import os

import pytest
from unittest.mock import AsyncMock, MagicMock

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


class TestConfigSubcommandRouting:
    """Tests that /config subcommands are correctly routed by process_input."""

    def test_config_no_args_routes_to_dashboard(self):
        """/config with no args routes to action='show_config' with empty args."""
        cp = _make_command_processor()
        action, data = cp.process_input("/config")
        assert action == "show_config"
        assert data["args"] == ""

    def test_config_category_routes_to_show_config(self):
        """/config tools routes to show_config with args='tools'."""
        cp = _make_command_processor()
        action, data = cp.process_input("/config tools")
        assert action == "show_config"
        assert data["args"] == "tools"

    def test_config_category_name_routes_with_args(self):
        """/config tools tool-bash routes with args='tools tool-bash'."""
        cp = _make_command_processor()
        action, data = cp.process_input("/config tools tool-bash")
        assert action == "show_config"
        assert data["args"] == "tools tool-bash"

    def test_config_disable_routes_with_full_args(self):
        """/config tools disable tool-bash routes with full args captured."""
        cp = _make_command_processor()
        action, data = cp.process_input("/config tools disable tool-bash")
        assert action == "show_config"
        assert data["args"] == "tools disable tool-bash"

    def test_config_diff_routes(self):
        """/config diff routes to show_config with args='diff'."""
        cp = _make_command_processor()
        action, data = cp.process_input("/config diff")
        assert action == "show_config"
        assert data["args"] == "diff"

    def test_config_save_routes(self):
        """/config save routes to show_config with args='save'."""
        cp = _make_command_processor()
        action, data = cp.process_input("/config save")
        assert action == "show_config"
        assert data["args"] == "save"

    def test_config_set_routes(self):
        """/config set ... routes to show_config with the full args string."""
        cp = _make_command_processor()
        action, data = cp.process_input(
            "/config set providers.anthropic.config.model claude-sonnet-4"
        )
        assert action == "show_config"
        assert data["args"] == "set providers.anthropic.config.model claude-sonnet-4"


class TestConfigCategoryList:
    """Tests that /config <category> calls the correct list method."""

    @pytest.mark.asyncio
    async def test_config_tools_calls_tools_list(self):
        """/config tools calls configurator.tools_list()."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("tools")
        mock_configurator.tools_list.assert_called()

    @pytest.mark.asyncio
    async def test_config_context_calls_context_list(self):
        """/config context calls configurator.context_list()."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("context")
        mock_configurator.context_list.assert_called()

    @pytest.mark.asyncio
    async def test_config_hooks_calls_hooks_list(self):
        """/config hooks calls configurator.hooks_list()."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("hooks")
        mock_configurator.hooks_list.assert_called()

    @pytest.mark.asyncio
    async def test_config_providers_calls_providers_list(self):
        """/config providers calls configurator.providers_list()."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("providers")
        mock_configurator.providers_list.assert_called()

    @pytest.mark.asyncio
    async def test_config_agents_calls_agents_list(self):
        """/config agents calls configurator.agents_list()."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("agents")
        mock_configurator.agents_list.assert_called()

    @pytest.mark.asyncio
    async def test_config_behaviors_calls_behaviors_list(self):
        """/config behaviors calls configurator.behaviors_list()."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("behaviors")
        mock_configurator.behaviors_list.assert_called()


class TestConfigMutation:
    """Tests for config mutation commands (enable/disable)."""

    @pytest.mark.asyncio
    async def test_config_context_disable(self):
        """context disable calls cfg.context_disable('foundation:system-base')."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.context_disable.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("context disable foundation:system-base")
        mock_configurator.context_disable.assert_called_once_with(
            "foundation:system-base"
        )

    @pytest.mark.asyncio
    async def test_config_context_enable(self):
        """context enable calls cfg.context_enable('caveman:caveman-rules')."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.context_enable.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("context enable caveman:caveman-rules")
        mock_configurator.context_enable.assert_called_once_with(
            "caveman:caveman-rules"
        )

    @pytest.mark.asyncio
    async def test_config_tools_disable(self):
        """tools disable calls cfg.tool_disable('tool-bash') — uses AsyncMock."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.tool_disable = AsyncMock(return_value=None)
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("tools disable tool-bash")
        mock_configurator.tool_disable.assert_called_once_with("tool-bash")

    @pytest.mark.asyncio
    async def test_config_tools_enable(self):
        """tools enable calls cfg.tool_enable('tool-bash') — uses AsyncMock."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.tool_enable = AsyncMock(return_value=None)
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("tools enable tool-bash")
        mock_configurator.tool_enable.assert_called_once_with("tool-bash")

    @pytest.mark.asyncio
    async def test_config_behaviors_disable(self):
        """behaviors disable calls cfg.behavior_disable('caveman') — returns dict."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.behavior_disable.return_value = {
            "disabled": ["caveman"],
            "warnings": [],
        }
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("behaviors disable caveman")
        mock_configurator.behavior_disable.assert_called_once_with("caveman")

    @pytest.mark.asyncio
    async def test_config_behaviors_enable(self):
        """behaviors enable calls cfg.behavior_enable('caveman') — returns dict."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.behavior_enable.return_value = {
            "enabled": ["caveman"],
            "warnings": [],
        }
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("behaviors enable caveman")
        mock_configurator.behavior_enable.assert_called_once_with("caveman")

    @pytest.mark.asyncio
    async def test_config_mutation_error_displayed(self):
        """ValueError from context_disable is caught, 'not found' or 'error' in result."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.context_disable.side_effect = ValueError("Item not found")
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("context disable nonexistent-item")
        assert isinstance(result, str)
        assert any(word in result.lower() for word in ["not found", "error"])


class TestConfigDiff:
    """Tests for /config diff subcommand."""

    @pytest.mark.asyncio
    async def test_config_diff_calls_configurator(self):
        """diff subcommand calls configurator.diff_from_original()."""
        mock_configurator = _make_mock_configurator()
        # _make_mock_configurator returns 1 change by default
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("diff")
        mock_configurator.diff_from_original.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_diff_no_changes(self):
        """Empty diff returns a 'no changes' message."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.diff_from_original.return_value = []
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("diff")
        assert "no changes" in result.lower()


class TestConfigSave:
    """Tests for /config save subcommand."""

    @pytest.mark.asyncio
    async def test_config_save_calls_configurator(self):
        """/config save calls cfg.save(scope='global'), result contains 'saved'."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.save.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("save")
        mock_configurator.save.assert_called_once_with(scope="global")
        assert "saved" in result.lower()

    @pytest.mark.asyncio
    async def test_config_save_project_scope(self):
        """/config save --scope project calls cfg.save(scope='project')."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.save.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("save --scope project")
        mock_configurator.save.assert_called_once_with(scope="project")

    @pytest.mark.asyncio
    async def test_config_save_error(self):
        """save raises ValueError → result contains 'error'."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.save.side_effect = ValueError("Permission denied")
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("save")
        assert "error" in result.lower()


class TestConfigSet:
    """Tests for /config set subcommand."""

    @pytest.mark.asyncio
    async def test_config_set_calls_configurator(self):
        """/config set path value calls config_set with path and string value, result contains 'set'."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.config_set.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display(
            "set providers.anthropic.config.model claude-sonnet-4"
        )
        mock_configurator.config_set.assert_called_once_with(
            "providers.anthropic.config.model", "claude-sonnet-4"
        )
        assert "set" in result.lower()

    @pytest.mark.asyncio
    async def test_config_set_parses_boolean(self):
        """/config set path true calls config_set with Python bool True."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.config_set.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("set providers.raw true")
        mock_configurator.config_set.assert_called_once_with("providers.raw", True)

    @pytest.mark.asyncio
    async def test_config_set_parses_integer(self):
        """/config set path 30 calls config_set with Python int 30."""
        mock_configurator = _make_mock_configurator()
        mock_configurator.config_set.return_value = None
        cp = _make_command_processor(configurator=mock_configurator)
        await cp._get_config_display("set tools.bash.timeout 30")
        mock_configurator.config_set.assert_called_once_with("tools.bash.timeout", 30)

    @pytest.mark.asyncio
    async def test_config_set_usage_error(self):
        """/config set with no path/value returns usage message containing 'usage'."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("set")
        assert "usage" in result.lower()
