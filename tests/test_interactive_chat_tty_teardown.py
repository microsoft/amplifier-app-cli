"""Regression test: interactive_chat() must close the dedicated TTY input fd.

Background
~~~~~~~~~~
``dedicated_tty_input.py`` opens a process-wide, dedicated non-blocking fd
against ``/dev/tty`` so prompt_toolkit never races a competing reader on fd 0
(see that module's docstring for the full freeze mechanism). The contract is:
whoever opens the fd (via ``get_dedicated_tty_input()``) must close it again
via ``close_dedicated_tty_input()`` once the session that opened it tears
down -- otherwise the fd leaks across sessions and spawned sub-sessions.

``execute_single()``'s ``finally`` block already calls
``close_dedicated_tty_input()`` (main.py:3706-3709) -- but that code path
never *opens* the dedicated fd in the first place (it doesn't build a
``PromptSession``). ``interactive_chat()`` is the REPL that DOES build
``PromptSession``s (both the main prompt via ``_create_prompt_session()``
and a fresh steering prompt each turn via ``SteeringInputManager``) -- i.e.
it is the path that actually opens the dedicated fd, and the exact scenario
where the freeze this fix targets was observed. Its ``finally`` block was
missing the matching close call.

RED phase: these tests FAIL on the pre-fix code because
``interactive_chat()``'s ``finally`` block never calls
``close_dedicated_tty_input()``.

GREEN phase: once the call is added (mirroring ``execute_single()``), both
tests pass.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MODULE = "amplifier_app_cli.main"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_always_render_final_response.py / test_session_lifecycle_events.py)
# ---------------------------------------------------------------------------


def _make_mock_session() -> MagicMock:
    mock_ctx = MagicMock()
    mock_ctx.get_messages = AsyncMock(return_value=[])

    def _coordinator_get(key: str):
        if key == "context":
            return mock_ctx
        if key == "providers":
            return {}
        return None  # hooks=None -> no hook emits

    session = MagicMock()
    session.session_id = "test-session-id"
    session.execute = AsyncMock(return_value="Hello!")
    session.coordinator = MagicMock()
    session.coordinator.get = _coordinator_get
    session.coordinator.cancellation = MagicMock()
    session.coordinator.cancellation.is_cancelled = False
    session.coordinator.cancellation.is_immediate = False
    session.coordinator.session_state = {}
    session.config = {}
    return session


def _make_initialized(session: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.session = session
    mock.session_id = "test-session-id"
    mock.configurator = None
    mock.cleanup = AsyncMock()
    return mock


def _run_interactive_chat(tmp_path: Path, mock_close: MagicMock) -> None:
    """Run interactive_chat() through a single EOFError-terminated turn,
    with dedicated-tty-input's close patched so we can observe teardown."""
    from amplifier_app_cli.main import interactive_chat

    session = _make_mock_session()
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
            f"{_MODULE}.process_runtime_mentions",
            new=AsyncMock(side_effect=lambda s, t: t),
        ),
        patch(f"{_MODULE}.get_effective_config_summary"),
        patch(f"{_MODULE}.close_dedicated_tty_input", new=mock_close),
    ):
        store_instance = MockStore.return_value
        store_instance.get_metadata.return_value = {}
        store_instance.save.return_value = None

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
            )
        )


class TestInteractiveChatClosesDedicatedTtyOnTeardown:
    """interactive_chat()'s finally block must close the dedicated TTY fd.

    Regression guard for the gap left by PR #14 / the initial
    dedicated-tty-input fix: execute_single() closed it, but
    interactive_chat() -- the path that actually opens it -- did not.
    """

    @pytest.mark.asyncio
    async def test_close_dedicated_tty_input_called_on_normal_exit(
        self, tmp_path: Path
    ):
        """close_dedicated_tty_input() must be called exactly once when the
        REPL loop exits normally (EOFError, e.g. Ctrl+D)."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session()
        initialized = _make_initialized(session)

        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=EOFError)
        mock_close = MagicMock()

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
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=lambda s, t: t),
            ),
            patch(f"{_MODULE}.get_effective_config_summary"),
            patch(f"{_MODULE}.close_dedicated_tty_input", new=mock_close),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
            )

        mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_dedicated_tty_input_called_even_after_initial_prompt(
        self, tmp_path: Path
    ):
        """Same guard, but through the initial_prompt auto-execute path (the
        shape used by the other interactive_chat regression tests in this
        suite) -- teardown must still close the fd."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session()
        initialized = _make_initialized(session)

        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=EOFError)
        mock_close = MagicMock()

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
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=lambda s, t: t),
            ),
            patch(f"{_MODULE}.get_effective_config_summary"),
            patch("amplifier_app_cli.ui.render_message"),
            patch(f"{_MODULE}.close_dedicated_tty_input", new=mock_close),
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

        mock_close.assert_called_once()


class TestCloseDedicatedTtyTeardownIsRobust:
    """The finally-block call site must not be able to mask the real error.

    ``close_dedicated_tty_input()`` itself is already idempotent by
    contract (safe even if never opened, or already closed) -- these tests
    guard that contract at the real call site: a session that never opened
    the dedicated fd (e.g. non-tty stdin, no controlling terminal) must
    still tear down cleanly, and calling close twice must not raise.
    """

    def test_close_is_idempotent_and_safe_when_never_opened(self):
        """Calling close_dedicated_tty_input() when get_dedicated_tty_input()
        was never called (e.g. stdin isn't a tty) must not raise."""
        from amplifier_app_cli import dedicated_tty_input as dti

        # Ensure clean slate regardless of prior test/process state.
        dti.close_dedicated_tty_input()
        # Calling again with nothing open must be a no-op, not an error.
        dti.close_dedicated_tty_input()
        dti.close_dedicated_tty_input()

    @pytest.mark.asyncio
    async def test_interactive_chat_teardown_does_not_raise_when_fd_never_opened(
        self, tmp_path: Path
    ):
        """End-to-end: even when the dedicated fd was never opened (this
        test's own environment has no attached tty), interactive_chat()'s
        finally block calling close_dedicated_tty_input() must not raise
        and must not mask the underlying session cleanup."""
        from amplifier_app_cli import dedicated_tty_input as dti
        from amplifier_app_cli.main import interactive_chat

        # Real, un-mocked close_dedicated_tty_input -- proves the actual
        # idempotent implementation is safe at this call site too.
        dti.close_dedicated_tty_input()  # clean slate

        session = _make_mock_session()
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
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=lambda s, t: t),
            ),
            patch(f"{_MODULE}.get_effective_config_summary"),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            # Must not raise -- proves the real close_dedicated_tty_input()
            # is safe to call from interactive_chat()'s finally block even
            # when nothing was ever opened.
            await interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
            )

        # cleanup() (session teardown) still ran despite no dedicated fd
        # having been opened -- the close call didn't short-circuit anything.
        initialized.cleanup.assert_awaited_once()
