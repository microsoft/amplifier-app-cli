"""Tests for cleanup-window observability events.

Verifies that the six new diagnostic events — cleanup:render_begin,
cleanup:render_end, cleanup:store_begin, cleanup:store_end,
cleanup:finally_begin, cleanup:finally_end — are emitted in the expected
order, sandwiched between prompt:complete and session:end, when
execute_single() runs a successful single-shot session.

Also verifies that the constants are exported from main with the correct
string values.

Design note
-----------
These events cannot live in amplifier_core.events (which re-exports from the
Rust kernel binary); they are owned by the app runtime, re-exported from main
for compatibility, and emitted via the same hooks.emit() path as kernel events.
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODULE = "amplifier_app_cli.main"


def _make_mock_hooks() -> tuple[MagicMock, list[tuple[str, dict]]]:
    """Return (mock_hooks, captured_calls).

    Every ``await hooks.emit(event, data)`` call appends ``(event, data)``
    to *captured_calls*.
    """
    captured: list[tuple[str, dict]] = []

    async def _emit(event: str, data: dict) -> None:
        captured.append((event, data))

    mock = MagicMock()
    mock.emit = AsyncMock(side_effect=_emit)
    return mock, captured


def _make_mock_session(
    hooks: MagicMock, messages: list[dict] | None = None
) -> MagicMock:
    """Return a minimal mock AmplifierSession."""
    mock_ctx = MagicMock()
    mock_ctx.get_messages = AsyncMock(
        return_value=messages or [{"role": "user", "content": "Hi"}]
    )

    def _coordinator_get(key: str):
        if key == "hooks":
            return hooks
        if key == "context":
            return mock_ctx
        if key == "providers":
            return {}
        return None

    mock_session = MagicMock()
    mock_session.session_id = "test-session-id"
    mock_session.execute = AsyncMock(return_value="Hello!")
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.get = _coordinator_get
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.coordinator.cancellation.is_cancelled = False
    return mock_session


def _make_mock_initialized(
    session: MagicMock, hooks: MagicMock | None = None
) -> MagicMock:
    """Return a minimal InitializedSession mock.

    If *hooks* is supplied, the cleanup() coroutine will emit ``session:end``
    exactly once — mirroring what the real ``session.cleanup()`` does via the
    kernel path.  Tests that assert on ``session:end`` ordering must pass hooks
    so that cleanup produces the event (instead of the now-removed explicit
    emit in main.py).
    """
    mock = MagicMock()
    mock.session = session
    mock.session_id = "test-session-id"

    if hooks is not None:
        _hooks_ref = hooks  # capture for closure

        async def _cleanup_with_session_end() -> None:
            from amplifier_core.events import SESSION_END  # type: ignore[import-untyped]

            await _hooks_ref.emit(
                SESSION_END,
                {"session_id": "test-session-id", "status": "completed"},
            )

        mock.cleanup = AsyncMock(side_effect=_cleanup_with_session_end)
    else:
        mock.cleanup = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# Constants correctness
# ---------------------------------------------------------------------------


class TestCleanupEventConstants:
    """Verify the string values of the six new constants."""

    def test_render_begin(self):
        from amplifier_app_cli.main import CLEANUP_RENDER_BEGIN

        assert CLEANUP_RENDER_BEGIN == "cleanup:render_begin"

    def test_render_end(self):
        from amplifier_app_cli.main import CLEANUP_RENDER_END

        assert CLEANUP_RENDER_END == "cleanup:render_end"

    def test_store_begin(self):
        from amplifier_app_cli.main import CLEANUP_STORE_BEGIN

        assert CLEANUP_STORE_BEGIN == "cleanup:store_begin"

    def test_store_end(self):
        from amplifier_app_cli.main import CLEANUP_STORE_END

        assert CLEANUP_STORE_END == "cleanup:store_end"

    def test_finally_begin(self):
        from amplifier_app_cli.main import CLEANUP_FINALLY_BEGIN

        assert CLEANUP_FINALLY_BEGIN == "cleanup:finally_begin"

    def test_finally_end(self):
        from amplifier_app_cli.main import CLEANUP_FINALLY_END

        assert CLEANUP_FINALLY_END == "cleanup:finally_end"

    def test_all_constants_are_strings(self):
        from amplifier_app_cli.main import (
            CLEANUP_FINALLY_BEGIN,
            CLEANUP_FINALLY_END,
            CLEANUP_RENDER_BEGIN,
            CLEANUP_RENDER_END,
            CLEANUP_STORE_BEGIN,
            CLEANUP_STORE_END,
        )

        for name, value in [
            ("CLEANUP_RENDER_BEGIN", CLEANUP_RENDER_BEGIN),
            ("CLEANUP_RENDER_END", CLEANUP_RENDER_END),
            ("CLEANUP_STORE_BEGIN", CLEANUP_STORE_BEGIN),
            ("CLEANUP_STORE_END", CLEANUP_STORE_END),
            ("CLEANUP_FINALLY_BEGIN", CLEANUP_FINALLY_BEGIN),
            ("CLEANUP_FINALLY_END", CLEANUP_FINALLY_END),
        ]:
            assert isinstance(value, str), f"{name} must be a str, got {type(value)}"
            assert value.startswith("cleanup:"), (
                f"{name} must start with 'cleanup:', got {value!r}"
            )


# ---------------------------------------------------------------------------
# execute_single() — cleanup event ordering
# ---------------------------------------------------------------------------


class TestExecuteSingleCleanupEvents:
    """execute_single() must emit cleanup events in the documented order."""

    @pytest.mark.asyncio
    async def test_cleanup_events_emitted_in_order(self, tmp_path: Path):
        """All six cleanup events are emitted in the right sequence.

        Expected order (successful single-shot session, text output):
          1. prompt:complete
          2. cleanup:render_begin
          3. cleanup:render_end
          4. cleanup:store_begin
          5. cleanup:store_end
          6. cleanup:finally_begin
          7. session:end
          8. cleanup:finally_end
        """
        from amplifier_app_cli.main import execute_single

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        # Pass hooks so cleanup() emits session:end (simulates real session.cleanup())
        initialized = _make_mock_initialized(session, hooks=hooks)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),  # suppress Rich output
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            # Minimal SessionStore mock
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        event_names = [e for e, _ in captured]

        # All six new events must be present
        assert "cleanup:render_begin" in event_names
        assert "cleanup:render_end" in event_names
        assert "cleanup:store_begin" in event_names
        assert "cleanup:store_end" in event_names
        assert "cleanup:finally_begin" in event_names
        assert "cleanup:finally_end" in event_names

        # Core events must also be present
        assert "prompt:complete" in event_names
        assert "session:end" in event_names

        # Order constraints
        def idx(name: str) -> int:
            return event_names.index(name)

        # render bracket is before store bracket
        assert idx("cleanup:render_begin") < idx("cleanup:render_end")
        assert idx("cleanup:render_end") <= idx("cleanup:store_begin")
        assert idx("cleanup:store_begin") < idx("cleanup:store_end")

        # finally bracket wraps session:end and comes after the render/store brackets
        assert idx("cleanup:store_end") <= idx("cleanup:finally_begin")
        assert idx("cleanup:finally_begin") < idx("session:end")
        assert idx("session:end") < idx("cleanup:finally_end")

        # prompt:complete precedes the cleanup window
        assert idx("prompt:complete") < idx("cleanup:render_begin")

    @pytest.mark.asyncio
    async def test_cleanup_render_begin_before_store_begin(self, tmp_path: Path):
        """cleanup:render_begin must appear before cleanup:store_begin."""
        from amplifier_app_cli.main import execute_single

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        event_names = [e for e, _ in captured]
        assert event_names.index("cleanup:render_begin") < event_names.index(
            "cleanup:store_begin"
        )

    @pytest.mark.asyncio
    async def test_store_end_payload_has_message_count(self, tmp_path: Path):
        """cleanup:store_end payload must contain 'message_count'."""
        from amplifier_app_cli.main import execute_single

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(
            hooks,
            messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ],
        )
        initialized = _make_mock_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        store_end_calls = [(e, d) for e, d in captured if e == "cleanup:store_end"]
        assert store_end_calls, "cleanup:store_end was not emitted"
        _event, data = store_end_calls[0]
        assert "message_count" in data, (
            f"cleanup:store_end payload missing 'message_count': {data}"
        )
        assert data["message_count"] == 2

    @pytest.mark.asyncio
    async def test_first_single_shot_save_treats_missing_metadata_as_empty(
        self, tmp_path: Path
    ):
        """A new single-shot session has no metadata file before its first save."""
        from amplifier_app_cli.main import execute_single

        hooks, _captured = _make_mock_hooks()
        session = _make_mock_session(
            hooks,
            messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ],
        )
        initialized = _make_mock_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.side_effect = FileNotFoundError(
                "Session 'test-session-id' not found"
            )
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        store_instance.save.assert_called_once()
        _session_id, _messages, metadata = store_instance.save.call_args.args
        assert metadata["session_id"] == "test-session-id"
        assert metadata["turn_count"] == 1

    @pytest.mark.asyncio
    async def test_all_cleanup_events_carry_session_id(self, tmp_path: Path):
        """Every cleanup event payload must include the session_id."""
        from amplifier_app_cli.main import execute_single

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        cleanup_events = [(e, d) for e, d in captured if e.startswith("cleanup:")]
        assert len(cleanup_events) == 6, (
            f"Expected 6 cleanup events, got {len(cleanup_events)}: "
            f"{[e for e, _ in cleanup_events]}"
        )
        for event, data in cleanup_events:
            assert "session_id" in data, f"{event} payload missing 'session_id': {data}"
            assert data["session_id"] == "test-session-id", (
                f"{event} session_id mismatch: {data['session_id']!r}"
            )

    @pytest.mark.asyncio
    async def test_cleanup_events_emitted_in_json_mode(self, tmp_path: Path):
        """Cleanup events are emitted even when output_format='json'."""
        from amplifier_app_cli.main import execute_single

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized(session)

        original_stdout = sys.stdout

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            # Capture stdout to prevent JSON from going to terminal
            import io

            captured_stdout = io.StringIO()
            sys.stdout = captured_stdout
            try:
                await execute_single(
                    prompt="Hi",
                    config={},
                    search_paths=[tmp_path],
                    verbose=False,
                    output_format="json",
                    bundle_name="test-bundle",
                )
            finally:
                sys.stdout = original_stdout

        json_output = json.loads(captured_stdout.getvalue())
        assert json_output["status"] == "success"
        assert json_output["response"] == "Hello!"
        assert json_output["session_id"] == "test-session-id"

        event_names = [e for e, _ in captured]
        for expected in [
            "cleanup:render_begin",
            "cleanup:render_end",
            "cleanup:store_begin",
            "cleanup:store_end",
            "cleanup:finally_begin",
            "cleanup:finally_end",
        ]:
            assert expected in event_names, (
                f"{expected} not emitted in JSON mode. Got: {event_names}"
            )


# ---------------------------------------------------------------------------
# execute_single() — no hooks scenario (hooks=None guard)
# ---------------------------------------------------------------------------


class TestExecuteSingleNoHooks:
    """execute_single() must not raise when the hooks coordinator slot is None."""

    @pytest.mark.asyncio
    async def test_runs_without_hooks(self, tmp_path: Path):
        """execute_single() completes normally when hooks=None."""
        from amplifier_app_cli.main import execute_single

        # Session with no hooks
        mock_ctx = MagicMock()
        mock_ctx.get_messages = AsyncMock(
            return_value=[{"role": "user", "content": "Hi"}]
        )

        def _coordinator_get_nohooks(key: str):
            if key == "context":
                return mock_ctx
            if key == "providers":
                return {}
            return None  # hooks is None

        mock_session = MagicMock()
        mock_session.session_id = "no-hooks-session"
        mock_session.execute = AsyncMock(return_value="Hello!")
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.get = _coordinator_get_nohooks

        initialized = _make_mock_initialized(mock_session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}._process_runtime_mentions", new=AsyncMock()),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            # Should not raise
            await execute_single(
                prompt="Hi",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )
