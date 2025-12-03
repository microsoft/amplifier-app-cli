# Phase 2 Implementation Specifications

This document provides detailed implementation specifications for the modular-builder agent to implement Phase 2 of the Enhanced Hooks System.

---

## Module 1: Event Types Extension

### File: `amplifier_app_cli/hooks/events.py`

**Operation**: MODIFY EXISTING FILE

**Purpose**: Add 4 new event types and their data models

**Contract**:
- **Inputs**: None (module defines constants and classes)
- **Outputs**: Event constants and dataclass definitions
- **Side Effects**: None (pure definitions)

**Existing Code to Preserve**:
- All existing event constants (PRE_TOOL_USE, POST_TOOL_USE, etc.)
- All existing dataclasses (ToolUseEvent, SessionEvent, NotificationEvent)
- All existing imports and functions
- EVENT_DATA_TYPES mapping (extend it)

**Changes Required**:

1. **Add Event Constants** (after line 47, before dataclass definitions):
```python
# Error events
ERROR = "Error"

# Checkpoint events  
CHECKPOINT = "Checkpoint"

# Model switch events
MODEL_SWITCH = "ModelSwitch"

# Memory update events
MEMORY_UPDATE = "MemoryUpdate"
```

2. **Add ErrorEvent Dataclass** (after NotificationEvent):
```python
@dataclass
class ErrorEvent:
    """Event data for error events.
    
    Fired when exceptions or errors occur during operations.
    Allows hooks to log, notify, or take action based on errors.
    
    Attributes:
        error_type: Exception type name (e.g., "ValueError")
        error_message: Error message text
        tool: Tool that caused error (if applicable)
        session_id: Current session ID
        stack_trace: Full stack trace (optional)
        severity: Error severity level
        timestamp: When the error occurred
    """
    
    error_type: str
    error_message: str
    tool: str | None = None
    session_id: str | None = None
    stack_trace: str | None = None
    severity: Literal["warning", "error", "critical"] = "error"
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "tool": self.tool,
            "session_id": self.session_id,
            "stack_trace": self.stack_trace,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
        }
```

3. **Add CheckpointEvent Dataclass**:
```python
@dataclass
class CheckpointEvent:
    """Event data for checkpoint events.
    
    Fired when session state is checkpointed.
    Allows hooks to backup, sync, or validate checkpoints.
    
    Attributes:
        checkpoint_id: Unique checkpoint identifier
        session_id: Session being checkpointed
        checkpoint_type: Why checkpoint was created
        message_count: Number of messages since last checkpoint
        duration_since_last_ms: Time since last checkpoint
        storage_path: Path where checkpoint is stored
        timestamp: When checkpoint was created
    """
    
    checkpoint_id: str
    session_id: str
    checkpoint_type: Literal["auto", "manual", "periodic"] = "auto"
    message_count: int = 0
    duration_since_last_ms: float | None = None
    storage_path: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "checkpoint_type": self.checkpoint_type,
            "message_count": self.message_count,
            "duration_since_last_ms": self.duration_since_last_ms,
            "storage_path": self.storage_path,
            "timestamp": self.timestamp.isoformat(),
        }
```

4. **Add ModelSwitchEvent Dataclass**:
```python
@dataclass
class ModelSwitchEvent:
    """Event data for model switch events.
    
    Fired when the active LLM model changes.
    Allows hooks to log usage, enforce policies, or notify.
    
    Attributes:
        old_model: Previous model name (None if first model)
        new_model: New model name
        reason: Why model was switched
        session_id: Current session ID
        profile: Active profile name
        triggered_by: What triggered the switch
        timestamp: When switch occurred
    """
    
    old_model: str | None
    new_model: str
    reason: str | None = None
    session_id: str | None = None
    profile: str | None = None
    triggered_by: Literal["user", "automatic", "fallback"] = "user"
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "old_model": self.old_model,
            "new_model": self.new_model,
            "reason": self.reason,
            "session_id": self.session_id,
            "profile": self.profile,
            "triggered_by": self.triggered_by,
            "timestamp": self.timestamp.isoformat(),
        }
```

