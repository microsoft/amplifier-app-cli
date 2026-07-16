"""Tests for metadata-only clipboard image availability detection."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from amplifier_app_cli.ui import clipboard_availability
from amplifier_app_cli.ui.clipboard_availability import ClipboardAvailability
from amplifier_app_cli.ui.clipboard_availability import (
    ClipboardImageAvailabilityDetector,
)
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices


def _set_linux_display(monkeypatch, *, wayland: bool, x11: bool) -> None:
    monkeypatch.setattr(clipboard_availability.sys, "platform", "linux")
    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    else:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if x11:
        monkeypatch.setenv("DISPLAY", ":0")
    else:
        monkeypatch.delenv("DISPLAY", raising=False)


def test_macos_probe_reads_only_bounded_clipboard_metadata(monkeypatch) -> None:
    monkeypatch.setattr(clipboard_availability.sys, "platform", "darwin")
    calls = []

    def read(command, **kwargs):
        calls.append((command, kwargs))
        return b"\xc2\xabclass PNGf\xc2\xbb, 9812, string, 4"

    monkeypatch.setattr(clipboard_availability, "_read_command_output", read)

    result = clipboard_availability.probe_clipboard_image_availability(
        timeout_seconds=0.1, max_output_bytes=1024
    )

    assert result == ClipboardAvailability.IMAGE
    assert calls == [
        (
            ["osascript", "-e", "clipboard info"],
            {"timeout_seconds": 0.1, "max_bytes": 1024},
        )
    ]


def test_wayland_probe_lists_types_without_requesting_image_bytes(monkeypatch) -> None:
    _set_linux_display(monkeypatch, wayland=True, x11=False)
    monkeypatch.setattr(
        clipboard_availability.shutil,
        "which",
        lambda name: "/usr/bin/wl-paste" if name == "wl-paste" else None,
    )
    commands = []
    monkeypatch.setattr(
        clipboard_availability,
        "_read_command_output",
        lambda command, **_kwargs: (
            commands.append(command) or b"text/plain\nimage/png\n"
        ),
    )

    result = clipboard_availability.probe_clipboard_image_availability()

    assert result == ClipboardAvailability.IMAGE
    assert commands == [["wl-paste", "--list-types"]]


def test_xclip_probe_requests_targets_and_rejects_text_only(monkeypatch) -> None:
    _set_linux_display(monkeypatch, wayland=False, x11=True)
    monkeypatch.setattr(
        clipboard_availability.shutil,
        "which",
        lambda name: "/usr/bin/xclip" if name == "xclip" else None,
    )
    commands = []
    monkeypatch.setattr(
        clipboard_availability,
        "_read_command_output",
        lambda command, **_kwargs: (
            commands.append(command) or b"TARGETS\nUTF8_STRING\ntext/plain\n"
        ),
    )

    result = clipboard_availability.probe_clipboard_image_availability()

    assert result == ClipboardAvailability.EMPTY
    assert commands == [["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]]


def test_probe_reports_unsupported_without_running_a_command(monkeypatch) -> None:
    monkeypatch.setattr(clipboard_availability.sys, "platform", "freebsd")
    monkeypatch.setattr(
        clipboard_availability,
        "_read_command_output",
        lambda *_args, **_kwargs: pytest.fail("unexpected subprocess"),
    )

    assert (
        clipboard_availability.probe_clipboard_image_availability()
        == ClipboardAvailability.UNSUPPORTED
    )


@pytest.mark.asyncio
async def test_detector_probe_does_not_block_event_loop_and_stops_cleanly() -> None:
    entered = threading.Event()
    release = threading.Event()

    def probe() -> ClipboardAvailability:
        entered.set()
        release.wait(timeout=1)
        return ClipboardAvailability.EMPTY

    detector = ClipboardImageAvailabilityDetector(probe=probe, interval_seconds=0.01)
    detector.start()

    loop_progressed = False

    async def tick() -> None:
        nonlocal loop_progressed
        await asyncio.sleep(0)
        loop_progressed = True
        release.set()

    await asyncio.wait_for(tick(), timeout=0.2)
    assert loop_progressed is True
    assert await asyncio.to_thread(entered.wait, 1)
    detector.request_stop()
    await detector.stop()

    assert detector.running is False


@pytest.mark.asyncio
async def test_detector_notifies_only_on_availability_transitions() -> None:
    results = iter(
        [
            ClipboardAvailability.EMPTY,
            ClipboardAvailability.EMPTY,
            ClipboardAvailability.IMAGE,
            ClipboardAvailability.IMAGE,
        ]
    )
    final_probe = threading.Event()

    def probe() -> ClipboardAvailability:
        try:
            result = next(results)
        except StopIteration:
            final_probe.set()
            return ClipboardAvailability.IMAGE
        return result

    detector = ClipboardImageAvailabilityDetector(probe=probe, interval_seconds=0.01)
    transitions = []
    detector.add_listener(lambda snapshot: transitions.append(snapshot.status))
    detector.start()
    assert await asyncio.to_thread(final_probe.wait, 1)
    await detector.stop()

    assert transitions == [ClipboardAvailability.EMPTY, ClipboardAvailability.IMAGE]
    assert detector.snapshot.probe_count >= 4


@pytest.mark.asyncio
async def test_layered_app_shows_notice_footer_hint_and_cleans_detector(
    tmp_path: Path,
) -> None:
    detector = ClipboardImageAvailabilityDetector(
        probe=lambda: ClipboardAvailability.IMAGE,
        interval_seconds=1,
    )

    with create_pipe_input() as pipe_input:
        app = LayeredReplApp(
            config=LayeredReplConfig(
                history_path=tmp_path / "history",
                completion=LayeredReplCompletion(
                    CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
                ),
                input=pipe_input,
                output=DummyOutput(),
            ),
            bindings=LayeredReplBindings(on_submit=lambda _submission: None),
            services=LayeredReplServices(clipboard_detector=detector),
        )
        run_task = asyncio.create_task(app.run_async())

        for _ in range(100):
            if detector.snapshot.image_available:
                break
            await asyncio.sleep(0.01)

        notice = app._notices.current()
        notice_line = "".join(text for _, text in app._notice_text())
        footer = "".join(text for _, text in app._status_text())
        assert notice is not None
        assert notice.text == "Image in clipboard · ctrl+v to paste"
        assert notice_line.endswith("Image in clipboard · ctrl+v to paste")
        assert notice_line.startswith(" ")
        assert "ctrl-v paste image" not in footer
        assert "/" in footer

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)

    assert detector.running is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": 3},
        {"max_output_bytes": 0},
        {"max_output_bytes": 70_000},
    ],
)
def test_probe_rejects_unbounded_limits(kwargs) -> None:
    with pytest.raises(ValueError):
        clipboard_availability.probe_clipboard_image_availability(**kwargs)
