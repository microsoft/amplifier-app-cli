"""Tests for subprocess opt-in spawning in session_spawner.py.

Tests that spawn_sub_session() correctly routes to subprocess runner when:
1. use_subprocess=True parameter is passed
2. spawn_mode: subprocess is in merged config

Also verifies that without these flags, the in-process AmplifierSession path is taken.
Also verifies spawn_mode is stripped from child config before passing to subprocess runner.
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
        """use_subprocess=True routes to run_session_in_subprocess, returns expected dict."""
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
                use_subprocess=True,
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
        """spawn_mode: subprocess in merged config routes to subprocess runner.

        Also verifies spawn_mode is stripped from the config passed to the runner.
        """
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
                # use_subprocess=False is default -- routing via config only
            )

        # Verify subprocess runner was called due to config
        fake_module.run_session_in_subprocess.assert_called_once()
        call_kwargs = fake_module.run_session_in_subprocess.call_args
        assert call_kwargs.kwargs["session_id"] == "fixed-test-id-2"
        # spawn_mode must be stripped from child config before passing to runner
        assert "spawn_mode" not in call_kwargs.kwargs["config"]

        assert result["status"] == "success"
        assert result["session_id"] == "fixed-test-id-2"
        assert result["output"] == "subprocess output"

    async def test_no_subprocess_flag_uses_inprocess(self, monkeypatch):
        """Without use_subprocess flag or spawn_mode config, AmplifierSession path is taken.

        Verifies run_session_in_subprocess is NOT called when use_subprocess=False
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
                    use_subprocess=False,  # Explicitly no subprocess
                )

        # Verify subprocess runner was NOT called
        fake_module.run_session_in_subprocess.assert_not_called()

    async def test_spawn_mode_stripped_from_child_config(self, monkeypatch):
        """spawn_mode is stripped from config passed to run_session_in_subprocess.

        When merged_config contains spawn_mode, it must not be forwarded to the
        subprocess runner -- otherwise the child would re-enter subprocess mode
        recursively (spawn_mode cascade bug, finding #7).
        """
        parent = _make_parent_session()

        fake_module = _make_subprocess_runner_module()
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        with (
            patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge,
        ):
            # Config has spawn_mode: subprocess (simulates cascade scenario)
            mock_merge.return_value = {
                "session": {"orchestrator": "loop-basic"},
                "spawn_mode": "subprocess",
                "other_key": "other_value",
            }

            from amplifier_app_cli.session_spawner import spawn_sub_session

            await spawn_sub_session(
                agent_name="some-agent",
                instruction="Cascade test",
                parent_session=parent,
                agent_configs={"some-agent": {}},
                sub_session_id="cascade-test-id",
            )

        # Verify subprocess runner was called
        fake_module.run_session_in_subprocess.assert_called_once()
        call_kwargs = fake_module.run_session_in_subprocess.call_args
        passed_config = call_kwargs.kwargs["config"]

        # spawn_mode must NOT be in the config passed to the child
        assert "spawn_mode" not in passed_config, (
            f"spawn_mode should be stripped from child config, but got: {passed_config}"
        )

        # Other keys must still be present
        assert passed_config.get("session") == {"orchestrator": "loop-basic"}
        assert passed_config.get("other_key") == "other_value"

    async def test_json_envelope_parsed_for_return_dict(self, monkeypatch):
        """When child returns JSON envelope, all fields are parsed into return dict.

        The subprocess runner may return a JSON string with structured fields:
        output, status, turn_count, session_id, metadata. These should be parsed
        and returned in the result dict instead of the raw string.
        """
        import json

        parent = _make_parent_session()

        # Create fake module that returns a JSON envelope string
        json_envelope = json.dumps(
            {
                "output": "analysis complete",
                "status": "success",
                "turn_count": 5,
                "session_id": "child-structured",
                "metadata": {"tokens_used": 12345},
            }
        )
        fake_module = ModuleType("amplifier_foundation.subprocess_runner")
        fake_module.run_session_in_subprocess = AsyncMock(return_value=json_envelope)
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        with patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge:
            mock_merge.return_value = {"session": {}}

            from amplifier_app_cli.session_spawner import spawn_sub_session

            result = await spawn_sub_session(
                agent_name="some-agent",
                instruction="Do analysis",
                parent_session=parent,
                agent_configs={"some-agent": {}},
                sub_session_id="fixed-sub-id",
                use_subprocess=True,
            )

        # All fields from JSON envelope should be parsed into the result dict
        assert result["output"] == "analysis complete"
        assert result["status"] == "success"
        assert result["turn_count"] == 5
        assert result["session_id"] == "child-structured"
        assert result["metadata"] == {"tokens_used": 12345}

    async def test_session_fork_event_emitted_for_subprocess(self, monkeypatch):
        """session:fork event is emitted from parent hooks when subprocess path is taken.

        When spawn_sub_session uses the subprocess path (use_subprocess=True or
        spawn_mode: subprocess in config), the parent's hooks should receive a
        session:fork event with child_session_id, parent_session_id, agent_name,
        and spawn_mode='subprocess'.
        """
        parent = _make_parent_session()

        # Create mock hooks
        mock_hooks = AsyncMock()
        mock_hooks.emit = AsyncMock()

        # Mock coordinator.get to return mock_hooks for 'hooks' key
        def coordinator_get(key):
            if key == "hooks":
                return mock_hooks
            return None

        parent.coordinator.get.side_effect = coordinator_get

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

            await spawn_sub_session(
                agent_name="test-agent",
                instruction="Do something",
                parent_session=parent,
                agent_configs={"test-agent": {}},
                sub_session_id="child-session-id",
                use_subprocess=True,
            )

        # Verify session:fork event was emitted exactly once with correct data
        mock_hooks.emit.assert_called_once_with(
            "session:fork",
            {
                "child_session_id": "child-session-id",
                "parent_session_id": "parent-session-id",
                "agent_name": "test-agent",
                "spawn_mode": "subprocess",
            },
        )

    async def test_session_fork_not_emitted_when_no_hooks(self, monkeypatch):
        """No error when parent has no hooks — session:fork is silently skipped.

        Acceptance criterion #3: no event emitted if hooks not available (no error).
        """
        parent = _make_parent_session()
        # coordinator.get returns None by default (no hooks)

        fake_module = _make_subprocess_runner_module()
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        with (
            patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge,
        ):
            mock_merge.return_value = {"session": {}}

            from amplifier_app_cli.session_spawner import spawn_sub_session

            # Should complete without error even when no hooks are registered
            result = await spawn_sub_session(
                agent_name="test-agent",
                instruction="Do something",
                parent_session=parent,
                agent_configs={"test-agent": {}},
                sub_session_id="no-hooks-session-id",
                use_subprocess=True,
            )

        # Result should still be returned normally
        assert result["output"] == "subprocess output"
        assert result["session_id"] == "no-hooks-session-id"


