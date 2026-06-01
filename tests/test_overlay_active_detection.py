"""Tests for _streaming_overlay_active() helper.

Verifies:
- Returns True when stream_tokens config is truthy AND stdout is a TTY.
- Returns False when stdout is not a TTY.
- Returns False when stream_tokens is False.
- Returns False when the hooks list is absent or malformed.
- Returns False on any unexpected exception (defensive behaviour).

Also verifies the render_message draw is SKIPPED when the overlay is active.
The gate lives in interactive_chat._execute_with_interrupt (NOT in
execute_single, which has its own console.print render path).  Gate tests
therefore drive interactive_chat with an initial_prompt and a mock
PromptSession that raises EOFError immediately after the prompt is processed
so the REPL exits cleanly.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))


_MODULE = "amplifier_app_cli.main"


# ---------------------------------------------------------------------------
# Session config factories
# ---------------------------------------------------------------------------


def _session_with_streaming_ui(stream_tokens: bool = True) -> MagicMock:
    """Return a minimal session mock whose config mirrors the real hooks-list shape."""
    session = MagicMock()
    session.config = {
        "hooks": [
            {
                "module": "hooks-streaming-ui",
                "config": {
                    "ui": {
                        "stream_tokens": stream_tokens,
                    }
                },
            }
        ]
    }
    return session


def _session_no_hooks() -> MagicMock:
    """Return a session with an empty hooks list."""
    session = MagicMock()
    session.config = {"hooks": []}
    return session


def _session_no_config() -> MagicMock:
    """Return a session where config is None."""
    session = MagicMock()
    session.config = None
    return session


def _session_missing_config_attr() -> MagicMock:
    """Return a session with NO config attribute at all."""
    session = MagicMock(spec=[])  # no attributes
    return session


def _session_top_level_ui(stream_tokens: bool = True) -> MagicMock:
    """Return a session whose top-level ui block (fallback path) has stream_tokens."""
    session = MagicMock()
    session.config = {
        "hooks": [],
        "ui": {"stream_tokens": stream_tokens},
    }
    return session


# ---------------------------------------------------------------------------
# _streaming_overlay_active — core detection logic
# ---------------------------------------------------------------------------


class TestStreamingOverlayActive:
    """Unit tests for the _streaming_overlay_active() helper."""

    def test_returns_true_when_stream_tokens_and_tty(self, monkeypatch):
        """Returns True when stream_tokens is True AND stdout is a TTY."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_with_streaming_ui(stream_tokens=True)
        assert _streaming_overlay_active(session) is True

    def test_returns_false_when_not_tty(self, monkeypatch):
        """Returns False when stdout is not a TTY, even if stream_tokens is True."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        session = _session_with_streaming_ui(stream_tokens=True)
        assert _streaming_overlay_active(session) is False

    def test_returns_false_when_stream_tokens_false(self, monkeypatch):
        """Returns False when stream_tokens is explicitly False."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_with_streaming_ui(stream_tokens=False)
        assert _streaming_overlay_active(session) is False

    def test_returns_false_when_no_hooks(self, monkeypatch):
        """Returns False when the hooks list is empty."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_no_hooks()
        assert _streaming_overlay_active(session) is False

    def test_returns_false_when_config_is_none(self, monkeypatch):
        """Returns False when session.config is None — no exception raised."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_no_config()
        assert _streaming_overlay_active(session) is False

    def test_returns_false_when_no_config_attr(self, monkeypatch):
        """Returns False when the session has no config attribute at all."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_missing_config_attr()
        assert _streaming_overlay_active(session) is False

    def test_returns_false_on_exception(self, monkeypatch):
        """Any unexpected exception must be swallowed and False returned."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        class BoomSession:
            @property
            def config(self):
                raise RuntimeError("unexpected boom")

        assert _streaming_overlay_active(BoomSession()) is False

    def test_matching_streaming_ui_hyphen_name(self, monkeypatch):
        """Module name 'hooks-streaming-ui' (with hyphen) is matched."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = MagicMock()
        session.config = {
            "hooks": [
                {
                    "module": "hooks-streaming-ui",
                    "config": {"ui": {"stream_tokens": True}},
                }
            ]
        }
        assert _streaming_overlay_active(session) is True

    def test_matching_streaming_ui_underscore_name(self, monkeypatch):
        """Module name 'hooks_streaming_ui' (with underscore) is also matched."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = MagicMock()
        session.config = {
            "hooks": [
                {
                    "module": "hooks_streaming_ui",
                    "config": {"ui": {"stream_tokens": True}},
                }
            ]
        }
        assert _streaming_overlay_active(session) is True

    def test_non_matching_hook_ignored(self, monkeypatch):
        """Hook modules that don't contain 'streaming-ui' or 'streaming_ui' are ignored."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = MagicMock()
        session.config = {
            "hooks": [
                {
                    "module": "hooks-notifications",
                    "config": {"ui": {"stream_tokens": True}},
                }
            ]
        }
        assert _streaming_overlay_active(session) is False

    def test_fallback_top_level_ui_stream_tokens(self, monkeypatch):
        """Fallback: a top-level ui.stream_tokens key is also honoured."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_top_level_ui(stream_tokens=True)
        assert _streaming_overlay_active(session) is True

    def test_fallback_top_level_ui_false(self, monkeypatch):
        """Fallback top-level ui.stream_tokens=False → False."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = _session_top_level_ui(stream_tokens=False)
        assert _streaming_overlay_active(session) is False

    def test_multiple_hooks_only_matching_one_matters(self, monkeypatch):
        """When multiple hooks are present, only the streaming-ui entry is checked."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = MagicMock()
        session.config = {
            "hooks": [
                {
                    "module": "hooks-notifications",
                    "config": {"ui": {"stream_tokens": True}},
                },
                {
                    "module": "hooks-streaming-ui",
                    "config": {"ui": {"stream_tokens": True}},
                },
            ]
        }
        assert _streaming_overlay_active(session) is True

    def test_stream_tokens_truthy_nonbool(self, monkeypatch):
        """stream_tokens=1 (truthy non-bool) is treated as True."""
        from amplifier_app_cli.main import _streaming_overlay_active

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        session = MagicMock()
        session.config = {
            "hooks": [
                {
                    "module": "hooks-streaming-ui",
                    "config": {"ui": {"stream_tokens": 1}},
                }
            ]
        }
        assert _streaming_overlay_active(session) is True


