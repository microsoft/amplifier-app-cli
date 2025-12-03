"""Tests for hooks models."""

import pytest

from amplifier_app_cli.hooks.models import (
    HookConfig,
    HookMatcher,
    HookResult,
    HookType,
)


class TestHookType:
    """Tests for HookType enum."""

    def test_values(self):
        """Test hook type values."""
        assert HookType.INTERNAL.value == "internal"
        assert HookType.COMMAND.value == "command"
        assert HookType.LLM.value == "llm"


class TestHookMatcher:
    """Tests for HookMatcher."""

    def test_empty_matcher_matches_all(self):
        """Test that empty matcher matches everything."""
        matcher = HookMatcher()
        assert matcher.matches("any:event", {"tool": "anything"})

    def test_event_filter(self):
        """Test event filtering."""
        matcher = HookMatcher(events=["tool:pre"])

        assert matcher.matches("tool:pre", {})
        assert not matcher.matches("tool:post", {})

    def test_tool_filter(self):
        """Test tool filtering."""
        matcher = HookMatcher(tools=["write", "edit"])

        assert matcher.matches("tool:pre", {"tool": "write"})
        assert matcher.matches("tool:pre", {"tool": "edit"})
        assert not matcher.matches("tool:pre", {"tool": "bash"})

    def test_path_pattern_filter(self):
        """Test path pattern filtering."""
        matcher = HookMatcher(path_patterns=["*.py", "*.md"])

        assert matcher.matches("tool:pre", {"path": "test.py"})
        assert matcher.matches("tool:pre", {"path": "README.md"})
        assert not matcher.matches("tool:pre", {"path": "test.js"})
        assert not matcher.matches("tool:pre", {})  # No path

    def test_command_pattern_filter(self):
        """Test command pattern filtering."""
        matcher = HookMatcher(command_patterns=["npm *", "git *"])

        assert matcher.matches("tool:pre", {"command": "npm install"})
        assert matcher.matches("tool:pre", {"command": "git status"})
        assert not matcher.matches("tool:pre", {"command": "rm -rf"})

    def test_session_type_filter(self):
        """Test session type filtering."""
        matcher = HookMatcher(session_types=["root"])

        # Root session (no parent)
        assert matcher.matches("event", {"session_id": "abc"})

        # Subagent session (has parent)
        assert not matcher.matches("event", {"parent_id": "parent123"})

    def test_combined_filters(self):
        """Test multiple filters combined (AND logic)."""
        matcher = HookMatcher(
            events=["tool:pre"],
            tools=["write"],
            path_patterns=["*.py"],
        )

        # All match
        assert matcher.matches("tool:pre", {"tool": "write", "path": "test.py"})

        # Event doesn't match
        assert not matcher.matches("tool:post", {"tool": "write", "path": "test.py"})

        # Tool doesn't match
        assert not matcher.matches("tool:pre", {"tool": "read", "path": "test.py"})

        # Path doesn't match
        assert not matcher.matches("tool:pre", {"tool": "write", "path": "test.js"})

    def test_serialization(self):
        """Test to_dict and from_dict."""
        matcher = HookMatcher(
            events=["tool:pre"],
            tools=["write"],
        )

        d = matcher.to_dict()
        assert d["events"] == ["tool:pre"]
        assert d["tools"] == ["write"]

        restored = HookMatcher.from_dict(d)
        assert restored.events == matcher.events
        assert restored.tools == matcher.tools


class TestHookConfig:
    """Tests for HookConfig."""

    def test_command_hook_requires_command(self):
        """Test that command hooks require command or script."""
        with pytest.raises(ValueError):
            HookConfig(
                name="test",
                type=HookType.COMMAND,
                # No command or script
            )

    def test_llm_hook_requires_prompt(self):
        """Test that LLM hooks require prompt."""
        with pytest.raises(ValueError):
            HookConfig(
                name="test",
                type=HookType.LLM,
                # No prompt
            )

    def test_valid_command_hook(self):
        """Test creating valid command hook."""
        config = HookConfig(
            name="my-hook",
            type=HookType.COMMAND,
            command="./validate.sh",
            priority=50,
        )
        assert config.name == "my-hook"
        assert config.command == "./validate.sh"
        assert config.priority == 50

    def test_valid_script_hook(self):
        """Test creating hook with script path."""
        config = HookConfig(
            name="script-hook",
            type=HookType.COMMAND,
            script="scripts/hook.py",
        )
        assert config.script == "scripts/hook.py"

    def test_serialization(self):
        """Test to_dict and from_dict."""
        config = HookConfig(
            name="test-hook",
            type=HookType.COMMAND,
            command="echo test",
            matcher=HookMatcher(events=["tool:pre"]),
            timeout=10.0,
            priority=25,
        )

        d = config.to_dict()
        assert d["name"] == "test-hook"
        assert d["type"] == "command"
        assert d["command"] == "echo test"
        assert d["timeout"] == 10.0
        assert d["priority"] == 25

        restored = HookConfig.from_dict(d)
        assert restored.name == config.name
        assert restored.type == config.type
        assert restored.command == config.command


class TestHookResult:
    """Tests for HookResult."""

    def test_continue_result(self):
        """Test creating continue result."""
        result = HookResult.continue_("OK")
        assert result.action == "continue"
        assert result.reason == "OK"

    def test_deny_result(self):
        """Test creating deny result."""
        result = HookResult.deny("Not allowed")
        assert result.action == "deny"
        assert result.reason == "Not allowed"

    def test_modify_result(self):
        """Test creating modify result."""
        modified = {"args": {"path": "new_path.txt"}}
        result = HookResult.modify(modified, "Changed path")
        assert result.action == "modify"
        assert result.modified_data == modified
        assert result.reason == "Changed path"

    def test_error_result(self):
        """Test creating error result."""
        result = HookResult.error("Something failed")
        assert result.action == "continue"  # Errors don't block
        assert result.error == "Something failed"
