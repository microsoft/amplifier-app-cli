"""Standalone tests for hook models using importlib to avoid __init__.py."""

import pytest
import sys
import importlib.util
from pathlib import Path


def load_module(module_name: str, file_path: Path):
    """Load a module directly from file path without going through package __init__."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Load the hooks modules directly
CLI_ROOT = Path(__file__).parent.parent.parent / "amplifier_app_cli"
models = load_module("hooks_models", CLI_ROOT / "hooks" / "models.py")


class TestHookMatcher:
    """Test HookMatcher class."""
    
    def test_empty_matcher_matches_all(self):
        """Test that empty matcher matches everything."""
        HookMatcher = models.HookMatcher
        
        matcher = HookMatcher()
        # Empty matcher should match any event/data
        assert matcher.matches("pre_tool_use", {"tool": "Write"})
        assert matcher.matches("pre_tool_use", {"tool": "Bash"})
        assert matcher.matches("post_tool_use", {"tool": "Read"})
    
    def test_tool_list_matching(self):
        """Test matching against tool list."""
        HookMatcher = models.HookMatcher
        
        matcher = HookMatcher(tools=["Write", "Read"])
        assert matcher.matches("pre_tool_use", {"tool": "Write"})
        assert matcher.matches("pre_tool_use", {"tool": "Read"})
        assert not matcher.matches("pre_tool_use", {"tool": "Bash"})
    
    def test_event_matching(self):
        """Test matching against event list."""
        HookMatcher = models.HookMatcher
        
        matcher = HookMatcher(events=["pre_tool_use"])
        assert matcher.matches("pre_tool_use", {})
        assert not matcher.matches("post_tool_use", {})
    
    def test_path_pattern_matching(self):
        """Test matching path patterns."""
        HookMatcher = models.HookMatcher
        
        matcher = HookMatcher(path_patterns=["*.py"])
        assert matcher.matches("pre_tool_use", {"path": "main.py"})
        assert not matcher.matches("pre_tool_use", {"path": "main.js"})
    
    def test_command_pattern_matching(self):
        """Test matching command patterns."""
        HookMatcher = models.HookMatcher
        
        matcher = HookMatcher(command_patterns=["rm *", "sudo *"])
        assert matcher.matches("pre_tool_use", {"command": "rm -rf /"})
        assert matcher.matches("pre_tool_use", {"command": "sudo apt install"})
        assert not matcher.matches("pre_tool_use", {"command": "ls -la"})
    
    def test_to_dict_and_from_dict(self):
        """Test serialization round-trip."""
        HookMatcher = models.HookMatcher
        
        original = HookMatcher(tools=["Write"], path_patterns=["*.py"])
        data = original.to_dict()
        restored = HookMatcher.from_dict(data)
        
        assert restored.tools == original.tools
        assert restored.path_patterns == original.path_patterns


class TestHookConfig:
    """Test HookConfig class."""
    
    def test_command_hook_creation(self):
        """Test creating a command hook."""
        HookConfig = models.HookConfig
        HookType = models.HookType
        
        hook = HookConfig(
            name="test-hook",
            type=HookType.COMMAND,
            command="echo hello",
        )
        assert hook.name == "test-hook"
        assert hook.command == "echo hello"
        assert hook.enabled == True  # Default
        assert hook.type == HookType.COMMAND
    
    def test_hook_with_matcher(self):
        """Test hook with matcher."""
        HookConfig = models.HookConfig
        HookMatcher = models.HookMatcher
        HookType = models.HookType
        
        hook = HookConfig(
            name="py-only",
            type=HookType.COMMAND,
            command="python lint.py",
            matcher=HookMatcher(path_patterns=["*.py"]),
        )
        assert hook.matcher is not None
        assert hook.matcher.matches("pre_tool_use", {"path": "main.py"})
    
    def test_hook_disabled(self):
        """Test disabled hook."""
        HookConfig = models.HookConfig
        HookType = models.HookType
        
        hook = HookConfig(
            name="disabled-hook",
            type=HookType.COMMAND,
            command="echo disabled",
            enabled=False,
        )
        assert hook.enabled == False
    
    def test_hook_priority(self):
        """Test hook priority."""
        HookConfig = models.HookConfig
        HookType = models.HookType
        
        hook = HookConfig(
            name="high-priority",
            type=HookType.COMMAND,
            command="echo first",
            priority=50,
        )
        assert hook.priority == 50
    
    def test_command_hook_requires_command_or_script(self):
        """Test that command hook requires command or script."""
        HookConfig = models.HookConfig
        HookType = models.HookType
        
        with pytest.raises(ValueError):
            HookConfig(
                name="invalid",
                type=HookType.COMMAND,
                # Missing command or script
            )
    
    def test_llm_hook_requires_prompt(self):
        """Test that LLM hook requires prompt."""
        HookConfig = models.HookConfig
        HookType = models.HookType
        
        with pytest.raises(ValueError):
            HookConfig(
                name="invalid",
                type=HookType.LLM,
                # Missing prompt
            )
    
    def test_serialization_roundtrip(self):
        """Test to_dict and from_dict."""
        HookConfig = models.HookConfig
        HookType = models.HookType
        
        original = HookConfig(
            name="test",
            type=HookType.COMMAND,
            command="echo test",
            priority=50,
            enabled=False,
        )
        data = original.to_dict()
        restored = HookConfig.from_dict(data)
        
        assert restored.name == original.name
        assert restored.type == original.type
        assert restored.command == original.command
        assert restored.priority == original.priority
        assert restored.enabled == original.enabled


class TestHookResult:
    """Test HookResult class."""
    
    def test_continue_result(self):
        """Test creating a continue result."""
        HookResult = models.HookResult
        
        result = HookResult.continue_("All good")
        assert result.action == "continue"
        assert result.reason == "All good"
    
    def test_deny_result(self):
        """Test creating a deny result."""
        HookResult = models.HookResult
        
        result = HookResult.deny("Operation not allowed")
        assert result.action == "deny"
        assert result.reason == "Operation not allowed"
    
    def test_modify_result(self):
        """Test creating a modify result."""
        HookResult = models.HookResult
        
        result = HookResult.modify({"path": "modified.py"}, "Changed path")
        assert result.action == "modify"
        assert result.modified_data == {"path": "modified.py"}
        assert result.reason == "Changed path"
    
    def test_error_result(self):
        """Test creating an error result."""
        HookResult = models.HookResult
        
        result = HookResult.error("Something went wrong")
        assert result.action == "continue"  # Errors default to continue
        assert result.error == "Something went wrong"


class TestHookType:
    """Test HookType enum."""
    
    def test_type_values(self):
        """Test hook type enum values."""
        HookType = models.HookType
        
        assert HookType.INTERNAL.value == "internal"
        assert HookType.COMMAND.value == "command"
        assert HookType.LLM.value == "llm"
