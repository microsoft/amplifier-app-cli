"""Startup terminal probe, focus tracking, and OSC 9 notification boundary."""

from __future__ import annotations

import os
import threading
import time
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest

from amplifier_app_cli.ui.keyboard_protocol import FOCUS_IN_KEY
from amplifier_app_cli.ui.keyboard_protocol import FOCUS_OUT_KEY
from amplifier_app_cli.ui.keyboard_protocol import FOCUS_TRACKING_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import FOCUS_TRACKING_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import KEYBOARD_ENHANCEMENT_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import KEYBOARD_ENHANCEMENT_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import KITTY_KEYBOARD_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import KITTY_KEYBOARD_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import MODIFY_OTHER_KEYS_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import MODIFY_OTHER_KEYS_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import (
    install_shift_enter_sequences,
    keyboard_enhancement_disable_sequence,
    keyboard_enhancement_enable_sequence,
    uninstall_shift_enter_sequences,
)
from amplifier_app_cli.ui.layered_repl_terminal import LayeredReplTerminalMixin
from amplifier_app_cli.ui.terminal_probe import OSC9_NOTIFICATIONS_ENV
from amplifier_app_cli.ui.terminal_probe import PROBE_QUERY
from amplifier_app_cli.ui.terminal_probe import TerminalCapabilities
from amplifier_app_cli.ui.terminal_probe import capability_hint_overrides
from amplifier_app_cli.ui.terminal_probe import has_device_attributes_reply
from amplifier_app_cli.ui.terminal_probe import has_kitty_keyboard_reply
from amplifier_app_cli.ui.terminal_probe import osc9_notification_sequence
from amplifier_app_cli.ui.terminal_probe import osc9_notifications_supported
from amplifier_app_cli.ui.terminal_probe import probe_terminal


# ---------------------------------------------------------------------------
# Reply parsing


def test_kitty_reply_requires_flags_digits():
    assert has_kitty_keyboard_reply(b"\x1b[?1u")
    assert has_kitty_keyboard_reply(b"noise\x1b[?31u\x1b[?64;1;2c")
    # Our own outgoing query has no digits and must never count as a reply.
    assert not has_kitty_keyboard_reply(b"\x1b[?u")
    assert not has_kitty_keyboard_reply(b"\x1b[?64;1;2c")
    assert not has_kitty_keyboard_reply(b"")


def test_device_attributes_reply_requires_digit_payload():
    assert has_device_attributes_reply(b"\x1b[?64;1;2c")
    assert has_device_attributes_reply(b"\x1b[?1u\x1b[?6c")
    assert not has_device_attributes_reply(b"\x1b[?;c")
    assert not has_device_attributes_reply(b"\x1b[?1u")
    assert not has_device_attributes_reply(b"")


# ---------------------------------------------------------------------------
# Probe behavior against a real PTY


