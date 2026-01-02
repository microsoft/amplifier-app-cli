"""Tests for session_runner module - unified session initialization."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.session_runner import InitializedSession, SessionConfig


class TestSessionConfig:
    """Test SessionConfig dataclass properties."""

    def test_is_resume_false_when_no_transcript(self):
        """New session has is_resume=False."""
        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
        )
        assert config.is_resume is False

    def test_is_resume_true_when_transcript_provided(self):
        """Resume session has is_resume=True."""
        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            initial_transcript=[{"role": "user", "content": "test"}],
        )
        assert config.is_resume is True

    def test_is_resume_true_with_empty_transcript(self):
        """Empty list still counts as resume (edge case)."""
        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            initial_transcript=[],
        )
        # Empty list is truthy for is_resume check (list exists)
        # This is intentional - empty transcript still means resume mode
        assert config.is_resume is True

    def test_is_bundle_mode_false_without_bundle(self):
        """Profile mode when no prepared_bundle."""
        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            profile_name="dev",
        )
        assert config.is_bundle_mode is False

    def test_is_bundle_mode_true_with_bundle_and_prefix(self):
        """Bundle mode requires both bundle: prefix AND prepared_bundle."""
        mock_bundle = MagicMock()

        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            profile_name="bundle:foundation",
            prepared_bundle=mock_bundle,
        )
        assert config.is_bundle_mode is True

    def test_is_bundle_mode_false_without_prefix(self):
        """Not bundle mode if profile_name lacks 'bundle:' prefix."""
        mock_bundle = MagicMock()

        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            profile_name="foundation",  # Missing bundle: prefix
            prepared_bundle=mock_bundle,
        )
        assert config.is_bundle_mode is False

    def test_is_bundle_mode_false_without_prepared_bundle(self):
        """Not bundle mode if prepared_bundle is None."""
        config = SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            profile_name="bundle:foundation",  # Has prefix
            prepared_bundle=None,  # But no bundle
        )
        assert config.is_bundle_mode is False

    def test_default_values(self):
        """Test default values are set correctly."""
        config = SessionConfig(
            config={"key": "value"},
            search_paths=[Path("/test")],
            verbose=True,
        )
        assert config.session_id is None
        assert config.profile_name == "unknown"
        assert config.initial_transcript is None
        assert config.prepared_bundle is None
        assert config.output_format == "text"


class TestInitializedSession:
    """Test InitializedSession container."""

    @pytest.mark.anyio
    async def test_cleanup_calls_session_cleanup(self):
        """cleanup() delegates to session.cleanup()."""
        mock_session = MagicMock()
        mock_session.cleanup = AsyncMock()

        config = SessionConfig(config={}, search_paths=[], verbose=False)
        initialized = InitializedSession(
            session=mock_session,
            session_id="test-123",
            config=config,
        )

        await initialized.cleanup()
        mock_session.cleanup.assert_called_once()

    def test_session_store_has_default(self):
        """SessionStore is created by default."""
        mock_session = MagicMock()
        config = SessionConfig(config={}, search_paths=[], verbose=False)

        initialized = InitializedSession(
            session=mock_session,
            session_id="test-123",
            config=config,
        )

        # Should have a store instance (default_factory)
        assert initialized.store is not None