5. **Add MemoryUpdateEvent Dataclass**:
```python
@dataclass
class MemoryUpdateEvent:
    """Event data for memory update events.
    
    Fired when memory files (AGENTS.md, etc.) are modified.
    Allows hooks to sync, backup, or validate memory changes.
    
    Attributes:
        file_path: Path to memory file
        update_type: Type of update operation
        session_id: Session that triggered update
        content_size: Size of file in bytes
        previous_hash: Hash before update (for validation)
        new_hash: Hash after update
        timestamp: When update occurred
    """
    
    file_path: str
    update_type: Literal["created", "modified", "deleted"] = "modified"
    session_id: str | None = None
    content_size: int | None = None
    previous_hash: str | None = None
    new_hash: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "file_path": self.file_path,
            "update_type": self.update_type,
            "session_id": self.session_id,
            "content_size": self.content_size,
            "previous_hash": self.previous_hash,
            "new_hash": self.new_hash,
            "timestamp": self.timestamp.isoformat(),
        }
```

6. **Update EVENT_DATA_TYPES Mapping** (extend existing dict):
```python
EVENT_DATA_TYPES = {
    PRE_TOOL_USE: ToolUseEvent,
    POST_TOOL_USE: ToolUseEvent,
    TOOL_PRE: ToolUseEvent,
    TOOL_POST: ToolUseEvent,
    NOTIFICATION: NotificationEvent,
    SESSION_START: SessionEvent,
    SESSION_END: SessionEvent,
    STOP: SessionEvent,
    SUBAGENT_STOP: SessionEvent,
    # New Phase 2 events
    ERROR: ErrorEvent,
    CHECKPOINT: CheckpointEvent,
    MODEL_SWITCH: ModelSwitchEvent,
    MEMORY_UPDATE: MemoryUpdateEvent,
}
```

**Validation**:
- All dataclasses must have `to_dict()` methods
- All use `datetime` with `default_factory=datetime.now`
- All use type hints with `str | None` syntax
- Must maintain backward compatibility with existing code

---

## Module 2: Model Extensions for Inline Hooks

### File: `amplifier_app_cli/hooks/models.py`

**Operation**: MODIFY EXISTING FILE

**Purpose**: Add support for inline hook type and configuration

**Changes Required**:

1. **Update HookType Enum** (around line 17):
```python
class HookType(str, Enum):
    """Type of hook handler.
    
    Types:
    - INTERNAL: Python async function
    - COMMAND: External shell command
    - LLM: AI-powered hook using an LLM
    - INLINE: Simple pattern-based rules
    """
    
    INTERNAL = "internal"
    COMMAND = "command"
    LLM = "llm"
    INLINE = "inline"  # NEW
```

2. **Update HookConfig Dataclass** (around line 127):

Add new field after `prompt`:
```python
@dataclass
class HookConfig:
    # ... existing fields ...
    prompt: str | None = None
    inline_rules: list[dict[str, Any]] = field(default_factory=list)  # NEW
    timeout: float = 30.0
    # ... rest of existing fields ...
```

Update `__post_init__` validation (around line 155):
```python
def __post_init__(self):
    """Validate configuration."""
    if self.type == HookType.COMMAND and not (self.command or self.script):
        raise ValueError("Command hook requires 'command' or 'script'")
    if self.type == HookType.LLM and not self.prompt:
        raise ValueError("LLM hook requires 'prompt'")
    if self.type == HookType.INLINE and not self.inline_rules:  # NEW
        raise ValueError("Inline hook requires 'inline_rules'")
```

Update `to_dict()` method (around line 162):
```python
def to_dict(self) -> dict[str, Any]:
    """Serialize to dictionary."""
    result: dict[str, Any] = {
        "name": self.name,
        "type": self.type.value,
        "timeout": self.timeout,
        "priority": self.priority,
        "enabled": self.enabled,
    }
    if self.matcher.to_dict():
        result["matcher"] = self.matcher.to_dict()
    if self.command:
        result["command"] = self.command
    if self.script:
        result["script"] = self.script
    if self.prompt:
        result["prompt"] = self.prompt
    if self.inline_rules:  # NEW
        result["inline_rules"] = self.inline_rules
    if self.description:
        result["description"] = self.description
    return result
```

Update `from_dict()` classmethod (around line 183):
```python
@classmethod
def from_dict(cls, data: dict[str, Any]) -> HookConfig:
    """Create from dictionary."""
    return cls(
        name=data["name"],
        type=HookType(data.get("type", "internal")),
        matcher=HookMatcher.from_dict(data.get("matcher", {})),
        command=data.get("command"),
        script=data.get("script"),
        prompt=data.get("prompt"),
        inline_rules=data.get("inline_rules", []),  # NEW
        timeout=data.get("timeout", 30.0),
        priority=data.get("priority", 100),
        enabled=data.get("enabled", True),
        description=data.get("description"),
    )
```

