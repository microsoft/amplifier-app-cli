"""One-shot startup terminal probes and desktop-notification capability.

Mirrors the Codex TUI's ``terminal_probe.rs``: the kitty keyboard query
(``CSI ? u``) and the primary device attributes query (``CSI c``) are batched
into ONE write. Every real terminal answers ``CSI c``, so a device-attributes
reply that arrives without a kitty reply is a definitive "kitty keyboard
unsupported" — the probe never has to sit out its full deadline on modern
terminals. Non-TTY stdio, platforms without ``termios``, and deadline expiry
all degrade to the conservative answer (``kitty_keyboard=False``).

The probe must own terminal input for its short window: it runs once at TUI
startup, before the prompt_toolkit application attaches its input reader.
Bytes read while hunting for the replies are consumed, so buffered type-ahead
inside the ~100ms window is discarded (same trade-off as Codex).

This module also hosts the OSC 9 desktop-notification boundary (allowlisted
by terminal identity like Codex ``notifications/``) used for unfocused-turn
notifications, and the capability seam the keymap hints read through
(``capability_hint_overrides``).
"""

from __future__ import annotations

import os
import re
import select
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import IO
from typing import Any

from .repl import _sanitize_terminal_title

try:  # pragma: no cover - absent only on non-Unix platforms
    import termios
    import tty
except ImportError:  # pragma: no cover - windows fallback
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

# Wall-clock budget for the whole startup probe (matches Codex).
DEFAULT_PROBE_TIMEOUT = 0.1

# kitty keyboard flags query + primary device attributes, batched in one write.
PROBE_QUERY = b"\x1b[?u\x1b[c"

# kitty reply: ``CSI ? <flags> u`` with at least one digit of flags.
_KITTY_REPLY = re.compile(rb"\x1b\[\?[0-9]+u")
# Primary device attributes reply: ``CSI ? <digits/;> c`` (every terminal).
_DEVICE_ATTRIBUTES_REPLY = re.compile(rb"\x1b\[\?[0-9][0-9;]*c")

# Never accumulate unbounded terminal noise while hunting for replies.
_MAX_PROBE_BUFFER = 4_096
_READ_CHUNK = 256

# Environment escape hatch for OSC 9 notifications: "off" silences them on
# allowlisted terminals, "force" enables them anywhere.
OSC9_NOTIFICATIONS_ENV = "AMPLIFIER_TERMINAL_NOTIFICATIONS"
_OSC9_OFF = frozenset({"off", "0", "false", "never", "none"})
_OSC9_FORCE = frozenset({"force", "on", "1", "true", "always"})
# TERM_PROGRAM values of terminals known to render OSC 9 notifications
# (Codex ``notifications/mod.rs`` allowlist); kitty identifies via TERM.
_OSC9_TERM_PROGRAMS = frozenset({"ghostty", "iTerm.app", "WezTerm", "WarpTerminal"})

_MAX_NOTIFICATION_CHARS = 200


@dataclass(frozen=True)
class TerminalCapabilities:
    """Snapshot of probed terminal capabilities for the keymap and footer."""

    kitty_keyboard: bool


# Conservative default for non-TTY stdio, unsupported platforms, and timeouts.
UNPROBED_CAPABILITIES = TerminalCapabilities(kitty_keyboard=False)


