"""Tests for hooks events."""

import pytest
from datetime import datetime

from amplifier_app_cli.hooks.events import (
    # Event constants
    PRE_TOOL_USE,
    POST_TOOL_USE,
    SESSION_START,
    SESSION_END,
    NOTIFICATION,
    TOOL_PRE,
    TOOL_POST,
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
    # Event data types
    ToolUseEvent,
    NotificationEvent,
    SessionEvent,
    ErrorEvent,
    CheckpointEvent,
    ModelSwitchEvent,
    MemoryUpdateEvent,
    EVENT_DATA_TYPES,
    create_event_data,
)


class TestEventConstants:
    """Tests for event name constants."""

    def test_cli_events(self):
        """Test CLI-specific event names."""
        assert PRE_TOOL_USE == "PreToolUse"
        assert POST_TOOL_USE == "PostToolUse"
        assert SESSION_START == "SessionStart"
        assert SESSION_END == "SessionEnd"
        assert NOTIFICATION == "Notification"

    def test_kernel_events(self):
        """Test kernel event names."""
        assert TOOL_PRE == "tool:pre"
        assert TOOL_POST == "tool:post"

    def test_phase2_events(self):
        """Test Phase 2 event constants."""
        assert ERROR == "Error"
        assert CHECKPOINT == "Checkpoint"
        assert MODEL_SWITCH == "ModelSwitch"
        assert MEMORY_UPDATE == "MemoryUpdate"


class TestToolUseEvent:
    """Tests for ToolUseEvent."""

    def test_basic_creation(self):
        """Test creating a basic tool use event."""
        event = ToolUseEvent(tool="write")
        assert event.tool == "write"
        assert event.args == {}
        assert event.result is None

    def test_full_creation(self):
        """Test creating a full tool use event."""
        event = ToolUseEvent(
            tool="write",
            args={"path": "test.py", "content": "print('hello')"},
            result="Success",
            session_id="abc123",
            duration_ms=150.5,
        )
        assert event.tool == "write"
        assert event.args["path"] == "test.py"
        assert event.result == "Success"
        assert event.session_id == "abc123"
        assert event.duration_ms == 150.5

    def test_error_event(self):
        """Test creating an error event."""
        event = ToolUseEvent(
            tool="bash",
            args={"command": "invalid"},
            error="Command not found",
        )
        assert event.error == "Command not found"
        assert event.result is None

    def test_to_dict(self):
        """Test serialization."""
        event = ToolUseEvent(
            tool="read",
            args={"path": "file.txt"},
        )
        d = event.to_dict()
        assert d["tool"] == "read"
        assert d["args"]["path"] == "file.txt"
        assert "timestamp" in d


class TestNotificationEvent:
    """Tests for NotificationEvent."""

    def test_basic_creation(self):
        """Test creating a basic notification."""
        event = NotificationEvent(message="Hello world")
        assert event.message == "Hello world"
        assert event.level == "info"

    def test_warning_notification(self):
        """Test creating a warning notification."""
        event = NotificationEvent(
            message="Something might be wrong",
            level="warning",
            source="validation",
        )
        assert event.level == "warning"
        assert event.source == "validation"

    def test_error_notification(self):
        """Test creating an error notification."""
        event = NotificationEvent(
            message="Something went wrong",
            level="error",
        )
        assert event.level == "error"

    def test_to_dict(self):
        """Test serialization."""
        event = NotificationEvent(
            message="Test",
            level="warning",
        )
        d = event.to_dict()
        assert d["message"] == "Test"
        assert d["level"] == "warning"


class TestSessionEvent:
    """Tests for SessionEvent."""

    def test_start_event(self):
        """Test creating a session start event."""
        event = SessionEvent(
            session_id="session123",
            event_type="start",
            profile="default",
        )
        assert event.session_id == "session123"
        assert event.event_type == "start"
        assert event.profile == "default"

    def test_end_event(self):
        """Test creating a session end event."""
        event = SessionEvent(
            session_id="session123",
            event_type="end",
            duration_ms=5000.0,
            exit_reason="user_exit",
        )
        assert event.event_type == "end"
        assert event.duration_ms == 5000.0
        assert event.exit_reason == "user_exit"

    def test_subagent_event(self):
        """Test creating a subagent session event."""
        event = SessionEvent(
            session_id="sub123",
            event_type="subagent_stop",
            parent_id="parent123",
        )
        assert event.parent_id == "parent123"
        assert event.event_type == "subagent_stop"

    def test_to_dict(self):
        """Test serialization."""
        event = SessionEvent(
            session_id="test",
            event_type="start",
        )
        d = event.to_dict()
        assert d["session_id"] == "test"
        assert d["event_type"] == "start"