**Validation**:
- Must maintain backward compatibility
- All existing HookConfig instances must continue to work
- inline_rules defaults to empty list

---

## Module 3: Inline Hooks Implementation

### File: `amplifier_app_cli/hooks/inline.py`

**Operation**: CREATE NEW FILE

**Purpose**: Implement pattern-based inline hooks

**Full Implementation**:

```python
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
```

**Key Functions**:
- `InlineRule.from_dict()` - Parse rule from config
- `InlineMatcher.matches()` - Check if rule matches data
- `InlineMatcher._get_field_value()` - Get nested field value
- `InlineHookExecutor.__call__()` - Execute rules in order
- `InlineHookExecutor._set_field_value()` - Set nested field value

**Operators Supported**:
- `equals` - Exact string match
- `contains` - Substring match
- `glob` - Glob pattern match
- `matches` or `regex` - Regex pattern match

---

## Module 4: Integration Wrappers

### File: `amplifier_app_cli/hooks/integration.py`

**Operation**: CREATE NEW FILE

**Purpose**: Provide integration points for hooks into tool execution and session lifecycle

**Full Implementation**:

```python
"""Integration wrappers for hooks system.

Provides wrapper functions for integrating hooks into tool execution
and session lifecycle without tight coupling.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .events import (
    PRE_TOOL_USE,
    POST_TOOL_USE,
    SESSION_START,
    SESSION_END,
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
)

logger = logging.getLogger(__name__)


class ToolDeniedError(Exception):
    """Raised when a hook denies a tool call."""
    pass


class ToolExecutionHooks:
    """Wrapper for tool execution with hooks.
    
    Integrates PreToolUse and PostToolUse hooks into tool calls.
    """
    
    def __init__(self, hooks_manager=None):
        """Initialize tool execution hooks.
        
        Args:
            hooks_manager: HooksManager instance (optional)
        """
        self.hooks_manager = hooks_manager
    
    async def wrap_tool_call(
        self,
        session,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_fn: Callable,
    ) -> Any:
        """Wrap tool call with pre/post hooks.
        
        Flow:
        1. Emit PreToolUse event
        2. Check for deny action
        3. Execute tool with potentially modified args
        4. Emit PostToolUse event
        5. Return result
        
        Args:
            session: AmplifierSession instance
            tool_name: Name of tool being called
            tool_args: Arguments for tool
            tool_fn: Async callable that executes tool
            
        Returns:
            Tool result
            
        Raises:
            ToolDeniedError: If hook denies tool call
        """
        # If no hooks manager, just execute tool
        if not self.hooks_manager:
            return await tool_fn(**tool_args)
        
        # Pre-tool hooks
        pre_event_data = {
            "tool": tool_name,
            "args": tool_args.copy(),
            "session_id": getattr(session, "session_id", None),
        }
        
        try:
            pre_results = await self.hooks_manager.emit(
                session, PRE_TOOL_USE, pre_event_data
            )
            
            # Check for deny or modify actions
            for result in pre_results:
                if result.action == "deny":
                    reason = result.reason or "Tool call denied by hook"
                    logger.info(f"Tool {tool_name} denied by hook: {reason}")
                    raise ToolDeniedError(reason)
                
                elif result.action == "modify" and result.modified_data:
                    # Apply modifications to args
                    if "args" in result.modified_data:
                        tool_args = result.modified_data["args"]
                        logger.debug(f"Tool {tool_name} args modified by hook")
        
        except ToolDeniedError:
            raise
        except Exception as e:
            logger.error(f"Error in pre-tool hooks: {e}")
            # Continue execution on hook errors
        
        # Execute tool
        error = None
        result = None
        start_time = time.time()
        
        try:
            result = await tool_fn(**tool_args)
        except Exception as e:
            error = e
            raise
        finally:
            # Post-tool hooks (always fire)
            duration_ms = (time.time() - start_time) * 1000
            post_event_data = {
                "tool": tool_name,
                "args": tool_args,
                "result": result,
                "error": str(error) if error else None,
                "duration_ms": duration_ms,
                "session_id": getattr(session, "session_id", None),
            }
            
            try:
                await self.hooks_manager.emit(
                    session, POST_TOOL_USE, post_event_data
                )
            except Exception as e:
                logger.error(f"Error in post-tool hooks: {e}")
        
        return result


class SessionLifecycleHooks:
    """Wrapper for session lifecycle with hooks.
    
    Provides methods to fire lifecycle events.
    """
    
    def __init__(self, hooks_manager=None):
        """Initialize session lifecycle hooks.
        
        Args:
            hooks_manager: HooksManager instance (optional)
        """
        self.hooks_manager = hooks_manager
    
    async def on_session_start(
        self,
        session,
        profile: str | None = None,
        config: dict | None = None,
    ):
        """Fire SessionStart event.
        
        Args:
            session: AmplifierSession instance
            profile: Active profile name
            config: Session configuration
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "session_id": getattr(session, "session_id", None),
            "event_type": "start",
            "profile": profile,
            "config": config or {},
        }
        
        try:
            await self.hooks_manager.emit(session, SESSION_START, event_data)
        except Exception as e:
            logger.error(f"Error in session start hooks: {e}")
    
    async def on_session_end(
        self,
        session,
        duration_ms: float | None = None,
        exit_reason: str | None = None,
    ):
        """Fire SessionEnd event.
        
        Args:
            session: AmplifierSession instance
            duration_ms: Session duration in milliseconds
            exit_reason: Why session ended
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "session_id": getattr(session, "session_id", None),
            "event_type": "end",
            "duration_ms": duration_ms,
            "exit_reason": exit_reason,
        }
        
        try:
            await self.hooks_manager.emit(session, SESSION_END, event_data)
        except Exception as e:
            logger.error(f"Error in session end hooks: {e}")
    
    async def on_error(
        self,
        session,
        error: Exception,
        tool: str | None = None,
        severity: str = "error",
    ):
        """Fire Error event.
        
        Args:
            session: AmplifierSession instance
            error: Exception that occurred
            tool: Tool that caused error (if applicable)
            severity: Error severity level
        """
        if not self.hooks_manager:
            return
        
        import traceback
        
        event_data = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "tool": tool,
            "session_id": getattr(session, "session_id", None),
            "stack_trace": "".join(traceback.format_tb(error.__traceback__)),
            "severity": severity,
        }
        
        try:
            await self.hooks_manager.emit(session, ERROR, event_data)
        except Exception as e:
            logger.error(f"Error in error hooks: {e}")
    
    async def on_checkpoint(
        self,
        session,
        checkpoint_id: str,
        checkpoint_type: str = "auto",
        message_count: int = 0,
        storage_path: str | None = None,
    ):
        """Fire Checkpoint event.
        
        Args:
            session: AmplifierSession instance
            checkpoint_id: Unique checkpoint identifier
            checkpoint_type: Type of checkpoint (auto, manual, periodic)
            message_count: Messages since last checkpoint
            storage_path: Path where checkpoint is stored
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "checkpoint_id": checkpoint_id,
            "session_id": getattr(session, "session_id", None),
            "checkpoint_type": checkpoint_type,
            "message_count": message_count,
            "storage_path": storage_path,
        }
        
        try:
            await self.hooks_manager.emit(session, CHECKPOINT, event_data)
        except Exception as e:
            logger.error(f"Error in checkpoint hooks: {e}")
    
    async def on_model_switch(
        self,
        session,
        old_model: str | None,
        new_model: str,
        reason: str | None = None,
        triggered_by: str = "user",
        profile: str | None = None,
    ):
        """Fire ModelSwitch event.
        
        Args:
            session: AmplifierSession instance
            old_model: Previous model name
            new_model: New model name
            reason: Why model switched
            triggered_by: What triggered the switch
            profile: Active profile name
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "old_model": old_model,
            "new_model": new_model,
            "reason": reason,
            "session_id": getattr(session, "session_id", None),
            "profile": profile,
            "triggered_by": triggered_by,
        }
        
        try:
            await self.hooks_manager.emit(session, MODEL_SWITCH, event_data)
        except Exception as e:
            logger.error(f"Error in model switch hooks: {e}")
    
    async def on_memory_update(
        self,
        session,
        file_path: str,
        update_type: str = "modified",
        content_size: int | None = None,
    ):
        """Fire MemoryUpdate event.
        
        Args:
            session: AmplifierSession instance
            file_path: Path to memory file
            update_type: Type of update (created, modified, deleted)
            content_size: Size of file in bytes
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "file_path": file_path,
            "update_type": update_type,
            "session_id": getattr(session, "session_id", None),
            "content_size": content_size,
        }
        
        try:
            await self.hooks_manager.emit(session, MEMORY_UPDATE, event_data)
        except Exception as e:
            logger.error(f"Error in memory update hooks: {e}")
```

