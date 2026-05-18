"""Tests for session lifecycle event correctness.

Verifies that session lifecycle events (session:end) are emitted exactly
once per session, not duplicated by both app-layer explicit emits AND
kernel cleanup path.

Root cause (Bug 1):
  main.py had an EXPLICIT emit of SESSION_END in both execute_single() and
  interactive_chat() finally-blocks, followed immediately by
  `initialized.cleanup()` which calls `session.cleanup()` which ALSO emits
  SESSION_END. Result: two session:end events per session.

Fix:
  Remove the explicit SESSION_END emit from main.py. Let session.cleanup()
  (the canonical kernel path) own the single authoritative emission. The
  cleanup-emitted event carries the full status payload, while the
  app-layer emit only carries session_id.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(__file__))

_MODULE = "amplifier_app_cli.main"

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


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
    mock_session.session_id = "test-lifecycle-session"
    mock_session.execute = AsyncMock(return_value="Hello!")
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.get = _coordinator_get
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.coordinator.cancellation.is_cancelled = False
    return mock_session


def _make_mock_initialized_with_cleanup_emit(
    session: MagicMock, hooks: MagicMock
) -> MagicMock:
    """Return a mock InitializedSession whose cleanup() emits SESSION_END.

    This simulates the REAL session.cleanup() path: when cleanup() runs,
    it emits SESSION_END exactly once (with a status field). This is the
    canonical kernel-path emission that should be the SOLE source of
    session:end after the Bug 1 fix.
    """
    mock = MagicMock()
    mock.session = session
    mock.session_id = "test-lifecycle-session"

    async def _cleanup_with_session_end():
        from amplifier_core.events import SESSION_END  # type: ignore[import-untyped]

        await hooks.emit(
            SESSION_END,
            {
                "session_id": "test-lifecycle-session",
                "status": "completed",
            },
        )

    mock.cleanup = AsyncMock(side_effect=_cleanup_with_session_end)
    return mock


# ────────────────────────────────────────────────────────────────────────────
# Bug 1 — execute_single() must not duplicate session:end
# ────────────────────────────────────────────────────────────────────────────


class TestSessionEndExactlyOnce:
    """session:end must be emitted exactly once per session, not twice.

    The root cause: main.py explicitly emits SESSION_END in the finally-block
    AND then calls initialized.cleanup() which also emits SESSION_END via
    session.cleanup(). This results in two session:end events per session.
    """

    @pytest.mark.asyncio
    async def test_execute_single_emits_session_end_exactly_once(self, tmp_path: Path):
        """execute_single() must emit session:end exactly once.

        With the bug: two session:end events are emitted (one explicit,
        one from cleanup). After the fix: exactly one.
        """
        from amplifier_app_cli.main import execute_single
        from unittest.mock import patch

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized_with_cleanup_emit(session, hooks)

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

        session_end_events = [e for e, _ in captured if e == "session:end"]
        assert len(session_end_events) == 1, (
            f"Expected exactly 1 session:end event, got {len(session_end_events)}. "
            f"Full event sequence: {[e for e, _ in captured]}"
        )

    @pytest.mark.asyncio
    async def test_execute_single_session_end_emitted_not_zero_times(
        self, tmp_path: Path
    ):
        """session:end must still be emitted (guard against over-correction)."""
        from amplifier_app_cli.main import execute_single
        from unittest.mock import patch

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized_with_cleanup_emit(session, hooks)

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

        assert any(e == "session:end" for e, _ in captured), (
            "session:end was not emitted at all — cleanup path must emit it."
        )

    @pytest.mark.asyncio
    async def test_execute_single_session_end_payload_has_session_id(
        self, tmp_path: Path
    ):
        """The single session:end event must carry a session_id."""
        from amplifier_app_cli.main import execute_single
        from unittest.mock import patch

        hooks, captured = _make_mock_hooks()
        session = _make_mock_session(hooks)
        initialized = _make_mock_initialized_with_cleanup_emit(session, hooks)

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

        session_end_calls = [(e, d) for e, d in captured if e == "session:end"]
        assert len(session_end_calls) == 1
        _event, data = session_end_calls[0]
        assert "session_id" in data, f"session:end payload missing 'session_id': {data}"
