"""Tests for bounded transcript text clipboard writes."""

from __future__ import annotations

import base64
from io import StringIO

import pytest

from amplifier_app_cli.ui import text_clipboard


def test_macos_copy_prefers_pbcopy(monkeypatch) -> None:
    calls: list[tuple[list[str], bytes, float]] = []
    monkeypatch.setattr(text_clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        text_clipboard.shutil,
        "which",
        lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else None,
    )

    def fake_write(command, payload, *, timeout_seconds):
        calls.append((command, payload, timeout_seconds))
        return True

    monkeypatch.setattr(text_clipboard, "_write_command_input", fake_write)

    assert text_clipboard.copy_text_to_clipboard("selected text") is True
    assert calls == [(["/usr/bin/pbcopy"], b"selected text", 1.0)]


def test_linux_wayland_copy_uses_utf8_text_target(monkeypatch) -> None:
    monkeypatch.setattr(text_clipboard.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        text_clipboard.shutil,
        "which",
        lambda name: "/usr/bin/wl-copy" if name == "wl-copy" else None,
    )
    calls = []
    monkeypatch.setattr(
        text_clipboard,
        "_write_command_input",
        lambda command, payload, **kwargs: calls.append((command, payload)) or True,
    )

    assert text_clipboard.copy_text_to_clipboard("café") is True
    assert calls == [
        (
            ["/usr/bin/wl-copy", "--type", "text/plain;charset=utf-8"],
            "café".encode(),
        )
    ]


def test_copy_falls_back_to_bounded_osc52(monkeypatch) -> None:
    terminal = StringIO()
    monkeypatch.setattr(text_clipboard, "_text_clipboard_command", lambda: None)

    assert text_clipboard.copy_text_to_clipboard("remote", terminal=terminal) is True
    encoded = base64.b64encode(b"remote").decode("ascii")
    assert terminal.getvalue() == f"\x1b]52;c;{encoded}\x07"


def test_copy_rejects_empty_and_oversized_payloads(monkeypatch) -> None:
    monkeypatch.setattr(text_clipboard, "_text_clipboard_command", lambda: ["pbcopy"])
    monkeypatch.setattr(
        text_clipboard,
        "_write_command_input",
        lambda *args, **kwargs: pytest.fail("clipboard helper must not run"),
    )

    assert text_clipboard.copy_text_to_clipboard("") is False
    assert text_clipboard.copy_text_to_clipboard("abcd", max_bytes=3) is False


@pytest.mark.parametrize("timeout", [0, -1, 5.1])
def test_copy_validates_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        text_clipboard.copy_text_to_clipboard("text", timeout_seconds=timeout)