**Key Classes**:
- `ToolExecutionHooks` - Wraps tool calls with hooks
- `SessionLifecycleHooks` - Fires lifecycle events
- `ToolDeniedError` - Exception when tool is denied

**Integration Pattern**:
```python
# In tool execution code:
tool_hooks = ToolExecutionHooks(hooks_manager)
result = await tool_hooks.wrap_tool_call(session, "bash", {"command": "ls"}, bash_tool)

# In session startup:
lifecycle_hooks = SessionLifecycleHooks(hooks_manager)
await lifecycle_hooks.on_session_start(session, profile="default")
```

---

## Module 5: Manager Extensions for LLM and Inline Hooks

### File: `amplifier_app_cli/hooks/manager.py`

**Operation**: MODIFY EXISTING FILE

**Purpose**: Add handler creation for LLM and inline hooks

**Changes Required**:

Update `_create_handler` method (around line 96):

```python
def _create_handler(self, config: HookConfig) -> Callable | None:
    """Create a handler from hook configuration.
    
    Args:
        config: Hook configuration
        
    Returns:
        Async callable handler or None if invalid
    """
    if config.type == HookType.COMMAND:
        # External command hook
        working_dir = Path.cwd()
        if self.search_paths:
            # Use first search path as base
            working_dir = self.search_paths[0]
        return ExternalCommandHook(config, working_dir)
    
    elif config.type == HookType.INTERNAL:
        # Internal hooks are registered directly, not created here
        return None
    
    elif config.type == HookType.LLM:
        # LLM hooks - try to create if dependencies available
        try:
            from .llm import LLMHookExecutor
            
            # Get model from settings or use default
            model_name = "claude-3-5-haiku-20241022"  # Fast, cheap model
            if self.config_manager:
                settings = self.config_manager.get_merged_settings()
                model_name = settings.get("hooks", {}).get("llm_model", model_name)
            
            return LLMHookExecutor(config, model_name=model_name)
        
        except ImportError:
            logger.warning(
                f"LLM hook {config.name} skipped: pydantic-ai not available. "
                f"Install with: uv pip install pydantic-ai"
            )
            return None
        except Exception as e:
            logger.error(f"Failed to create LLM hook {config.name}: {e}")
            return None
    
    elif config.type == HookType.INLINE:
        # Inline matcher hooks
        try:
            from .inline import InlineHookExecutor
            return InlineHookExecutor(config)
        except Exception as e:
            logger.error(f"Failed to create inline hook {config.name}: {e}")
            return None
    
    return None
```

