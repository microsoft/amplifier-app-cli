"""Tests for observability event registration via the additional_events mechanism.

Verifies that the six cleanup:* events (PR #183) are registered with
hooks-logging and hook-context-intelligence via the ``additional_events``
config key injected into the mount plan by ``_inject_observability_events()``
before ``create_session()`` is called.

MIGRATION NOTE (see foundation PR feat/move-observability-injection-to-foundation)
---------
``session:config`` (PR #79) has been moved to foundation's
``PreparedBundle.create_session()`` via
``amplifier_foundation.bundle._observability.inject_additional_events()``.
The tests that formerly asserted on ``session:config`` here have been migrated
to ``amplifier-foundation/tests/test_observability_injection.py``.

App-cli now owns ONLY the six ``cleanup:*`` events — events emitted by
``amplifier_app_cli.main`` that no other layer knows about.

ROOT CAUSE BEING TESTED
-----------------------
PR #183 emits new events via ``hooks.emit("cleanup:...", ...)`` but the
events would never appear in events.jsonl because hooks-logging only subscribes
to events in ``amplifier_core.events.ALL_EVENTS`` plus whatever is in its
``additional_events`` config key.  The fix injects the new names into the
mount plan before ``create_session()`` runs.

KEY INVARIANT
-------------
Every test here MUST FAIL if ``_inject_observability_events`` is removed or
bypassed.  Use ``patch.object`` to make the absence detectable, and assert on
the actual mount-plan state after injection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

_MODULE = "amplifier_app_cli.session_runner"

# The 6 cleanup events app-cli owns.
# session:config is now handled by foundation — NOT in this list.
EXPECTED_EVENTS = [
    "cleanup:render_begin",
    "cleanup:render_end",
    "cleanup:store_begin",
    "cleanup:store_end",
    "cleanup:finally_begin",
    "cleanup:finally_end",
]

SUBSCRIBER_MODULES = {"hooks-logging", "hook-context-intelligence"}


# ---------------------------------------------------------------------------
# Helper: build a minimal PreparedBundle mock with a hooks section
# ---------------------------------------------------------------------------


def _bundle_with_hooks(*modules: str) -> MagicMock:
    """Return a PreparedBundle mock whose mount_plan has the given hook modules."""
    bundle = MagicMock()
    bundle.mount_plan = {"hooks": [{"module": m, "config": {}} for m in modules]}
    return bundle


def _bundle_with_no_hooks() -> MagicMock:
    bundle = MagicMock()
    bundle.mount_plan = {}
    return bundle


# ---------------------------------------------------------------------------
# Tests: _inject_observability_events function
# ---------------------------------------------------------------------------


class TestInjectObservabilityEventsFunction:
    """Unit tests for _inject_observability_events(prepared_bundle).

    These tests FAIL before the function is added to session_runner.py
    (ImportError) and FAIL if the function stops injecting cleanup events.
    """

    def _import_fn(self):
        from amplifier_app_cli.session_runner import _inject_observability_events  # noqa: PLC0415  # type: ignore[reportAttributeAccessIssue]

        return _inject_observability_events

    # --- hooks-logging ---

    def test_injects_all_cleanup_events_into_hooks_logging(self):
        """All 6 cleanup events are added to hooks-logging config['additional_events']."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-logging")

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"]["additional_events"]
        for ev in EXPECTED_EVENTS:
            assert ev in added, f"'{ev}' missing from hooks-logging additional_events"

    def test_injects_all_cleanup_events_into_hook_context_intelligence(self):
        """All 6 cleanup events are added to hook-context-intelligence config."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hook-context-intelligence")

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"]["additional_events"]
        for ev in EXPECTED_EVENTS:
            assert ev in added, (
                f"'{ev}' missing from hook-context-intelligence additional_events"
            )

    def test_session_config_not_injected_by_app_cli(self):
        """session:config is NOT injected by app-cli (handled by foundation now)."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-logging")

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"].get("additional_events", [])
        assert "session:config" not in added, (
            "session:config should NOT be injected by app-cli — "
            "foundation's create_session() handles it now. "
            "See feat/move-observability-injection-to-foundation PR."
        )

    def test_does_not_inject_into_unrelated_hooks(self):
        """Non-subscriber hooks are left untouched."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-notify-push", "hooks-logging")
        # The unrelated hook starts with no config
        notify_hook = bundle.mount_plan["hooks"][0]
        notify_hook["config"] = {"foo": "bar"}

        inject(bundle)

        # hooks-notify-push must be unchanged
        notify_cfg = bundle.mount_plan["hooks"][0]["config"]
        assert "additional_events" not in notify_cfg
        assert notify_cfg.get("foo") == "bar"

    def test_preserves_existing_additional_events(self):
        """User-configured additional_events are preserved; new events are appended."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-logging")
        bundle.mount_plan["hooks"][0]["config"]["additional_events"] = [
            "my:custom:event"
        ]

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"]["additional_events"]
        assert "my:custom:event" in added, "Existing event was lost"
        for ev in EXPECTED_EVENTS:
            assert ev in added, f"'{ev}' missing after preserving existing events"

    def test_deduplicates_events(self):
        """Events already present are not duplicated."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-logging")
        bundle.mount_plan["hooks"][0]["config"]["additional_events"] = [
            "cleanup:render_begin"
        ]

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"]["additional_events"]
        count = added.count("cleanup:render_begin")
        assert count == 1, f"cleanup:render_begin duplicated: count={count}"

    def test_handles_no_hooks_section(self):
        """Gracefully handles mount_plan with no hooks key."""
        inject = self._import_fn()
        bundle = _bundle_with_no_hooks()
        # Should not raise
        inject(bundle)

    def test_handles_hooks_logging_with_null_config(self):
        """Handles hooks-logging entry with config=None gracefully."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-logging")
        bundle.mount_plan["hooks"][0]["config"] = None

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"]["additional_events"]
        assert "cleanup:render_begin" in added

    def test_handles_hooks_logging_without_config_key(self):
        """Handles hooks-logging entry that has no 'config' key at all."""
        inject = self._import_fn()
        bundle = MagicMock()
        bundle.mount_plan = {
            "hooks": [{"module": "hooks-logging"}]  # no 'config' key
        }

        inject(bundle)

        added = bundle.mount_plan["hooks"][0]["config"]["additional_events"]
        assert "cleanup:render_begin" in added

    def test_both_subscribers_in_same_plan(self):
        """When both subscribers are present, both get injected."""
        inject = self._import_fn()
        bundle = _bundle_with_hooks("hooks-logging", "hook-context-intelligence")

        inject(bundle)

        for i, module in enumerate(["hooks-logging", "hook-context-intelligence"]):
            added = bundle.mount_plan["hooks"][i]["config"]["additional_events"]
            assert "cleanup:render_begin" in added, (
                f"{module} missing cleanup:render_begin"
            )
            assert "cleanup:finally_end" in added, (
                f"{module} missing cleanup:finally_end"
            )


