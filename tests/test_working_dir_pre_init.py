"""Regression test: session.working_dir registered BEFORE child_session.initialize().

ROOT CAUSE:
  Modules that read capabilities while mounting or during on_session_ready
  (both run DURING initialize()) received working_dir=None because the
  registration happened AFTER initialize() in both the new-session and resume
  paths.

  General rule: any capability a module consumes while mounting or in
  on_session_ready must be registered before initialize(); a capability
  registered afterwards is invisible to them, so the module sees it as absent
  and may silently disable behavior that depends on it. This affects ANY module,
  not just hooks.

FIX:
  Move register_capability("session.working_dir", ...) to BEFORE
  child_session.initialize() in both paths (session_spawner.py).

REGRESSION CONTRACT:
  register_capability("session.working_dir", ...) must appear in the call log
  BEFORE initialize() for both the new-session (spawn_sub_session) and resume
  (resume_sub_session) paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_call_log() -> list[str]:
    """Return a shared list used to record key event names in order."""
    return []


def _make_parent_session(working_dir: str = "/parent/wd") -> MagicMock:
    """Build a minimal MagicMock parent session for spawn tests."""
    parent = MagicMock()

    parent.config = {
        "agents": {"test_agent": {"description": "Test agent"}},
        "session": {"orchestrator": "loop-basic"},
    }

    parent.coordinator = MagicMock()
    parent.coordinator.config = {
        "agents": {"test_agent": {"description": "Test agent"}}
    }

    cap_store: dict = {"session.working_dir": working_dir}

    def _get_capability(name: str):
        return cap_store.get(name)

    parent.coordinator.get_capability = MagicMock(side_effect=_get_capability)
    parent.session_id = "parent-session-id"
    parent.coordinator.display_system = MagicMock()
    parent.coordinator.approval_system = MagicMock()
    parent.coordinator.cancellation = MagicMock()
    parent.coordinator.cancellation.register_child = MagicMock()
    parent.coordinator.cancellation.unregister_child = MagicMock()
    parent.coordinator.get = MagicMock(return_value=None)
    parent.loader = None

    return parent


def _make_child_session_mock(call_log: list[str]) -> MagicMock:
    """Build a MagicMock child session that records register_capability / initialize order.

    Records events into call_log:
      - "register:session.working_dir" when that capability is registered
      - "initialize" when initialize() is called
    """
    child = MagicMock()
    child.session_id = "child-session-id"

    cap_store: dict = {}

    def _register_capability(name: str, value) -> None:
        cap_store[name] = value
        if name == "session.working_dir":
            call_log.append("register:session.working_dir")

    def _get_capability(name: str):
        return cap_store.get(name)

    child.coordinator = MagicMock()
    child.coordinator.register_capability = MagicMock(side_effect=_register_capability)
    child.coordinator.get_capability = MagicMock(side_effect=_get_capability)
    child.coordinator.get = MagicMock(return_value=None)
    child.coordinator.cancellation = MagicMock()
    child.coordinator.mount = AsyncMock()
    child.coordinator.display_system = MagicMock()
    child.coordinator.hooks = MagicMock()
    child.coordinator.hooks.emit = AsyncMock()
    child.coordinator.hooks.register = MagicMock(return_value=MagicMock())
    child.coordinator.approval_system = MagicMock()

    async def _initialize_recording():
        call_log.append("initialize")

    child.initialize = AsyncMock(side_effect=_initialize_recording)
    child.execute = AsyncMock(return_value="agent output")
    child.cleanup = AsyncMock()

    return child


# ---------------------------------------------------------------------------
# New-session path: spawn_sub_session
# ---------------------------------------------------------------------------


class TestSpawnWorkingDirBeforeInit:
    """session.working_dir must be registered before initialize() in spawn_sub_session."""

    @pytest.mark.asyncio
    async def test_working_dir_registered_before_initialize(self) -> None:
        """register_capability('session.working_dir') must precede initialize().

        This ensures any module that reads session.working_dir while mounting or
        inside on_session_ready (both run during initialize()) sees a non-None
        value. Any capability a module consumes during initialize() must be
        registered before initialize() — this affects ANY module, not just hooks.
        """
        from amplifier_app_cli.session_spawner import spawn_sub_session

        call_log: list[str] = _build_call_log()
        parent = _make_parent_session(working_dir="/test/working/dir")
        child = _make_child_session_mock(call_log)

        def _make_session(config, **kwargs):
            return child

        with (
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                side_effect=_make_session,
            ),
            patch(
                "amplifier_app_cli.session_spawner.generate_sub_session_id",
                return_value="child-session-id",
            ),
            patch(
                "amplifier_app_cli.session_spawner.bridge_child_cost",
                new_callable=AsyncMock,
            ),
            patch(
                "amplifier_app_cli.session_spawner._extract_bundle_context",
                return_value=None,
            ),
            patch("amplifier_app_cli.session_store.SessionStore"),
            patch(
                "amplifier_app_cli.lib.mention_loading.app_resolver.AppMentionResolver"
            ),
            patch(
                "amplifier_app_cli.paths.create_foundation_resolver",
                return_value=MagicMock(),
            ),
            patch("amplifier_foundation.mentions.ContentDeduplicator"),
        ):
            await spawn_sub_session(
                agent_name="test_agent",
                instruction="Do something",
                parent_session=parent,
                agent_configs={"test_agent": {"description": "Test agent"}},
            )

        # Assert ordering: working_dir registration must come BEFORE initialize
        assert "register:session.working_dir" in call_log, (
            "session.working_dir was never registered on the child session. "
            f"call_log: {call_log}"
        )
        assert "initialize" in call_log, (
            f"child_session.initialize() was never called. call_log: {call_log}"
        )

        wd_idx = call_log.index("register:session.working_dir")
        init_idx = call_log.index("initialize")

        assert wd_idx < init_idx, (
            "session.working_dir must be registered BEFORE initialize() so that "
            "any module reading it during initialize() sees the working_dir value "
            "(any capability a module consumes during initialize() must be "
            "registered before initialize() — not just hooks). "
            f"Actual order: {call_log} "
            f"(register:session.working_dir at index {wd_idx}, "
            f"initialize at index {init_idx})"
        )

    @pytest.mark.asyncio
    async def test_working_dir_uses_cwd_fallback_when_parent_has_none(self) -> None:
        """When parent has no working_dir capability, cwd is used — never empty."""
        from amplifier_app_cli.session_spawner import spawn_sub_session

        call_log: list[str] = _build_call_log()
        parent = _make_parent_session(
            working_dir=""
        )  # empty → falsy → triggers cwd fallback
        # Rebuild so get_capability returns None for session.working_dir
        parent.coordinator.get_capability = MagicMock(return_value=None)

        child = _make_child_session_mock(call_log)
        registered_values: list = []

        original_register = child.coordinator.register_capability.side_effect

        def _track_wd(name: str, value) -> None:
            if name == "session.working_dir":
                registered_values.append(value)
            original_register(name, value)

        child.coordinator.register_capability = MagicMock(side_effect=_track_wd)

        def _make_session(config, **kwargs):
            return child

        with (
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                side_effect=_make_session,
            ),
            patch(
                "amplifier_app_cli.session_spawner.generate_sub_session_id",
                return_value="child-session-id",
            ),
            patch(
                "amplifier_app_cli.session_spawner.bridge_child_cost",
                new_callable=AsyncMock,
            ),
            patch(
                "amplifier_app_cli.session_spawner._extract_bundle_context",
                return_value=None,
            ),
            patch("amplifier_app_cli.session_store.SessionStore"),
            patch(
                "amplifier_app_cli.lib.mention_loading.app_resolver.AppMentionResolver"
            ),
            patch(
                "amplifier_app_cli.paths.create_foundation_resolver",
                return_value=MagicMock(),
            ),
            patch("amplifier_foundation.mentions.ContentDeduplicator"),
        ):
            await spawn_sub_session(
                agent_name="test_agent",
                instruction="Do something",
                parent_session=parent,
                agent_configs={"test_agent": {"description": "Test agent"}},
            )

        assert registered_values, (
            "session.working_dir should always be registered — even when parent "
            "has no working_dir capability. Got no registrations."
        )
        assert registered_values[0], (
            "session.working_dir fallback value must be non-empty (cwd). "
            f"Got: {registered_values[0]!r}"
        )


# ---------------------------------------------------------------------------
# Resume path: resume_sub_session
# ---------------------------------------------------------------------------


class TestResumeWorkingDirBeforeInit:
    """session.working_dir must be registered before initialize() in resume_sub_session."""

    @pytest.mark.asyncio
    async def test_working_dir_registered_before_initialize_on_resume(self) -> None:
        """register_capability('session.working_dir') must precede initialize() on resume.

        Resume reconstructs the child session from stored metadata. The same
        ordering requirement applies: on_session_ready fires during initialize(),
        so working_dir must already be registered at that point.
        """
        from amplifier_app_cli.session_spawner import resume_sub_session

        call_log: list[str] = _build_call_log()
        child = _make_child_session_mock(call_log)

        def _make_session(config, **kwargs):
            return child

        stored_metadata = {
            "config": {
                "agents": {"test_agent": {"description": "Test agent"}},
                "session": {"orchestrator": "loop-basic"},
            },
            "agent_name": "test_agent",
            "parent_id": "parent-session-id",
            "trace_id": "trace-id",
            "working_dir": "/stored/working/dir",
            "self_delegation_depth": 0,
        }

        mock_store = MagicMock()
        mock_store.exists.return_value = True
        mock_store.load.return_value = ([], stored_metadata)
        mock_store.save = MagicMock()

        with (
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                side_effect=_make_session,
            ),
            patch(
                "amplifier_app_cli.session_store.SessionStore", return_value=mock_store
            ),
            patch(
                "amplifier_app_cli.lib.mention_loading.app_resolver.AppMentionResolver"
            ),
            patch(
                "amplifier_app_cli.paths.create_foundation_resolver",
                return_value=MagicMock(),
            ),
            patch("amplifier_foundation.mentions.ContentDeduplicator"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            await resume_sub_session(
                sub_session_id="child-session-id",
                instruction="Follow-up instruction",
                parent_session=None,
            )

        assert "register:session.working_dir" in call_log, (
            "session.working_dir was never registered on the resumed child session. "
            f"call_log: {call_log}"
        )
        assert "initialize" in call_log, (
            "child_session.initialize() was never called during resume. "
            f"call_log: {call_log}"
        )

        wd_idx = call_log.index("register:session.working_dir")
        init_idx = call_log.index("initialize")

        assert wd_idx < init_idx, (
            "session.working_dir must be registered BEFORE initialize() on resume so "
            "that any module reading it during initialize() sees the working_dir value. "
            f"Actual order: {call_log} "
            f"(register:session.working_dir at index {wd_idx}, "
            f"initialize at index {init_idx})"
        )

    @pytest.mark.asyncio
    async def test_resume_uses_parent_fallback_when_metadata_working_dir_absent(
        self,
    ) -> None:
        """When metadata has no working_dir, parent session's value is used."""
        from amplifier_app_cli.session_spawner import resume_sub_session

        call_log: list[str] = _build_call_log()
        child = _make_child_session_mock(call_log)
        registered_values: list = []

        original_register = child.coordinator.register_capability.side_effect

        def _track_wd(name: str, value) -> None:
            if name == "session.working_dir":
                registered_values.append(value)
            original_register(name, value)

        child.coordinator.register_capability = MagicMock(side_effect=_track_wd)

        def _make_session(config, **kwargs):
            return child

        stored_metadata = {
            "config": {
                "agents": {"test_agent": {"description": "Test agent"}},
                "session": {"orchestrator": "loop-basic"},
            },
            "agent_name": "test_agent",
            "parent_id": "parent-session-id",
            "trace_id": "trace-id",
            # No "working_dir" key — should fall back to parent then cwd
            "self_delegation_depth": 0,
        }

        mock_store = MagicMock()
        mock_store.exists.return_value = True
        mock_store.load.return_value = ([], stored_metadata)
        mock_store.save = MagicMock()

        parent = _make_parent_session(working_dir="/parent/fallback/dir")

        with (
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                side_effect=_make_session,
            ),
            patch(
                "amplifier_app_cli.session_store.SessionStore", return_value=mock_store
            ),
            patch(
                "amplifier_app_cli.lib.mention_loading.app_resolver.AppMentionResolver"
            ),
            patch(
                "amplifier_app_cli.paths.create_foundation_resolver",
                return_value=MagicMock(),
            ),
            patch("amplifier_foundation.mentions.ContentDeduplicator"),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        ):
            await resume_sub_session(
                sub_session_id="child-session-id",
                instruction="Follow-up",
                parent_session=parent,
            )

        assert registered_values, (
            "session.working_dir should always be registered on resume. "
            "Got no registrations."
        )
        assert registered_values[0] == "/parent/fallback/dir", (
            "When metadata has no working_dir, the parent session's "
            "session.working_dir capability should be used as the fallback. "
            f"Got: {registered_values[0]!r}"
        )