def probe_terminal(
    stdin: IO[Any] | None = None,
    stdout: IO[Any] | None = None,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> TerminalCapabilities:
    """Probe the controlling terminal once, before input reading starts.

    Writes ``CSI ? u`` + ``CSI c`` in one batch and reads until the device
    attributes reply arrives or *timeout* expires. The raw-mode window is
    scoped: terminal attributes are saved up front and restored in a
    ``finally`` so no failure path leaves the terminal in cbreak mode.
    """
    reader = stdin if stdin is not None else sys.stdin
    writer = stdout if stdout is not None else sys.stdout
    if termios is None or tty is None:
        return UNPROBED_CAPABILITIES
    try:
        read_fd = reader.fileno()
        write_fd = writer.fileno()
        if not (os.isatty(read_fd) and os.isatty(write_fd)):
            return UNPROBED_CAPABILITIES
    except (AttributeError, OSError, ValueError):
        return UNPROBED_CAPABILITIES
    try:
        saved_attributes = termios.tcgetattr(read_fd)
    except termios.error:
        return UNPROBED_CAPABILITIES
    try:
        # cbreak: byte-at-a-time reads with echo off, so replies are neither
        # line-buffered nor painted onto the user's screen.
        tty.setcbreak(read_fd, termios.TCSANOW)
        os.write(write_fd, PROBE_QUERY)
        return _read_probe_replies(read_fd, timeout)
    except OSError:
        return UNPROBED_CAPABILITIES
    finally:
        try:
            termios.tcsetattr(read_fd, termios.TCSADRAIN, saved_attributes)
        except termios.error:  # pragma: no cover - restore is best-effort
            pass


def _read_probe_replies(read_fd: int, timeout: float) -> TerminalCapabilities:
    """Read until the device-attributes reply resolves the probe or time ends.

    A kitty reply alone keeps draining until the deadline so the trailing
    device-attributes bytes are consumed here instead of leaking into the
    application's input stream (Codex ``finish_startup_probe``).
    """
    deadline = monotonic() + max(0.0, timeout)
    buffer = b""
    saw_kitty = False
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return TerminalCapabilities(kitty_keyboard=saw_kitty)
        try:
            readable, _, _ = select.select([read_fd], [], [], remaining)
        except InterruptedError:  # pragma: no cover - EINTR retry
            continue
        if not readable:
            return TerminalCapabilities(kitty_keyboard=saw_kitty)
        chunk = os.read(read_fd, _READ_CHUNK)
        if not chunk:
            return TerminalCapabilities(kitty_keyboard=saw_kitty)
        buffer = (buffer + chunk)[-_MAX_PROBE_BUFFER:]
        saw_kitty = saw_kitty or has_kitty_keyboard_reply(buffer)
        if has_device_attributes_reply(buffer):
            # Every terminal answers CSI c; its arrival is the definitive
            # end of the probe, with or without a kitty reply before it.
            return TerminalCapabilities(kitty_keyboard=saw_kitty)


def has_kitty_keyboard_reply(buffer: bytes) -> bool:
    """Report whether *buffer* contains a kitty keyboard flags reply."""
    return _KITTY_REPLY.search(buffer) is not None


def has_device_attributes_reply(buffer: bytes) -> bool:
    """Report whether *buffer* contains a primary device attributes reply."""
    return _DEVICE_ATTRIBUTES_REPLY.search(buffer) is not None


def capability_hint_overrides(
    capabilities: TerminalCapabilities | None,
) -> dict[str, str] | None:
    """Keymap-hint overrides for the probed terminal (``hint_label`` seam).

    Legacy terminals (no kitty keyboard protocol confirmed) cannot be trusted
    to deliver a real shift+enter, so the queue hint advertises the alt+enter
    chord, which works everywhere. ``None`` (never probed, or kitty
    confirmed) keeps the table's own labels.
    """
    if capabilities is None or capabilities.kitty_keyboard:
        return None
    return {"queue_message": "alt+enter"}


def osc9_notifications_supported(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Allowlist OSC 9 desktop notifications by terminal identity.

    ghostty, iTerm2, WezTerm, Warp (via ``TERM_PROGRAM``) and kitty (via
    ``TERM``/``KITTY_WINDOW_ID``) render OSC 9; other terminals may print
    garbage, so they are excluded. ``AMPLIFIER_TERMINAL_NOTIFICATIONS=off``
    silences notifications anywhere and ``=force`` enables them anywhere.
    """
    env = os.environ if environ is None else environ
    override = env.get(OSC9_NOTIFICATIONS_ENV, "").strip().lower()
    if override in _OSC9_OFF:
        return False
    if override in _OSC9_FORCE:
        return True
    if env.get("TERM_PROGRAM", "") in _OSC9_TERM_PROGRAMS:
        return True
    return "kitty" in env.get("TERM", "") or bool(env.get("KITTY_WINDOW_ID"))


def osc9_notification_sequence(message: str) -> str:
    """Return a bounded OSC 9 notification with escape injection stripped."""
    safe = _sanitize_terminal_title(message)[:_MAX_NOTIFICATION_CHARS].rstrip()
    return f"\x1b]9;{safe}\x07"


__all__ = [
    "DEFAULT_PROBE_TIMEOUT",
    "OSC9_NOTIFICATIONS_ENV",
    "PROBE_QUERY",
    "TerminalCapabilities",
    "UNPROBED_CAPABILITIES",
    "capability_hint_overrides",
    "has_device_attributes_reply",
    "has_kitty_keyboard_reply",
    "osc9_notification_sequence",
    "osc9_notifications_supported",
    "probe_terminal",
]
