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

import asyncio
import contextlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.output import DummyOutput

_MODULE = "amplifier_app_cli.main"


@pytest.fixture(autouse=True, scope="module")
def _use_headless_patch_stdout():
    """Keep mocked REPL tests from opening a real Windows console preflight."""
    # These tests already fake PromptSession and its console. Patch only main's
    # imported alias so a headless CI console cannot mask their startup contract.
    original_init = PromptSession.__init__

    def _headless_init(self, *args, **kwargs):
        kwargs["output"] = DummyOutput()
        return original_init(self, *args, **kwargs)

    with patch(
        f"{_MODULE}.patch_stdout",
        new=lambda *_args, **_kwargs: contextlib.nullcontext(),
    ):
        with patch.object(PromptSession, "__init__", new=_headless_init):
            yield


@pytest.fixture
def participating_mode_root(tmp_path, monkeypatch):
    """Reuse the optional installed-mode assembly without adding it to old tests."""
    pytest.importorskip("amplifier_module_hooks_mode", reason="Installed participating modes fixture")
    pytest.importorskip("amplifier_module_tool_mode", reason="Installed participating modes fixture")
    # conftest makes this directory importable. Borrow only the fixture, never
    # another checkout or a source-path override for the installed dependencies.
    from test_session_media_modes import mode_root

    yield from mode_root.__wrapped__(tmp_path, monkeypatch)


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

        mock_ps.prompt_async.assert_awaited_once_with(set_exception_handler=False)
        mock_close.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["/exit", "/exit ", "/quit", "/quit "])
    async def test_slash_exit_commands_teardown_without_executing_a_turn(
        self, tmp_path: Path, command: str
    ):
        """A slash exit leaves through the REPL's normal cleanup path."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session()
        initialized = _make_initialized(session)
        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=[command])
        mock_close = MagicMock()
        runtime_mentions = AsyncMock(side_effect=lambda s, t: t)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}._create_prompt_session", return_value=mock_ps),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}.process_runtime_mentions", new=runtime_mentions),
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

        mock_ps.prompt_async.assert_awaited_once()
        session.execute.assert_not_awaited()
        runtime_mentions.assert_not_awaited()
        initialized.cleanup.assert_awaited_once()
        mock_close.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["exit", "quit"])
    async def test_bare_exit_commands_remain_compatible(
        self, tmp_path: Path, command: str
    ):
        """The pre-existing bare exit aliases continue to leave the REPL."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session()
        initialized = _make_initialized(session)
        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=[command])
        mock_close = MagicMock()
        runtime_mentions = AsyncMock(side_effect=lambda s, t: t)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}._create_prompt_session", return_value=mock_ps),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}.process_runtime_mentions", new=runtime_mentions),
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

        mock_ps.prompt_async.assert_awaited_once()
        session.execute.assert_not_awaited()
        runtime_mentions.assert_not_awaited()
        initialized.cleanup.assert_awaited_once()
        mock_close.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["/exit later", "/quit later"])
    async def test_slash_exit_with_arguments_prints_usage_and_keeps_prompting(
        self, tmp_path: Path, command: str
    ):
        """Exit arguments are rejected without sending a model turn."""
        from amplifier_app_cli.main import interactive_chat

        session = _make_mock_session()
        initialized = _make_initialized(session)
        mock_ps = MagicMock()
        mock_ps.prompt_async = AsyncMock(side_effect=[command, EOFError])
        mock_close = MagicMock()
        runtime_mentions = AsyncMock(side_effect=lambda s, t: t)
        mock_console = MagicMock()

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}._create_prompt_session", return_value=mock_ps),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console", new=mock_console),
            patch(f"{_MODULE}.process_runtime_mentions", new=runtime_mentions),
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

        assert mock_ps.prompt_async.await_count == 2
        mock_console.print.assert_any_call(
            f"[cyan]Usage: {command.split()[0]}[/cyan]"
        )
        session.execute.assert_not_awaited()
        runtime_mentions.assert_not_awaited()
        initialized.cleanup.assert_awaited_once()
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
        mock_ps.prompt_async.assert_awaited_once()
        initialized.cleanup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_loop_error_reporter_spans_public_chat_lifetime(
    tmp_path: Path, cleanup_fails: bool
):
    """The same owner must cover initialization, idle input, and cleanup."""
    from amplifier_app_cli.main import interactive_chat

    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    observer = MagicMock()
    owners = {}
    session = _make_mock_session()
    initialized = _make_initialized(session)
    failure = RuntimeError("synthetic cleanup failure")

    async def initialize(*_args, **_kwargs):
        owners["initialize"] = loop.get_exception_handler()
        return initialized

    async def prompt(*_args, **_kwargs):
        owners["prompt"] = loop.get_exception_handler()
        raise EOFError

    async def cleanup():
        owners["cleanup"] = loop.get_exception_handler()
        if cleanup_fails:
            raise failure

    initialized.cleanup.side_effect = cleanup
    mock_ps = MagicMock()
    mock_ps.prompt_async = AsyncMock(side_effect=prompt)
    mock_close = MagicMock()

    loop.set_exception_handler(observer)
    try:
        with (
            patch(f"{_MODULE}.create_initialized_session", new=initialize),
            patch(f"{_MODULE}._create_prompt_session", return_value=mock_ps),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.AppSettings"),
            patch(f"{_MODULE}.console"),
            patch(f"{_MODULE}.get_effective_config_summary"),
            patch(f"{_MODULE}.close_dedicated_tty_input", new=mock_close),
        ):
            MockStore.return_value.get_metadata_if_exists.return_value = {}
            chat = interactive_chat(
                config={},
                search_paths=[tmp_path],
                verbose=False,
                bundle_name="test-bundle",
            )
            if cleanup_fails:
                with pytest.raises(RuntimeError) as caught:
                    await chat
                assert caught.value is failure
            else:
                await chat

        assert set(owners) == {"initialize", "prompt", "cleanup"}
        reporter = owners["initialize"]
        assert reporter is not None and reporter is not observer, (
            "interactive_chat must own loop error reporting before initialization"
        )
        assert owners["prompt"] is reporter
        assert owners["cleanup"] is reporter, (
            "The reporter must remain active while session cleanup runs"
        )
        assert loop.get_exception_handler() is observer
        initialized.cleanup.assert_awaited_once()
        session.execute.assert_not_awaited()
        mock_close.assert_called_once()
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [
    "amplifier_app_cli.incremental_save.register_incremental_save",
    "amplifier_app_cli.goal_progress_hook.register_goal_progress_hook",
    "amplifier_app_cli.goal_circuit_breaker.register_goal_circuit_breaker",
    "amplifier_app_cli.session_hooks.seal_session_hook_graph",
])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_interactive_late_setup_failure_unwinds_initialized_owner(tmp_path, stage, cancelled):
    from amplifier_app_cli.main import interactive_chat
    from amplifier_app_cli.session_runner import InitializedSession, SessionConfig
    from contextlib import ExitStack
    session = _make_mock_session()
    effects = []
    session.cleanup = AsyncMock(side_effect=lambda: effects.append("media-cleanup"))
    writer = MagicMock()
    writer.release.side_effect = lambda: effects.append("release")
    initialized = InitializedSession(
        session, "test-session-id", SessionConfig({}, [], False),
        store=MagicMock(), root_state=writer,
    )
    failure = asyncio.CancelledError() if cancelled else RuntimeError("late setup failed")
    with ExitStack() as stack:
        stack.enter_context(patch(f"{_MODULE}.create_initialized_session", AsyncMock(return_value=initialized)))
        stack.enter_context(patch("amplifier_app_cli.incremental_save.register_incremental_save"))
        stack.enter_context(patch("amplifier_app_cli.goal_progress_hook.register_goal_progress_hook"))
        stack.enter_context(patch("amplifier_app_cli.goal_circuit_breaker.register_goal_circuit_breaker"))
        stack.enter_context(patch("amplifier_app_cli.session_hooks.seal_session_hook_graph"))
        stack.enter_context(patch(stage, side_effect=failure))
        prompt = stack.enter_context(patch(f"{_MODULE}._create_prompt_session"))
        close = stack.enter_context(patch(f"{_MODULE}.close_dedicated_tty_input"))
        stack.enter_context(patch(f"{_MODULE}.console"))
        with pytest.raises(type(failure)) as caught:
            await interactive_chat(config={}, search_paths=[tmp_path], verbose=False, bundle_name="fixture")
    assert caught.value is failure
    assert effects == ["media-cleanup", "release"]
    session.cleanup.assert_awaited_once()
    session.execute.assert_not_awaited()
    prompt.assert_not_called()
    close.assert_called_once()


