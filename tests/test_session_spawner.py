"""Tests for session_spawner module (spawn and resume).

Focus on testing error handling and persistence logic.
Full end-to-end integration testing done manually (see test report).
"""

import pytest
from amplifier_app_cli.session_spawner import resume_sub_session
from amplifier_app_cli.session_store import SessionStore

# Configure anyio for async tests (asyncio backend only)
pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    """Configure anyio to use asyncio backend only."""
    return "asyncio"


class TestResumeErrorHandling:
    """Test resume_sub_session() error handling."""

    async def test_resume_nonexistent_session_fails(self, tmp_path, monkeypatch):
        """Test that resuming non-existent session raises FileNotFoundError."""
        monkeypatch.setenv("HOME", str(tmp_path))

        with pytest.raises(FileNotFoundError, match="not found.*may have expired"):
            await resume_sub_session("fake-session-id", "Test instruction")

    async def test_resume_with_missing_config(self, tmp_path, monkeypatch):
        """Test that resume fails gracefully when metadata lacks config."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # Use default SessionStore (will use HOME/.amplifier/projects/...)
        store = SessionStore()

        # Manually create a session with incomplete metadata
        session_id = "test-incomplete"
        transcript = [{"role": "user", "content": "test"}]
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            # Missing "config" key - intentionally incomplete
        }

        store.save(session_id, transcript, metadata)

        # Try to resume - should fail with clear error
        with pytest.raises(RuntimeError, match="Corrupted session metadata.*Cannot reconstruct"):
            await resume_sub_session(session_id, "Follow-up")

    async def test_resume_with_corrupted_metadata_file(self, tmp_path, monkeypatch):
        """Test that resume handles corrupted metadata.json gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # Use default SessionStore (will resolve to HOME/.amplifier/projects/...)
        store = SessionStore()

        # Create valid session first
        session_id = "test-corrupt"
        transcript = [{"role": "user", "content": "test"}]
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "config": {"session": {"orchestrator": "loop-basic", "context": "context-simple"}},
        }
        store.save(session_id, transcript, metadata)

        # Verify session exists
        assert store.exists(session_id)

        # Corrupt metadata file directly (using store's resolved base_dir)
        metadata_file = store.base_dir / session_id / "metadata.json"
        assert metadata_file.exists(), "Metadata file should exist before corruption"
        with open(metadata_file, "w") as f:
            f.write("{ corrupt json")

        # Try to resume - SessionStore recovers but we detect missing config
        with pytest.raises(RuntimeError, match="Corrupted session metadata"):
            await resume_sub_session(session_id, "Follow-up")


class TestSessionStoreIntegration:
    """Test that SessionStore correctly handles sub-session data."""

    async def test_session_store_handles_hierarchical_ids(self, tmp_path):
        """Test that SessionStore works with hierarchical session IDs."""
        store = SessionStore(base_dir=tmp_path)

        # Use hierarchical ID format (parent-agent-uuid)
        session_id = "parent-123-zen-architect-abc456"
        transcript = [{"role": "user", "content": "Design cache"}]
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "agent_name": "zen-architect",
            "config": {"session": {"orchestrator": "loop-basic", "context": "context-simple"}},
        }

        # Save and verify
        store.save(session_id, transcript, metadata)
        assert store.exists(session_id)

        # Load and verify
        loaded_transcript, loaded_metadata = store.load(session_id)
        assert loaded_transcript == transcript
        assert loaded_metadata["session_id"] == session_id
        assert loaded_metadata["parent_id"] == "parent-123"

    async def test_session_store_preserves_full_config(self, tmp_path):
        """Test that SessionStore preserves complete merged config."""
        store = SessionStore(base_dir=tmp_path)

        session_id = "test-config-preservation"
        transcript = []
        metadata = {
            "session_id": session_id,
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"},
                "providers": [{"module": "provider-anthropic", "config": {"model": "claude-sonnet-4-5"}}],
                "tools": [{"module": "tool-filesystem"}],
                "hooks": [{"module": "hooks-logging"}],
            },
            "agent_overlay": {
                "description": "Test agent",
                "providers": [{"module": "provider-anthropic", "config": {"temperature": 0.7}}],
            },
        }

        # Save
        store.save(session_id, transcript, metadata)

        # Load and verify complete config preserved
        _, loaded_metadata = store.load(session_id)
        assert "config" in loaded_metadata
        assert "session" in loaded_metadata["config"]
        assert "providers" in loaded_metadata["config"]
        assert "agent_overlay" in loaded_metadata
