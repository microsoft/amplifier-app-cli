"""Inline matcher hooks.

Provides hooks with inline pattern matching rules that execute
actions without external commands.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from typing import Any

from .models import HookConfig, HookResult, HookType

logger = logging.getLogger(__name__)


@dataclass
class InlineRule:
    """Pattern matching rule with action.
    
    Attributes:
        field: Field path to match (e.g., "tool", "args.command")
        operator: Comparison operator
        value: Pattern value
        action: Action to take on match
        reason: Explanation for action
        modify_field: Field to modify (for modify action)
        modify_value: New value (for modify action)
    """
    
    field: str
    operator: str
    value: str
    action: str
    reason: str | None = None
    modify_field: str | None = None
    modify_value: Any = None
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InlineRule:
        """Create rule from configuration dictionary."""
        return cls(
            field=data["field"],
            operator=data.get("operator", "equals"),
            value=data["value"],
            action=data.get("action", "continue"),
            reason=data.get("reason"),
            modify_field=data.get("modify_field"),
            modify_value=data.get("modify_value"),
        )


class InlineMatcher:
    """Pattern matching engine for inline rules."""
    
    @staticmethod
    def matches(rule: InlineRule, data: dict[str, Any]) -> bool:
        """Check if rule matches data.
        
        Args:
            rule: Rule to evaluate
            data: Event data
            
        Returns:
            True if rule matches
        """
        # Get field value
        field_value = InlineMatcher._get_field_value(rule.field, data)
        if field_value is None:
            return False
        
        # Convert to string for comparison
        field_str = str(field_value)
        pattern = rule.value
        
        # Apply operator
        if rule.operator == "equals":
            return field_str == pattern
        
        elif rule.operator == "contains":
            return pattern in field_str
        
        elif rule.operator == "glob":
            return fnmatch.fnmatch(field_str, pattern)
        
        elif rule.operator == "matches" or rule.operator == "regex":
            try:
                return bool(re.search(pattern, field_str))
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")
                return False
        
        else:
            logger.warning(f"Unknown operator: {rule.operator}")
            return False
    
    @staticmethod
    def _get_field_value(field: str, data: dict[str, Any]) -> Any:
        """Get nested field value from data.
        
        Supports dot notation: "args.command", "result.status"
        
        Args:
            field: Field path
            data: Data dictionary
            
        Returns:
            Field value or None if not found
        """
        parts = field.split(".")
        current = data
        
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return None
            else:
                return None
        
        return current


class InlineHookExecutor:
    """Execute inline matcher hooks.
    
    Evaluates rules in order and returns action from first match.
    """
    
    def __init__(self, config: HookConfig):
        """Initialize inline hook executor.
        
        Args:
            config: Hook configuration with inline_rules
        """
        if config.type != HookType.INLINE:
            raise ValueError(f"Expected inline hook, got {config.type}")
        
        self.config = config
        self.rules: list[InlineRule] = []
        self._parse_rules()
    
    def _parse_rules(self):
        """Parse rules from configuration."""
        for rule_dict in self.config.inline_rules:
            try:
                rule = InlineRule.from_dict(rule_dict)
                self.rules.append(rule)
            except (KeyError, ValueError) as e:
                logger.warning(f"Invalid inline rule in {self.config.name}: {e}")
    
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Execute inline rules.
        
        Args:
            event: Event name
            data: Event data
            
        Returns:
            HookResult with action from first matching rule
        """
        # Evaluate rules in order
        for rule in self.rules:
            if InlineMatcher.matches(rule, data):
                logger.debug(
                    f"Inline hook {self.config.name}: rule matched "
                    f"({rule.field} {rule.operator} {rule.value})"
                )
                
                # Return appropriate result based on action
                if rule.action == "deny":
                    return HookResult.deny(
                        rule.reason or f"Denied by rule: {rule.field} {rule.operator} {rule.value}"
                    )
                
                elif rule.action == "modify":
                    if rule.modify_field and rule.modify_value is not None:
                        modified_data = data.copy()
                        self._set_field_value(rule.modify_field, modified_data, rule.modify_value)
                        return HookResult.modify(
                            modified_data,
                            rule.reason or f"Modified by rule: {rule.field}"
                        )
                    else:
                        logger.warning(
                            f"Modify action in {self.config.name} missing "
                            f"modify_field or modify_value"
                        )
                        return HookResult.continue_(rule.reason)
                
                else:  # continue
                    return HookResult.continue_(rule.reason)
        
        # No rules matched
        return HookResult.continue_()
    
    @staticmethod
    def _set_field_value(field: str, data: dict[str, Any], value: Any):
        """Set nested field value in data.
        
        Supports dot notation: "args.command"
        
        Args:
            field: Field path
            data: Data dictionary (modified in place)
            value: Value to set
        """
        parts = field.split(".")
        current = data
        
        # Navigate to parent
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        
        # Set final value
        current[parts[-1]] = value


def create_inline_hook(config: HookConfig) -> InlineHookExecutor:
    """Factory function to create an inline hook.
    
    Args:
        config: Hook configuration
        
    Returns:
        Configured InlineHookExecutor
    """
    return InlineHookExecutor(config)
