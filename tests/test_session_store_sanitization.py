"""Test session store message sanitization for extended thinking."""

import tempfile
from pathlib import Path

import pytest

from amplifier_app_cli.session_store import SessionStore


def message_matches_ignoring_timestamp(loaded: dict, original: dict) -> bool:
    """Compare a message ignoring the auto-added timestamp field."""
    loaded_without_ts = {k: v for k, v in loaded.items() if k != "timestamp"}
    return loaded_without_ts == original


def messages_equal_ignoring_timestamp(loaded: list, original: list) -> bool:
    """Compare message lists ignoring the timestamp field added by SessionStore."""
    if len(loaded) != len(original):
        return False
    for loaded_msg, orig_msg in zip(loaded, original, strict=True):
        if not message_matches_ignoring_timestamp(loaded_msg, orig_msg):
            return False
    return True


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
                "thinking_block": NonSerializable(
                    "some thinking"
                ),  # Non-serializable object
                "content_blocks": [
                    NonSerializable("block1"),
                    NonSerializable("block2"),
                ],  # More non-serializable
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
        assert message_matches_ignoring_timestamp(
            loaded_transcript[0], {"role": "user", "content": "Hello"}
        )
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
                "tool_calls": [
                    {"id": "1", "tool": "test", "arguments": {"arg": "value"}}
                ],
                "metadata": {"key": "value", "nested": {"deep": "value"}},
            },
        ]

        metadata = {"test": "metadata"}

        store.save("test-session", transcript, metadata)

        # Load and verify all fields are preserved (ignoring auto-added timestamps)
        loaded_transcript, loaded_metadata = store.load("test-session")

        assert messages_equal_ignoring_timestamp(loaded_transcript, transcript)
        assert loaded_metadata == metadata


def test_resave_preserves_existing_media_reference_and_inline_image(tmp_path):
    import json

    store = SessionStore(tmp_path)
    # An existing conversation may have been created by another host. The CLI
    # must keep the original reference identity, including a null expiry.
    png = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4"
           "nGNg+M8AAAICAQB7CYF4AAAAAElFTkSuQmCC")
    digest = "64abf93fb4c16b4258aa6eff5660a6b97b013a1b9c0cc877fcceb14c6764680f"
    ref = {
        "v": 1, "session_id": "chat", "sha256": digest, "mime": "image/png",
        "bytes": 69, "width": 1, "height": 1,
        "provenance": {"kind": "upload", "session_id": "chat", "id": "1" * 32,
                       "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None},
    }
    row = {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": png}},
            {"type": "text", "text": f"[Session image {digest}]"},
        ],
        "metadata": {"experimental.media.v1": {"refs": [ref]}},
    }
    directory = tmp_path / "chat"
    directory.mkdir()
    (directory / "transcript.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (directory / "metadata.json").write_text("{}", encoding="utf-8")
    rows, metadata = store.load("chat")
    store.save("chat", [*rows, {"role": "assistant", "content": "next turn"}], metadata)
    restored, _ = store.load("chat")
    assert restored[0] == row


def test_malformed_media_carrier_preserves_previous_transcript(tmp_path):
    store = SessionStore(tmp_path)
    rows = [{"role": "user", "content": "existing turn"}]
    store.save("chat", rows, {})
    transcript = tmp_path / "chat" / "transcript.jsonl"
    before = transcript.read_bytes()
    malformed = {"role": "assistant", "content": "later turn",
                 "metadata": {"experimental.media.v1": {"refs": "invalid"}}}
    with pytest.raises(ValueError, match="Invalid canonical media metadata carrier"):
        store.save("chat", [*rows, malformed], {})
    assert transcript.read_bytes() == before
    assert store.load("chat")[0] == rows


def test_sanitize_nested_non_serializable():
    """Test that nested non-serializable objects are handled.

    Note: Foundation's sanitize_for_json converts objects via __dict__ when possible,
    so NonSerializable objects become {"value": ...} dicts rather than being removed.
    This is better behavior as it preserves more information.
    """
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
                    "list": [
                        1,
                        2,
                        NonSerializable("in list"),
                        {"key": NonSerializable("in dict")},
                    ],
                },
            }
        ]

        metadata = {"test": "metadata"}

        # This should not raise an error
        store.save("test-session", transcript, metadata)

        # Load and verify sanitization
        loaded_transcript, loaded_metadata = store.load("test-session")

        # The structure should be preserved - foundation's sanitize_for_json
        # converts objects via __dict__, so NonSerializable becomes {"value": ...}
        assert loaded_transcript[0]["role"] == "assistant"
        assert loaded_transcript[0]["content"] == "Test"
        assert loaded_transcript[0]["nested"]["level1"]["safe"] == "value"
        # NonSerializable("deep") becomes {"value": "deep"} via __dict__
        assert loaded_transcript[0]["nested"]["level1"]["level2"] == {"value": "deep"}
        # NonSerializable items in lists become dicts via __dict__
        assert loaded_transcript[0]["nested"]["list"] == [
            1,
            2,
            {"value": "in list"},
            {"key": {"value": "in dict"}},
        ]