**Validation**:
- Must not break existing command hook creation
- Gracefully handle missing LLM dependencies
- Log clear messages when hooks can't be created

---

## Module 6: LLM Hooks Implementation (OPTIONAL)

### File: `amplifier_app_cli/hooks/llm.py`

**Operation**: CREATE NEW FILE (OPTIONAL)

**Purpose**: Execute hooks using LLM for decision making

**Status**: This module is OPTIONAL for Phase 2. It can be implemented later if LLM dependencies are added to the project. The architecture document describes the full design.

**Placeholder Implementation** (if not implementing full version):

```python
"""LLM-powered hooks (placeholder).

This module requires pydantic-ai to be installed.
Install with: uv pip install pydantic-ai
"""

from __future__ import annotations

import logging
from typing import Any

from .models import HookConfig, HookResult

logger = logging.getLogger(__name__)


class LLMHookExecutor:
    """LLM-powered hook executor (not yet implemented)."""
    
    def __init__(self, config: HookConfig, model_name: str = "claude-3-5-haiku-20241022"):
        self.config = config
        self.model_name = model_name
        logger.warning(
            f"LLM hooks not yet fully implemented. "
            f"Hook {config.name} will always return continue."
        )
    
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Execute LLM hook (placeholder)."""
        logger.warning(f"LLM hook {self.config.name} called but not implemented")
        return HookResult.continue_("LLM hooks not yet implemented")
```

