"""Tests for cross-platform clipboard image extraction."""

from __future__ import annotations

import base64
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core.message_models import ImageBlock, Message

from amplifier_app_cli.ui import clipboard


PNG = b"\x89PNG\r\n\x1a\nclipboard-image"
JPEG = b"\xff\xd8\xff\xe0clipboard-image"
GIF = b"GIF89aclipboard-image"
WEBP = b"RIFF\x10\x00\x00\x00WEBPclipboard-image"


def _set_linux_display(monkeypatch, *, wayland: bool, x11: bool) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    else:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if x11:
        monkeypatch.setenv("DISPLAY", ":0")
    else:
        monkeypatch.delenv("DISPLAY", raising=False)


def test_reads_macos_clipboard_png(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    output = b"\xc2\xabdata PNGf" + PNG.hex().encode("ascii") + b"\xc2\xbb\n"
    calls = []

    def fake_read(command, **kwargs):
        calls.append((command, kwargs))
        return output

    monkeypatch.setattr(clipboard, "_read_command_output", fake_read)

    attachment = clipboard.read_clipboard_image(timeout_seconds=1.25)

    assert attachment == clipboard.ImageAttachment(PNG, "image/png")
    assert calls[0][0] == [
        "osascript",
        "-e",
        "get the clipboard as \u00abclass PNGf\u00bb",
    ]
    assert calls[0][1]["timeout_seconds"] == 1.25


def test_build_image_message_uses_provider_neutral_content_blocks():
    message = clipboard.build_image_message(
        [
            clipboard.ImageAttachment(PNG, "image/png"),
            clipboard.ImageAttachment(JPEG, "image/jpeg"),
        ]
    )

    assert message["role"] == "user"
    assert message["metadata"]["attachment_count"] == 2
    assert message["content"][0]["type"] == "text"
    assert message["content"][1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(PNG).decode("ascii"),
        },
    }
    validated = Message(**message)
    assert isinstance(validated.content[1], ImageBlock)


def test_build_image_message_rejects_empty_attachments():
    with pytest.raises(ValueError, match="at least one"):
        clipboard.build_image_message([])


def test_reads_validated_local_image_file(tmp_path):
    image_path = tmp_path / "Screenshot with spaces.png"
    image_path.write_bytes(PNG)

    assert clipboard.read_image_file(image_path) == clipboard.ImageAttachment(
        PNG, "image/png"
    )


def test_local_image_file_rejects_non_images_and_oversized_data(tmp_path):
    text_path = tmp_path / "not-an-image.txt"
    text_path.write_text("not an image")
    large_path = tmp_path / "large.png"
    large_path.write_bytes(PNG + b"x" * 20)

    assert clipboard.read_image_file(text_path) is None
    assert clipboard.read_image_file(large_path, max_bytes=len(PNG)) is None


@pytest.mark.asyncio
async def test_image_injector_upgrades_matching_prompt_in_place():
    context = SimpleNamespace(
        get_messages=AsyncMock(
            return_value=[
                {"role": "assistant", "content": "Earlier response"},
                {"role": "user", "content": "Review this image"},
            ]
        ),
        set_messages=AsyncMock(),
    )
    attachment = clipboard.ImageAttachment(PNG, "image/png")
    injector = clipboard.ClipboardImageInjector(context)
    injector.prepare("Review this image", [attachment])

    result = await injector.handle_provider_request("provider:request", {})

    assert result.action == "continue"
    updated = context.set_messages.await_args.args[0]
    assert updated[-1]["role"] == "user"
    assert updated[-1]["content"][0] == {
        "type": "text",
        "text": "Review this image",
    }
    assert updated[-1]["content"][1]["type"] == "image"
    assert updated[-1]["metadata"] == {
        "source": "cli-clipboard",
        "attachment_count": 1,
    }


