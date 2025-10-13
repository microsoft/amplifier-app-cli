"""Test session store message sanitization for extended thinking."""

import tempfile
from pathlib import Path

from amplifier_app_cli.session_store import SessionStore


class NonSerializable:
    """A class that can't be JSON serialized."""

    def __init__(self, value):
        self.value = value


def test_sanitize_message_with_thinking_block():
    """Test that thinking blocks are properly sanitized."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        # Create a message with non-serializable thinking block
        transcript = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": "I'll help you with that.",
                "thinking_block": NonSerializable("some thinking"),  # Non-serializable object
                "content_blocks": [NonSerializable("block1"), NonSerializable("block2")],  # More non-serializable
            },
        ]

        metadata = {"test": "metadata"}

        # This should not raise an error
        store.save("test-session", transcript, metadata)

        # Verify the session was saved
        assert store.exists("test-session")

        # Load and verify the sanitized content
        loaded_transcript, loaded_metadata = store.load("test-session")

        # Check that non-serializable fields were removed
        assert len(loaded_transcript) == 2
        assert loaded_transcript[0] == {"role": "user", "content": "Hello"}
        assert loaded_transcript[1]["role"] == "assistant"
        assert loaded_transcript[1]["content"] == "I'll help you with that."
        assert "thinking_block" not in loaded_transcript[1]
        assert "content_blocks" not in loaded_transcript[1]


def test_sanitize_message_preserves_serializable():
    """Test that serializable fields are preserved."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        # Create a message with all serializable content
        transcript = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": "Response",
                "tool_calls": [{"id": "1", "tool": "test", "arguments": {"arg": "value"}}],
                "metadata": {"key": "value", "nested": {"deep": "value"}},
            },
        ]

        metadata = {"test": "metadata"}

        store.save("test-session", transcript, metadata)

        # Load and verify all fields are preserved
        loaded_transcript, loaded_metadata = store.load("test-session")

        assert loaded_transcript == transcript
        assert loaded_metadata == metadata


def test_sanitize_nested_non_serializable():
    """Test that nested non-serializable objects are handled."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        # Create a message with nested non-serializable objects
        transcript = [
            {
                "role": "assistant",
                "content": "Test",
                "nested": {
                    "level1": {
                        "level2": NonSerializable("deep"),
                        "safe": "value",
                    },
                    "list": [1, 2, NonSerializable("in list"), {"key": NonSerializable("in dict")}],
                },
            }
        ]

        metadata = {"test": "metadata"}

        # This should not raise an error
        store.save("test-session", transcript, metadata)

        # Load and verify sanitization
        loaded_transcript, loaded_metadata = store.load("test-session")

        # The structure should be preserved but non-serializable removed
        assert loaded_transcript[0]["role"] == "assistant"
        assert loaded_transcript[0]["content"] == "Test"
        assert loaded_transcript[0]["nested"]["level1"]["safe"] == "value"
        assert "level2" not in loaded_transcript[0]["nested"]["level1"]
        # Non-serializable items in lists are removed entirely
        assert loaded_transcript[0]["nested"]["list"] == [
            1,
            2,
            {},
        ]  # The dict with non-serializable value becomes empty


def test_sanitize_with_thinking_text():
    """Test that thinking text is extracted when possible."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        # Create a message with thinking block containing text
        transcript = [
            {
                "role": "assistant",
                "content": "Response",
                "thinking_block": {"text": "This is my thinking process", "raw": NonSerializable("raw data")},
            }
        ]

        metadata = {"test": "metadata"}

        store.save("test-session", transcript, metadata)

        # Load and verify thinking text was extracted
        loaded_transcript, loaded_metadata = store.load("test-session")

        assert loaded_transcript[0]["thinking_text"] == "This is my thinking process"
        assert "thinking_block" not in loaded_transcript[0]
