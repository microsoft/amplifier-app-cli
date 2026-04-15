"""Tests for config command helpers in CommandProcessor."""

import sys
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
    """Create a MagicMock configurator with realistic multi-claimant list data for testing.

    Uses multi-claimant format:
    - Items use 'behaviors' list (not 'source' string) for behavior attribution
    - Tool items include 'module_id' field
    - behaviors_list contributions use lists of item name strings (not int counts)
    """
    mock_configurator = MagicMock()

    # context_list: 3 items (2 enabled foundation, 1 disabled caveman)
    mock_configurator.context_list.return_value = [
        {"name": "foundation-base", "enabled": True, "behaviors": ["foundation"]},
        {
            "name": "foundation-tools-context",
            "enabled": True,
            "behaviors": ["foundation"],
        },
        {"name": "caveman-context", "enabled": False, "behaviors": ["caveman"]},
    ]

    # tools_list: 3 items (2 enabled foundation, 1 disabled caveman)
    # Includes 'module_id' and 'behaviors' list for _render_tools_section compatibility
    mock_configurator.tools_list.return_value = [
        {
            "name": "foundation-tool-fs",
            "enabled": True,
            "behaviors": ["foundation"],
            "module_id": "amplifier_core.tools.filesystem",
            "config": {},
        },
        {
            "name": "foundation-tool-web",
            "enabled": True,
            "behaviors": ["foundation"],
            "module_id": "amplifier_core.tools.web",
            "config": {},
        },
        {
            "name": "caveman-tool",
            "enabled": False,
            "behaviors": ["caveman"],
            "module_id": "amplifier_bundle_caveman.tools.caveman",
            "config": {},
        },
    ]

    # hooks_list: 1 item (enabled modes hook)
    mock_configurator.hooks_list.return_value = [
        {
            "name": "modes-hook",
            "enabled": True,
            "behaviors": ["foundation"],
            "event": "llm:request",
        },
    ]

    # providers_list: 1 item (enabled anthropic with api_key in config)
    mock_configurator.providers_list.return_value = [
        {
            "name": "anthropic",
            "enabled": True,
            "behaviors": ["foundation"],
            "config": {
                "api_key": "sk-ant-1234567890abcdef1234567890",
                "model": "claude-3-5-sonnet",
            },
        },
    ]

    # agents_list: 1 item (enabled foundation:explorer)
    mock_configurator.agents_list.return_value = [
        {"name": "foundation:explorer", "enabled": True, "behaviors": ["foundation"]},
    ]

    # behaviors_list: 2 items (enabled foundation, disabled caveman)
    # Contributions use lists of item name strings (not int counts)
    mock_configurator.behaviors_list.return_value = [
        {
            "name": "foundation",
            "enabled": True,
            "contributions": {
                "context": [
                    "context:foundation-base",
                    "context:foundation-tools-context",
                ],
                "tools": ["tools:foundation-tool-fs", "tools:foundation-tool-web"],
                "hooks": ["hooks:modes-hook"],
                "providers": ["providers:anthropic"],
                "agents": ["agents:foundation:explorer"],
            },
        },
        {
            "name": "caveman",
            "enabled": False,
            "contributions": {
                "context": ["context:caveman-context"],
                "tools": ["tools:caveman-tool"],
                "hooks": [],
                "providers": [],
                "agents": [],
            },
        },
    ]

    # diff_from_original: 1 change (caveman context disabled)
    mock_configurator.diff_from_original.return_value = [
        {"category": "context", "name": "caveman-context", "action": "disabled"},
    ]

    return mock_configurator