---

## Module 7: Update Public API

### File: `amplifier_app_cli/hooks/__init__.py`

**Operation**: MODIFY EXISTING FILE

**Purpose**: Export new public API

**Changes Required**:

Update imports and __all__:

```python
"""Enhanced hooks system for Amplifier CLI.

Provides event-driven hooks with multiple handler types:
- Command hooks: Execute external commands
- Internal hooks: Python async functions
- LLM hooks: AI-powered decision making
- Inline hooks: Pattern-based rules
"""

from .models import (
    HookType,
    HookConfig,
    HookMatcher,
    HookResult,
)

from .events import (
    # Tool events
    PRE_TOOL_USE,
    POST_TOOL_USE,
    # Session events
    SESSION_START,
    SESSION_END,
    SUBAGENT_STOP,
    STOP,
    # Notification events
    NOTIFICATION,
    # Phase 2 events
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
    # Data classes
    ToolUseEvent,
    SessionEvent,
    NotificationEvent,
    ErrorEvent,
    CheckpointEvent,
    ModelSwitchEvent,
    MemoryUpdateEvent,
)

from .manager import HooksManager

from .integration import (
    ToolExecutionHooks,
    SessionLifecycleHooks,
    ToolDeniedError,
)

__all__ = [
    # Models
    "HookType",
    "HookConfig",
    "HookMatcher",
    "HookResult",
    # Events
    "PRE_TOOL_USE",
    "POST_TOOL_USE",
    "SESSION_START",
    "SESSION_END",
    "SUBAGENT_STOP",
    "STOP",
    "NOTIFICATION",
    "ERROR",
    "CHECKPOINT",
    "MODEL_SWITCH",
    "MEMORY_UPDATE",
    # Data classes
    "ToolUseEvent",
    "SessionEvent",
    "NotificationEvent",
    "ErrorEvent",
    "CheckpointEvent",
    "ModelSwitchEvent",
    "MemoryUpdateEvent",
    # Manager
    "HooksManager",
    # Integration
    "ToolExecutionHooks",
    "SessionLifecycleHooks",
    "ToolDeniedError",
]
```

---

## Testing Specifications

### Test File 1: `tests/hooks/test_events.py`

**Operation**: MODIFY EXISTING FILE

**Add Tests**:

```python
def test_error_event_creation():
    """Test ErrorEvent creation and serialization."""
    event = ErrorEvent(
        error_type="ValueError",
        error_message="Invalid value",
        tool="bash",
        session_id="test-123",
        severity="error",
    )
    
    assert event.error_type == "ValueError"
    assert event.error_message == "Invalid value"
    assert event.tool == "bash"
    assert event.severity == "error"
    
    data = event.to_dict()
    assert data["error_type"] == "ValueError"
    assert "timestamp" in data


def test_checkpoint_event_creation():
    """Test CheckpointEvent creation and serialization."""
    event = CheckpointEvent(
        checkpoint_id="ckpt-123",
        session_id="sess-456",
        checkpoint_type="auto",
        message_count=10,
    )
    
    assert event.checkpoint_id == "ckpt-123"
    assert event.checkpoint_type == "auto"
    assert event.message_count == 10
    
    data = event.to_dict()
    assert data["checkpoint_id"] == "ckpt-123"


def test_model_switch_event_creation():
    """Test ModelSwitchEvent creation and serialization."""
    event = ModelSwitchEvent(
        old_model="gpt-4",
        new_model="claude-3-opus",
        reason="user request",
        triggered_by="user",
    )
    
    assert event.old_model == "gpt-4"
    assert event.new_model == "claude-3-opus"
    assert event.triggered_by == "user"
    
    data = event.to_dict()
    assert data["new_model"] == "claude-3-opus"


def test_memory_update_event_creation():
    """Test MemoryUpdateEvent creation and serialization."""
    event = MemoryUpdateEvent(
        file_path="/path/to/AGENTS.md",
        update_type="modified",
        content_size=1024,
    )
    
    assert event.file_path == "/path/to/AGENTS.md"
    assert event.update_type == "modified"
    assert event.content_size == 1024
    
    data = event.to_dict()
    assert data["file_path"] == "/path/to/AGENTS.md"


def test_new_events_in_mapping():
    """Test new events are in EVENT_DATA_TYPES."""
    from amplifier_app_cli.hooks.events import (
        EVENT_DATA_TYPES,
        ERROR,
        CHECKPOINT,
        MODEL_SWITCH,
        MEMORY_UPDATE,
        ErrorEvent,
        CheckpointEvent,
        ModelSwitchEvent,
        MemoryUpdateEvent,
    )
    
    assert ERROR in EVENT_DATA_TYPES
    assert CHECKPOINT in EVENT_DATA_TYPES
    assert MODEL_SWITCH in EVENT_DATA_TYPES
    assert MEMORY_UPDATE in EVENT_DATA_TYPES
    
    assert EVENT_DATA_TYPES[ERROR] == ErrorEvent
    assert EVENT_DATA_TYPES[CHECKPOINT] == CheckpointEvent
    assert EVENT_DATA_TYPES[MODEL_SWITCH] == ModelSwitchEvent
    assert EVENT_DATA_TYPES[MEMORY_UPDATE] == MemoryUpdateEvent
```