@pytest.mark.asyncio
async def test_interactive_late_setup_cleanup_failure_retains_writer_and_original_cause(tmp_path):
    from amplifier_app_cli.main import interactive_chat
    from amplifier_app_cli.session_runner import InitializedSession, SessionConfig
    session = _make_mock_session()
    session.cleanup = AsyncMock(side_effect=RuntimeError("cleanup unsettled"))
    writer = MagicMock()
    initialized = InitializedSession(
        session, "test-session-id", SessionConfig({}, [], False),
        store=MagicMock(), root_state=writer,
    )
    failure = RuntimeError("seal failed")
    with (
        patch(f"{_MODULE}.create_initialized_session", AsyncMock(return_value=initialized)),
        patch("amplifier_app_cli.incremental_save.register_incremental_save"),
        patch("amplifier_app_cli.goal_progress_hook.register_goal_progress_hook"),
        patch("amplifier_app_cli.goal_circuit_breaker.register_goal_circuit_breaker"),
        patch("amplifier_app_cli.session_hooks.seal_session_hook_graph", side_effect=failure),
        patch(f"{_MODULE}.close_dedicated_tty_input") as close,
        patch(f"{_MODULE}.console"),
    ):
        with pytest.raises(RuntimeError) as caught:
            await interactive_chat(config={}, search_paths=[tmp_path], verbose=False, bundle_name="fixture")
    assert caught.value is failure
    assert any("writer retained" in note for note in failure.__notes__)
    session.cleanup.assert_awaited_once()
    writer.release.assert_not_called()
    close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_cancelled", [False, True])