class TestBundleContextSubprocess:
    """Tests for bundle context propagation in subprocess dispatch."""

    async def test_bundle_context_passed_to_subprocess(self, monkeypatch):
        """module_paths, bundle_package_paths, sys_paths are forwarded to run_session_in_subprocess.

        When use_subprocess=True, spawn_sub_session must extract bundle context from the
        parent session and pass module_paths, bundle_package_paths, and sys_paths to
        run_session_in_subprocess. Without this, bundle modules are not importable in child.
        """
        from pathlib import Path

        parent = _make_parent_session()

        # Set up a fake BundleModuleResolver on the coordinator
        fake_paths = {"my_tool": Path("/bundle/tools/my_tool")}
        fake_resolver = type("FakeBMR", (), {"_paths": fake_paths})()

        def coordinator_get(key):
            if key == "module-source-resolver":
                return fake_resolver
            return None

        parent.coordinator.get.side_effect = coordinator_get

        # bundle_package_paths capability
        bundle_pkg_paths = ["/bundle/src", "/bundle/extra/src"]

        def coordinator_get_cap(key):
            if key == "bundle_package_paths":
                return bundle_pkg_paths
            if key == "session.working_dir":
                return None
            return None

        parent.coordinator.get_capability.side_effect = coordinator_get_cap

        fake_module = _make_subprocess_runner_module()
        monkeypatch.setitem(
            sys.modules, "amplifier_foundation.subprocess_runner", fake_module
        )

        with patch("amplifier_app_cli.session_spawner.merge_configs") as mock_merge:
            mock_merge.return_value = {"session": {}}

            from amplifier_app_cli.session_spawner import spawn_sub_session

            await spawn_sub_session(
                agent_name="some-agent",
                instruction="Do something",
                parent_session=parent,
                agent_configs={"some-agent": {}},
                sub_session_id="bundle-ctx-test-id",
                use_subprocess=True,
            )

        fake_module.run_session_in_subprocess.assert_called_once()
        call_kwargs = fake_module.run_session_in_subprocess.call_args.kwargs

        # module_paths must be passed and contain our bundle module paths
        assert "module_paths" in call_kwargs, (
            "module_paths not forwarded to run_session_in_subprocess"
        )
        assert call_kwargs["module_paths"] is not None
        assert "my_tool" in call_kwargs["module_paths"], (
            f"Expected 'my_tool' in module_paths, got: {call_kwargs['module_paths']}"
        )

        # bundle_package_paths must be passed
        assert "bundle_package_paths" in call_kwargs, (
            "bundle_package_paths not forwarded to run_session_in_subprocess"
        )
        assert call_kwargs["bundle_package_paths"] == bundle_pkg_paths

        # sys_paths must be passed as a list
        assert "sys_paths" in call_kwargs, (
            "sys_paths not forwarded to run_session_in_subprocess"
        )
        assert isinstance(call_kwargs["sys_paths"], list)


class TestResumeSubSessionChildSpawnCapability:
    """Tests for child_spawn_capability closure in resume_sub_session."""

    def test_resume_child_spawn_capability_has_use_subprocess_param(self):
        """child_spawn_capability registered in resume_sub_session accepts use_subprocess.

        The closure registered as session.spawn inside resume_sub_session must include
        use_subprocess: bool = False in its signature and thread it through to spawn_sub_session.
        Without this, resumed sessions cannot spawn subprocess children.
        """
        import inspect

        from amplifier_app_cli.session_spawner import resume_sub_session

        # Get the source of resume_sub_session to inspect the closure signature
        source = inspect.getsource(resume_sub_session)

        # Locate the child_spawn_capability definition within resume_sub_session
        child_spawn_idx = source.find("async def child_spawn_capability(")
        assert child_spawn_idx != -1, (
            "No child_spawn_capability closure found in resume_sub_session"
        )

        # Extract the signature portion (up to ') -> dict:')
        sig_end = source.find(") -> dict:", child_spawn_idx)
        if sig_end == -1:
            sig_end = source.find("->", child_spawn_idx) + 40
        closure_sig = source[child_spawn_idx:sig_end]

        # RED: Currently the closure in resume_sub_session does NOT have use_subprocess
        assert "use_subprocess" in closure_sig, (
            f"child_spawn_capability in resume_sub_session is missing 'use_subprocess' param.\n"
            f"Found signature fragment:\n{closure_sig}"
        )
