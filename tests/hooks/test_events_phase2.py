"""Tests for Phase 2 event types."""

import pytest
from datetime import datetime

from amplifier_app_cli.hooks.events import (
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
    ErrorEvent,
    CheckpointEvent,
    ModelSwitchEvent,
    MemoryUpdateEvent,
    EVENT_DATA_TYPES,
    create_event_data,
)


class TestPhase2EventConstants:
    """Tests for Phase 2 event constants."""

    def test_error_event_constant(self):
        """Test ERROR event constant."""
        assert ERROR == "Error"

    def test_checkpoint_event_constant(self):
        """Test CHECKPOINT event constant."""
        assert CHECKPOINT == "Checkpoint"

    def test_model_switch_event_constant(self):
        """Test MODEL_SWITCH event constant."""
        assert MODEL_SWITCH == "ModelSwitch"

    def test_memory_update_event_constant(self):
        """Test MEMORY_UPDATE event constant."""
        assert MEMORY_UPDATE == "MemoryUpdate"


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


class TestCreateEventData:
    """Tests for create_event_data with Phase 2 events."""

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