@pytest.mark.parametrize("cleanup_cancelled", [False, True])
async def test_late_cleanup_log_withholds_private_exception_chain_at_entry(
    tmp_path, primary_cancelled, cleanup_cancelled
):
    """The logger must never receive exceptions, traceback or private messages."""
    import logging
    from amplifier_app_cli.main import interactive_chat
    from amplifier_app_cli.session_runner import InitializedSession, SessionConfig

    sentinel = "data:image/png;base64,PRIVATEPIXELS?secret=cleanup-token"
    base_primary = asyncio.CancelledError if primary_cancelled else RuntimeError
    class PrimaryError(base_primary):
        def add_note(self, note):
            raise AssertionError("exception override must not replace original cause")
    primary_type = PrimaryError
    cleanup_type = asyncio.CancelledError if cleanup_cancelled else ValueError
    primary = primary_type(sentinel + "-primary")
    cleanup = cleanup_type(sentinel + "-cleanup")
    cleanup.__cause__ = RuntimeError(sentinel + "-chain")
    session = _make_mock_session()
    session.cleanup = AsyncMock(side_effect=cleanup)
    writer = MagicMock()
    initialized = InitializedSession(
        session, "test-session-id", SessionConfig({}, [], False),
        store=MagicMock(), root_state=writer,
    )
    received = []

    def log_at_entry(message, *args, **kwargs):
        # Inspect before any formatter/filter can remove evidence.
        received.append((message, args, kwargs))
        assert args == ()
        assert kwargs == {"exc_info": None, "stack_info": False}
        assert sentinel not in message
        record = logging.LogRecord("test", logging.WARNING, __file__, 1, message, args, None)
        rendered = logging.Formatter("%(levelname)s %(message)s").format(record)
        assert sentinel not in rendered and "Traceback" not in rendered
        assert len(rendered) < 256

    with (
        patch(f"{_MODULE}.create_initialized_session", AsyncMock(return_value=initialized)),
        patch("amplifier_app_cli.incremental_save.register_incremental_save"),
        patch("amplifier_app_cli.goal_progress_hook.register_goal_progress_hook"),
        patch("amplifier_app_cli.goal_circuit_breaker.register_goal_circuit_breaker"),
        patch("amplifier_app_cli.session_hooks.seal_session_hook_graph", side_effect=primary),
        patch(f"{_MODULE}.logger.warning", side_effect=log_at_entry),
        patch(f"{_MODULE}.close_dedicated_tty_input") as close,
        patch(f"{_MODULE}.console"),
    ):
        with pytest.raises(primary_type) as caught:
            await interactive_chat(
                config={}, search_paths=[tmp_path], verbose=False, bundle_name="fixture"
            )
    assert caught.value is primary
    assert primary.args == (sentinel + "-primary",)
    assert cleanup.args == (sentinel + "-cleanup",)
    assert cleanup.__cause__.args == (sentinel + "-chain",)
    assert len(received) == 1
    assert primary.__notes__ == ["Interactive setup cleanup did not settle; writer retained"]
    session.cleanup.assert_awaited_once()
    writer.release.assert_not_called()
    close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX durable media acknowledgement")
