"""Tests for hooks configuration loading."""

import pytest

from amplifier_app_cli.hooks.config import (
    HooksConfig,
    discover_hook_scripts,
    get_default_hooks,
)
from amplifier_app_cli.hooks.models import HookType


class TestHooksConfig:
    """Tests for HooksConfig."""

    def test_from_empty_settings(self):
        """Test loading from empty settings."""
        config = HooksConfig.from_settings({})
        assert config.hooks == []
        assert config.disabled_hooks == []
        assert config.global_timeout == 30.0

    def test_from_settings_with_timeout(self):
        """Test loading global timeout."""
        settings = {"hooks": {"timeout": 60.0}}
        config = HooksConfig.from_settings(settings)
        assert config.global_timeout == 60.0

    def test_from_settings_with_disabled(self):
        """Test loading disabled hooks list."""
        settings = {"hooks": {"disabled": ["hook1", "hook2"]}}
        config = HooksConfig.from_settings(settings)
        assert config.disabled_hooks == ["hook1", "hook2"]

    def test_from_settings_with_definitions(self):
        """Test loading hook definitions."""
        settings = {
            "hooks": {
                "definitions": [
                    {
                        "name": "my-hook",
                        "type": "command",
                        "command": "./test.sh",
                        "matcher": {
                            "events": ["PreToolUse"],
                            "tools": ["write"],
                        },
                        "timeout": 10,
                        "priority": 50,
                    }
                ]
            }
        }
        config = HooksConfig.from_settings(settings)

        assert len(config.hooks) == 1
        hook = config.hooks[0]
        assert hook.name == "my-hook"
        assert hook.type == HookType.COMMAND
        assert hook.command == "./test.sh"
        assert hook.timeout == 10
        assert hook.priority == 50
        assert hook.matcher.events == ["PreToolUse"]
        assert hook.matcher.tools == ["write"]

    def test_disabled_hooks_not_loaded(self):
        """Test that disabled hooks are filtered out."""
        settings = {
            "hooks": {
                "disabled": ["hook-to-disable"],
                "definitions": [
                    {
                        "name": "hook-to-disable",
                        "type": "command",
                        "command": "echo disabled",
                    },
                    {
                        "name": "active-hook",
                        "type": "command",
                        "command": "echo active",
                    },
                ],
            }
        }
        config = HooksConfig.from_settings(settings)

        assert len(config.hooks) == 1
        assert config.hooks[0].name == "active-hook"

    def test_invalid_hook_skipped(self):
        """Test that invalid hooks are skipped with warning."""
        settings = {
            "hooks": {
                "definitions": [
                    {"name": "valid", "type": "command", "command": "echo ok"},
                    {"type": "command"},  # Missing name
                    {"name": "no-type"},  # Missing type info
                ]
            }
        }
        config = HooksConfig.from_settings(settings)

        # Only valid hook should be loaded
        # (the third one might fail validation for command type)
        assert any(h.name == "valid" for h in config.hooks)

    def test_get_hooks_for_event(self):
        """Test filtering hooks by event."""
        settings = {
            "hooks": {
                "definitions": [
                    {
                        "name": "pre-hook",
                        "type": "command",
                        "command": "echo pre",
                        "matcher": {"events": ["PreToolUse"]},
                        "priority": 10,
                    },
                    {
                        "name": "post-hook",
                        "type": "command",
                        "command": "echo post",
                        "matcher": {"events": ["PostToolUse"]},
                        "priority": 20,
                    },
                ]
            }
        }
        config = HooksConfig.from_settings(settings)

        pre_hooks = config.get_hooks_for_event("PreToolUse")
        assert len(pre_hooks) == 1
        assert pre_hooks[0].name == "pre-hook"

        post_hooks = config.get_hooks_for_event("PostToolUse")
        assert len(post_hooks) == 1
        assert post_hooks[0].name == "post-hook"

    def test_hooks_sorted_by_priority(self):
        """Test that hooks are sorted by priority."""
        settings = {
            "hooks": {
                "definitions": [
                    {
                        "name": "low-priority",
                        "type": "command",
                        "command": "echo low",
                        "priority": 100,
                    },
                    {
                        "name": "high-priority",
                        "type": "command",
                        "command": "echo high",
                        "priority": 10,
                    },
                ]
            }
        }
        config = HooksConfig.from_settings(settings)

        hooks = config.get_hooks_for_event("any")
        assert hooks[0].name == "high-priority"
        assert hooks[1].name == "low-priority"


class TestDefaultHooks:
    """Tests for default hooks."""

    def test_default_hooks_exist(self):
        """Test that default hooks are defined."""
        hooks = get_default_hooks()
        assert len(hooks) > 0

    def test_default_hooks_have_names(self):
        """Test that all default hooks have names."""
        hooks = get_default_hooks()
        for hook in hooks:
            assert hook.name
            assert hook.type == HookType.INTERNAL
