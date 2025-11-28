"""Tests for session_spawner module (spawn and resume).

Focus on testing error handling and persistence logic.
Full end-to-end integration testing done manually (see test report).
"""

import re

import pytest
from amplifier_app_cli.session_spawner import DEFAULT_PARENT_SPAN
from amplifier_app_cli.session_spawner import SPAN_HEX_LEN
from amplifier_app_cli.session_spawner import _generate_sub_session_id
from amplifier_app_cli.session_spawner import resume_sub_session
from amplifier_app_cli.session_store import SessionStore

# Configure anyio for async tests (asyncio backend only)
pytestmark = pytest.mark.anyio


def _mock_uuid(monkeypatch, hex_value: str = "f" * 32) -> None:
    class _FakeUUID:
        def __init__(self, value: str):
            self.hex = value

    # Patch uuid.uuid4 directly on the uuid module imported by session_spawner
    # String path "module.uuid.uuid4" doesn't work reliably with monkeypatch
    import uuid

    monkeypatch.setattr(uuid, "uuid4", lambda: _FakeUUID(hex_value))


@pytest.fixture(scope="module")
def anyio_backend():
    """Configure anyio to use asyncio backend only."""
    return "asyncio"


class TestGenerateSubSessionId:
    def _assert_format(self, result: str, expected_suffix: str, expected_parent: str, expected_child: str) -> None:
        # Format: {parent-span}-{child-span}_{agent-name}
        spans_part, suffix = result.rsplit("_", 1)
        parent_span, child_span = spans_part.split("-", 1)

        assert suffix == expected_suffix
        assert parent_span == expected_parent
        assert re.fullmatch(r"[0-9a-f]{16}", parent_span)
        assert re.fullmatch(r"[0-9a-f]{16}", child_span)
        assert child_span == expected_child

    def test_preserves_clean_prefix_with_parent_suffix(self, monkeypatch):
        hex_value = "a" * 32
        _mock_uuid(monkeypatch, hex_value)

        parent_session_id = "1111111111111111-2222222222222222_zen-architect"
        result = _generate_sub_session_id(
            "zen-architect",
            parent_session_id,
            None,
        )

        self._assert_format(
            result,
            expected_suffix="zen-architect",
            expected_parent="2222222222222222",
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_sanitizes_spaces_and_punctuation(self, monkeypatch):
        hex_value = "b" * 32
        _mock_uuid(monkeypatch, hex_value)

        result = _generate_sub_session_id(
            "Zen Architect!",
            "root-session",
            "1234567890abcdef1234567890abcdef",
        )

        self._assert_format(
            result,
            expected_suffix="zen-architect",
            expected_parent="90abcdef12345678",
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_removes_leading_dots(self, monkeypatch):
        hex_value = "c" * 32
        _mock_uuid(monkeypatch, hex_value)

        result = _generate_sub_session_id(
            ".hidden.agent",
            None,
            None,
        )

        self._assert_format(
            result,
            expected_suffix="hidden-agent",
            expected_parent=DEFAULT_PARENT_SPAN,
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_collapses_multiple_invalid_sequences(self, monkeypatch):
        hex_value = "d" * 32
        _mock_uuid(monkeypatch, hex_value)

        result = _generate_sub_session_id(
            "agent__###__core",
            None,
            None,
        )

        self._assert_format(
            result,
            expected_suffix="agent-core",
            expected_parent=DEFAULT_PARENT_SPAN,
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    @pytest.mark.parametrize("raw_name", ["", "   ", None])
    def test_defaults_to_agent_when_empty(self, raw_name, monkeypatch):
        hex_value = "e" * 32
        _mock_uuid(monkeypatch, hex_value)

        result = _generate_sub_session_id(raw_name, None, None)

        self._assert_format(
            result,
            expected_suffix="agent",
            expected_parent=DEFAULT_PARENT_SPAN,
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_preserves_long_names(self, monkeypatch):
        hex_value = "f" * 32
        _mock_uuid(monkeypatch, hex_value)

        long_name = "VeryVeryLongAgentNameWith123Numbers"
        parent_session_id = "aaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb_builder"
        result = _generate_sub_session_id(long_name, parent_session_id, None)

        # Agent name should be fully preserved (just lowercased)
        expected_suffix = "veryverylongagentnamewith123numbers"

        self._assert_format(
            result,
            expected_suffix=expected_suffix,
            expected_parent="bbbbbbbbbbbbbbbb",
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_uses_trace_id_when_parent_suffix_missing(self, monkeypatch):
        hex_value = "1" * 32
        _mock_uuid(monkeypatch, hex_value)

        trace_id = "0123456789abcdef0123456789abcdef"
        result = _generate_sub_session_id("observer", "root", trace_id)

        self._assert_format(
            result,
            expected_suffix="observer",
            expected_parent="89abcdef01234567",
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_falls_back_when_no_parent_info(self, monkeypatch):
        hex_value = "2" * 32
        _mock_uuid(monkeypatch, hex_value)

        result = _generate_sub_session_id("inspector", None, "invalid-trace")

        self._assert_format(
            result,
            expected_suffix="inspector",
            expected_parent=DEFAULT_PARENT_SPAN,
            expected_child=hex_value[:SPAN_HEX_LEN],
        )


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