class TestMockConfiguratorFormat:
    """Tests that _make_mock_configurator returns multi-claimant format data.

    These tests verify the mock data uses 'behaviors' lists instead of 'source' strings,
    tools have 'module_id' fields, and behaviors contributions are lists of strings.
    """

    def test_context_items_use_behaviors_list(self):
        """Context items should have 'behaviors' list, not 'source' string."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.context_list()
        assert len(items) > 0, "context_list should return items"
        for item in items:
            assert "behaviors" in item, (
                f"context item {item['name']!r} should have 'behaviors' key"
            )
            assert isinstance(item["behaviors"], list), (
                f"context item {item['name']!r} 'behaviors' should be a list"
            )

    def test_tools_items_use_behaviors_list(self):
        """Tool items should have 'behaviors' list, not 'source' string."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.tools_list()
        assert len(items) > 0, "tools_list should return items"
        for item in items:
            assert "behaviors" in item, (
                f"tool item {item['name']!r} should have 'behaviors' key"
            )
            assert isinstance(item["behaviors"], list), (
                f"tool item {item['name']!r} 'behaviors' should be a list"
            )

    def test_tools_items_have_module_id(self):
        """Tool items should have 'module_id' field for _render_tools_section."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.tools_list()
        assert len(items) > 0, "tools_list should return items"
        for item in items:
            assert "module_id" in item, (
                f"tool item {item['name']!r} should have 'module_id' field"
            )

    def test_hooks_items_use_behaviors_list(self):
        """Hook items should have 'behaviors' list, not 'source' string."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.hooks_list()
        assert len(items) > 0, "hooks_list should return items"
        for item in items:
            assert "behaviors" in item, (
                f"hook item {item['name']!r} should have 'behaviors' key"
            )
            assert isinstance(item["behaviors"], list), (
                f"hook item {item['name']!r} 'behaviors' should be a list"
            )

    def test_provider_items_use_behaviors_list(self):
        """Provider items should have 'behaviors' list, not 'source' string."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.providers_list()
        assert len(items) > 0, "providers_list should return items"
        for item in items:
            assert "behaviors" in item, (
                f"provider item {item['name']!r} should have 'behaviors' key"
            )
            assert isinstance(item["behaviors"], list), (
                f"provider item {item['name']!r} 'behaviors' should be a list"
            )

    def test_agent_items_use_behaviors_list(self):
        """Agent items should have 'behaviors' list, not 'source' string."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.agents_list()
        assert len(items) > 0, "agents_list should return items"
        for item in items:
            assert "behaviors" in item, (
                f"agent item {item['name']!r} should have 'behaviors' key"
            )
            assert isinstance(item["behaviors"], list), (
                f"agent item {item['name']!r} 'behaviors' should be a list"
            )

    def test_behaviors_contributions_are_lists(self):
        """behaviors_list contributions should use lists of strings, not int counts."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.behaviors_list()
        assert len(items) > 0, "behaviors_list should return items"
        enabled_item = next((i for i in items if i.get("enabled", True)), None)
        assert enabled_item is not None, "should have at least one enabled behavior"
        contributions = enabled_item.get("contributions", {})
        assert isinstance(contributions, dict), "contributions should be a dict"
        for cat, value in contributions.items():
            assert isinstance(value, list), (
                f"contributions[{cat!r}] should be a list of strings, got {type(value).__name__}"
            )

    def test_behaviors_contributions_contain_strings(self):
        """behaviors_list contribution lists should contain string item names."""
        mock_configurator = _make_mock_configurator()
        items = mock_configurator.behaviors_list()
        enabled_item = next((i for i in items if i.get("enabled", True)), None)
        assert enabled_item is not None
        contributions = enabled_item.get("contributions", {})
        non_empty_cats = {k: v for k, v in contributions.items() if v}
        assert len(non_empty_cats) > 0, (
            "enabled behavior should have some non-empty contributions"
        )
        for cat, value in non_empty_cats.items():
            for name in value:
                assert isinstance(name, str), (
                    f"contributions[{cat!r}] items should be strings, got {type(name).__name__}"
                )


class TestConfigDashboard:
    """Tests for the /config live dashboard rendering via SessionConfigurator."""

    @pytest.mark.asyncio
    async def test_config_no_args_renders_dashboard(self):
        """/config show calls all 6 list methods on the configurator (dashboard rendering)."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)

        await cp._get_config_display("show")

        mock_configurator.context_list.assert_called_once()
        mock_configurator.tools_list.assert_called_once()
        mock_configurator.hooks_list.assert_called_once()
        mock_configurator.providers_list.assert_called_once()
        mock_configurator.agents_list.assert_called_once()
        mock_configurator.behaviors_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_no_args_shows_help(self):
        """/config with no args should show help text, not the dashboard."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)

        mock_console = MagicMock()
        with patch("amplifier_app_cli.console.console", mock_console):
            await cp._get_config_display()

        all_output = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "show" in all_output.lower(), "help should mention /config show"
        assert "diff" in all_output.lower(), "help should mention diff subcommand"
        assert "save" in all_output.lower(), "help should mention save subcommand"
        assert "Categories" in all_output, "help should list available categories"

        # Help should NOT trigger the list methods (those belong to the dashboard)
        mock_configurator.context_list.assert_not_called()
        mock_configurator.tools_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_show_renders_dashboard(self):
        """/config show renders the full dashboard (all 6 list methods are called)."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)

        await cp._get_config_display("show")

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
    async def test_config_hooks_disable_not_supported(self):
        """hooks disable prints a 'not supported' warning — does NOT call hook_disable."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("hooks disable hooks-mode")
        # Output goes via console.print (yellow warning), so return value is empty
        assert result == ""
        # Confirm the configurator was never asked to do anything
        mock_configurator.hook_disable.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_hooks_enable_not_supported(self):
        """hooks enable prints a 'not supported' warning — does NOT call hook_enable."""
        mock_configurator = _make_mock_configurator()
        cp = _make_command_processor(configurator=mock_configurator)
        result = await cp._get_config_display("hooks enable hooks-mode")
        # Output goes via console.print (yellow warning), so return value is empty
        assert result == ""
        # Confirm the configurator was never asked to do anything
        mock_configurator.hook_enable.assert_not_called()

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


class TestConfigHelpEntry:
    """Tests that the /config COMMANDS dict entry has the expected description."""

    def test_config_help_description(self):
        """/config command description reflects subcommand syntax."""
        cp = _make_command_processor()
        description = cp.COMMANDS["/config"]["description"]
        assert (
            description
            == "Live session config \u2014 /config [category] [disable|enable name]"
        )


class TestRenderSimpleSectionWithConfig:
    """Tests for config summary inline display in _render_simple_section."""

    def _item_call_containing(self, mock_console, text):
        """Return the string of the first console.print call containing `text`, or None."""
        for call in mock_console.print.call_args_list:
            args, _ = call
            if args and text in str(args[0]):
                return str(args[0])
        return None

    def test_show_config_true_renders_config_inline(self):
        """When show_config=True, config key/value appears inline on the item line."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "bash",
                "enabled": True,
                "config": {"timeout": 30},
                "source": "foundation",
            }
        ]
        cp._render_simple_section(mock_console, "Tools", items, show_config=True)
        bash_call = self._item_call_containing(mock_console, "bash")
        assert bash_call is not None
        assert "timeout" in bash_call, (
            "config key 'timeout' should appear inline on the item line"
        )
        assert "30" in bash_call, "config value '30' should appear inline"

    def test_show_config_false_does_not_render_config(self):
        """When show_config=False (default), config is not shown."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "bash",
                "enabled": True,
                "config": {"timeout": 30},
                "source": "foundation",
            }
        ]
        cp._render_simple_section(mock_console, "Tools", items, show_config=False)
        bash_call = self._item_call_containing(mock_console, "bash")
        assert bash_call is not None
        assert "timeout" not in bash_call, (
            "config should not appear when show_config=False"
        )

    def test_config_truncated_after_three_keys(self):
        """When config has >3 keys, only first 3 shown with '...' marker."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "mytool",
                "enabled": True,
                "config": {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5},
                "source": "foundation",
            }
        ]
        cp._render_simple_section(mock_console, "Tools", items, show_config=True)
        all_calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "..." in all_calls, "ellipsis should appear when config has >3 keys"

    def test_config_sensitive_key_redacted_inline(self):
        """Sensitive config keys (api_key) are redacted when shown inline."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "anthropic",
                "enabled": True,
                "config": {"api_key": "sk-ant-1234567890abcdef1234567890"},
                "source": "foundation",
            }
        ]
        cp._render_simple_section(mock_console, "Providers", items, show_config=True)
        all_calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "redacted" in all_calls, "sensitive api_key should be redacted"
        assert "sk-ant-1234567890abcdef1234567890" not in all_calls, (
            "raw key must not appear"
        )

    def test_empty_config_not_shown(self):
        """Items with empty config dict show no config summary."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {"name": "bash", "enabled": True, "config": {}, "source": "foundation"}
        ]
        cp._render_simple_section(mock_console, "Tools", items, show_config=True)
        bash_call = self._item_call_containing(mock_console, "bash")
        assert bash_call is not None
        assert "{" not in bash_call, "empty config should not produce a {} block"

    def test_three_keys_no_truncation(self):
        """Exactly 3 config keys show all without ellipsis."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "mytool",
                "enabled": True,
                "config": {"a": 1, "b": 2, "c": 3},
                "source": "foundation",
            }
        ]
        cp._render_simple_section(mock_console, "Tools", items, show_config=True)
        all_calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "..." not in all_calls, "no ellipsis for exactly 3 keys"


class TestSectionHeaderFormat:
    """Tests that section headers use the design spec '── title ──' format."""

    def test_simple_section_header_uses_em_dash_format(self):
        """_render_simple_section header should use '──' dividers, not '[bold]Title:[/bold]'."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [{"name": "bash", "enabled": True, "source": "foundation"}]
        cp._render_simple_section(mock_console, "Tools", items)
        first_call = str(mock_console.print.call_args_list[0])
        assert "──" in first_call, "section header should contain em-dash '──' dividers"
        assert "[bold]" not in first_call, "old bold format should not appear in header"

    def test_hooks_section_header_uses_em_dash_format(self):
        """_render_hooks_section_v2 header should use '──' dividers."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {"name": "routing-resolve", "event": "provider:request", "enabled": True}
        ]
        cp._render_hooks_section_v2(mock_console, items)
        first_call = str(mock_console.print.call_args_list[0])
        assert "──" in first_call, "hooks section header should contain '──' dividers"
        assert "[bold]" not in first_call, "old bold format should not appear"

    def test_behaviors_section_header_uses_em_dash_format(self):
        """_render_behaviors_section_v2 header should use '──' dividers."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [{"name": "foundation", "enabled": True, "contributions": {}}]
        cp._render_behaviors_section_v2(mock_console, items)
        first_call = str(mock_console.print.call_args_list[0])
        assert "──" in first_call, (
            "behaviors section header should contain '──' dividers"
        )
        assert "[bold]" not in first_call, "old bold format should not appear"

    def test_simple_section_uses_active_terminology(self):
        """_render_simple_section header should use 'active' count, not 'on' count."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {"name": "bash", "enabled": True, "source": "foundation"},
            {"name": "other", "enabled": False, "source": "foundation"},
        ]
        cp._render_simple_section(mock_console, "Tools", items)
        first_call = str(mock_console.print.call_args_list[0])
        assert "active" in first_call, "header should use 'active' count terminology"
        assert "disabled" in first_call, "disabled items should say 'disabled'"
        assert " on" not in first_call, "old 'N on' format should not appear"


class TestNewToolsRendering:
    """Tests for the new _render_tools_section method."""

    def _find_call_containing(self, mock_console, text):
        """Return the string of the first console.print call containing text, or None."""
        for call in mock_console.print.call_args_list:
            args, _ = call
            if args and text in str(args[0]):
                return str(args[0])
        return None

    def _all_calls_str(self, mock_console):
        return " ".join(str(c) for c in mock_console.print.call_args_list)

    def test_tools_show_module_on_indented_line(self):
        """module_id appears on a separate indented line below the tool name."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "tool-bash",
                "enabled": True,
                "module_id": "amplifier_core.tools.bash",
                "behaviors": ["foundation"],
                "config": {},
            }
        ]
        cp._render_tools_section(mock_console, items)
        name_call = self._find_call_containing(mock_console, "tool-bash")
        assert name_call is not None, "tool-bash should appear in output"
        module_call = self._find_call_containing(
            mock_console, "amplifier_core.tools.bash"
        )
        assert module_call is not None, "module_id should appear in output"
        assert name_call != module_call, (
            "module_id should be on a different line from the name"
        )

    def test_tools_show_config_tree_indented(self):
        """Config key-value pairs appear as an indented tree below the item."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "tool-bash",
                "enabled": True,
                "module_id": "amplifier_core.tools.bash",
                "behaviors": ["foundation"],
                "config": {"timeout": 30, "max_output": 1000},
            }
        ]
        cp._render_tools_section(mock_console, items)
        all_calls = self._all_calls_str(mock_console)
        assert "config:" in all_calls, "config: label should appear"
        assert "timeout" in all_calls, "config key 'timeout' should appear"
        assert "30" in all_calls, "config value '30' should appear"

    def test_tools_show_multi_claimant_behavior(self):
        """Multiple behaviors appear comma-separated on the module line (not the name line).

        Attribution moves to the module line to avoid line-wrapping confusion
        when there are many behaviors in the list.
        """
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "tool-bash",
                "enabled": True,
                "module_id": "amplifier_core.tools.bash",
                "behaviors": ["foundation", "caveman"],
                "config": {},
            }
        ]
        cp._render_tools_section(mock_console, items)
        name_call = self._find_call_containing(mock_console, "tool-bash")
        assert name_call is not None, "tool-bash should appear in output"
        # Behaviors appear on the MODULE line (with module_id), not the name line
        module_call = self._find_call_containing(mock_console, "amplifier_core.tools.bash")
        assert module_call is not None, "module_id should appear in output"
        assert "foundation" in module_call, "first behavior should appear on module line"
        assert "caveman" in module_call, "second behavior should appear on module line"
        assert "," in module_call, "behaviors should be comma-separated on module line"

    def test_tools_green_on_red_off(self):
        """Enabled tools show green [on], disabled tools show red [off] with dim entire line."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "tool-bash",
                "enabled": True,
                "module_id": "amplifier_core.tools.bash",
                "behaviors": ["foundation"],
                "config": {},
            },
            {
                "name": "tool-disabled",
                "enabled": False,
                "module_id": "amplifier_core.tools.disabled",
                "behaviors": ["foundation"],
                "config": {},
            },
        ]
        cp._render_tools_section(mock_console, items)
        enabled_call = self._find_call_containing(mock_console, "tool-bash")
        assert enabled_call is not None, "enabled tool should appear in output"
        assert "green" in enabled_call, "enabled tool should have green markup"

        disabled_call = self._find_call_containing(mock_console, "tool-disabled")
        assert disabled_call is not None, "disabled tool should appear in output"
        assert "dim" in disabled_call, "disabled tool's line should be in dim"
        assert "red" in disabled_call, "disabled tool should have red markup"


