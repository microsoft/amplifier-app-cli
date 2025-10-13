"""
Integration test for SessionStore in realistic usage scenarios.
"""

import tempfile
from datetime import UTC
from datetime import datetime
from pathlib import Path

from amplifier_app_cli.session_store import SessionStore


def test_realistic_session_workflow():
    """Test a realistic session workflow with persistence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(base_dir=Path(tmpdir))

        # Session 1: Initial conversation
        session_id = f"s-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"

        transcript = [
            {"role": "user", "content": "Hello, can you help me with Python?"},
            {"role": "assistant", "content": "Of course! I'd be happy to help you with Python."},
            {"role": "user", "content": "How do I read a file?"},
            {"role": "assistant", "content": "You can use `open()` with a context manager..."},
        ]

        metadata = {
            "created": datetime.now(UTC).isoformat(),
            "profile": "dev",
            "model": "claude-3-5-sonnet",
            "turn_count": 2,
            "tokens_used": 150,
        }

        profile = {
            "profile": {"name": "dev", "extends": "foundation", "model": "anthropic/claude-3-5-sonnet"},
            "session": {"orchestrator": "loop-streaming", "max_tokens": 100000},
            "tools": [{"module": "tool-filesystem"}, {"module": "tool-bash"}],
        }

        # Save everything
        store.save(session_id, transcript, metadata)
        store.save_profile(session_id, profile)

        # Verify session exists
        assert store.exists(session_id)
        assert session_id in store.list_sessions()

        # Simulate resuming the session later
        loaded_transcript, loaded_metadata = store.load(session_id)

        # Continue the conversation
        loaded_transcript.append({"role": "user", "content": "Can you show an example?"})
        loaded_transcript.append(
            {
                "role": "assistant",
                "content": "Here's an example:\n```python\nwith open('file.txt', 'r') as f:\n    content = f.read()\n```",
            }
        )

        # Update metadata
        loaded_metadata["turn_count"] = 3
        loaded_metadata["tokens_used"] = 250
        loaded_metadata["last_activity"] = datetime.now(UTC).isoformat()

        # Save updated session
        store.save(session_id, loaded_transcript, loaded_metadata)

        # Load again to verify updates
        final_transcript, final_metadata = store.load(session_id)

        assert len(final_transcript) == 6
        assert final_metadata["turn_count"] == 3
        assert final_metadata["tokens_used"] == 250

        # Test profile loading
        profile_file = Path(tmpdir) / session_id / "profile.toml"
        assert profile_file.exists()

        import tomllib

        with open(profile_file, "rb") as f:
            loaded_profile = tomllib.load(f)

        assert loaded_profile["profile"]["model"] == "anthropic/claude-3-5-sonnet"
        assert loaded_profile["session"]["max_tokens"] == 100000

        print(f"✅ Session {session_id} successfully created, updated, and loaded")


def test_multiple_sessions_management():
    """Test managing multiple sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(base_dir=Path(tmpdir))

        # Create multiple sessions
        session_ids = []
        for i in range(3):
            session_id = f"session-{i}"
            transcript = [{"role": "user", "content": f"Message {i}"}]
            metadata = {"index": i, "created": datetime.now(UTC).isoformat()}

            store.save(session_id, transcript, metadata)
            session_ids.append(session_id)

            # Small delay to ensure different modification times
            import time

            time.sleep(0.01)

        # List should return all sessions (newest first)
        listed = store.list_sessions()
        assert len(listed) == 3
        assert listed == ["session-2", "session-1", "session-0"]

        # Each session should be loadable
        for session_id in session_ids:
            transcript, metadata = store.load(session_id)
            expected_index = int(session_id.split("-")[1])
            assert metadata["index"] == expected_index

        print("✅ Multiple sessions managed successfully")
        return True


if __name__ == "__main__":
    test_realistic_session_workflow()
    test_multiple_sessions_management()
    print("\n🎉 All integration tests passed!")
