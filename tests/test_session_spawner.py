"""Tests for session_spawner module (spawn and resume).

Focus on testing error handling and persistence logic.
Full end-to-end integration testing done manually (see test report).
"""

import re

import pytest
from amplifier_app_cli.session_spawner import resume_sub_session
from amplifier_app_cli.session_store import SessionStore
from amplifier_foundation import generate_sub_session_id

# W3C Trace Context constants (these are private in amplifier_foundation.tracing)
SPAN_HEX_LEN = 16
DEFAULT_PARENT_SPAN = "0" * SPAN_HEX_LEN

# Configure anyio for async tests (asyncio backend only)
pytestmark = pytest.mark.anyio


def messages_equal_ignoring_timestamp(loaded: list, original: list) -> bool:
    """Compare message lists ignoring the timestamp field added by SessionStore."""
    if len(loaded) != len(original):
        return False
    for loaded_msg, orig_msg in zip(loaded, original, strict=True):
        loaded_without_ts = {k: v for k, v in loaded_msg.items() if k != "timestamp"}
        if loaded_without_ts != orig_msg:
            return False
    return True


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
    def _assert_format(
        self,
        result: str,
        expected_suffix: str,
        expected_parent: str,
        expected_child: str,
    ) -> None:
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
        result = generate_sub_session_id(
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

        result = generate_sub_session_id(
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

        result = generate_sub_session_id(
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

        result = generate_sub_session_id(
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

        result = generate_sub_session_id(raw_name, None, None)

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
        result = generate_sub_session_id(long_name, parent_session_id, None)

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
        result = generate_sub_session_id("observer", "root", trace_id)

        self._assert_format(
            result,
            expected_suffix="observer",
            expected_parent="89abcdef01234567",
            expected_child=hex_value[:SPAN_HEX_LEN],
        )

    def test_falls_back_when_no_parent_info(self, monkeypatch):
        hex_value = "2" * 32
        _mock_uuid(monkeypatch, hex_value)

        result = generate_sub_session_id("inspector", None, "invalid-trace")

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
        with pytest.raises(
            RuntimeError, match="Corrupted session metadata.*Cannot reconstruct"
        ):
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
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
        }
        store.save(session_id, transcript, metadata)

        # Verify session exists
        assert store.exists(session_id)

        # Corrupt metadata file directly (using store's resolved base_dir)
        metadata_file = store.base_dir / session_id / "metadata.json"
        assert metadata_file.exists(), "Metadata file should exist before corruption"
        with open(metadata_file, "w", encoding="utf-8") as f:
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
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
        }

        # Save and verify
        store.save(session_id, transcript, metadata)
        assert store.exists(session_id)

        # Load and verify (ignoring auto-added timestamps)
        loaded_transcript, loaded_metadata = store.load(session_id)
        assert messages_equal_ignoring_timestamp(loaded_transcript, transcript)
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
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "config": {"model": "claude-sonnet-4-5"},
                    }
                ],
                "tools": [{"module": "tool-filesystem"}],
                "hooks": [{"module": "hooks-logging"}],
            },
            "agent_overlay": {
                "description": "Test agent",
                "providers": [
                    {"module": "provider-anthropic", "config": {"temperature": 0.7}}
                ],
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


class TestCapabilityRegistration:
    """Test that capabilities are properly registered on spawned and resumed sessions.

    These tests verify the fix for issue #32: Sub-sessions don't inherit session.spawn capability.
    """

    async def test_metadata_includes_working_dir(self, tmp_path):
        """Test that spawn_sub_session saves working_dir in metadata."""
        store = SessionStore(base_dir=tmp_path)

        # Create metadata with working_dir (as spawn_sub_session now does)
        session_id = "test-working-dir-persistence"
        transcript = [{"role": "user", "content": "test"}]
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
            "working_dir": "/home/user/project",  # New field added by fix
            "self_delegation_depth": 0,
        }

        # Save and load
        store.save(session_id, transcript, metadata)
        _, loaded_metadata = store.load(session_id)

        # Verify working_dir is preserved
        assert "working_dir" in loaded_metadata
        assert loaded_metadata["working_dir"] == "/home/user/project"

    async def test_metadata_working_dir_can_be_none(self, tmp_path):
        """Test that working_dir can be None (root session without working_dir capability)."""
        store = SessionStore(base_dir=tmp_path)

        session_id = "test-working-dir-none"
        transcript = []
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
            "working_dir": None,  # Can be None if parent had no working_dir
        }

        # Save and load
        store.save(session_id, transcript, metadata)
        _, loaded_metadata = store.load(session_id)

        # Verify working_dir is preserved as None
        assert "working_dir" in loaded_metadata
        assert loaded_metadata["working_dir"] is None