class TestNewHooksRendering:
    """Tests for the new _render_hooks_section_v2 method that lists all hooks individually."""

    def _find_call_containing(self, mock_console, text):
        """Return the string of the first console.print call containing text, or None."""
        for call in mock_console.print.call_args_list:
            args, _ = call
            if args and text in str(args[0]):
                return str(args[0])
        return None

    def _all_calls_str(self, mock_console):
        return " ".join(str(c) for c in mock_console.print.call_args_list)

    def test_shell_hooks_listed_individually(self):
        """shell-* hooks are listed individually — 'shell-*' pattern must NOT appear."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {"name": "shell-notification", "event": "tool:pre", "enabled": True},
            {"name": "shell-pre-tool", "event": "tool:pre", "enabled": True},
            {"name": "shell-post-tool", "event": "tool:post", "enabled": True},
        ]
        cp._render_hooks_section_v2(mock_console, items)
        calls = self._all_calls_str(mock_console)
        # Each shell hook should appear individually
        assert "shell-notification" in calls, (
            "shell-notification should be listed individually"
        )
        assert "shell-pre-tool" in calls, "shell-pre-tool should be listed individually"
        assert "shell-post-tool" in calls, (
            "shell-post-tool should be listed individually"
        )
        # The 'shell-*' collapsed pattern must NOT appear
        assert "shell-*" not in calls, "shell-* collapsed group pattern must NOT appear"

    def test_auto_hooks_listed_individually(self):
        """_auto_* hooks are listed individually — '_auto_*' pattern must NOT appear."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "_auto_tool:pre_bf121312-41db",
                "event": "tool:pre",
                "enabled": True,
            },
            {
                "name": "_auto_tool:post_4cccb727-1ace",
                "event": "tool:post",
                "enabled": True,
            },
            {
                "name": "_auto_llm:response_9b9847ed",
                "event": "llm:response",
                "enabled": True,
            },
        ]
        cp._render_hooks_section_v2(mock_console, items)
        calls = self._all_calls_str(mock_console)
        # Each _auto_* hook should appear individually
        assert "_auto_tool:pre_bf121312-41db" in calls, (
            "_auto_ hook should be listed individually"
        )
        assert "_auto_tool:post_4cccb727-1ace" in calls, (
            "_auto_ hook should be listed individually"
        )
        assert "_auto_llm:response_9b9847ed" in calls, (
            "_auto_ hook should be listed individually"
        )
        # The '_auto_*' collapsed pattern must NOT appear
        assert "_auto_*" not in calls, "_auto_* collapsed group pattern must NOT appear"

    def test_hooks_show_event_on_indented_line(self):
        """The hook event appears on a separate indented line (different print call from name)."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {"name": "routing-resolve", "event": "provider:request", "enabled": True},
        ]
        cp._render_hooks_section_v2(mock_console, items)
        name_call = self._find_call_containing(mock_console, "routing-resolve")
        assert name_call is not None, "routing-resolve should appear in output"
        event_call = self._find_call_containing(mock_console, "provider:request")
        assert event_call is not None, "provider:request event should appear in output"
        # event must be on a DIFFERENT print call than the name
        assert name_call != event_call, (
            "event should be on a separate indented line from the name"
        )
        # The event line should contain 'event:' label
        assert "event:" in event_call, "event line should contain 'event:' label"


class TestNewProvidersRendering:
    """Tests for _render_providers_section_v2 method — full config tree with source URI."""

    def _find_call_containing(self, mock_console, text):
        """Return the string of the first console.print call containing text, or None."""
        for call in mock_console.print.call_args_list:
            args, _ = call
            if args and text in str(args[0]):
                return str(args[0])
        return None

    def _all_calls_str(self, mock_console):
        return " ".join(str(c) for c in mock_console.print.call_args_list)

    def test_providers_show_source_uri(self):
        """Source URI is shown on a separate indented line from the provider name."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "anthropic",
                "enabled": True,
                "source": "foundation",
                "source_uri": "git+https://github.com/example/amplifier-foundation.git",
                "config": {
                    "model": "claude-3-5-sonnet",
                },
            }
        ]
        cp._render_providers_section_v2(mock_console, items)
        name_call = self._find_call_containing(mock_console, "anthropic")
        assert name_call is not None, "anthropic should appear in output"
        source_uri_call = self._find_call_containing(mock_console, "git+https://")
        assert source_uri_call is not None, "source URI should appear in output"
        # Source URI must be on a DIFFERENT print call than the name
        assert name_call != source_uri_call, (
            "source URI should be on a separate indented line from the name"
        )
        # The source line should contain 'source:' label
        assert "source:" in source_uri_call, (
            "source line should contain 'source:' label"
        )

    def test_providers_show_config_tree(self):
        """Config keys model and max_tokens are visible in the config tree."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "anthropic",
                "enabled": True,
                "source": "foundation",
                "source_uri": "git+https://github.com/example/amplifier-foundation.git",
                "config": {
                    "model": "claude-3-5-sonnet",
                    "max_tokens": 16384,
                    "api_key": "sk-ant-1234567890abcdef1234567890",
                },
            }
        ]
        cp._render_providers_section_v2(mock_console, items)
        calls = self._all_calls_str(mock_console)
        # model and max_tokens should be visible
        assert "model" in calls, "model config key should be visible"
        assert "claude-3-5-sonnet" in calls, "model value should be visible"
        assert "max_tokens" in calls, "max_tokens config key should be visible"
        assert "16384" in calls, "max_tokens value should be visible"
        # 'config:' header should be shown
        assert "config:" in calls, "config: header should be present in output"

    def test_providers_redact_api_key(self):
        """API key is redacted (original value not in output, 'redacted' is)."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        api_key = "sk-ant-1234567890abcdef1234567890"
        items = [
            {
                "name": "anthropic",
                "enabled": True,
                "source": "foundation",
                "source_uri": "git+https://github.com/example/amplifier-foundation.git",
                "config": {
                    "api_key": api_key,
                    "model": "claude-3-5-sonnet",
                },
            }
        ]
        cp._render_providers_section_v2(mock_console, items)
        calls = self._all_calls_str(mock_console)
        # Original api_key value should NOT be in output
        assert api_key not in calls, (
            "original API key value should not appear in output"
        )
        # 'redacted' should appear instead
        assert "redacted" in calls, "'redacted' should appear in output for API key"