# ---------------------------------------------------------------------------
# Tests: _CLEANUP_EVENTS constant is accessible from session_runner
# ---------------------------------------------------------------------------


class TestCleanupEventsConstant:
    """Verify the _CLEANUP_EVENTS tuple has the right shape (no session:config)."""

    def test_cleanup_events_has_six_events(self):
        from amplifier_app_cli.session_runner import _CLEANUP_EVENTS  # noqa: PLC0415  # type: ignore[reportAttributeAccessIssue]

        assert len(_CLEANUP_EVENTS) == 6, (
            f"Expected 6 cleanup events, got {len(_CLEANUP_EVENTS)}: {_CLEANUP_EVENTS}"
        )

    def test_cleanup_events_has_no_session_config(self):
        from amplifier_app_cli.session_runner import _CLEANUP_EVENTS  # noqa: PLC0415  # type: ignore[reportAttributeAccessIssue]

        assert "session:config" not in _CLEANUP_EVENTS, (
            "session:config must NOT be in _CLEANUP_EVENTS — it moved to foundation"
        )

    def test_cleanup_events_are_all_cleanup_prefix(self):
        from amplifier_app_cli.session_runner import _CLEANUP_EVENTS  # noqa: PLC0415  # type: ignore[reportAttributeAccessIssue]

        for ev in _CLEANUP_EVENTS:
            assert ev.startswith("cleanup:"), (
                f"Non-cleanup event in _CLEANUP_EVENTS: {ev!r}"
            )