class TestCreateEventData:
    """Tests for create_event_data helper."""

    def test_create_tool_event(self):
        """Test creating tool event data."""
        data = create_event_data(PRE_TOOL_USE, tool="write", args={"path": "test.py"})
        assert isinstance(data, ToolUseEvent)
        assert data.tool == "write"

    def test_create_notification_event(self):
        """Test creating notification event data."""
        data = create_event_data(NOTIFICATION, message="Hello", level="info")
        assert isinstance(data, NotificationEvent)
        assert data.message == "Hello"

    def test_create_session_event(self):
        """Test creating session event data."""
        data = create_event_data(SESSION_START, session_id="abc", event_type="start")
        assert isinstance(data, SessionEvent)
        assert data.session_id == "abc"

    def test_unknown_event_returns_dict(self):
        """Test that unknown events return raw dict."""
        data = create_event_data("custom:event", foo="bar")
        assert isinstance(data, dict)
        assert data["foo"] == "bar"

    def test_filters_invalid_fields(self):
        """Test that invalid fields are filtered out."""
        data = create_event_data(
            PRE_TOOL_USE,
            tool="read",
            invalid_field="should_be_ignored",
        )
        assert isinstance(data, ToolUseEvent)
        assert not hasattr(data, "invalid_field")

    def test_create_error_event(self):
        """Test creating ErrorEvent via create_event_data."""
        data = create_event_data(
            ERROR,
            error_type="ValueError",
            error_message="Test error"
        )
        assert isinstance(data, ErrorEvent)
        assert data.error_type == "ValueError"

    def test_create_checkpoint_event(self):
        """Test creating CheckpointEvent via create_event_data."""
        data = create_event_data(
            CHECKPOINT,
            checkpoint_id="ckpt-123",
            session_id="sess-456"
        )
        assert isinstance(data, CheckpointEvent)
        assert data.checkpoint_id == "ckpt-123"

    def test_create_model_switch_event(self):
        """Test creating ModelSwitchEvent via create_event_data."""
        data = create_event_data(
            MODEL_SWITCH,
            old_model="gpt-4",
            new_model="claude-3-opus"
        )
        assert isinstance(data, ModelSwitchEvent)
        assert data.new_model == "claude-3-opus"

    def test_create_memory_update_event(self):
        """Test creating MemoryUpdateEvent via create_event_data."""
        data = create_event_data(
            MEMORY_UPDATE,
            file_path="/path/to/AGENTS.md"
        )
        assert isinstance(data, MemoryUpdateEvent)
        assert data.file_path == "/path/to/AGENTS.md"


class TestErrorEvent:
    """Tests for ErrorEvent."""

    def test_basic_creation(self):
        """Test creating a basic error event."""
        event = ErrorEvent(
            error_type="ValueError",
            error_message="Test error"
        )
        assert event.error_type == "ValueError"
        assert event.error_message == "Test error"
        assert event.severity == "error"

    def test_full_creation(self):
        """Test creating a full error event."""
        event = ErrorEvent(
            error_type="ValueError",
            error_message="Invalid value",
            tool="bash",
            session_id="test-123",
            stack_trace="Traceback...",
            severity="critical"
        )
        assert event.error_type == "ValueError"
        assert event.error_message == "Invalid value"
        assert event.tool == "bash"
        assert event.session_id == "test-123"
        assert event.stack_trace == "Traceback..."
        assert event.severity == "critical"

    def test_to_dict(self):
        """Test serialization."""
        event = ErrorEvent(
            error_type="ValueError",
            error_message="Test error",
            tool="bash"
        )
        d = event.to_dict()
        assert d["error_type"] == "ValueError"
        assert d["error_message"] == "Test error"
        assert d["tool"] == "bash"
        assert "timestamp" in d