class TestNewBehaviorsRendering:
    """Tests for _render_behaviors_section_v2 — non-zero categories with item names."""

    def _all_calls_str(self, mock_console):
        return " ".join(str(c) for c in mock_console.print.call_args_list)

    def test_zero_categories_omitted(self):
        """Categories with empty item lists are not shown in the output."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "foundation",
                "enabled": True,
                "contributions": {
                    "context": ["context:readme"],
                    "tools": [],
                    "hooks": [],
                    "providers": [],
                    "agents": [],
                },
            }
        ]
        cp._render_behaviors_section_v2(mock_console, items)
        calls = self._all_calls_str(mock_console)
        # context has items so should appear
        assert "context" in calls, "non-empty context category should be shown"
        # tools and hooks have empty lists so must NOT appear as category labels
        assert "tools" not in calls, "empty tools category should be omitted"
        assert "hooks" not in calls, "empty hooks category should be omitted"

    def test_item_names_listed(self):
        """Actual item names are shown with provenance key prefix stripped."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "foundation",
                "enabled": True,
                "contributions": {
                    "context": ["context:readme", "context:guide"],
                    "tools": ["tools:tool-bash"],
                    "hooks": [],
                    "providers": [],
                    "agents": [],
                },
            }
        ]
        cp._render_behaviors_section_v2(mock_console, items)
        calls = self._all_calls_str(mock_console)
        # Prefix stripped: "context:readme" -> "readme"
        assert "readme" in calls, "readme should appear with prefix stripped"
        assert "guide" in calls, "guide should appear with prefix stripped"
        assert "tool-bash" in calls, "tool-bash should appear with prefix stripped"
        # The raw prefixed form should NOT appear
        assert "context:readme" not in calls, (
            "provenance prefix should be stripped from display"
        )