# ---------------------------------------------------------------------------
# Tests: _inject_observability_events is called from _create_bundle_session
# ---------------------------------------------------------------------------


class TestInjectCalledFromCreateBundleSession:
    """Verify that _inject_observability_events is wired into the session creation path.

    FAILS if the call is removed from _create_bundle_session.
    """

    @pytest.mark.asyncio
    async def test_inject_is_called_during_bundle_session_creation(self):
        """_inject_observability_events must be called inside _create_bundle_session."""
        # Import the private functions we expect to exist
        from amplifier_app_cli.session_runner import _create_bundle_session  # noqa: PLC0415  # type: ignore[reportAttributeAccessIssue]
        from amplifier_app_cli.session_runner import _inject_observability_events  # noqa: PLC0415  # type: ignore[reportAttributeAccessIssue]
        from amplifier_app_cli.session_runner import (  # noqa: PLC0415
            SessionConfig,
        )

        prepared = MagicMock()
        prepared.mount_plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}
        prepared.bundle_package_paths = []

        config = SessionConfig(
            config={"session": {"orchestrator": "orch", "context": "ctx"}},
            search_paths=[],
            verbose=False,
            prepared_bundle=prepared,
            bundle_name="test",
        )

        mock_session = MagicMock()
        mock_session.config = {}
        mock_session.coordinator.get_capability.return_value = None
        mock_session.coordinator.register_capability = MagicMock()

        call_record: list[Any] = []

        original_inject = _inject_observability_events

        def _spy(b):
            call_record.append(b)
            return original_inject(b)

        # The imports inside _create_bundle_session are local (lazy), so we patch
        # the module-level name that gets imported into the function's local namespace.
        # Since these are 'from .lib... import' inside the function body, we patch the
        # _MODULE level after the function has imported them, or we patch their source
        # modules directly.
        with patch(f"{_MODULE}._inject_observability_events", side_effect=_spy):
            with patch(
                "amplifier_app_cli.lib.bundle_loader.AppModuleResolver",
                return_value=MagicMock(),
            ):
                with patch(
                    "amplifier_app_cli.paths.create_foundation_resolver",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "amplifier_app_cli.runtime.config.inject_user_providers"
                    ):
                        with patch(f"{_MODULE}.register_mention_handling"):
                            with patch(f"{_MODULE}.register_session_spawning"):
                                with patch(
                                    f"{_MODULE}._should_attempt_self_healing",
                                    return_value=False,
                                ):
                                    prepared.create_session = AsyncMock(
                                        return_value=mock_session
                                    )

                                    console = MagicMock()
                                    console.status.return_value.__enter__ = MagicMock(
                                        return_value=None
                                    )
                                    console.status.return_value.__exit__ = MagicMock(
                                        return_value=False
                                    )

                                    await _create_bundle_session(
                                        config=config,
                                        session_id="test-123",
                                        approval_system=None,
                                        display_system=None,
                                        console=console,
                                    )

        assert len(call_record) >= 1, (
            "_inject_observability_events was NOT called from _create_bundle_session. "
            "The production event-registration path is broken: cleanup events will be emitted "
            "but never logged to events.jsonl."
        )


# ---------------------------------------------------------------------------
# Tests: hooks-logging production logger path
# ---------------------------------------------------------------------------