# ---------------------------------------------------------------------------
# render_message gate — interactive_chat._execute_with_interrupt
# ---------------------------------------------------------------------------


def _make_mock_session_for_gate() -> MagicMock:
    """Minimal session mock for interactive_chat gate tests."""
    mock_ctx = MagicMock()
    mock_ctx.get_messages = AsyncMock(return_value=[])

    def _coordinator_get(key: str):
        if key == "context":
            return mock_ctx
        if key == "providers":
            return {}
        return None  # hooks=None → no hook emits

    mock_session = MagicMock()
    mock_session.session_id = "test-session-id"
    mock_session.execute = AsyncMock(return_value="Hello!")
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.get = _coordinator_get
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.coordinator.cancellation.is_cancelled = False
    mock_session.coordinator.cancellation.is_immediate = False
    mock_session.coordinator.session_state = {}
    mock_session.config = {}
    return mock_session


def _make_mock_initialized_for_gate(session: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.session = session
    mock.session_id = "test-session-id"
    mock.configurator = None
    mock.cleanup = AsyncMock()
    return mock


class TestRenderMessageGate:
    """render_message draw is gated by _streaming_overlay_active.

    The gate lives inside interactive_chat._execute_with_interrupt.
    Tests drive interactive_chat with initial_prompt="Hi" and a mock
    PromptSession that raises EOFError on the first REPL iteration so
    the function exits after processing the initial prompt.
    """

    @pytest.mark.asyncio
    async def test_render_message_skipped_when_overlay_active(self, tmp_path: Path):
        """When _streaming_overlay_active returns True, render_message is not called."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session_for_gate()
        initialized = _make_mock_initialized_for_gate(session)

        # PromptSession raises EOFError immediately so the REPL exits after
        # _execute_with_interrupt processes the initial prompt.
        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=EOFError)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}._create_prompt_session", return_value=mock_ps),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}._process_runtime_mentions",
                new=AsyncMock(side_effect=lambda s, t: t),
            ),
            patch(f"{_MODULE}.get_effective_config_summary"),
            # Force the overlay active — render_message must be skipped.
            patch(f"{_MODULE}._streaming_overlay_active", return_value=True),
            # Intercept render_message at its source module.
            patch("amplifier_app_cli.ui.render_message") as mock_render,
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
                initial_prompt="Hi",
            )

        mock_render.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_message_called_when_overlay_inactive(self, tmp_path: Path):
        """When _streaming_overlay_active returns False, render_message IS called."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session_for_gate()
        initialized = _make_mock_initialized_for_gate(session)

        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=EOFError)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}._create_prompt_session", return_value=mock_ps),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}._process_runtime_mentions",
                new=AsyncMock(side_effect=lambda s, t: t),
            ),
            patch(f"{_MODULE}.get_effective_config_summary"),
            # Overlay inactive — render_message must be called.
            patch(f"{_MODULE}._streaming_overlay_active", return_value=False),
            patch("amplifier_app_cli.ui.render_message") as mock_render,
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
                initial_prompt="Hi",
            )

        mock_render.assert_called_once()
