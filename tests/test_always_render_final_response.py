"""Tests: render_message is ALWAYS called for the final assistant response.

Fix #256: The streaming-UI hook no longer paints the final response.
app-cli's _execute_with_interrupt is the sole owner — it calls render_message
unconditionally, regardless of overlay / streaming state.

RED phase: These tests FAIL on the pre-fix code because _execute_with_interrupt
gates render_message behind ``if not _streaming_overlay_active(session):``.
When the overlay would be active (stream_tokens=True + TTY), the gate suppresses
the call and assert_called_once() raises.

GREEN phase: Once the gate is removed and _streaming_overlay_active is deleted
the calls go through and both assertions pass.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MODULE = "amplifier_app_cli.main"


# ---------------------------------------------------------------------------
# Session / initialized mock helpers (mirrors test_overlay_active_detection.py)
# ---------------------------------------------------------------------------


def _make_mock_session(config: dict | None = None) -> MagicMock:
    """Minimal session mock for _execute_with_interrupt tests."""
    mock_ctx = MagicMock()
    mock_ctx.get_messages = AsyncMock(return_value=[])

    def _coordinator_get(key: str):
        if key == "context":
            return mock_ctx
        if key == "providers":
            return {}
        return None  # hooks=None → no hook emits

    session = MagicMock()
    session.session_id = "test-session-id"
    session.execute = AsyncMock(return_value="Hello!")
    session.coordinator = MagicMock()
    session.coordinator.get = _coordinator_get
    session.coordinator.cancellation = MagicMock()
    session.coordinator.cancellation.is_cancelled = False
    session.coordinator.cancellation.is_immediate = False
    session.coordinator.session_state = {}
    session.config = config if config is not None else {}
    return session


def _make_initialized(session: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.session = session
    mock.session_id = "test-session-id"
    mock.configurator = None
    mock.cleanup = AsyncMock()
    return mock


def _streaming_config() -> dict:
    """Session config that would make _streaming_overlay_active return True."""
    return {
        "hooks": [
            {
                "module": "hooks-streaming-ui",
                "config": {"ui": {"stream_tokens": True}},
            }
        ]
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAlwaysRenderFinalResponse:
    """render_message is called after session.execute() in every scenario.

    The streaming-UI hook no longer owns the final response render (#256);
    _execute_with_interrupt is the sole owner and must ALWAYS call it.
    """

    @pytest.mark.asyncio
    async def test_render_message_called_even_when_overlay_would_be_active(
        self, tmp_path: Path
    ):
        """render_message IS called even when streaming overlay conditions are met.

        Before #256 the gate ``if not _streaming_overlay_active(session):``
        suppressed render_message when stream_tokens=True + TTY.  After the
        fix the gate is gone and render_message fires unconditionally.

        This test is the regression guard: it was RED on pre-fix code.
        """
        from amplifier_app_cli.main import interactive_chat

        # Config that would have activated the overlay (stream_tokens=True).
        session = _make_mock_session(config=_streaming_config())
        initialized = _make_initialized(session)

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
            # Force isatty=True so the old gate would have fired and suppressed
            # render_message.  After the fix the gate is gone, so this has no
            # effect on the render path.
            patch.object(sys.stdout, "isatty", new=lambda: True),
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

        # Must be called exactly once — app-cli is the sole render owner.
        mock_render.assert_called_once()


class TestInteractiveQueueWhileRunning:
    """The prompt loop must keep accepting input while a turn is running."""

    @pytest.mark.asyncio
    async def test_second_prompt_is_queued_before_first_finishes(self, tmp_path: Path):
        from amplifier_app_cli.main import interactive_chat

        first_started = asyncio.Event()
        second_prompt_seen = asyncio.Event()
        allow_first_to_finish = asyncio.Event()
        executed: list[str] = []

        async def execute(prompt: str) -> str:
            executed.append(f"start:{prompt}")
            if prompt == "first":
                first_started.set()
                await allow_first_to_finish.wait()
            executed.append(f"end:{prompt}")
            return f"{prompt} done"

        session = _make_mock_session()
        session.execute = AsyncMock(side_effect=execute)
        initialized = _make_initialized(session)

        prompt_calls = 0

        async def prompt_async():
            nonlocal prompt_calls
            prompt_calls += 1
            if prompt_calls == 1:
                return "first"
            if prompt_calls == 2:
                await first_started.wait()
                assert "end:first" not in executed
                second_prompt_seen.set()
                return "second"
            await second_prompt_seen.wait()
            allow_first_to_finish.set()
            raise EOFError

        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=prompt_async)

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
            patch("amplifier_app_cli.ui.render_message"),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await asyncio.wait_for(
                interactive_chat(
                    config={},
                    search_paths=[tmp_path],
                    verbose=False,
                    bundle_name="test-bundle",
                ),
                timeout=2,
            )

        assert executed == [
            "start:first",
            "end:first",
            "start:second",
            "end:second",
        ]


class TestAlwaysRenderFinalResponseNoStreaming:
    """Additional final-response render coverage."""

    @pytest.mark.asyncio
    async def test_render_message_called_when_no_streaming_config(self, tmp_path: Path):
        """render_message IS called when no streaming-ui hook is configured.

        Sanity-check: the non-streaming path must also always render.
        """
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session()  # empty config — no streaming
        initialized = _make_initialized(session)

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