### Test File 2: `tests/hooks/test_inline.py`

**Operation**: CREATE NEW FILE

**Full Test Suite**:

```python
"""Tests for inline matcher hooks."""

import pytest
from amplifier_app_cli.hooks.inline import (
    InlineRule,
    InlineMatcher,
    InlineHookExecutor,
)
from amplifier_app_cli.hooks.models import HookConfig, HookMatcher, HookType


def test_inline_rule_from_dict():
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


def test_inline_matcher_equals():
    """Test equals operator."""
    rule = InlineRule(
        field="tool",
        operator="equals",
        value="bash",
        action="continue",
    )
    
    assert InlineMatcher.matches(rule, {"tool": "bash"})
    assert not InlineMatcher.matches(rule, {"tool": "python"})


def test_inline_matcher_contains():
    """Test contains operator."""
    rule = InlineRule(
        field="args.command",
        operator="contains",
        value="rm -rf",
        action="deny",
    )
    
    assert InlineMatcher.matches(rule, {"args": {"command": "rm -rf /tmp"}})
    assert not InlineMatcher.matches(rule, {"args": {"command": "ls -la"}})


def test_inline_matcher_glob():
    """Test glob operator."""
    rule = InlineRule(
        field="args.path",
        operator="glob",
        value="*.py",
        action="continue",
    )
    
    assert InlineMatcher.matches(rule, {"args": {"path": "test.py"}})
    assert not InlineMatcher.matches(rule, {"args": {"path": "test.txt"}})


def test_inline_matcher_regex():
    """Test regex operator."""
    rule = InlineRule(
        field="args.command",
        operator="regex",
        value=r"^rm\s+-rf",
        action="deny",
    )
    
    assert InlineMatcher.matches(rule, {"args": {"command": "rm -rf /tmp"}})
    assert not InlineMatcher.matches(rule, {"args": {"command": "ls rm -rf"}})


def test_inline_matcher_nested_field():
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


@pytest.mark.asyncio
async def test_inline_hook_deny_action():
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


@pytest.mark.asyncio
async def test_inline_hook_continue_action():
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


@pytest.mark.asyncio
async def test_inline_hook_modify_action():
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


@pytest.mark.asyncio
async def test_inline_hook_no_match():
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


@pytest.mark.asyncio
async def test_inline_hook_multiple_rules():
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
```

### Test File 3: `tests/hooks/test_integration.py`

**Operation**: CREATE NEW FILE

**Core Integration Tests**:

