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

from .keyboard_protocol import FOCUS_IN_KEY
from .keyboard_protocol import FOCUS_OUT_KEY
from .keyboard_protocol import keyboard_enhancement_disable_sequence
from .keyboard_protocol import keyboard_enhancement_enable_sequence
from .repl import terminal_notification_sequence
from .repl import terminal_tab_color_sequence
from .repl import terminal_title_sequence
from .terminal_probe import TerminalCapabilities
from .terminal_probe import capability_hint_overrides
from .terminal_probe import osc9_notification_sequence
from .terminal_probe import osc9_notifications_supported
from .terminal_probe import probe_terminal

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
        _focus_bindings_installed: bool
        _keyboard_enhancements_active: bool
        _notices: TransientNoticeState
        _owner_loop: asyncio.AbstractEventLoop | None
        _pending_terminal_sequences: list[str]
        _session_id: str | None
        _terminal_capabilities: TerminalCapabilities | None
        _terminal_file: Any
        _terminal_focused: bool

        def _emit_terminal_sequence(self, sequence: str) -> None: ...

        def _install_focus_bindings(self, application: Any) -> None: ...

        def _keyboard_enhancement_pop_sequence(self) -> str: ...

        def _set_terminal_focused(self, focused: bool) -> None: ...

        def _sync_keyboard_enhancements(self, application: Any) -> bool: ...

        async def _run_background_shell(self) -> None: ...

        def commit_plan_state(self, lifecycle: str) -> bool: ...


class LayeredReplTerminalMixin:
    """Emit terminal metadata and temporarily suspend into a shell."""

    # Class-level defaults; flipped per instance while the application owns
    # the terminal with keyboard enhancements pushed.
    _keyboard_enhancements_active = False
    # One-shot startup probe result; None until ``probe_terminal_capabilities``
    # runs (embedders and unit tests keep the historical blind push).
    _terminal_capabilities: TerminalCapabilities | None = None
    # Focus tracking (mode 1004) state; assumed focused until a report says
    # otherwise, so notifications never fire without a probed terminal.
    _terminal_focused = True
    _focus_bindings_installed = False

    def probe_terminal_capabilities(
        self: _LayeredReplTerminalOwner,
    ) -> TerminalCapabilities:
        """Probe once at startup, before the application reads input.

        Call from the owner right before ``application.run_async`` takes over
        the terminal: the probe consumes its replies from stdin, which is only
        safe while nothing else is reading. Also installs the focus-report
        key handlers, since a probed terminal gets mode 1004 pushed.
        """
        capabilities = self._terminal_capabilities
        if capabilities is None:
            capabilities = probe_terminal()
            self._terminal_capabilities = capabilities
            self._install_focus_bindings(self.application)
        return capabilities

    def capability_hint_overrides(
        self: _LayeredReplTerminalOwner,
    ) -> dict[str, str] | None:
        """Footer/keymap seam: per-action hint labels for this terminal."""
        return capability_hint_overrides(self._terminal_capabilities)

    def _install_focus_bindings(
        self: _LayeredReplTerminalOwner, application: Any
    ) -> None:
        """Flip the focused flag on focus reports without any key dispatch."""
        if self._focus_bindings_installed:
            return
        bindings = getattr(application, "key_bindings", None)
        add = getattr(bindings, "add", None)
        if add is None:
            return
        self._focus_bindings_installed = True
        owner = self

        def focus_in(event: Any) -> None:
            owner._set_terminal_focused(True)

        def focus_out(event: Any) -> None:
            owner._set_terminal_focused(False)

        add(FOCUS_IN_KEY, eager=True)(focus_in)
        add(FOCUS_OUT_KEY, eager=True)(focus_out)

    def _set_terminal_focused(self: _LayeredReplTerminalOwner, focused: bool) -> None:
        self._terminal_focused = focused

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
                    if self._keyboard_enhancements_active:
                        # Hand the shell a legacy keyboard; the next render
                        # after resume pushes the enhancements again.
                        self._terminal_file.write(
                            self._keyboard_enhancement_pop_sequence()
                        )
                        self._keyboard_enhancements_active = False
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
        if self._backgrounded:
            self._emit_terminal_sequence(
                terminal_notification_sequence("Amplifier turn complete", summary)
            )
            self._backgrounded = False
            return
        # Desktop notification only when the turn finished while the terminal
        # window was unfocused (mode 1004 report) on an allowlisted terminal.
        if self._terminal_focused or not osc9_notifications_supported():
            return
        self._emit_terminal_sequence(
            osc9_notification_sequence(f"Amplifier — {summary}")
        )

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
        wrote = self._sync_keyboard_enhancements(application)
        if self._pending_terminal_sequences:
            sequences = tuple(self._pending_terminal_sequences)
            self._pending_terminal_sequences.clear()
            for sequence in sequences:
                application.output.write_raw(sequence)
            wrote = True
        if wrote:
            application.output.flush()

    def _sync_keyboard_enhancements(
        self: _LayeredReplTerminalOwner, application: Any
    ) -> bool:
        """Push keyboard enhancements while the application owns the terminal.

        Runs on every render (``after_render``): the first render enables
        kitty/modifyOtherKeys reporting so real shift+enter arrives (plus
        focus tracking on probed terminals; the kitty push is gated on the
        startup probe), and the final done render pops exactly what was
        pushed so the shell gets a legacy keyboard back. Unsupported
        terminals ignore every sequence involved.
        """
        if self._background_terminal_active:
            return False
        if application.is_done:
            if not self._keyboard_enhancements_active:
                return False
            application.output.write_raw(self._keyboard_enhancement_pop_sequence())
            self._keyboard_enhancements_active = False
            return True
        if self._keyboard_enhancements_active:
            return False
        capabilities = self._terminal_capabilities
        application.output.write_raw(
            keyboard_enhancement_enable_sequence(
                None if capabilities is None else capabilities.kitty_keyboard
            )
        )
        self._keyboard_enhancements_active = True
        return True

    def _keyboard_enhancement_pop_sequence(self: _LayeredReplTerminalOwner) -> str:
        """The disable pair matching what this instance pushes on render."""
        capabilities = self._terminal_capabilities
        return keyboard_enhancement_disable_sequence(
            None if capabilities is None else capabilities.kitty_keyboard
        )


__all__ = ["LayeredReplTerminalMixin"]
