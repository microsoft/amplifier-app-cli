"""
Tests for SessionStore class.

Focus on runtime invariants, edge cases, and integration behavior
rather than code inspection tests.
"""

import json
import tempfile
import time
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from amplifier_app_cli.session_store import SessionStore


class TestSessionStore:
    """Test session persistence functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_dir):
        """Create a SessionStore instance with temp directory."""
        return SessionStore(base_dir=temp_dir)

    @pytest.fixture
    def sample_transcript(self):
        """Sample transcript for testing."""
        return [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "What's 2+2?"},
            {"role": "assistant", "content": "2+2 equals 4"},
        ]

    @pytest.fixture
    def sample_metadata(self):
        """Sample metadata for testing."""
        return {
            "created": datetime.now(UTC).isoformat(),
            "profile": "dev",
            "model": "claude-sonnet-4-5",
            "turn_count": 2,
        }

    def test_save_load_roundtrip(self, store, sample_transcript, sample_metadata):
        """Test that saved sessions can be loaded correctly."""
        session_id = "test-session-123"

        # Save session
        store.save(session_id, sample_transcript, sample_metadata)

        # Load session
        loaded_transcript, loaded_metadata = store.load(session_id)

        # Verify data integrity
        assert loaded_transcript == sample_transcript
        assert loaded_metadata == sample_metadata

    def test_atomic_write_on_failure(self, store, sample_metadata):
        """Test that non-serializable objects are sanitized gracefully."""
        session_id = "test-atomic"
        good_transcript = [{"role": "user", "content": "good"}]

        # Save initial good data
        store.save(session_id, good_transcript, sample_metadata)

        # Verify initial save worked
        loaded_transcript, _ = store.load(session_id)
        assert loaded_transcript == good_transcript

        # Try to save with an object that can't be JSON serialized
        # This should now succeed with sanitization instead of failing
        bad_transcript = [{"role": "user", "content": "bad", "obj": object()}]

        # This should not raise an error anymore - objects are sanitized
        store.save(session_id, bad_transcript, sample_metadata)

        # Data should be saved with non-serializable fields removed
        loaded_transcript, _ = store.load(session_id)
        assert len(loaded_transcript) == 1
        assert loaded_transcript[0]["role"] == "user"
        assert loaded_transcript[0]["content"] == "bad"
        assert "obj" not in loaded_transcript[0]  # Non-serializable field removed

    def test_corruption_recovery_transcript(self, store, sample_transcript, sample_metadata, temp_dir):
        """Test recovery from corrupted transcript file using backup."""
        session_id = "test-corruption"

        # Save good data
        store.save(session_id, sample_transcript, sample_metadata)

        # Save again to create backup
        updated_transcript = sample_transcript + [{"role": "user", "content": "update"}]
        store.save(session_id, updated_transcript, sample_metadata)

        # Corrupt the main transcript file
        transcript_file = temp_dir / session_id / "transcript.jsonl"
        with open(transcript_file, "w") as f:
            f.write("{ corrupt json }\n")

        # Load should recover from backup
        loaded_transcript, _ = store.load(session_id)
        assert loaded_transcript == sample_transcript  # Should get the backup version

    def test_corruption_recovery_metadata(self, store, sample_transcript, sample_metadata, temp_dir):
        """Test recovery from corrupted metadata file using backup."""
        session_id = "test-metadata-corruption"

        # Save good data
        store.save(session_id, sample_transcript, sample_metadata)

        # Save again to create backup
        updated_metadata = {**sample_metadata, "updated": True}
        store.save(session_id, sample_transcript, updated_metadata)

        # Corrupt the main metadata file
        metadata_file = temp_dir / session_id / "metadata.json"
        with open(metadata_file, "w") as f:
            f.write("{ corrupt json")

        # Load should recover from backup
        _, loaded_metadata = store.load(session_id)
        assert loaded_metadata == sample_metadata  # Should get the backup version

    def test_both_files_corrupted(self, store, sample_transcript, sample_metadata, temp_dir):
        """Test graceful degradation when both main and backup files are corrupted."""
        session_id = "test-both-corrupted"

        # Save initial data
        store.save(session_id, sample_transcript, sample_metadata)

        # Corrupt both transcript files
        transcript_file = temp_dir / session_id / "transcript.jsonl"
        backup_file = temp_dir / session_id / "transcript.jsonl.backup"

        with open(transcript_file, "w") as f:
            f.write("corrupt")
        with open(backup_file, "w") as f:
            f.write("also corrupt")

        # Corrupt both metadata files too
        metadata_file = temp_dir / session_id / "metadata.json"
        metadata_backup = temp_dir / session_id / "metadata.json.backup"

        with open(metadata_file, "w") as f:
            f.write("corrupt")
        if metadata_backup.exists():
            with open(metadata_backup, "w") as f:
                f.write("also corrupt")

        # Should return empty transcript but not crash
        loaded_transcript, loaded_metadata = store.load(session_id)
        assert loaded_transcript == []
        assert "recovered" in loaded_metadata
        assert loaded_metadata["session_id"] == session_id

    def test_missing_session(self, store):
        """Test loading non-existent session raises appropriate error."""
        with pytest.raises(FileNotFoundError, match="Session 'nonexistent' not found"):
            store.load("nonexistent")

    def test_invalid_session_id(self, store):
        """Test that invalid session IDs are rejected."""
        invalid_ids = [
            "",
            "   ",
            "path/with/slash",
            "path\\with\\backslash",
            ".",
            "..",
        ]

        for invalid_id in invalid_ids:
            with pytest.raises(ValueError, match="session_id|Invalid session_id"):
                store.save(invalid_id, [], {})

            with pytest.raises(ValueError, match="session_id|Invalid session_id"):
                store.load(invalid_id)

    def test_exists_check(self, store, sample_transcript, sample_metadata):
        """Test session existence checking."""
        session_id = "test-exists"

        # Should not exist initially
        assert not store.exists(session_id)

        # Save session
        store.save(session_id, sample_transcript, sample_metadata)

        # Should exist now
        assert store.exists(session_id)

        # Invalid IDs should return False
        assert not store.exists("")
        assert not store.exists("../invalid")

    def test_list_sessions(self, store, sample_transcript, sample_metadata):
        """Test listing all sessions sorted by modification time."""
        # Save multiple sessions with delays
        session_ids = ["session-1", "session-2", "session-3"]

        for session_id in session_ids:
            store.save(session_id, sample_transcript, sample_metadata)
            time.sleep(0.01)  # Small delay to ensure different mtimes

        # List should be in reverse order (newest first)
        listed = store.list_sessions()
        assert listed == ["session-3", "session-2", "session-1"]

        # Should not include hidden directories
        hidden_dir = store.base_dir / ".hidden"
        hidden_dir.mkdir()
        listed = store.list_sessions()
        assert ".hidden" not in listed

    def test_save_profile(self, store, temp_dir):
        """Test saving profile snapshot."""
        session_id = "test-profile"
        profile = {
            "profile": {
                "name": "dev",
                "model": "claude-sonnet-4-5",
            },
            "session": {
                "max_tokens": 100000,
            },
        }

        # Save profile
        store.save_profile(session_id, profile)

        # Verify file exists and contains correct content
        profile_file = temp_dir / session_id / "profile.md"
        assert profile_file.exists()

        # Read and verify YAML frontmatter content
        import yaml

        loaded_profile = None
        with open(profile_file, encoding="utf-8") as f:
            content = f.read()
            # Extract YAML frontmatter between --- markers
            if content.startswith("---\n"):
                # Find the closing ---
                end_marker = content.find("\n---\n", 4)
                if end_marker > 0:
                    yaml_content = content[4:end_marker]
                    loaded_profile = yaml.safe_load(yaml_content)

        assert loaded_profile is not None, "Failed to parse YAML frontmatter"
        assert loaded_profile == profile

    def test_cleanup_old_sessions(self, store, sample_transcript, sample_metadata):
        """Test cleanup of old sessions."""
        # Create sessions with different ages
        old_session = "old-session"
        recent_session = "recent-session"

        # Save old session
        store.save(old_session, sample_transcript, sample_metadata)

        # Manually modify its timestamp to be old
        old_dir = store.base_dir / old_session
        old_time = (datetime.now(UTC) - timedelta(days=40)).timestamp()
        import os

        os.utime(old_dir, (old_time, old_time))

        # Save recent session
        store.save(recent_session, sample_transcript, sample_metadata)

        # Cleanup sessions older than 30 days
        removed = store.cleanup_old_sessions(days=30)

        assert removed == 1
        assert not store.exists(old_session)
        assert store.exists(recent_session)

    def test_cleanup_with_no_sessions(self, store):
        """Test cleanup when no sessions exist."""
        removed = store.cleanup_old_sessions()
        assert removed == 0

    def test_cleanup_invalid_days(self, store):
        """Test cleanup with invalid days parameter."""
        with pytest.raises(ValueError, match="days must be non-negative"):
            store.cleanup_old_sessions(days=-1)

    def test_empty_transcript_handling(self, store, sample_metadata):
        """Test handling of empty transcripts."""
        session_id = "test-empty"
        empty_transcript = []

        # Save and load empty transcript
        store.save(session_id, empty_transcript, sample_metadata)
        loaded_transcript, loaded_metadata = store.load(session_id)

        assert loaded_transcript == []
        assert loaded_metadata == sample_metadata

    def test_large_transcript(self, store, sample_metadata):
        """Test handling of large transcripts."""
        session_id = "test-large"

        # Create a large transcript
        large_transcript = [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} with some content to make it larger",
            }
            for i in range(1000)
        ]

        # Save and load
        store.save(session_id, large_transcript, sample_metadata)
        loaded_transcript, _ = store.load(session_id)

        assert len(loaded_transcript) == 1000
        assert loaded_transcript == large_transcript

    def test_concurrent_save_protection(self, store, sample_transcript, sample_metadata):
        """Test that atomic writes protect against concurrent saves."""
        session_id = "test-concurrent"

        # Save initial version
        store.save(session_id, sample_transcript, sample_metadata)

        # Simulate concurrent saves with different data
        transcript1 = [{"role": "user", "content": "version1"}]
        transcript2 = [{"role": "user", "content": "version2"}]

        # Both saves should complete without corruption
        store.save(session_id, transcript1, sample_metadata)
        store.save(session_id, transcript2, sample_metadata)

        # Final state should be consistent (last write wins)
        loaded_transcript, _ = store.load(session_id)
        assert loaded_transcript == transcript2

    def test_jsonl_format_with_empty_lines(self, store, sample_metadata, temp_dir):
        """Test that JSONL files with empty lines are handled correctly."""
        session_id = "test-empty-lines"

        # Create session directory and write JSONL with empty lines
        session_dir = temp_dir / session_id
        session_dir.mkdir()

        transcript_file = session_dir / "transcript.jsonl"
        with open(transcript_file, "w") as f:
            f.write('{"role": "user", "content": "line1"}\n')
            f.write("\n")  # Empty line
            f.write('{"role": "assistant", "content": "line2"}\n')
            f.write("\n")  # Empty line
            f.write("\n")  # Another empty line
            f.write('{"role": "user", "content": "line3"}\n')

        # Write metadata
        metadata_file = session_dir / "metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(sample_metadata, f)

        # Load should skip empty lines
        loaded_transcript, _ = store.load(session_id)

        assert len(loaded_transcript) == 3
        assert loaded_transcript[0]["content"] == "line1"
        assert loaded_transcript[1]["content"] == "line2"
        assert loaded_transcript[2]["content"] == "line3"
