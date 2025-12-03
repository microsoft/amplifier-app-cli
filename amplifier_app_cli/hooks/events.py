"""Event definitions for the hooks system.

Defines the standard event types and their data structures.
Compatible with kernel events while adding CLI-specific events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# Re-export kernel event constants for compatibility
try:
    from amplifier_core.events import (
        PROMPT_START,
        PROMPT_COMPLETE,
    )
    # Kernel uses tool:pre, tool:post
    TOOL_PRE = "tool:pre"
    TOOL_POST = "tool:post"
except ImportError:
    # Fallback definitions if kernel not available
    PROMPT_START = "prompt:start"
    PROMPT_COMPLETE = "prompt:complete"
    TOOL_PRE = "tool:pre"
    TOOL_POST = "tool:post"

# CLI-specific event names
# These are additional events beyond what the kernel provides

# Tool lifecycle (aliases for kernel events, plus extended data)
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"

# Session lifecycle
SESSION_START = "SessionStart"
SESSION_END = "SessionEnd"

# Subagent events
SUBAGENT_STOP = "SubagentStop"

# Notification events
NOTIFICATION = "Notification"

# Stop events
STOP = "Stop"

# Error events
ERROR = "Error"

# Checkpoint events  
CHECKPOINT = "Checkpoint"

# Model switch events
MODEL_SWITCH = "ModelSwitch"

# Memory update events
MEMORY_UPDATE = "MemoryUpdate"


@dataclass
class ToolUseEvent:
    """Event data for tool usage events (PreToolUse, PostToolUse).

    Attributes:
        tool: Name of the tool being used
        args: Arguments passed to the tool
        result: Tool result (only for PostToolUse)
        error: Error message if tool failed (only for PostToolUse)
        session_id: Current session ID
        duration_ms: Execution time in milliseconds (only for PostToolUse)
        timestamp: When the event occurred
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    session_id: str | None = None
    duration_ms: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "tool": self.tool,
            "args": self.args,
            "result": self.result,
            "error": self.error,
            "session_id": self.session_id,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class NotificationEvent:
    """Event data for notification events.

    Attributes:
        message: The notification message
        level: Severity level (info, warning, error)
        source: Where the notification originated
        session_id: Current session ID
        timestamp: When the event occurred
    """

    message: str
    level: Literal["info", "warning", "error"] = "info"
    source: str | None = None
    session_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "message": self.message,
            "level": self.level,
            "source": self.source,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SessionEvent:
    """Event data for session lifecycle events.

    Attributes:
        session_id: The session ID
        parent_id: Parent session ID if this is a sub-session
        event_type: Type of session event (start, end, stop)
        profile: Active profile name
        config: Session configuration summary
        duration_ms: Session duration (for end events)
        exit_reason: Why session ended (for end/stop events)
        timestamp: When the event occurred
    """

    session_id: str
    event_type: Literal["start", "end", "stop", "subagent_stop"]
    parent_id: str | None = None
    profile: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    exit_reason: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "event_type": self.event_type,
            "parent_id": self.parent_id,
            "profile": self.profile,
            "config": self.config,
            "duration_ms": self.duration_ms,
            "exit_reason": self.exit_reason,
            "timestamp": self.timestamp.isoformat(),
        }


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


# Event type to data class mapping
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


def create_event_data(event_name: str, **kwargs) -> Any:
    """Create typed event data for an event.

    Args:
        event_name: Name of the event
        **kwargs: Event data fields

    Returns:
        Typed event data object or raw dict if no type defined
    """
    data_class = EVENT_DATA_TYPES.get(event_name)
    if data_class:
        # Filter kwargs to only valid fields
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(data_class)}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_fields}
        return data_class(**filtered_kwargs)
    return kwargs