class TestCapabilityRegistrationIntegration:
    """Integration tests that verify capability registration through actual resume.

    These tests require mocking AmplifierSession but verify the actual code path.
    """

    async def test_resume_registers_session_spawn_capability(
        self, tmp_path, monkeypatch
    ):
        """Test that resume_sub_session registers session.spawn capability.

        This is the core fix for issue #32.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        monkeypatch.setenv("HOME", str(tmp_path))

        # Create a valid session to resume
        store = SessionStore()
        session_id = "test-spawn-capability"
        transcript = [{"role": "user", "content": "initial"}]
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
            "working_dir": "/test/project",
            "self_delegation_depth": 1,
        }
        store.save(session_id, transcript, metadata)

        # Track what capabilities are registered
        registered_capabilities = {}

        def mock_register_capability(name, value):
            registered_capabilities[name] = value

        # Create mock coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.register_capability = mock_register_capability
        mock_coordinator.get_capability = MagicMock(return_value=None)
        mock_coordinator.get = MagicMock(return_value=None)
        mock_coordinator.mount = AsyncMock()

        # Create mock session
        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator
        mock_session.initialize = AsyncMock()
        mock_session.execute = AsyncMock(return_value="test response")
        mock_session.cleanup = AsyncMock()

        # Patch at correct locations - imports are inside the function from their source modules
        with patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=mock_session,
        ):
            with patch("amplifier_app_cli.ui.CLIApprovalSystem"):
                with patch("amplifier_app_cli.ui.CLIDisplaySystem"):
                    with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                        try:
                            await resume_sub_session(
                                session_id, "follow-up instruction"
                            )
                        except Exception:
                            # May fail on other parts, but we can still check registrations
                            pass

        # Verify critical capabilities were registered
        assert "session.spawn" in registered_capabilities, (
            "session.spawn capability must be registered on resumed sessions (issue #32 fix)"
        )
        assert "session.resume" in registered_capabilities, (
            "session.resume capability must be registered on resumed sessions"
        )
        assert callable(registered_capabilities["session.spawn"]), (
            "session.spawn must be a callable"
        )
        assert callable(registered_capabilities["session.resume"]), (
            "session.resume must be a callable"
        )

    async def test_resume_restores_working_dir_capability(self, tmp_path, monkeypatch):
        """Test that resume_sub_session restores session.working_dir from metadata."""
        from unittest.mock import AsyncMock, MagicMock, patch

        monkeypatch.setenv("HOME", str(tmp_path))

        # Create session with working_dir
        store = SessionStore()
        session_id = "test-working-dir-capability"
        transcript = []
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
            "working_dir": "/test/project/path",
            "self_delegation_depth": 0,
        }
        store.save(session_id, transcript, metadata)

        # Track registered capabilities
        registered_capabilities = {}

        def mock_register_capability(name, value):
            registered_capabilities[name] = value

        mock_coordinator = MagicMock()
        mock_coordinator.register_capability = mock_register_capability
        mock_coordinator.get_capability = MagicMock(return_value=None)
        mock_coordinator.get = MagicMock(return_value=None)
        mock_coordinator.mount = AsyncMock()

        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator
        mock_session.initialize = AsyncMock()
        mock_session.execute = AsyncMock(return_value="response")
        mock_session.cleanup = AsyncMock()

        with patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=mock_session,
        ):
            with patch("amplifier_app_cli.ui.CLIApprovalSystem"):
                with patch("amplifier_app_cli.ui.CLIDisplaySystem"):
                    with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                        try:
                            await resume_sub_session(session_id, "test")
                        except Exception:
                            pass

        # Verify working_dir was restored
        assert "session.working_dir" in registered_capabilities, (
            "session.working_dir capability must be restored on resume"
        )
        assert registered_capabilities["session.working_dir"] == "/test/project/path"

    async def test_resume_without_working_dir_skips_registration(
        self, tmp_path, monkeypatch
    ):
        """Test that resume_sub_session handles missing working_dir gracefully."""
        from unittest.mock import AsyncMock, MagicMock, patch

        monkeypatch.setenv("HOME", str(tmp_path))

        # Create session WITHOUT working_dir
        store = SessionStore()
        session_id = "test-no-working-dir"
        transcript = []
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
            # working_dir intentionally omitted
            "self_delegation_depth": 0,
        }
        store.save(session_id, transcript, metadata)

        registered_capabilities = {}

        def mock_register_capability(name, value):
            registered_capabilities[name] = value

        mock_coordinator = MagicMock()
        mock_coordinator.register_capability = mock_register_capability
        mock_coordinator.get_capability = MagicMock(return_value=None)
        mock_coordinator.get = MagicMock(return_value=None)
        mock_coordinator.mount = AsyncMock()

        mock_session = MagicMock()
        mock_session.coordinator = mock_coordinator
        mock_session.initialize = AsyncMock()
        mock_session.execute = AsyncMock(return_value="response")
        mock_session.cleanup = AsyncMock()

        with patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=mock_session,
        ):
            with patch("amplifier_app_cli.ui.CLIApprovalSystem"):
                with patch("amplifier_app_cli.ui.CLIDisplaySystem"):
                    with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                        try:
                            await resume_sub_session(session_id, "test")
                        except Exception:
                            pass

        # Verify session.working_dir was NOT registered (no value to restore)
        assert "session.working_dir" not in registered_capabilities, (
            "session.working_dir should not be registered when metadata has no working_dir"
        )

        # But session.spawn/resume should still be registered
        assert "session.spawn" in registered_capabilities
        assert "session.resume" in registered_capabilities


class TestSpawnEnrichment:
    """Test that spawn/resume results include orchestrator completion data.

    The spawn enrichment fix captures orchestrator:complete hook data and
    surfaces status, turn_count, and metadata in the spawn/resume return dict.
    """

    async def test_spawn_result_includes_status_and_turn_count(
        self, tmp_path, monkeypatch
    ):
        """Test that spawn_sub_session returns status and turn_count from orchestrator:complete."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from amplifier_app_cli.session_spawner import spawn_sub_session

        monkeypatch.setenv("HOME", str(tmp_path))

        # --- parent session mock ---
        parent_coordinator = MagicMock()
        parent_coordinator.get.return_value = None
        parent_coordinator.get_capability.return_value = None
        parent_coordinator.display_system = MagicMock()
        parent_coordinator.cancellation = MagicMock()
        parent_coordinator.cancellation.register_child = MagicMock()
        parent_coordinator.cancellation.unregister_child = MagicMock()

        parent_session = MagicMock()
        parent_session.coordinator = parent_coordinator
        parent_session.config = {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        }
        parent_session.session_id = "parent-123"
        parent_session.trace_id = "trace-abc"
        parent_session.loader = None

        # --- child session mock ---
        # We need the hooks register to actually invoke the callback during execute
        captured_handler = None

        class FakeHooks:
            def register(self, event, handler, priority=0, name=None):
                nonlocal captured_handler
                captured_handler = handler

                def _unregister():
                    nonlocal captured_handler
                    captured_handler = None

                return _unregister

            async def emit(self, event, data):
                pass

        fake_hooks = FakeHooks()

        child_coordinator = MagicMock()
        child_coordinator.register_capability = MagicMock()
        child_coordinator.get_capability.return_value = None
        child_coordinator.display_system = MagicMock()

        # Return fake_hooks for coordinator.get("hooks")
        def child_get(name):
            if name == "hooks":
                return fake_hooks
            if name == "context":
                ctx = AsyncMock()
                ctx.get_messages = AsyncMock(return_value=[])
                ctx.add_message = AsyncMock()
                return ctx
            return None

        child_coordinator.get = child_get
        child_coordinator.mount = AsyncMock()

        async def mock_execute(instruction):
            # Simulate orchestrator emitting orchestrator:complete during execute
            if captured_handler:
                await captured_handler(
                    "orchestrator:complete",
                    {
                        "status": "success",
                        "turn_count": 5,
                        "metadata": {"orchestrator": "loop-basic"},
                    },
                )
            return "agent response"

        child_session = MagicMock()
        child_session.coordinator = child_coordinator
        child_session.initialize = AsyncMock()
        child_session.execute = AsyncMock(side_effect=mock_execute)
        child_session.cleanup = AsyncMock()
        child_session.session_id = "child-001"

        agent_configs = {
            "test-agent": {
                "description": "A test agent",
            },
        }

        with patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=child_session,
        ):
            with patch(
                "amplifier_app_cli.session_spawner.generate_sub_session_id",
                return_value="child-001",
            ):
                with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                    with patch("amplifier_app_cli.session_store.SessionStore.save"):
                        result = await spawn_sub_session(
                            agent_name="test-agent",
                            instruction="Do something",
                            parent_session=parent_session,
                            agent_configs=agent_configs,
                        )

        # Verify enriched fields from orchestrator:complete
        assert result["output"] == "agent response"
        assert result["session_id"] == "child-001"
        assert result["status"] == "success"
        assert result["turn_count"] == 5
        assert result["metadata"] == {"orchestrator": "loop-basic"}

    async def test_spawn_result_defaults_when_no_hook_fires(
        self, tmp_path, monkeypatch
    ):
        """Test that spawn returns sensible defaults when orchestrator:complete never fires."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from amplifier_app_cli.session_spawner import spawn_sub_session

        monkeypatch.setenv("HOME", str(tmp_path))

        # --- parent session mock ---
        parent_coordinator = MagicMock()
        parent_coordinator.get.return_value = None
        parent_coordinator.get_capability.return_value = None
        parent_coordinator.display_system = MagicMock()
        parent_coordinator.cancellation = MagicMock()
        parent_coordinator.cancellation.register_child = MagicMock()
        parent_coordinator.cancellation.unregister_child = MagicMock()

        parent_session = MagicMock()
        parent_session.coordinator = parent_coordinator
        parent_session.config = {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        }
        parent_session.session_id = "parent-123"
        parent_session.trace_id = "trace-abc"
        parent_session.loader = None

        # --- child session mock (hooks.register is never called back) ---
        class FakeHooks:
            def register(self, event, handler, priority=0, name=None):
                # Don't store handler - orchestrator:complete never fires
                def _unregister():
                    pass

                return _unregister

            async def emit(self, event, data):
                pass

        fake_hooks = FakeHooks()

        child_coordinator = MagicMock()
        child_coordinator.register_capability = MagicMock()
        child_coordinator.get_capability.return_value = None
        child_coordinator.display_system = MagicMock()

        def child_get(name):
            if name == "hooks":
                return fake_hooks
            if name == "context":
                ctx = AsyncMock()
                ctx.get_messages = AsyncMock(return_value=[])
                ctx.add_message = AsyncMock()
                return ctx
            return None

        child_coordinator.get = child_get
        child_coordinator.mount = AsyncMock()

        child_session = MagicMock()
        child_session.coordinator = child_coordinator
        child_session.initialize = AsyncMock()
        child_session.execute = AsyncMock(return_value="agent response")
        child_session.cleanup = AsyncMock()
        child_session.session_id = "child-002"

        agent_configs = {
            "test-agent": {"description": "A test agent"},
        }

        with patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=child_session,
        ):
            with patch(
                "amplifier_app_cli.session_spawner.generate_sub_session_id",
                return_value="child-002",
            ):
                with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                    with patch("amplifier_app_cli.session_store.SessionStore.save"):
                        result = await spawn_sub_session(
                            agent_name="test-agent",
                            instruction="Do something",
                            parent_session=parent_session,
                            agent_configs=agent_configs,
                        )

        # Should still have output and session_id
        assert result["output"] == "agent response"
        assert result["session_id"] == "child-002"
        # Defaults when orchestrator:complete never fires
        assert result["status"] == "success"
        assert result["turn_count"] == 1
        assert result["metadata"] == {}

    async def test_resume_result_includes_status_and_turn_count(
        self, tmp_path, monkeypatch
    ):
        """Test that resume_sub_session returns status and turn_count from orchestrator:complete."""
        from unittest.mock import AsyncMock, MagicMock, patch

        monkeypatch.setenv("HOME", str(tmp_path))

        # Create a valid session to resume
        store = SessionStore()
        session_id = "test-enriched-resume"
        transcript = [{"role": "user", "content": "initial"}]
        metadata = {
            "session_id": session_id,
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {"orchestrator": "loop-basic", "context": "context-simple"}
            },
            "working_dir": "/test/project",
            "self_delegation_depth": 0,
        }
        store.save(session_id, transcript, metadata)

        # --- child session mock with hook capture ---
        captured_handler = None

        class FakeHooks:
            def register(self, event, handler, priority=0, name=None):
                nonlocal captured_handler
                captured_handler = handler

                def _unregister():
                    nonlocal captured_handler
                    captured_handler = None

                return _unregister

            async def emit(self, event, data):
                pass

        fake_hooks = FakeHooks()

        child_coordinator = MagicMock()
        child_coordinator.register_capability = MagicMock()
        child_coordinator.get_capability.return_value = None

        def child_get(name):
            if name == "hooks":
                return fake_hooks
            if name == "context":
                ctx = AsyncMock()
                ctx.get_messages = AsyncMock(
                    return_value=[
                        {"role": "user", "content": "initial"},
                        {"role": "assistant", "content": "response"},
                        {"role": "user", "content": "follow-up"},
                    ]
                )
                ctx.add_message = AsyncMock()
                return ctx
            return None

        child_coordinator.get = child_get
        child_coordinator.mount = AsyncMock()

        async def mock_execute(instruction):
            if captured_handler:
                await captured_handler(
                    "orchestrator:complete",
                    {
                        "status": "incomplete",
                        "turn_count": 3,
                        "metadata": {"reason": "max_turns"},
                    },
                )
            return "resumed response"

        child_session = MagicMock()
        child_session.coordinator = child_coordinator
        child_session.initialize = AsyncMock()
        child_session.execute = AsyncMock(side_effect=mock_execute)
        child_session.cleanup = AsyncMock()

        with patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=child_session,
        ):
            with patch("amplifier_app_cli.ui.CLIApprovalSystem"):
                with patch("amplifier_app_cli.ui.CLIDisplaySystem"):
                    with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                        result = await resume_sub_session(
                            session_id, "follow-up instruction"
                        )

        assert result["output"] == "resumed response"
        assert result["session_id"] == session_id
        assert result["status"] == "incomplete"
        assert result["turn_count"] == 3
        assert result["metadata"] == {"reason": "max_turns"}