class TestNewContextAgentsRendering:
    """Tests for the new _render_context_section and _render_agents_section methods."""

    def _find_call_containing(self, mock_console, text):
        """Return the string of the first console.print call containing text, or None."""
        for call in mock_console.print.call_args_list:
            args, _ = call
            if args and text in str(args[0]):
                return str(args[0])
        return None

    def test_context_shows_behavior_dim(self):
        """Context item name and its dim behavior attribution are shown on one line."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "readme",
                "enabled": True,
                "behaviors": ["foundation"],
            }
        ]
        cp._render_context_section(mock_console, items)
        name_call = self._find_call_containing(mock_console, "readme")
        assert name_call is not None, "context item 'readme' should appear in output"
        assert "foundation" in name_call, (
            "behavior 'foundation' should appear on same line as name"
        )
        assert "[dim]" in name_call, "behavior attribution should be wrapped in [dim]"

    def test_agents_shows_multi_claimant(self):
        """Agent item with multiple behaviors shows both comma-separated on one line."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "foundation:explorer",
                "enabled": True,
                "behaviors": ["foundation", "superpowers"],
            }
        ]
        cp._render_agents_section(mock_console, items)
        name_call = self._find_call_containing(mock_console, "foundation:explorer")
        assert name_call is not None, (
            "agent 'foundation:explorer' should appear in output"
        )
        assert "foundation" in name_call, (
            "'foundation' behavior should be visible on same line"
        )
        assert "superpowers" in name_call, (
            "'superpowers' behavior should be visible on same line"
        )
        assert "," in name_call, "multiple behaviors should be comma-separated"

    def test_disabled_items_are_dimmed(self):
        """Disabled items have [red][off] and the entire line is [dim]."""
        cp = _make_command_processor()
        mock_console = MagicMock()
        items = [
            {
                "name": "disabled-ctx",
                "enabled": False,
                "behaviors": ["foundation"],
            }
        ]
        cp._render_context_section(mock_console, items)
        disabled_call = self._find_call_containing(mock_console, "disabled-ctx")
        assert disabled_call is not None, "disabled item should appear in output"
        assert "red" in disabled_call, "disabled item should have red markup for [off]"
        assert "dim" in disabled_call, "disabled item's entire line should be dimmed"


