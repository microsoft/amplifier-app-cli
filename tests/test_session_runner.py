"""Tests for session_runner module - unified session initialization."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.session_runner import (
    InitializedSession,
    SessionConfig,
    create_initialized_session,
)

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

_MODULE = "amplifier_app_cli.session_runner"


def _make_session_config(session_config_initial=None, **kwargs):
    """Return a minimal SessionConfig with a PreparedBundle mock."""
    prepared_bundle = MagicMock()
    prepared_bundle.mount_plan = {"providers": [], "tools": []}
    return SessionConfig(
        config=dict(session_config_initial or {}),
        search_paths=[],
        verbose=False,
        prepared_bundle=prepared_bundle,
        bundle_name="test-bundle",
        **kwargs,
    )


def _make_mock_session(initial_session_config=None):
    """Return a mock AmplifierSession with a real dict for session.config.

    Using a real dict means we can assert on its contents after the call.
    """
    mock_sess = MagicMock()
    mock_sess.config = dict(initial_session_config or {})
    mock_sess.coordinator.get_capability.return_value = None
    mock_sess.coordinator.register_capability = MagicMock()
    mock_sess.coordinator.get.return_value = None
    return mock_sess


# ---------------------------------------------------------------------------
# SessionConfig tests
# ---------------------------------------------------------------------------


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

    def test_default_values(self):
        """Test default values are set correctly."""
        config = SessionConfig(
            config={"key": "value"},
            search_paths=[Path("/test")],
            verbose=True,
        )
        assert config.session_id is None
        assert config.bundle_name == "unknown"
        assert config.initial_transcript is None
        assert config.prepared_bundle is None
        assert config.output_format == "text"


# ---------------------------------------------------------------------------
# InitializedSession tests
# ---------------------------------------------------------------------------


class TestInitializedSession:
    """Test InitializedSession container."""

    @pytest.mark.anyio
    async def test_cleanup_calls_session_cleanup(self):
        """Cleanup properly disposes the session."""
        mock_session = AsyncMock()
        mock_config = SessionConfig(config={}, search_paths=[], verbose=False)

        initialized = InitializedSession(
            session=mock_session,
            session_id="test-123",
            config=mock_config,
            store=MagicMock(),
        )

        await initialized.cleanup()
        mock_session.cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# Post-session metadata stamping tests
# ---------------------------------------------------------------------------


class TestPostSessionMetadataStamping:
    """Tests for the two-phase root-level metadata stamping.

    create_initialized_session writes root-level metadata into config.config
    BEFORE session creation (to propagate via deep-merge to child sessions)
    and then into session.config AFTER creation (belt-and-suspenders: the
    foundation layer may copy config.config into a fresh dict when building
    the coordinator, so session.config and config.config are not guaranteed
    to be the same object — hooks read coordinator.config, so we must ensure
    the values are present there too).

    Bugs fixed in this batch:
    - project_dir / project_name guards checked ``config.config`` (always
      False after the pre-session write) instead of ``session.config``.
    - ``cwd`` was only defined inside the try block, causing NameError in
      the post-session block when Path.cwd() raised OSError.
    """

    @pytest.mark.anyio
    async def test_project_dir_written_to_session_config(self):
        """project_dir is written to session.config after session creation.

        Before the fix the guard was:
            if "project_dir" not in config.config:   # always False after pre-session write
        Because config.config already has project_dir from the pre-session block.
        The fix checks session.config instead, which starts empty when the
        foundation layer copies the config dict into a fresh coordinator dict.
        """
        mock_sess = _make_mock_session()  # session.config starts empty
        cfg = _make_session_config()
        console = MagicMock()

        with (
            patch(f"{_MODULE}._create_bundle_session", new_callable=AsyncMock, return_value=mock_sess),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.project_utils.get_project_slug", return_value="test-slug"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            result = await create_initialized_session(cfg, console)

        assert "project_dir" in result.session.config
        # project_dir should be a non-empty path (CWD)
        assert result.session.config["project_dir"] != ""

    @pytest.mark.anyio
    async def test_project_name_written_to_session_config(self):
        """project_name is written to session.config after session creation.

        Same copy-paste bug as project_dir — the guard now checks session.config.
        """
        mock_sess = _make_mock_session()
        cfg = _make_session_config()
        console = MagicMock()

        with (
            patch(f"{_MODULE}._create_bundle_session", new_callable=AsyncMock, return_value=mock_sess),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.project_utils.get_project_slug", return_value="test-slug"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            result = await create_initialized_session(cfg, console)

        assert "project_name" in result.session.config
        assert result.session.config["project_name"] != ""

    @pytest.mark.anyio
    async def test_root_session_id_stamped_for_root_session(self):
        """root_session_id in session.config equals the generated session_id."""
        mock_sess = _make_mock_session()
        cfg = _make_session_config()
        console = MagicMock()

        with (
            patch(f"{_MODULE}._create_bundle_session", new_callable=AsyncMock, return_value=mock_sess),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.project_utils.get_project_slug", return_value="test-slug"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            result = await create_initialized_session(cfg, console)

        assert result.session.config["root_session_id"] == result.session_id

    @pytest.mark.anyio
    async def test_child_session_root_session_id_not_overwritten(self):
        """Child sessions: inherited root_session_id in session.config is preserved.

        When a child session is spawned, the coordinator config inherits
        root_session_id from the parent via config deep-merge.  The
        post-session guard must not overwrite this value with the child's
        own session_id.
        """
        parent_root_id = "parent-root-aaaa-bbbb-cccc"
        # Simulate coordinator config that already has the parent's root_session_id
        mock_sess = _make_mock_session(
            initial_session_config={"root_session_id": parent_root_id}
        )
        # config.config also carries it (from deep-merge during spawn_sub_session)
        cfg = _make_session_config(
            session_config_initial={"root_session_id": parent_root_id},
            session_id="child-abc-def",
        )
        console = MagicMock()

        with (
            patch(f"{_MODULE}._create_bundle_session", new_callable=AsyncMock, return_value=mock_sess),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.project_utils.get_project_slug", return_value="test-slug"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            result = await create_initialized_session(cfg, console)

        # Must keep the parent's root_session_id, NOT overwrite with child's session_id
        assert result.session.config["root_session_id"] == parent_root_id

    @pytest.mark.anyio
    async def test_child_session_project_dir_not_overwritten(self):
        """Child sessions: project_dir inherited in session.config is not overwritten."""
        parent_project_dir = "/home/user/parent-project"
        mock_sess = _make_mock_session(
            initial_session_config={"project_dir": parent_project_dir}
        )
        cfg = _make_session_config(
            session_config_initial={"project_dir": parent_project_dir}
        )
        console = MagicMock()

        with (
            patch(f"{_MODULE}._create_bundle_session", new_callable=AsyncMock, return_value=mock_sess),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.project_utils.get_project_slug", return_value="test-slug"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            result = await create_initialized_session(cfg, console)

        assert result.session.config["project_dir"] == parent_project_dir

    @pytest.mark.anyio
    async def test_cwd_empty_fallback_on_os_error_no_name_error(self):
        """cwd = '' before try block prevents NameError when Path.cwd() raises OSError.

        Before the fix, cwd was only defined inside the try block.  If OSError
        fired (e.g. sandboxed environment with no working directory), the
        post-session ``session.config["working_dir"] = cwd`` line would raise
        NameError.  The fix initialises cwd = "" before the try block.
        """
        mock_sess = _make_mock_session()
        cfg = _make_session_config()
        console = MagicMock()

        with (
            patch(f"{_MODULE}._create_bundle_session", new_callable=AsyncMock, return_value=mock_sess),
            patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
            # Simulate no working directory available
            patch(f"{_MODULE}.Path") as mock_path_cls,
        ):
            mock_path_cls.cwd.side_effect = OSError("no cwd in sandbox")
            # Must NOT raise NameError — cwd falls back to ""
            result = await create_initialized_session(cfg, console)

        # working_dir is the empty-string fallback
        assert result.session.config.get("working_dir") == ""