def test_sanitize_with_thinking_text():
    """Test that thinking text is extracted when possible."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        # Create a message with thinking block containing text
        transcript = [
            {
                "role": "assistant",
                "content": "Response",
                "thinking_block": {
                    "text": "This is my thinking process",
                    "raw": NonSerializable("raw data"),
                },
            }
        ]

        metadata = {"test": "metadata"}

        store.save("test-session", transcript, metadata)

        # Load and verify thinking text was extracted
        loaded_transcript, loaded_metadata = store.load("test-session")

        assert loaded_transcript[0]["thinking_text"] == "This is my thinking process"
        assert "thinking_block" not in loaded_transcript[0]


def test_metadata_secrets_redacted_on_save():
    """Test that secrets in metadata are redacted before writing to disk."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        # Simulate sub-session metadata with provider config containing secrets
        metadata = {
            "session_id": "test-session",
            "parent_id": "parent-123",
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "config": {
                            "api_key": "sk-ant-api03-REAL-SECRET-KEY",
                            "model": "claude-sonnet-4-20250514",
                        },
                    }
                ],
                "tools": [{"module": "tool-bash"}],
            },
        }

        transcript = [{"role": "user", "content": "Hello"}]
        store.save("test-session", transcript, metadata)

        # Load the raw JSON from disk to verify redaction
        import json

        metadata_file = Path(temp_dir) / "test-session" / "metadata.json"
        on_disk = json.loads(metadata_file.read_text())

        # Secret should be redacted
        provider_config = on_disk["config"]["providers"][0]["config"]
        assert provider_config["api_key"] == "[REDACTED]"

        # Non-secret fields should be preserved
        assert provider_config["model"] == "claude-sonnet-4-20250514"
        assert on_disk["session_id"] == "test-session"
        assert on_disk["config"]["tools"] == [{"module": "tool-bash"}]


def test_metadata_redaction_covers_common_secret_keys():
    """Test that all common secret key patterns are redacted."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SessionStore(Path(temp_dir))

        metadata = {
            "session_id": "test-session",
            "api_key": "secret1",
            "password": "secret2",
            "token": "secret3",
            "secret": "secret4",
            "credentials": "secret5",
            "private_key": "secret6",
            "authorization": "secret7",
            "safe_field": "not-a-secret",
        }

        store.save("test-session", [], metadata)

        import json

        metadata_file = Path(temp_dir) / "test-session" / "metadata.json"
        on_disk = json.loads(metadata_file.read_text())

        # All sensitive keys should be redacted
        assert on_disk["api_key"] == "[REDACTED]"
        assert on_disk["password"] == "[REDACTED]"
        assert on_disk["token"] == "[REDACTED]"
        assert on_disk["secret"] == "[REDACTED]"
        assert on_disk["credentials"] == "[REDACTED]"
        assert on_disk["private_key"] == "[REDACTED]"
        assert on_disk["authorization"] == "[REDACTED]"

        # Non-secret fields preserved
        assert on_disk["safe_field"] == "not-a-secret"
        assert on_disk["session_id"] == "test-session"