@pytest.mark.asyncio
async def test_image_injector_denies_when_prompt_is_missing():
    context = SimpleNamespace(
        get_messages=AsyncMock(return_value=[]),
        set_messages=AsyncMock(),
    )
    injector = clipboard.ClipboardImageInjector(context)
    injector.prepare("missing", [clipboard.ImageAttachment(PNG, "image/png")])

    result = await injector.handle_provider_request("provider:request", {})

    assert result.action == "deny"
    context.set_messages.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload", "media_type"),
    [(JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_linux_wayland_validates_image_magic(monkeypatch, payload, media_type):
    _set_linux_display(monkeypatch, wayland=True, x11=False)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda command: "/usr/bin/wl-paste" if command == "wl-paste" else None,
    )
    calls = []

    def fake_read(command, **kwargs):
        calls.append(command)
        return payload

    monkeypatch.setattr(clipboard, "_read_command_output", fake_read)

    attachment = clipboard.read_clipboard_image()

    assert attachment == clipboard.ImageAttachment(payload, media_type)
    assert calls == [["wl-paste", "-t", "image"]]


def test_linux_x11_uses_xclip(monkeypatch):
    _set_linux_display(monkeypatch, wayland=False, x11=True)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda command: "/usr/bin/xclip" if command == "xclip" else None,
    )
    calls = []

    def fake_read(command, **kwargs):
        calls.append(command)
        return PNG

    monkeypatch.setattr(clipboard, "_read_command_output", fake_read)

    attachment = clipboard.read_clipboard_image()

    assert attachment == clipboard.ImageAttachment(PNG, "image/png")
    assert calls == [["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]]


def test_linux_falls_back_to_xclip_when_wayland_tool_is_missing(monkeypatch):
    _set_linux_display(monkeypatch, wayland=True, x11=True)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda command: "/usr/bin/xclip" if command == "xclip" else None,
    )
    calls = []

    def fake_read(command, **kwargs):
        calls.append(command)
        return PNG

    monkeypatch.setattr(clipboard, "_read_command_output", fake_read)

    assert clipboard.read_clipboard_image() == clipboard.ImageAttachment(
        PNG, "image/png"
    )
    assert calls == [["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]]


def test_linux_without_display_uses_available_clipboard_command(monkeypatch):
    _set_linux_display(monkeypatch, wayland=False, x11=False)
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda command: "/usr/bin/wl-paste" if command == "wl-paste" else None,
    )
    monkeypatch.setattr(clipboard, "_read_command_output", lambda *args, **kwargs: PNG)

    assert clipboard.read_clipboard_image() == clipboard.ImageAttachment(
        PNG, "image/png"
    )


@pytest.mark.parametrize(
    "failure",
    [
        None,
        b"plain clipboard text",
        PNG + b"too-large",
    ],
)
def test_returns_none_for_failed_invalid_or_oversized_data(monkeypatch, failure):
    _set_linux_display(monkeypatch, wayland=True, x11=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        clipboard, "_read_command_output", lambda *args, **kwargs: failure
    )

    max_bytes = len(PNG) if failure and failure.startswith(PNG) else 1024

    assert clipboard.read_clipboard_image(max_bytes=max_bytes) is None


def test_returns_none_when_clipboard_command_times_out(monkeypatch):
    _set_linux_display(monkeypatch, wayland=True, x11=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda command: command)

    monkeypatch.setattr(clipboard, "_read_command_output", lambda *args, **kwargs: None)

    assert clipboard.read_clipboard_image(timeout_seconds=0.1) is None


def test_returns_none_for_malformed_macos_clipboard_data(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        clipboard,
        "_read_command_output",
        lambda *args, **kwargs: b"\xc2\xabdata PNGfnot-hex\xc2\xbb\n",
    )

    assert clipboard.read_clipboard_image() is None


def test_returns_none_for_unsupported_platform_without_running_command(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "freebsd14")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr(clipboard, "_read_command_output", unexpected_run)

    assert clipboard.read_clipboard_image() is None


def test_returns_none_when_linux_clipboard_tools_are_missing(monkeypatch):
    _set_linux_display(monkeypatch, wayland=True, x11=True)
    monkeypatch.setattr(clipboard.shutil, "which", lambda command: None)

    assert clipboard.read_clipboard_image() is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
        ({"max_bytes": 0}, "max_bytes must be positive"),
    ],
)
def test_rejects_invalid_limits(kwargs, message):
    with pytest.raises(ValueError, match=message):
        clipboard.read_clipboard_image(**kwargs)


def test_command_reader_enforces_output_bound():
    output = clipboard._read_command_output(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 4096)"],
        timeout_seconds=2,
        max_bytes=1024,
    )

    assert output is None


def test_command_reader_enforces_timeout():
    output = clipboard._read_command_output(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=0.05,
        max_bytes=1024,
    )

    assert output is None


def test_attachment_validation_rejects_mismatch_and_excess_count():
    with pytest.raises(ValueError, match="does not match"):
        clipboard.ImageAttachment(PNG, "image/jpeg")

    images = [clipboard.ImageAttachment(PNG, "image/png")] * (
        clipboard.MAX_CLIPBOARD_ATTACHMENTS + 1
    )
    with pytest.raises(ValueError, match="too many"):
        clipboard.build_image_message(images)


def test_long_text_paste_round_trips_exact_payload():
    state = clipboard.LosslessTextPasteState(line_threshold=10)
    payload = "first\r\nsecond \u2603\n" + "\n".join(f"line {i}" for i in range(9))

    part = state.capture(payload)

    assert isinstance(part, clipboard.TextPasteReference)
    assert part.stub == "[Pasted #1 \u00b7 11 lines]"
    assert state.render([part]) == part.stub
    assert state.expand([part]) == payload
    assert state.payload(part).encode("utf-8") == payload.encode("utf-8")
    assert state.total_bytes == len(payload.encode("utf-8"))