class TestCheckpointEvent:
    """Tests for CheckpointEvent."""

    def test_basic_creation(self):
        """Test creating a basic checkpoint event."""
        event = CheckpointEvent(
            checkpoint_id="ckpt-123",
            session_id="sess-456"
        )
        assert event.checkpoint_id == "ckpt-123"
        assert event.session_id == "sess-456"
        assert event.checkpoint_type == "auto"

    def test_full_creation(self):
        """Test creating a full checkpoint event."""
        event = CheckpointEvent(
            checkpoint_id="ckpt-123",
            session_id="sess-456",
            checkpoint_type="manual",
            message_count=10,
            duration_since_last_ms=5000.0,
            storage_path="/tmp/checkpoint"
        )
        assert event.checkpoint_id == "ckpt-123"
        assert event.checkpoint_type == "manual"
        assert event.message_count == 10
        assert event.duration_since_last_ms == 5000.0

    def test_to_dict(self):
        """Test serialization."""
        event = CheckpointEvent(
            checkpoint_id="ckpt-123",
            session_id="sess-456"
        )
        d = event.to_dict()
        assert d["checkpoint_id"] == "ckpt-123"
        assert d["session_id"] == "sess-456"
        assert "timestamp" in d


class TestModelSwitchEvent:
    """Tests for ModelSwitchEvent."""

    def test_basic_creation(self):
        """Test creating a basic model switch event."""
        event = ModelSwitchEvent(
            old_model="gpt-4",
            new_model="claude-3-opus"
        )
        assert event.old_model == "gpt-4"
        assert event.new_model == "claude-3-opus"
        assert event.triggered_by == "user"

    def test_full_creation(self):
        """Test creating a full model switch event."""
        event = ModelSwitchEvent(
            old_model="gpt-4",
            new_model="claude-3-opus",
            reason="user request",
            session_id="sess-123",
            profile="default",
            triggered_by="automatic"
        )
        assert event.old_model == "gpt-4"
        assert event.new_model == "claude-3-opus"
        assert event.reason == "user request"
        assert event.triggered_by == "automatic"

    def test_to_dict(self):
        """Test serialization."""
        event = ModelSwitchEvent(
            old_model="gpt-4",
            new_model="claude-3-opus"
        )
        d = event.to_dict()
        assert d["old_model"] == "gpt-4"
        assert d["new_model"] == "claude-3-opus"
        assert "timestamp" in d


class TestMemoryUpdateEvent:
    """Tests for MemoryUpdateEvent."""

    def test_basic_creation(self):
        """Test creating a basic memory update event."""
        event = MemoryUpdateEvent(
            file_path="/path/to/AGENTS.md"
        )
        assert event.file_path == "/path/to/AGENTS.md"
        assert event.update_type == "modified"

    def test_full_creation(self):
        """Test creating a full memory update event."""
        event = MemoryUpdateEvent(
            file_path="/path/to/AGENTS.md",
            update_type="created",
            session_id="sess-123",
            content_size=1024,
            previous_hash="abc123",
            new_hash="def456"
        )
        assert event.file_path == "/path/to/AGENTS.md"
        assert event.update_type == "created"
        assert event.content_size == 1024
        assert event.previous_hash == "abc123"

    def test_to_dict(self):
        """Test serialization."""
        event = MemoryUpdateEvent(
            file_path="/path/to/AGENTS.md"
        )
        d = event.to_dict()
        assert d["file_path"] == "/path/to/AGENTS.md"
        assert "timestamp" in d


class TestEventDataTypes:
    """Tests for EVENT_DATA_TYPES mapping."""

    def test_phase2_events_in_mapping(self):
        """Test that Phase 2 events are in the mapping."""
        assert ERROR in EVENT_DATA_TYPES
        assert CHECKPOINT in EVENT_DATA_TYPES
        assert MODEL_SWITCH in EVENT_DATA_TYPES
        assert MEMORY_UPDATE in EVENT_DATA_TYPES

    def test_phase2_mappings_correct(self):
        """Test that Phase 2 events map to correct classes."""
        assert EVENT_DATA_TYPES[ERROR] == ErrorEvent
        assert EVENT_DATA_TYPES[CHECKPOINT] == CheckpointEvent
        assert EVENT_DATA_TYPES[MODEL_SWITCH] == ModelSwitchEvent
        assert EVENT_DATA_TYPES[MEMORY_UPDATE] == MemoryUpdateEvent