```python
"""Tests for hooks integration."""

import pytest
from amplifier_app_cli.hooks.integration import (
    ToolExecutionHooks,
    SessionLifecycleHooks,
    ToolDeniedError,
)
from amplifier_app_cli.hooks.manager import HooksManager
from amplifier_app_cli.hooks.models import HookConfig, HookMatcher, HookType
from amplifier_app_cli.hooks.config import HooksConfig


class MockSession:
    """Mock session for testing."""
    def __init__(self):
        self.session_id = "test-session-123"
        self.coordinator = {"hooks": None}


class MockHooksSystem:
    """Mock hooks system for testing."""
    def __init__(self):
        self.emitted_events = []
    
    async def emit(self, event, data):
        """Record emitted events."""
        self.emitted_events.append((event, data))
        return []
    
    def register(self, event, handler, priority=100, name=None):
        """Mock register."""
        return lambda: None


@pytest.mark.asyncio
async def test_tool_execution_no_hooks():
    """Test tool execution without hooks manager."""
    tool_hooks = ToolExecutionHooks(hooks_manager=None)
    
    async def mock_tool(arg1, arg2):
        return f"result: {arg1} {arg2}"
    
    result = await tool_hooks.wrap_tool_call(
        MockSession(),
        "test_tool",
        {"arg1": "a", "arg2": "b"},
        mock_tool
    )
    
    assert result == "result: a b"


@pytest.mark.asyncio
async def test_tool_execution_with_hooks():
    """Test tool execution with hooks fires events."""
    # Create hooks manager
    hooks_manager = HooksManager()
    hooks_manager.hooks_config = HooksConfig()
    
    session = MockSession()
    mock_hooks = MockHooksSystem()
    session.coordinator["hooks"] = mock_hooks
    
    tool_hooks = ToolExecutionHooks(hooks_manager)
    
    async def mock_tool(command):
        return f"executed: {command}"
    
    result = await tool_hooks.wrap_tool_call(
        session,
        "bash",
        {"command": "ls"},
        mock_tool
    )
    
    assert result == "executed: ls"
    assert len(mock_hooks.emitted_events) == 2  # pre and post
    
    # Check pre-tool event
    pre_event, pre_data = mock_hooks.emitted_events[0]
    assert pre_event == "PreToolUse"
    assert pre_data["tool"] == "bash"
    assert pre_data["args"]["command"] == "ls"
    
    # Check post-tool event
    post_event, post_data = mock_hooks.emitted_events[1]
    assert post_event == "PostToolUse"
    assert post_data["tool"] == "bash"
    assert post_data["result"] == "executed: ls"


@pytest.mark.asyncio
async def test_session_lifecycle_start():
    """Test session start event firing."""
    hooks_manager = HooksManager()
    hooks_manager.hooks_config = HooksConfig()
    
    session = MockSession()
    mock_hooks = MockHooksSystem()
    session.coordinator["hooks"] = mock_hooks
    
    lifecycle = SessionLifecycleHooks(hooks_manager)
    
    await lifecycle.on_session_start(
        session,
        profile="default",
        config={"model": "claude-3"}
    )
    
    assert len(mock_hooks.emitted_events) == 1
    event, data = mock_hooks.emitted_events[0]
    assert event == "SessionStart"
    assert data["profile"] == "default"
    assert data["config"]["model"] == "claude-3"


@pytest.mark.asyncio
async def test_session_lifecycle_error():
    """Test error event firing."""
    hooks_manager = HooksManager()
    hooks_manager.hooks_config = HooksConfig()
    
    session = MockSession()
    mock_hooks = MockHooksSystem()
    session.coordinator["hooks"] = mock_hooks
    
    lifecycle = SessionLifecycleHooks(hooks_manager)
    
    error = ValueError("Test error")
    await lifecycle.on_error(session, error, tool="bash", severity="error")
    
    assert len(mock_hooks.emitted_events) == 1
    event, data = mock_hooks.emitted_events[0]
    assert event == "Error"
    assert data["error_type"] == "ValueError"
    assert data["error_message"] == "Test error"
    assert data["tool"] == "bash"
```

---

## Summary

This specification document provides complete implementation details for Phase 2, including:

1. **Events Extension** - 4 new event types with full data models
2. **Models Extension** - Inline hook support
3. **Inline Hooks** - Complete pattern-based hook implementation
4. **Integration Wrappers** - Tool and session lifecycle integration
5. **Manager Extensions** - Handler creation for new hook types
6. **Public API** - Updated exports
7. **Testing** - Comprehensive test coverage

**Implementation Order**:
1. Module 1: Events (foundation)
2. Module 2: Models (foundation)
3. Module 3: Inline hooks (high value)
4. Module 4: Integration wrappers (core value)
5. Module 5: Manager extensions (glue)
6. Module 7: Public API (exposure)
7. Testing: All test files

**Total Files**:
- Modified: 4 (events.py, models.py, manager.py, __init__.py)
- Created: 2 (inline.py, integration.py)
- Tests: 2 modified, 2 created

The LLM hooks (Module 6) are OPTIONAL and can be implemented in a future phase.
