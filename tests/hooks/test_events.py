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
    # Event data types
    ToolUseEvent,
    NotificationEvent,
    SessionEvent,
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