def _find_hooks_logging_module() -> str | None:
    """Find the hooks-logging module directory in the amplifier cache."""
    import glob
    import os

    patterns = [
        os.path.expanduser(
            "~/.amplifier/cache/amplifier-module-hooks-logging-*/amplifier_module_hooks_logging"
        ),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return str(Path(matches[0]).parent)
    return None


_HOOKS_LOGGING_PATH = _find_hooks_logging_module()
_hooks_logging_available = _HOOKS_LOGGING_PATH is not None


class TestHooksLoggingProductionPath:
    """Verify that hooks-logging registers handlers for additional_events entries.

    These tests use the REAL hooks-logging _setup_and_register function (not a
    mock) to ensure the production logger path picks up the injected events.

    FAILS if the additional_events mechanism in hooks-logging is broken, or if
    _setup_and_register stops reading the config key.

    Skipped when hooks-logging is not installed in the dev environment cache
    (e.g., fresh CI checkout without amplifier installed).
    """

    def _import_setup_fn(self):
        """Import _setup_and_register from the live hooks-logging module."""
        import sys

        if _HOOKS_LOGGING_PATH and _HOOKS_LOGGING_PATH not in sys.path:
            sys.path.insert(0, _HOOKS_LOGGING_PATH)
        from amplifier_module_hooks_logging import (  # type: ignore[import-not-found]
            _setup_and_register,
        )

        return _setup_and_register

    @pytest.mark.skipif(
        not _hooks_logging_available,
        reason="hooks-logging not found in amplifier cache — skipping production path tests",
    )
    @pytest.mark.asyncio
    async def test_hooks_logging_registers_handlers_for_all_cleanup_events(
        self, tmp_path: Path
    ):
        """All six cleanup:* events are registered when in additional_events."""
        _setup_and_register = self._import_setup_fn()

        cleanup_events = [
            "cleanup:render_begin",
            "cleanup:render_end",
            "cleanup:store_begin",
            "cleanup:store_end",
            "cleanup:finally_begin",
            "cleanup:finally_end",
        ]

        registered_events: list[str] = []

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.collect_contributions = AsyncMock(return_value=[])

        def _track_register(event, handler, priority=0, name=None):
            registered_events.append(event)

        coordinator.hooks = MagicMock()
        coordinator.hooks.register = _track_register

        config = {
            "additional_events": cleanup_events,
            "session_log_template": str(tmp_path / "{project}/{session_id}.jsonl"),
        }

        await _setup_and_register(coordinator, config, use_collect=False)

        for ev in cleanup_events:
            assert ev in registered_events, (
                f"hooks-logging did NOT register a handler for {ev}. "
                "It will never appear in events.jsonl."
            )

    @pytest.mark.skipif(
        not _hooks_logging_available,
        reason="hooks-logging not found in amplifier cache — skipping production path tests",
    )
    @pytest.mark.asyncio
    async def test_hooks_logging_writes_cleanup_event_to_jsonl(self, tmp_path: Path):
        """When cleanup:render_begin is registered via additional_events, it is written to events.jsonl."""
        import json

        _setup_and_register = self._import_setup_fn()

        log_template = str(tmp_path / "{session_id}.jsonl")

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.collect_contributions = AsyncMock(return_value=[])

        registered_handlers: dict[str, Any] = {}

        def _track_register(event, handler, priority=0, name=None):
            registered_handlers[event] = handler

        coordinator.hooks = MagicMock()
        coordinator.hooks.register = _track_register

        config = {
            "additional_events": ["cleanup:render_begin"],
            "session_log_template": log_template,
        }

        await _setup_and_register(coordinator, config, use_collect=False)

        assert "cleanup:render_begin" in registered_handlers, (
            "No handler registered for cleanup:render_begin"
        )

        handler = registered_handlers["cleanup:render_begin"]
        await handler(
            "cleanup:render_begin",
            {
                "session_id": "test-session-cleanup",
                "timestamp": "2026-01-01T00:00:00.000+00:00",
            },
        )

        log_path = tmp_path / "test-session-cleanup.jsonl"

        assert log_path.exists(), (
            f"events.jsonl not found at {log_path} — "
            "cleanup:render_begin was registered but not written to disk."
        )

        lines = [json.loads(line) for line in log_path.read_text().splitlines()]
        events = [line["event"] for line in lines]
        assert "cleanup:render_begin" in events, (
            f"cleanup:render_begin not in events.jsonl. Got: {events}"
        )