def test_short_text_paste_remains_inline_and_unstored():
    state = clipboard.LosslessTextPasteState(line_threshold=10)
    payload = "\n".join(str(index) for index in range(10))

    part = state.capture(payload)

    assert part == payload
    assert state.paste_count == 0
    assert state.render([part]) == payload
    assert state.expand([part]) == payload


def test_long_single_line_paste_collapses_by_character_count_and_round_trips():
    state = clipboard.LosslessTextPasteState(
        line_threshold=10,
        char_threshold=20,
    )
    payload = "0123456789abcdefghijk"

    part = state.capture(payload)

    assert isinstance(part, clipboard.TextPasteReference)
    assert part.stub == "[Pasted #1 · 21 chars]"
    assert state.expand([part]) == payload
    assert state.payload(part).encode() == payload.encode()


def test_multiple_text_pastes_render_and_expand_in_order():
    state = clipboard.LosslessTextPasteState(line_threshold=1)
    first_payload = "alpha\nbeta"
    second_payload = "one\ntwo\nthree"

    first = state.capture(first_payload)
    second = state.capture(second_payload)

    assert isinstance(first, clipboard.TextPasteReference)
    assert isinstance(second, clipboard.TextPasteReference)
    assert state.render(["before ", first, " middle ", second, " after"]) == (
        "before [Pasted #1 \u00b7 2 lines] middle [Pasted #2 \u00b7 3 lines] after"
    )
    assert state.expand(["before ", first, " middle ", second, " after"]) == (
        f"before {first_payload} middle {second_payload} after"
    )
    assert state.paste_count == 2


def test_literal_stub_text_never_expands_or_collides_with_reference():
    state = clipboard.LosslessTextPasteState(line_threshold=1)
    payload = "secret\npayload"
    reference = state.capture(payload)
    assert isinstance(reference, clipboard.TextPasteReference)
    literal = reference.stub

    assert state.render([literal, " | ", reference]) == f"{literal} | {literal}"
    assert state.expand([literal, " | ", reference]) == f"{literal} | {payload}"


def test_text_paste_removal_releases_capacity_without_reusing_identifier():
    state = clipboard.LosslessTextPasteState(
        line_threshold=1,
        max_pastes=1,
        max_paste_bytes=64,
        max_total_bytes=64,
    )
    first = state.capture("first\npayload")
    assert isinstance(first, clipboard.TextPasteReference)

    assert state.remove(first) == "first\npayload"
    assert state.paste_count == 0
    assert state.total_bytes == 0
    with pytest.raises(KeyError, match="not retained"):
        state.expand([first])

    second = state.capture("second\npayload")
    assert isinstance(second, clipboard.TextPasteReference)
    assert second.paste_id == 2
    assert state.discard(second) is True
    assert state.discard(second) is False


def test_text_paste_state_enforces_entry_and_byte_limits():
    state = clipboard.LosslessTextPasteState(
        line_threshold=1,
        max_pastes=2,
        max_paste_bytes=8,
        max_total_bytes=10,
    )

    first = state.capture("a\nb")
    assert isinstance(first, clipboard.TextPasteReference)
    with pytest.raises(ValueError, match="per-paste"):
        state.capture("1234\n5678")
    with pytest.raises(ValueError, match="aggregate"):
        state.retain("12345678")

    second = state.capture("c\nd")
    assert isinstance(second, clipboard.TextPasteReference)
    with pytest.raises(ValueError, match="count"):
        state.capture("e\nf")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"line_threshold": 0}, "line_threshold"),
        ({"max_pastes": True}, "max_pastes"),
        ({"max_paste_bytes": 0}, "max_paste_bytes"),
        ({"max_total_bytes": -1}, "max_total_bytes"),
        (
            {"max_paste_bytes": 2, "max_total_bytes": 1},
            "cannot exceed",
        ),
    ],
)
def test_text_paste_state_rejects_invalid_limits(kwargs, message):
    with pytest.raises(ValueError, match=message):
        clipboard.LosslessTextPasteState(**kwargs)


def test_text_paste_state_rejects_invalid_payloads_and_foreign_references():
    state = clipboard.LosslessTextPasteState()
    other_state = clipboard.LosslessTextPasteState()
    foreign = other_state.retain("foreign")

    with pytest.raises(TypeError, match="must be a string"):
        state.capture(b"bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="different state"):
        state.expand([foreign])
    with pytest.raises(TypeError, match="paste parts"):
        state.expand([object()])  # type: ignore[list-item]
