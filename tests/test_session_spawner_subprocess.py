"""Tests for subprocess opt-in spawning in session_spawner.py.

Tests that spawn_sub_session() correctly routes to subprocess runner when:
1. subprocess=True parameter is passed
2. spawn_mode: subprocess is in merged config

Also verifies that without these flags, the in-process AmplifierSession path is taken.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Configure anyio for async tests (asyncio backend only)
pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    """Configure anyio to use asyncio backend only."""
    return "asyncio"


def _make_parent_session(config=None, session_id="parent-session-id"):
    """Create a minimal mock parent session for testing.

    Creates a mock with session_id, config, and coordinator attributes,
    as required by spawn_sub_session().
    """
    parent = MagicMock()
    parent.session_id = session_id
    parent.config = config or {"session": {"orchestrator": "loop-basic"}}
    parent.trace_id = session_id
    parent.loader = None

    # Mock coordinator
    coordinator = MagicMock()
    coordinator.get_capability.return_value = None  # Default: no capabilities
    coordinator.get.return_value = None  # No mounted modules by default
    coordinator.approval_system = MagicMock()
    coordinator.display_system = MagicMock()
    coordinator.cancellation = MagicMock()
    coordinator.cancellation.register_child = MagicMock()
    coordinator.cancellation.unregister_child = MagicMock()
    coordinator.mount = AsyncMock()
    coordinator.register_capability = MagicMock()
    parent.coordinator = coordinator

    return parent


def _make_subprocess_runner_module():
    """Create a fake amplifier_foundation.subprocess_runner module."""
    module = ModuleType("amplifier_foundation.subprocess_runner")
    module.run_session_in_subprocess = AsyncMock(return_value="subprocess output")
    return module


class TestSubprocessRouting:
    """Tests for subprocess opt-in parameter routing in spawn_sub_session."""

    async def test_subprocess_param_routes_to_subprocess(self, monkeypatch):
        """subprocess=True routes to run_session_in_subprocess, returns expected dict."""
        parent = _make_parent_session()

        # Create and inject fake subprocess_runner module
        fake_module = _make_subprocess_runner_module()
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        with (
            patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge,
        ):
            mock_merge.return_value = {"session": {}}

            from amplifier_app_cli.session_spawner import spawn_sub_session

            result = await spawn_sub_session(
                agent_name="some-agent",
                instruction="Do something",
                parent_session=parent,
                agent_configs={"some-agent": {}},
                sub_session_id="fixed-test-id",
                subprocess=True,
            )

        # Verify subprocess runner was called
        fake_module.run_session_in_subprocess.assert_called_once()
        call_kwargs = fake_module.run_session_in_subprocess.call_args
        assert call_kwargs.kwargs["config"] == {"session": {}}
        assert call_kwargs.kwargs["prompt"] == "Do something"
        assert call_kwargs.kwargs["parent_id"] == "parent-session-id"
        assert call_kwargs.kwargs["session_id"] == "fixed-test-id"
        assert isinstance(call_kwargs.kwargs["project_path"], str), (
            "project_path must be str, not Path object (would fail JSON serialization)"
        )

        # Verify correct return dict structure
        assert result["output"] == "subprocess output"
        assert result["session_id"] == "fixed-test-id"
        assert result["status"] == "success"
        assert result["turn_count"] == 1
        assert result["metadata"] == {}

    async def test_spawn_mode_config_routes_to_subprocess(self, monkeypatch):
        """spawn_mode: subprocess in merged config routes to subprocess runner."""
        parent = _make_parent_session()

        fake_module = _make_subprocess_runner_module()
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        with (
            patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge,
        ):
            # Config has spawn_mode: subprocess
            mock_merge.return_value = {"session": {}, "spawn_mode": "subprocess"}

            from amplifier_app_cli.session_spawner import spawn_sub_session

            result = await spawn_sub_session(
                agent_name="some-agent",
                instruction="Do something else",
                parent_session=parent,
                agent_configs={"some-agent": {}},
                sub_session_id="fixed-test-id-2",
                # subprocess=False is default -- routing via config only
            )

        # Verify subprocess runner was called due to config
        fake_module.run_session_in_subprocess.assert_called_once()
        call_kwargs = fake_module.run_session_in_subprocess.call_args
        assert call_kwargs.kwargs["session_id"] == "fixed-test-id-2"
        assert call_kwargs.kwargs["config"]["spawn_mode"] == "subprocess"

        assert result["status"] == "success"
        assert result["session_id"] == "fixed-test-id-2"
        assert result["output"] == "subprocess output"

    async def test_no_subprocess_flag_uses_inprocess(self, monkeypatch):
        """Without subprocess flag or spawn_mode config, AmplifierSession path is taken.

        Verifies run_session_in_subprocess is NOT called when subprocess=False
        and spawn_mode is not set in merged config.
        """
        parent = _make_parent_session()

        # Set up subprocess module mock to track calls
        fake_module = _make_subprocess_runner_module()
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        # Use a unique exception to prove AmplifierSession (in-process) path was taken
        class InProcessPathReached(Exception):
            pass

        with (
            patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge,
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                side_effect=InProcessPathReached("in-process path reached"),
            ),
        ):
            mock_merge.return_value = {"session": {}}  # No spawn_mode in config

            from amplifier_app_cli.session_spawner import spawn_sub_session

            with pytest.raises(InProcessPathReached):
                await spawn_sub_session(
                    agent_name="some-agent",
                    instruction="In-process task",
                    parent_session=parent,
                    agent_configs={"some-agent": {}},
                    sub_session_id="fixed-test-id-3",
                    subprocess=False,  # Explicitly no subprocess
                )

        # Verify subprocess runner was NOT called
        fake_module.run_session_in_subprocess.assert_not_called()
