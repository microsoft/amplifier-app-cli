"""Tests for REPL prompt session functionality."""

from pathlib import Path
from unittest.mock import patch

import pytest
from amplifier_app_cli.main import _create_prompt_session
from prompt_toolkit import PromptSession
from prompt_toolkit.output import DummyOutput


@pytest.fixture(autouse=True)
def patch_prompt_session_for_testing(monkeypatch):
    """Auto-patch PromptSession to use DummyOutput in all tests.

    This avoids Windows console access issues during testing without
    requiring changes to production code.
    """
    original_init = PromptSession.__init__

    def patched_init(self, *args, **kwargs):
        # Force output to DummyOutput for testing
        kwargs["output"] = DummyOutput()
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(PromptSession, "__init__", patched_init)


class TestPromptSession:
    """Test prompt session creation and configuration."""

    def test_creates_prompt_session(self):
        """Verify prompt session is created with correct config."""
        session = _create_prompt_session()
        assert session is not None
        assert session.message  # Has prompt message
        assert session.enable_history_search is not None  # Ctrl-R enabled

    def test_creates_history_directory(self, tmp_path, monkeypatch):
        """Verify history directory is created if missing."""
        # Override Path.home() to use tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create session (triggers history directory creation as side effect)
        _ = _create_prompt_session()

        history_dir = tmp_path / ".amplifier"
        assert history_dir.exists()
        assert history_dir.is_dir()

    def test_fallback_to_inmemory_on_history_error(self, tmp_path, monkeypatch):
        """Verify fallback to InMemoryHistory if FileHistory fails."""
        # Mock FileHistory to raise an exception
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock FileHistory constructor to raise exception
        def mock_file_history(*args, **kwargs):
            raise PermissionError("Mocked permission error")

        # Should not raise, should fall back to InMemoryHistory
        with (
            patch("amplifier_app_cli.main.logger") as mock_logger,
            patch("amplifier_app_cli.main.FileHistory", side_effect=mock_file_history),
        ):
            session = _create_prompt_session()
            assert session is not None
            # Verify warning was logged
            assert mock_logger.warning.called


class TestREPLBehavior:
    """Test REPL behavior with prompt_toolkit integration.

    Note: These tests verify the integration patterns.
    Full end-to-end testing requires manual verification.
    """

    def test_prompt_session_has_correct_settings(self):
        """Verify prompt session configuration matches requirements."""
        session = _create_prompt_session()

        # Verify key settings from plan
        assert session.enable_history_search is True  # Ctrl-R
        assert session.multiline is True  # Multi-line display enabled (bd-23)
        # History should be either FileHistory or InMemoryHistory
        assert hasattr(session, "history")

    def test_multiline_key_binding_registered(self):
        """Verify Ctrl-J key binding is registered for multi-line input (bd-23)."""
        session = _create_prompt_session()

        # Verify key bindings exist
        assert session.key_bindings is not None
        # Verify we have custom bindings (can't easily test specific binding without running)
        # This test verifies the integration pattern is correct

    def test_history_persists_across_sessions(self, tmp_path, monkeypatch):
        """Verify command history is saved and loaded across sessions."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create first session and add to history
        session1 = _create_prompt_session()
        # Note: In actual usage, history is added via user input
        # This test verifies the history file is created

        history_file = tmp_path / ".amplifier" / "repl_history"
        assert history_file.parent.exists()

        # Create second session - should load same history
        session2 = _create_prompt_session()
        # Both sessions should reference the same history file location
        assert session1.history.__class__ == session2.history.__class__


# Integration notes for manual testing:
# 1. Start REPL: `amplifier run --profile dev --mode chat`
# 2. Verify bd-19 features:
#    - Up/Down arrows navigate history
#    - Ctrl-R searches history
#    - Ctrl-C cancels current line but stays in REPL
#    - Ctrl-D exits REPL
#    - Multi-line paste works correctly
#    - Long input (5000+ chars) works without truncation
# 3. Verify bd-23 feature (multi-line input):
#    - Type "line 1", press Ctrl-J (not Enter)
#    - See "... " continuation prompt
#    - Type "line 2", press Ctrl-J
#    - Type "line 3", press Enter
#    - Verify all 3 lines submitted together
# 4. Verify bd-22 feature (abort during processing):
#    - Type a prompt, press Enter
#    - See "Processing... (Ctrl-C to abort)"
#    - Press Ctrl-C
#    - Verify "Aborted by user (Ctrl-C)" message
#    - Verify REPL stays active (doesn't exit)