class TestRealConfiguratorRenderIntegration:
    """Integration tests verifying real SessionConfigurator output flows correctly
    through the renderer methods.

    These tests use a real SessionConfigurator instance (not a MagicMock) to detect
    key-name mismatches between the configurator's dict contract and the renderer's
    expectations. A mock configurator would mask such mismatches.

    The real SessionConfigurator is imported from the caveman-test amplifier-foundation
    package (../amplifier-foundation relative to this repo), which is the local
    development version containing the configurator module.
    """

    @staticmethod
    def _get_session_configurator_class():
        """Import SessionConfigurator from the caveman-test amplifier-foundation package.

        Adds the local amplifier-foundation source tree to sys.path so the configurator
        module is importable even when the system-installed amplifier-foundation
        (from resolve-platform) does not contain the configurator subpackage.

        Returns None if the import fails, allowing tests to be skipped gracefully.
        """
        import sys
        from pathlib import Path

        # caveman-test/amplifier-foundation lives one level up from caveman-test/amplifier-app-cli
        candidate = Path(__file__).parent.parent.parent / "amplifier-foundation"
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

        try:
            from amplifier_foundation.configurator import SessionConfigurator  # noqa: PLC0415
            return SessionConfigurator
        except ImportError:
            return None

    def _make_real_configurator(self):
        """Create a real SessionConfigurator with minimal mocked dependencies.

        Returns a SessionConfigurator whose tools_list() produces one enabled tool
        'bash' with behaviors=['foundation'] and module_id='tool-bash'.
        """
        from unittest.mock import MagicMock
        from amplifier_foundation.bundle import Bundle

        SessionConfigurator = self._get_session_configurator_class()
        if SessionConfigurator is None:
            return None

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.get = MagicMock(side_effect=lambda mp: {"bash": MagicMock()} if mp == "tools" else {})
        coordinator.config = {
            "tools": [{"module": "tool-bash"}],
            "agents": {},
        }

        bundle = Bundle(
            name="test-behavior",
            context={},
            tools=[{"module": "tool-bash"}],
            hooks=[],
            providers=[],
        )
        bundle._provenance = {"tool:tool-bash": ["foundation"]}  # type: ignore[misc]

        prepared = MagicMock()
        prepared.bundle = bundle

        session = MagicMock()
        session.coordinator = coordinator

        return SessionConfigurator(session=session, prepared_bundle=prepared)

    def _find_call_containing(self, mock_console, text):
        """Return first print() call whose argument contains the text, or None."""
        for call in mock_console.print.call_args_list:
            args, _ = call
            if args and text in str(args[0]):
                return str(args[0])
        return None

    def test_real_tools_list_behavior_renders_as_string(self):
        """Real tools_list() output: behavior attribution renders as 'foundation', not \"['foundation']\".

        Behavior attribution appears on the MODULE line (not the name line), to avoid
        wrapping confusion when many behaviors are attributed.  The module line format is:
          module: tool-bash  (foundation)
        The check verifies that 'foundation' renders as a plain string, not as \"['foundation']\".
        """
        cfg = self._make_real_configurator()
        if cfg is None:
            import pytest
            pytest.skip("amplifier_foundation.configurator not available in this environment")

        items = cfg.tools_list()
        assert len(items) == 1, "Expected one tool item"

        cp = _make_command_processor()
        mock_console = MagicMock()
        cp._render_tools_section(mock_console, items)

        # Attribution is on the MODULE line (contains "module: tool-bash")
        module_call = self._find_call_containing(mock_console, "module: tool-bash")
        assert module_call is not None, "'module: tool-bash' should appear in rendered output"

        assert "['foundation']" not in module_call, (
            "Behavior must render as string 'foundation', not as Python list repr \"['foundation']\". "
            "This indicates a key mismatch: configurator returns 'behavior' but renderer reads 'behaviors'."
        )
        assert "foundation" in module_call, (
            "Behavior 'foundation' must appear on the module line as a plain string."
        )

    def test_real_tools_list_module_id_renders(self):
        """Real tools_list() output: module: field renders on a separate indented line.

        When the configurator returns 'module: \"tool-bash\"' but the renderer reads
        'module_id', the module line is never rendered (empty string → skipped).
        """
        cfg = self._make_real_configurator()
        if cfg is None:
            import pytest
            pytest.skip("amplifier_foundation.configurator not available in this environment")

        items = cfg.tools_list()

        cp = _make_command_processor()
        mock_console = MagicMock()
        cp._render_tools_section(mock_console, items)

        # The module line contains "module: tool-bash"
        module_line = self._find_call_containing(mock_console, "module:")
        assert module_line is not None, (
            "Module line ('module: tool-bash') must appear in rendered output. "
            "This indicates a key mismatch: configurator returns 'module' but renderer reads 'module_id'."
        )
        assert "tool-bash" in module_line, (
            "Module value 'tool-bash' must appear on the module: line."
        )