@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize("failure", ["overlay-error", "overlay-cancel", "external-cancel"])
@pytest.mark.parametrize("custody", [None, "root", "binding"])
async def test_actual_repl_participating_mode_failure_is_private_before_rendering(
    participating_mode_root, verbose, failure, custody,
):
    """Real REPL -> CLI command -> installed ModeHooks -> real graph refusal.

    Only prompt input/output and external ownership listeners are synthetic.
    The borrowed root has already registered/sealed its hooks, so skip duplicate
    late REPL observers. Never execute inference or open a terminal.
    """
    import base64
    import importlib
    import json
    import logging
    import sys
    from io import StringIO
    from rich.console import Console
    from amplifier_app_cli.main import CommandProcessor, interactive_chat
    from test_session_media_modes import RASTERS, _observe_real_mode_verify

    main = importlib.import_module(_MODULE)
    private = "data:image/png;base64," + base64.b64encode(RASTERS["PNG"]).decode() + "?secret=mode-token"
    conversions, prints, exceptions, events, logs, processors, closed = [], [], [], [], [], [], []

    class PrivateError(RuntimeError):
        def __str__(self):
            conversions.append("str")
            return private

        def __repr__(self):
            conversions.append("repr")
            return private

    class PrivateCancellation(asyncio.CancelledError):
        def __str__(self):
            conversions.append("cancel-str")
            return private

        def __repr__(self):
            conversions.append("cancel-repr")
            return private

    class CleanupUnsettled(RuntimeError):
        pass

    primary = PrivateCancellation(private) if failure == "overlay-cancel" else PrivateError(private)
    primary.__cause__ = ValueError(private + "-cause")
    primary.__context__ = ValueError(private + "-context")
    BaseException.add_note(primary, private + "-note")
    cleanup_error = CleanupUnsettled("synthetic cleanup uncertainty")
    stream = StringIO()

    class EntryConsole(Console):
        def print(self, *objects, **kwargs):
            prints.append((objects, kwargs))
            return super().print(*objects, **kwargs)

        def print_exception(self, *args, **kwargs):
            exceptions.append(sys.exception())
            return super().print_exception(*args, **kwargs)

    def log_entry(logger, level, message, arguments, exc_info=None, extra=None,
                  stack_info=False, stacklevel=1):
        logs.append((message, arguments, exc_info, extra, stack_info))

    original_init = CommandProcessor.__init__

    def processor_init(processor, *args, **kwargs):
        original_init(processor, *args, **kwargs)
        processors.append(processor)

    # A failed physical-close witness must also survive the borrowed fixture's
    # final cleanup call without replay. Its cached failure is expected there.
    with contextlib.ExitStack() as expected:
        if custody:
            expected.enter_context(pytest.raises(CleanupUnsettled))
        async with participating_mode_root() as root:
            await root.add_pixels("PNG")
            predecessor = root.graph.construction
            receipt = root.bridge.graph_receipt
            overlay = root.mode_hooks._get_or_create_overlay()
            calls = []
            native_emit = root.mode_hooks.coordinator.hooks.emit
            original_close = root.host._close
            writer, binding = MagicMock(), MagicMock()
            if custody == "root":
                root.initialized.root_state = writer
            elif custody == "binding":
                root.initialized.config.canonical_binding = binding

            async def physical_close():
                closed.append("media")
                await original_close()
                if custody:
                    raise cleanup_error

            async def event_entry(name, payload):
                events.append((name, payload))
                return await native_emit(name, payload)

            async def fail_overlay(*args, **kwargs):
                calls.append("overlay")
                if failure != "external-cancel":
                    raise primary
                return await original_apply(*args, **kwargs)

            original_apply = overlay.apply
            if failure == "external-cancel":
                # Real Core swallows this unrelated callback's cancellation;
                # the issuer, not the REPL, converts its failed seal to error.
                root.fault_event = "mode:activated"
                root.fault = asyncio.CancelledError("synthetic external callback cancellation")
            prompt = MagicMock()
            prompt.prompt_async = AsyncMock(side_effect=[
                "/mode alpha on must-not-run", "/mode off", "/exit",
            ])
            runtime_mentions = AsyncMock(side_effect=AssertionError("must not execute trailing prompt"))
            cleanup = AsyncMock(wraps=root.initialized.cleanup)
            patchers = (
                patch.object(main, "create_initialized_session", AsyncMock(return_value=root.initialized)),
                patch.object(CommandProcessor, "__init__", new=processor_init),
                patch.object(main, "_create_prompt_session", return_value=prompt),
                patch("amplifier_app_cli.incremental_save.register_incremental_save"),
                patch("amplifier_app_cli.goal_progress_hook.register_goal_progress_hook"),
                patch("amplifier_app_cli.goal_circuit_breaker.register_goal_circuit_breaker"),
                patch("amplifier_app_cli.session_handoff.CLIHandoff.start", AsyncMock()),
                patch.object(main, "AppSettings"),
                patch.object(main, "console", EntryConsole(file=stream, width=100)),
                patch.object(main, "process_runtime_mentions", runtime_mentions),
                patch.object(root.initialized, "cleanup", cleanup),
                patch.object(root.host, "_close", side_effect=physical_close),
                patch.object(overlay, "apply", side_effect=fail_overlay),
                patch.object(root.mode_hooks.coordinator.hooks, "emit", side_effect=event_entry),
                patch.object(logging.Logger, "_log", new=log_entry),
            )
            with contextlib.ExitStack() as stack:
                for patcher in patchers:
                    stack.enter_context(patcher)
                tty = stack.enter_context(patch.object(main, "close_dedicated_tty_input"))
                verifications = stack.enter_context(_observe_real_mode_verify(root))
                try:
                    await asyncio.wait_for(
                        interactive_chat(config={}, search_paths=[], verbose=verbose, bundle_name="mode-fixture"),
                        timeout=5,
                    )
                except BaseException as error:
                    if custody:
                        assert error is cleanup_error
                    else:
                        assert failure == "overlay-cancel" and error is primary
                else:
                    assert not custody and failure != "overlay-cancel"
                cleanup.assert_awaited_once()
                tty.assert_called_once()
                runtime_mentions.assert_not_awaited()
            assert main._INTERACTIVE_SETUP.get() is None
            assert closed == ["media"]
            assert not conversions, "Private error was formatted before reaching the console"
            assert not exceptions, "Private exception reached the verbose traceback renderer"
            for objects, kwargs in prints:
                assert all(not isinstance(value, BaseException) for value in objects)
                assert all(private not in value for value in objects if type(value) is str)
                assert not kwargs
            assert private not in stream.getvalue() and "Traceback" not in stream.getvalue()
            safe_message = (
                "[red]Participating mode operation failed; host recovery is required. "
                "Exception details withheld.[/red]"
            )
            safe_prints = [objects for objects, _kwargs in prints if objects == (safe_message,)]
            assert len(safe_prints) == (0 if failure == "overlay-cancel" else 2)
            for message, arguments, exc_info, extra, stack_info in logs:
                assert type(message) is str and private not in message
                assert not arguments and exc_info is None and extra is None and not stack_info
            assert private not in json.dumps(events)
            assert len(calls) == 1 and len(verifications) == 1
            assert root.graph.construction is not predecessor
            assert root.bridge.graph_receipt is None and not root.bridge.validate_receipt(receipt)
            assert root.evidence()["state"] == "invalid"
            state = root.coordinator.session_state
            assert state["active_mode"] is None and state["mode_transition_failure"] == {
                "source": "cli.command", "target_mode": "alpha",
                "reason": "participating-mode-operation-did-not-settle",
            }
            assert private not in json.dumps(state["mode_transition_failure"])
            if failure != "external-cancel":
                assert verifications[0]["error"] is primary
                assert verifications[0]["raised"] is primary
                assert processors[0]._mode_transition_error is primary
                assert primary.args == (private,)
                assert primary.__cause__.args == (private + "-cause",)
                assert primary.__context__.args == (private + "-context",)
                assert primary.__notes__ == [private + "-note"]
            else:
                assert verifications[0]["error"] is None and verifications[0]["raised"] is None
                assert isinstance(processors[0]._mode_transition_error, ValueError)
            assert len(root.events) == 1  # No /mode off recovery or replay.
            assert root.raw.observed == []  # No count/send or inference.
            await root.unchanged_canonical()
            assert root.host.cleanup_attempted and root.host.cleanup_settled is (not custody)
            writer.release.assert_not_called()
            binding.close.assert_not_called()
    assert closed == ["media"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verbose", [False, True])
async def test_actual_repl_legacy_mode_error_keeps_existing_presentation(tmp_path, verbose):
    """A nonparticipating command must still use the normal error renderer."""
    from io import StringIO
    from rich.console import Console
    from amplifier_app_cli.main import CommandProcessor, interactive_chat

    session = _make_mock_session()
    session.coordinator.get_capability.return_value = None
    initialized = _make_initialized(session)
    primary = RuntimeError("synthetic legacy-visible-mode-error")
    prompt = MagicMock()
    prompt.prompt_async = AsyncMock(side_effect=["/mode alpha on", "/exit"])
    stream = StringIO()
    console = Console(file=stream)
    patchers = (
        patch(f"{_MODULE}.create_initialized_session", AsyncMock(return_value=initialized)),
        patch(f"{_MODULE}._create_prompt_session", return_value=prompt),
        patch(f"{_MODULE}.SessionStore"),
        patch(f"{_MODULE}.AppSettings"),
        patch(f"{_MODULE}.console", console),
        patch("amplifier_app_cli.incremental_save.register_incremental_save"),
        patch.object(CommandProcessor, "_emit_mode_event", AsyncMock(side_effect=primary)),
    )
    with contextlib.ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        traceback = stack.enter_context(patch.object(console, "print_exception", wraps=console.print_exception))
        tty = stack.enter_context(patch(f"{_MODULE}.close_dedicated_tty_input"))
        await interactive_chat(config={}, search_paths=[tmp_path], verbose=verbose)
    assert "synthetic legacy-visible-mode-error" in stream.getvalue()
    assert "Exception details withheld" not in stream.getvalue()
    assert traceback.call_count == int(verbose)
    assert "mode_transition_failure" not in session.coordinator.session_state
    initialized.cleanup.assert_awaited_once()
    session.execute.assert_not_awaited()
    tty.assert_called_once()
