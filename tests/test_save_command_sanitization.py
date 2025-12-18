"""Test /save command handles ThinkingBlock serialization properly."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


class MockThinkingBlock:
    """Mock ThinkingBlock that can't be JSON serialized."""

    def __init__(self, text):
        self.text = text
        self.thinking = text


class MockSession:
    """Mock session for testing."""

    def __init__(self, session_id: str = "test-session-123"):
        self.session_id = session_id
        self.config = {"test": "config"}
        self.coordinator = MagicMock()
        self.coordinator.session_id = session_id


@pytest.mark.asyncio
async def test_save_transcript_with_thinking_blocks():
    """Test that /save command properly sanitizes ThinkingBlock objects."""
    from amplifier_app_cli.main import CommandProcessor

    # Create mock session with context containing thinking blocks
    mock_session = MockSession()

    # Create mock context with messages containing ThinkingBlock
    mock_context = MagicMock()
    messages_with_thinking = [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": "I'll help you.",
            "thinking_block": MockThinkingBlock("This is my thinking"),  # Non-serializable!
            "content_blocks": [MockThinkingBlock("block1"), MockThinkingBlock("block2")],  # Also non-serializable!
        },
    ]
    mock_context.get_messages = AsyncMock(return_value=messages_with_thinking)
    mock_session.coordinator.get = MagicMock(return_value=mock_context)

    processor = CommandProcessor(mock_session)  # type: ignore[arg-type]

    # Use temp directory for SessionStore base_dir
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Patch SessionStore to use temp directory
        with patch("amplifier_app_cli.main.SessionStore") as mock_store_class:
            mock_store = MagicMock()
            mock_store.base_dir = temp_path
            # Forward _sanitize_message to real implementation
            from amplifier_app_cli.session_store import SessionStore

            real_store = SessionStore.__new__(SessionStore)
            mock_store._sanitize_message = real_store._sanitize_message
            mock_store_class.return_value = mock_store

            # Call _save_transcript - should NOT crash
            result = await processor._save_transcript("test_save.json")

            # Verify file was created in session subdirectory
            assert "test_save.json" in result
            saved_file = Path(result)
            assert saved_file.exists()

            # Load and verify sanitization worked
            with open(saved_file) as f:
                data = json.load(f)

            # Check structure
            assert "timestamp" in data
            assert "messages" in data
            assert len(data["messages"]) == 2

            # Check thinking_block was removed/sanitized
            assert data["messages"][0] == {"role": "user", "content": "Hello"}
            assert data["messages"][1]["role"] == "assistant"
            assert data["messages"][1]["content"] == "I'll help you."

            # ThinkingBlock should be removed (not serializable)
            assert "thinking_block" not in data["messages"][1]
            assert "content_blocks" not in data["messages"][1]

            # If thinking text was extracted, it should be preserved
            if "thinking_text" in data["messages"][1]:
                assert data["messages"][1]["thinking_text"] == "This is my thinking"


@pytest.mark.asyncio
async def test_save_transcript_without_thinking():
    """Test that /save works normally without thinking blocks."""
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MockSession()
    mock_context = MagicMock()
    normal_messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    mock_context.get_messages = AsyncMock(return_value=normal_messages)
    mock_session.coordinator.get = MagicMock(return_value=mock_context)

    processor = CommandProcessor(mock_session)  # type: ignore[arg-type]

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        with patch("amplifier_app_cli.main.SessionStore") as mock_store_class:
            mock_store = MagicMock()
            mock_store.base_dir = temp_path
            from amplifier_app_cli.session_store import SessionStore

            real_store = SessionStore.__new__(SessionStore)
            mock_store._sanitize_message = real_store._sanitize_message
            mock_store_class.return_value = mock_store

            result = await processor._save_transcript("test_normal.json")

            assert "test_normal.json" in result
            saved_file = Path(result)
            assert saved_file.exists()

            with open(saved_file) as f:
                data = json.load(f)

            # All messages should be preserved exactly
            assert data["messages"] == normal_messages
