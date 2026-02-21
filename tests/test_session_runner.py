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


# --- Runtime context stamping ---


class TestStampRuntimeContext:
    """Tests for _stamp_runtime_context helper."""

    def test_stamps_into_tool_entries(self):
        """project_slug and project_dir are injected into tools config."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {
            "tools": [
                {"module": "tool-bash", "config": {}},
                {"module": "tool-filesystem", "config": {}},
            ]
        }
        _stamp_runtime_context(mount_plan, "my-project", "/home/user/my-project")

        for entry in mount_plan["tools"]:
            assert entry["config"]["project_slug"] == "my-project"
            assert entry["config"]["project_dir"] == "/home/user/my-project"

    def test_stamps_into_hook_entries(self):
        """project_slug and project_dir are injected into hooks config."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {
            "hooks": [
                {"module": "hooks-logging", "config": {}},
                {"module": "cxdb-session-storage", "config": {}},
            ]
        }
        _stamp_runtime_context(mount_plan, "my-project", "/home/user/my-project")

        for entry in mount_plan["hooks"]:
            assert entry["config"]["project_slug"] == "my-project"
            assert entry["config"]["project_dir"] == "/home/user/my-project"

    def test_creates_config_dict_if_absent(self):
        """Creates entry['config'] if the key is missing entirely."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {"tools": [{"module": "tool-bash"}]}
        _stamp_runtime_context(mount_plan, "slug", "/path")
        assert mount_plan["tools"][0]["config"]["project_slug"] == "slug"
        assert mount_plan["tools"][0]["config"]["project_dir"] == "/path"

    def test_does_not_overwrite_explicitly_set_values(self):
        """Pre-existing non-empty bundle YAML values are not overwritten."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {
            "tools": [
                {
                    "module": "cxdb-session-storage",
                    "config": {
                        "project_slug": "explicit-slug",
                        "project_dir": "/explicit/path",
                    },
                }
            ]
        }
        _stamp_runtime_context(mount_plan, "runtime-slug", "/runtime/path")

        assert mount_plan["tools"][0]["config"]["project_slug"] == "explicit-slug"
        assert mount_plan["tools"][0]["config"]["project_dir"] == "/explicit/path"

    def test_skips_empty_project_slug(self):
        """Empty project_slug is not stamped."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {"tools": [{"module": "tool-bash", "config": {}}]}
        _stamp_runtime_context(mount_plan, project_slug="", project_dir="/some/path")

        assert "project_slug" not in mount_plan["tools"][0]["config"]
        assert mount_plan["tools"][0]["config"]["project_dir"] == "/some/path"

    def test_skips_empty_project_dir(self):
        """Empty project_dir is not stamped."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {"tools": [{"module": "tool-bash", "config": {}}]}
        _stamp_runtime_context(mount_plan, project_slug="my-project", project_dir="")

        assert mount_plan["tools"][0]["config"]["project_slug"] == "my-project"
        assert "project_dir" not in mount_plan["tools"][0]["config"]

    def test_both_empty_is_noop(self):
        """Both empty values — no config keys are added at all."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {"tools": [{"module": "tool-bash", "config": {}}]}
        _stamp_runtime_context(mount_plan, project_slug="", project_dir="")

        assert mount_plan["tools"][0]["config"] == {}

    def test_handles_none_config_entry(self):
        """None config value is replaced with a new dict and stamped."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {"tools": [{"module": "tool-bash", "config": None}]}
        _stamp_runtime_context(mount_plan, "slug", "/path")
        assert mount_plan["tools"][0]["config"]["project_slug"] == "slug"

    def test_handles_missing_sections(self):
        """Mount plan without tools/hooks/providers sections does not raise."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {}
        _stamp_runtime_context(mount_plan, "slug", "/path")  # must not raise

    def test_handles_non_dict_entries_gracefully(self):
        """Non-dict entries in a section are skipped without error."""
        from amplifier_app_cli.session_runner import _stamp_runtime_context

        mount_plan = {
            "tools": ["not-a-dict", None, {"module": "tool-bash", "config": {}}]
        }
        _stamp_runtime_context(mount_plan, "slug", "/path")
        assert mount_plan["tools"][2]["config"]["project_slug"] == "slug"


