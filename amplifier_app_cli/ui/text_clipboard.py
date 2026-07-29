"""Bounded system-clipboard writes for explicit transcript selections."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess  # nosec B404 - commands are fixed local clipboard helpers.
import sys
from typing import TextIO


DEFAULT_TEXT_CLIPBOARD_TIMEOUT_SECONDS = 1.0
MAX_TEXT_CLIPBOARD_BYTES = 1024 * 1024
MAX_OSC52_BYTES = 100_000


def copy_text_to_clipboard(
    text: str,
    *,
    terminal: TextIO | None = None,
    timeout_seconds: float = DEFAULT_TEXT_CLIPBOARD_TIMEOUT_SECONDS,
    max_bytes: int = MAX_TEXT_CLIPBOARD_BYTES,
) -> bool:
    """Copy one explicit text selection without invoking a shell.

    Native helpers are preferred because they work reliably through terminal
    multiplexers. OSC 52 is a bounded fallback for remote terminals and
    platforms without a supported helper.
    """
    if not 0 < timeout_seconds <= 5:
        raise ValueError("timeout_seconds must be between 0 and 5")
    if not 0 < max_bytes <= MAX_TEXT_CLIPBOARD_BYTES:
        raise ValueError("max_bytes must be between 1 and 1048576")

    payload = str(text).encode("utf-8")
    if not payload or len(payload) > max_bytes:
        return False

    command = _text_clipboard_command()
    if command is not None and _write_command_input(
        command,
        payload,
        timeout_seconds=timeout_seconds,
    ):
        return True
    return _write_osc52(terminal, payload)


def _text_clipboard_command() -> list[str] | None:
    if sys.platform == "darwin":
        pbcopy = shutil.which("pbcopy")
        return [pbcopy] if pbcopy else None
    if not sys.platform.startswith("linux"):
        return None

    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    wl_copy = shutil.which("wl-copy")
    if (wayland or not x11) and wl_copy:
        return [wl_copy, "--type", "text/plain;charset=utf-8"]
    xclip = shutil.which("xclip")
    if (x11 or not wayland) and xclip:
        return [
            xclip,
            "-selection",
            "clipboard",
            "-in",
            "-t",
            "text/plain;charset=utf-8",
        ]
    return None


def _write_command_input(
    command: list[str],
    payload: bytes,
    *,
    timeout_seconds: float,
) -> bool:
    try:
        process = subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return False
    try:
        process.communicate(payload, timeout=timeout_seconds)
        return process.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
        process.communicate()
        return False


def _write_osc52(terminal: TextIO | None, payload: bytes) -> bool:
    if terminal is None or len(payload) > MAX_OSC52_BYTES:
        return False
    encoded = base64.b64encode(payload).decode("ascii")
    try:
        terminal.write(f"\x1b]52;c;{encoded}\x07")
        terminal.flush()
    except (AttributeError, OSError, ValueError):
        return False
    return True


__all__ = [
    "DEFAULT_TEXT_CLIPBOARD_TIMEOUT_SECONDS",
    "MAX_OSC52_BYTES",
    "MAX_TEXT_CLIPBOARD_BYTES",
    "copy_text_to_clipboard",
]
