"""Terminal ambient signals and background-shell ownership."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.application import in_terminal
from prompt_toolkit.application.current import set_app

from .repl import terminal_notification_sequence
from .repl import terminal_tab_color_sequence
from .repl import terminal_title_sequence

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from .notices import TransientNoticeState

    class _LayeredReplTerminalOwner(Protocol):
        application: Application[Any]
        _ambient_state: str
        _background_process: asyncio.subprocess.Process | None
        _background_shell_task: asyncio.Task[None] | None
        _background_terminal_active: bool
        _backgrounded: bool
        _notices: TransientNoticeState
        _owner_loop: asyncio.AbstractEventLoop | None
        _pending_terminal_sequences: list[str]
        _session_id: str | None
        _terminal_file: Any

        def _emit_terminal_sequence(self, sequence: str) -> None: ...

        async def _run_background_shell(self) -> None: ...

        def commit_plan_state(self, lifecycle: str) -> bool: ...


class LayeredReplTerminalMixin:
    """Emit terminal metadata and temporarily suspend into a shell."""

    def capability_hint_overrides(self) -> dict[str, str] | None:
        """No per-capability keybinding-label catalog exists at this revision."""
        return None

    def emit_terminal_title(self: _LayeredReplTerminalOwner, title: str) -> None:
        self._emit_terminal_sequence(terminal_title_sequence(title))

    def emit_ambient_state(
        self: _LayeredReplTerminalOwner,
        *,
        is_running: bool,
        needs_count: int,
    ) -> None:
        state = "needs-you" if needs_count else ("running" if is_running else "idle")
        if state == self._ambient_state:
            return
        self._ambient_state = state
        self._emit_terminal_sequence(terminal_tab_color_sequence(state))

    def mark_backgrounded(self: _LayeredReplTerminalOwner) -> bool:
        self._backgrounded = True
        owner_loop = self._owner_loop
        if (
            owner_loop is None
            or owner_loop.is_closed()
            or not self.application.is_running
        ):
            self._notices.show("completion notification armed")
            return False
        if (
            self._background_shell_task is not None
            and not self._background_shell_task.done()
        ):
            self._notices.show("background shell is already active")
            return True
        self._notices.show("detaching to shell · exit returns to session")
        self._background_shell_task = owner_loop.create_task(
            self._run_background_shell()
        )
        return True

    async def _run_background_shell(self: _LayeredReplTerminalOwner) -> None:
        shell = os.environ.get("SHELL") or "/bin/sh"
        shell_path = Path(shell).expanduser()
        if (
            not shell_path.is_absolute()
            or not shell_path.is_file()
            or not os.access(shell_path, os.X_OK)
        ):
            shell = "/bin/sh"
        else:
            shell = str(shell_path)
        environment = {
            **os.environ,
            "AMPLIFIER_BACKGROUND_SESSION": self._session_id,
        }
        process: asyncio.subprocess.Process | None = None
        self._background_terminal_active = True
        try:
            with set_app(self.application):
                async with in_terminal(render_cli_done=False):
                    self._terminal_file.write(
                        "\nAmplifier is running in the background. "
                        "Type 'exit' to return to the session.\n"
                    )
                    self._terminal_file.flush()
                    process = await asyncio.create_subprocess_exec(
                        shell,
                        "-l",
                        env=environment,
                    )
                    self._background_process = process
                    await process.wait()
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        finally:
            self._background_process = None
            self._background_terminal_active = False
            self._backgrounded = False
            self._background_shell_task = None
            self.application.invalidate()

    def notify_turn_complete(self: _LayeredReplTerminalOwner, summary: str) -> None:
        self.commit_plan_state(
            "interrupted" if summary.strip() == "interrupted" else "incomplete"
        )
        if not self._backgrounded:
            return
        self._emit_terminal_sequence(
            terminal_notification_sequence("Amplifier turn complete", summary)
        )
        self._backgrounded = False

    def notify_turn_failed(self: _LayeredReplTerminalOwner) -> None:
        """Persist a failed plan snapshot before transient turn state clears."""
        self.commit_plan_state("failed")

    def _emit_terminal_sequence(self: _LayeredReplTerminalOwner, sequence: str) -> None:
        if self._background_terminal_active:
            self._terminal_file.write(sequence)
            self._terminal_file.flush()
            return
        if self.application.is_running:
            self._pending_terminal_sequences.append(sequence)
            self.application.invalidate()
            return
        self._terminal_file.write(sequence)
        self._terminal_file.flush()

    def _flush_terminal_sequences(
        self: _LayeredReplTerminalOwner, application: Any
    ) -> None:
        if not self._pending_terminal_sequences:
            return
        sequences = tuple(self._pending_terminal_sequences)
        self._pending_terminal_sequences.clear()
        for sequence in sequences:
            application.output.write_raw(sequence)
        application.output.flush()


__all__ = ["LayeredReplTerminalMixin"]