class _PtyTerminal:
    """Test-side terminal: answers the batched probe query on a real pty."""

    def __init__(self, reply: bytes | None):
        self.master_fd, self.slave_fd = os.openpty()
        self._reply = reply
        self._thread = threading.Thread(target=self._respond, daemon=True)
        self._thread.start()

    def _respond(self) -> None:
        seen = b""
        while PROBE_QUERY not in seen:
            try:
                chunk = os.read(self.master_fd, 64)
            except OSError:
                return
            if not chunk:
                return
            seen += chunk
        if self._reply is not None:
            os.write(self.master_fd, self._reply)

    @property
    def stdio(self) -> Any:
        return SimpleNamespace(fileno=lambda: self.slave_fd)

    def close(self) -> None:
        self._thread.join(timeout=2)
        for fd in (self.master_fd, self.slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def pty_terminal():
    terminals: list[_PtyTerminal] = []

    def make(reply: bytes | None) -> _PtyTerminal:
        terminal = _PtyTerminal(reply)
        terminals.append(terminal)
        return terminal

    yield make
    for terminal in terminals:
        terminal.close()


def test_probe_detects_kitty_keyboard_support(pty_terminal):
    terminal = pty_terminal(b"\x1b[?1u\x1b[?64;1;2c")

    result = probe_terminal(terminal.stdio, terminal.stdio, timeout=2.0)

    assert result == TerminalCapabilities(kitty_keyboard=True)


def test_probe_treats_device_attributes_alone_as_unsupported_without_waiting(
    pty_terminal,
):
    terminal = pty_terminal(b"\x1b[?64;1;2c")

    started = monotonic()
    result = probe_terminal(terminal.stdio, terminal.stdio, timeout=5.0)
    elapsed = monotonic() - started

    assert result == TerminalCapabilities(kitty_keyboard=False)
    # CSI c is answered by every terminal: its arrival is definitive, so the
    # probe must not sit out the deadline.
    assert elapsed < 2.5


def test_probe_times_out_to_conservative_false(pty_terminal):
    terminal = pty_terminal(None)

    result = probe_terminal(terminal.stdio, terminal.stdio, timeout=0.05)

    assert result == TerminalCapabilities(kitty_keyboard=False)


def test_probe_restores_terminal_attributes(pty_terminal):
    termios = pytest.importorskip("termios")
    terminal = pty_terminal(b"\x1b[?1u\x1b[?64;1;2c")

    def configuration() -> list[Any]:
        attributes = termios.tcgetattr(terminal.slave_fd)
        # PENDIN is transient line-discipline state the kernel may set while
        # canonical mode is being restored, not terminal configuration.
        attributes[3] &= ~termios.PENDIN
        return attributes

    before = configuration()

    probe_terminal(terminal.stdio, terminal.stdio, timeout=2.0)

    # Poll briefly: TCSADRAIN restore completes before the probe returns, but
    # give slow CI schedulers a beat before asserting.
    deadline = time.time() + 1.0
    while configuration() != before and time.time() < deadline:
        time.sleep(0.01)
    assert configuration() == before


def test_probe_is_conservative_without_a_tty(tmp_path):
    with open(tmp_path / "not-a-tty", "w+") as file:
        assert probe_terminal(file, file) == TerminalCapabilities(kitty_keyboard=False)
    assert probe_terminal(object(), object()) == TerminalCapabilities(  # type: ignore[arg-type]
        kitty_keyboard=False
    )


# ---------------------------------------------------------------------------
# Capability seam for the keymap hints


def test_hint_overrides_advertise_alt_enter_only_on_probed_legacy_terminals():
    assert capability_hint_overrides(None) is None
    assert capability_hint_overrides(TerminalCapabilities(kitty_keyboard=True)) is None
    assert capability_hint_overrides(TerminalCapabilities(kitty_keyboard=False)) == {
        "queue_message": "alt+enter"
    }


# ---------------------------------------------------------------------------
# Capability-gated enhancement sequences


def test_unprobed_terminal_keeps_the_historical_blind_push():
    assert keyboard_enhancement_enable_sequence(None) == KEYBOARD_ENHANCEMENT_ENABLE
    assert keyboard_enhancement_disable_sequence(None) == KEYBOARD_ENHANCEMENT_DISABLE


def test_probed_terminal_gates_kitty_and_adds_focus_tracking():
    assert keyboard_enhancement_enable_sequence(True) == (
        KITTY_KEYBOARD_ENABLE + MODIFY_OTHER_KEYS_ENABLE + FOCUS_TRACKING_ENABLE
    )
    assert keyboard_enhancement_disable_sequence(True) == (
        FOCUS_TRACKING_DISABLE + MODIFY_OTHER_KEYS_DISABLE + KITTY_KEYBOARD_DISABLE
    )
    assert keyboard_enhancement_enable_sequence(False) == (
        MODIFY_OTHER_KEYS_ENABLE + FOCUS_TRACKING_ENABLE
    )
    assert keyboard_enhancement_disable_sequence(False) == (
        FOCUS_TRACKING_DISABLE + MODIFY_OTHER_KEYS_DISABLE
    )


def test_focus_report_sequences_map_to_the_carrier_keys():
    uninstall_shift_enter_sequences()
    try:
        install_shift_enter_sequences()
        from prompt_toolkit.input import ansi_escape_sequences

        table = ansi_escape_sequences.ANSI_SEQUENCES
        assert table["\x1b[I"] == FOCUS_IN_KEY
        assert table["\x1b[O"] == FOCUS_OUT_KEY
    finally:
        uninstall_shift_enter_sequences()


# ---------------------------------------------------------------------------
# Terminal mixin: probe wiring, focus flag, unfocused-turn notification


class _RecordingBindings:
    def __init__(self):
        self.handlers: dict[Any, Any] = {}

    def add(self, key, **kwargs):
        def decorator(handler):
            self.handlers[key] = handler
            return handler

        return decorator


class _TerminalHarness(LayeredReplTerminalMixin):
    """Just enough owner state for probe, focus, and notification paths."""

    _ambient_state = "idle"
    _background_process: Any = None
    _background_shell_task: Any = None
    _backgrounded = False
    _notices: Any = None
    _owner_loop: Any = None
    _session_id: Any = None
    _terminal_file: Any = None

    def __init__(self):
        self._pending_terminal_sequences: list[str] = []
        self._background_terminal_active = False
        self.emitted: list[str] = []
        self.application = SimpleNamespace(
            key_bindings=_RecordingBindings(),
            output=SimpleNamespace(write_raw=lambda data: None, flush=lambda: None),
            is_done=False,
            is_running=False,
        )

    def commit_plan_state(self, lifecycle: str) -> bool:
        return True

    def _emit_terminal_sequence(self, sequence: str) -> None:
        self.emitted.append(sequence)


def test_probe_terminal_capabilities_is_one_shot_and_installs_focus_handlers(
    monkeypatch,
):
    harness = _TerminalHarness()
    calls = []

    def fake_probe():
        calls.append(True)
        return TerminalCapabilities(kitty_keyboard=True)

    monkeypatch.setattr(
        "amplifier_app_cli.ui.layered_repl_terminal.probe_terminal", fake_probe
    )

    first = harness.probe_terminal_capabilities()
    second = harness.probe_terminal_capabilities()

    assert first == second == TerminalCapabilities(kitty_keyboard=True)
    assert calls == [True]
    handlers = harness.application.key_bindings.handlers
    assert set(handlers) == {FOCUS_IN_KEY, FOCUS_OUT_KEY}

    assert harness._terminal_focused is True
    handlers[FOCUS_OUT_KEY](None)
    assert harness._terminal_focused is False
    handlers[FOCUS_IN_KEY](None)
    assert harness._terminal_focused is True


def test_render_push_uses_probed_capabilities_and_pops_the_same_pair():
    harness = _TerminalHarness()
    harness._terminal_capabilities = TerminalCapabilities(kitty_keyboard=False)
    raw: list[str] = []
    application = SimpleNamespace(
        output=SimpleNamespace(write_raw=raw.append, flush=lambda: None),
        is_done=False,
    )

    harness._flush_terminal_sequences(application)
    application.is_done = True
    harness._flush_terminal_sequences(application)

    assert raw == [
        MODIFY_OTHER_KEYS_ENABLE + FOCUS_TRACKING_ENABLE,
        FOCUS_TRACKING_DISABLE + MODIFY_OTHER_KEYS_DISABLE,
    ]


def test_unfocused_turn_completion_emits_osc9_on_allowlisted_terminals(
    monkeypatch,
):
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv(OSC9_NOTIFICATIONS_ENV, raising=False)
    harness = _TerminalHarness()
    harness._terminal_focused = False

    harness.notify_turn_complete("3 files · tests ✔")

    assert harness.emitted == ["\x1b]9;Amplifier — 3 files · tests ✔\x07"]


def test_focused_turn_completion_stays_quiet(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv(OSC9_NOTIFICATIONS_ENV, raising=False)
    harness = _TerminalHarness()

    harness.notify_turn_complete("answer")

    assert harness.emitted == []


def test_unfocused_turn_completion_respects_the_allowlist_and_escape_hatch(
    monkeypatch,
):
    harness = _TerminalHarness()
    harness._terminal_focused = False

    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    monkeypatch.delenv(OSC9_NOTIFICATIONS_ENV, raising=False)
    harness.notify_turn_complete("answer")
    assert harness.emitted == []

    monkeypatch.setenv(OSC9_NOTIFICATIONS_ENV, "force")
    harness.notify_turn_complete("answer")
    assert harness.emitted == ["\x1b]9;Amplifier — answer\x07"]

    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setenv(OSC9_NOTIFICATIONS_ENV, "off")
    harness.notify_turn_complete("answer")
    assert harness.emitted == ["\x1b]9;Amplifier — answer\x07"]


def test_backgrounded_completion_prefers_the_shell_notification(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.delenv(OSC9_NOTIFICATIONS_ENV, raising=False)
    harness = _TerminalHarness()
    harness._backgrounded = True
    harness._terminal_focused = False

    harness.notify_turn_complete("tests ✔")

    assert harness.emitted == ["\x1b]777;notify;Amplifier turn complete;tests ✔\x07"]
    assert harness._backgrounded is False


# ---------------------------------------------------------------------------
# OSC 9 boundary functions


def test_osc9_allowlist_covers_the_codex_terminals():
    for term_program in ("ghostty", "iTerm.app", "WezTerm", "WarpTerminal"):
        assert osc9_notifications_supported({"TERM_PROGRAM": term_program})
    assert osc9_notifications_supported({"TERM": "xterm-kitty"})
    assert osc9_notifications_supported({"KITTY_WINDOW_ID": "1"})
    assert not osc9_notifications_supported({"TERM_PROGRAM": "Apple_Terminal"})
    assert not osc9_notifications_supported({})


def test_osc9_sequence_is_sanitized_and_bounded():
    hostile = "done\x1b]0;pwned\x07\r\n" + "x" * 400
    sequence = osc9_notification_sequence(hostile)

    assert sequence.startswith("\x1b]9;")
    assert sequence.endswith("\x07")
    payload = sequence[len("\x1b]9;") : -1]
    assert "\x1b" not in payload
    assert "\x07" not in payload
    assert len(payload) <= 200


def test_capability_snapshot_is_frozen():
    capabilities = TerminalCapabilities(kitty_keyboard=True)
    with pytest.raises(AttributeError):
        capabilities.kitty_keyboard = False  # type: ignore[misc]
