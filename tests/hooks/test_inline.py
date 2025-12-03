"""Tests for inline matcher hooks."""

import pytest
from amplifier_app_cli.hooks.inline import (
    InlineRule,
    InlineMatcher,
    InlineHookExecutor,
)
from amplifier_app_cli.hooks.models import HookConfig, HookMatcher, HookType


class TestInlineRule:
    """Tests for InlineRule."""

    def test_from_dict_basic(self):
        """Test creating InlineRule from dict."""
        rule_dict = {
            "field": "args.command",
            "operator": "contains",
            "value": "rm",
            "action": "deny",
            "reason": "Dangerous command",
        }
        
        rule = InlineRule.from_dict(rule_dict)
        assert rule.field == "args.command"
        assert rule.operator == "contains"
        assert rule.value == "rm"
        assert rule.action == "deny"
        assert rule.reason == "Dangerous command"

    def test_from_dict_defaults(self):
        """Test InlineRule from dict with defaults."""
        rule_dict = {
            "field": "tool",
            "value": "bash",
        }
        
        rule = InlineRule.from_dict(rule_dict)
        assert rule.operator == "equals"
        assert rule.action == "continue"


class TestInlineMatcher:
    """Tests for InlineMatcher."""

    def test_equals_operator(self):
        """Test equals operator."""
        rule = InlineRule(
            field="tool",
            operator="equals",
            value="bash",
            action="continue",
        )
        
        assert InlineMatcher.matches(rule, {"tool": "bash"})
        assert not InlineMatcher.matches(rule, {"tool": "python"})

    def test_contains_operator(self):
        """Test contains operator."""
        rule = InlineRule(
            field="args.command",
            operator="contains",
            value="rm -rf",
            action="deny",
        )
        
        assert InlineMatcher.matches(rule, {"args": {"command": "rm -rf /tmp"}})
        assert not InlineMatcher.matches(rule, {"args": {"command": "ls -la"}})

    def test_glob_operator(self):
        """Test glob operator."""
        rule = InlineRule(
            field="args.path",
            operator="glob",
            value="*.py",
            action="continue",
        )
        
        assert InlineMatcher.matches(rule, {"args": {"path": "test.py"}})
        assert not InlineMatcher.matches(rule, {"args": {"path": "test.txt"}})

    def test_regex_operator(self):
        """Test regex operator."""
        rule = InlineRule(
            field="args.command",
            operator="regex",
            value=r"^rm\s+-rf",
            action="deny",
        )
        
        assert InlineMatcher.matches(rule, {"args": {"command": "rm -rf /tmp"}})
        assert not InlineMatcher.matches(rule, {"args": {"command": "ls rm -rf"}})

    def test_get_field_value_nested(self):
        """Test accessing nested fields."""
        data = {
            "tool": "bash",
            "args": {
                "command": "rm file.txt",
                "cwd": "/tmp",
            },
        }
        
        assert InlineMatcher._get_field_value("tool", data) == "bash"
        assert InlineMatcher._get_field_value("args.command", data) == "rm file.txt"
        assert InlineMatcher._get_field_value("args.cwd", data) == "/tmp"
        assert InlineMatcher._get_field_value("missing.field", data) is None

    def test_get_field_value_missing(self):
        """Test accessing missing field returns None."""
        data = {"tool": "bash"}
        assert InlineMatcher._get_field_value("args.command", data) is None


@pytest.mark.asyncio
class TestInlineHookExecutor:
    """Tests for InlineHookExecutor."""

    async def test_deny_action(self):
        """Test inline hook denying action."""
        config = HookConfig(
            name="test-deny",
            type=HookType.INLINE,
            matcher=HookMatcher(),
            inline_rules=[
                {
                    "field": "args.command",
                    "operator": "contains",
                    "value": "rm -rf",
                    "action": "deny",
                    "reason": "Dangerous command blocked",
                }
            ],
        )
        
        executor = InlineHookExecutor(config)
        result = await executor(
            "PreToolUse",
            {"tool": "bash", "args": {"command": "rm -rf /tmp"}}
        )
        
        assert result.action == "deny"
        assert "Dangerous command blocked" in result.reason

    async def test_continue_action(self):
        """Test inline hook continuing action."""
        config = HookConfig(
            name="test-continue",
            type=HookType.INLINE,
            matcher=HookMatcher(),
            inline_rules=[
                {
                    "field": "tool",
                    "operator": "equals",
                    "value": "bash",
                    "action": "continue",
                    "reason": "Bash allowed",
                }
            ],
        )
        
        executor = InlineHookExecutor(config)
        result = await executor(
            "PreToolUse",
            {"tool": "bash", "args": {"command": "ls"}}
        )
        
        assert result.action == "continue"
        assert "Bash allowed" in result.reason

    async def test_modify_action(self):
        """Test inline hook modifying data."""
        config = HookConfig(
            name="test-modify",
            type=HookType.INLINE,
            matcher=HookMatcher(),
            inline_rules=[
                {
                    "field": "args.unsafe",
                    "operator": "equals",
                    "value": "true",
                    "action": "modify",
                    "reason": "Made safe",
                    "modify_field": "args.unsafe",
                    "modify_value": "false",
                }
            ],
        )
        
        executor = InlineHookExecutor(config)
        result = await executor(
            "PreToolUse",
            {"tool": "test", "args": {"unsafe": "true"}}
        )
        
        assert result.action == "modify"
        assert result.modified_data["args"]["unsafe"] == "false"

    async def test_no_match(self):
        """Test inline hook with no matching rules."""
        config = HookConfig(
            name="test-nomatch",
            type=HookType.INLINE,
            matcher=HookMatcher(),
            inline_rules=[
                {
                    "field": "tool",
                    "operator": "equals",
                    "value": "python",
                    "action": "deny",
                }
            ],
        )
        
        executor = InlineHookExecutor(config)
        result = await executor(
            "PreToolUse",
            {"tool": "bash", "args": {}}
        )
        
        assert result.action == "continue"
        assert result.reason is None

    async def test_multiple_rules_first_match(self):
        """Test inline hook with multiple rules (first match wins)."""
        config = HookConfig(
            name="test-multiple",
            type=HookType.INLINE,
            matcher=HookMatcher(),
            inline_rules=[
                {
                    "field": "args.command",
                    "operator": "contains",
                    "value": "rm",
                    "action": "deny",
                    "reason": "Rule 1 matched",
                },
                {
                    "field": "args.command",
                    "operator": "contains",
                    "value": "file",
                    "action": "continue",
                    "reason": "Rule 2 matched",
                },
            ],
        )
        
        executor = InlineHookExecutor(config)
        
        # First rule should match
        result = await executor(
            "PreToolUse",
            {"tool": "bash", "args": {"command": "rm file.txt"}}
        )
        assert result.action == "deny"
        assert "Rule 1 matched" in result.reason

    async def test_invalid_hook_type_raises(self):
        """Test that non-inline hook type raises error."""
        config = HookConfig(
            name="test-wrong-type",
            type=HookType.COMMAND,
            command="echo test",
        )
        
        with pytest.raises(ValueError, match="Expected inline hook"):
            InlineHookExecutor(config)
