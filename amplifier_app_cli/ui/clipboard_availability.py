"""Nonblocking clipboard-image metadata detection for the layered TUI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from .clipboard import _read_command_output

DEFAULT_PROBE_INTERVAL_SECONDS = 2.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 0.25
MAX_PROBE_OUTPUT_BYTES = 8 * 1024
MAX_PROBE_COUNT = 2**31 - 1

_IMAGE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
)
_MACOS_IMAGE_CLASS = re.compile(rb"\b(?:PNGf|TIFF|JPEG|GIFf|WEBP)\b", re.I)

logger = logging.getLogger(__name__)


class ClipboardAvailability(str, Enum):
    UNKNOWN = "unknown"
    IMAGE = "image"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ClipboardAvailabilitySnapshot:
    status: ClipboardAvailability
    checked_at: float | None
    probe_count: int

    @property
    def image_available(self) -> bool:
        return self.status == ClipboardAvailability.IMAGE


def probe_clipboard_image_availability(
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_PROBE_OUTPUT_BYTES,
) -> ClipboardAvailability:
    """Inspect clipboard metadata without reading or decoding image bytes."""
    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 2:
        raise ValueError("timeout_seconds must be between 0 and 2")
    if isinstance(max_output_bytes, bool) or not 0 < max_output_bytes <= 64 * 1024:
        raise ValueError("max_output_bytes must be between 1 and 65536")

    probe = _probe_command()
    if probe is None:
        return ClipboardAvailability.UNSUPPORTED
    command, platform = probe
    output = _read_command_output(
        command,
        timeout_seconds=timeout_seconds,
        max_bytes=max_output_bytes,
    )
    if output is None:
        return ClipboardAvailability.ERROR
    if platform == "macos":
        available = _MACOS_IMAGE_CLASS.search(output) is not None
    else:
        available = bool(_linux_image_media_types(output))
    return ClipboardAvailability.IMAGE if available else ClipboardAvailability.EMPTY


class ClipboardImageAvailabilityDetector:
    """Periodically probe clipboard metadata off the event-loop thread."""

    def __init__(
        self,
        *,
        interval_seconds: float = DEFAULT_PROBE_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
        max_output_bytes: int = MAX_PROBE_OUTPUT_BYTES,
        probe: Callable[[], ClipboardAvailability] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if isinstance(interval_seconds, bool) or not 0.01 <= interval_seconds <= 60:
            raise ValueError("interval_seconds must be between 0.01 and 60")
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 2:
            raise ValueError("timeout_seconds must be between 0 and 2")
        if isinstance(max_output_bytes, bool) or not 0 < max_output_bytes <= 64 * 1024:
            raise ValueError("max_output_bytes must be between 1 and 65536")
        self._interval_seconds = float(interval_seconds)
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._probe = probe
        self._clock = clock
        self._snapshot = ClipboardAvailabilitySnapshot(
            ClipboardAvailability.UNKNOWN, None, 0
        )
        self._listeners: list[Callable[[ClipboardAvailabilitySnapshot], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._stop_requested: asyncio.Event | None = None

    @property
    def snapshot(self) -> ClipboardAvailabilitySnapshot:
        return self._snapshot

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def add_listener(
        self, listener: Callable[[ClipboardAvailabilitySnapshot], None]
    ) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def start(self) -> None:
        if self.running:
            return
        if self._stop_requested is not None and self._stop_requested.is_set():
            raise RuntimeError("clipboard detector cannot restart after stop")
        self._stop_requested = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name="amplifier-clipboard-image-detector"
        )

    def request_stop(self) -> None:
        if self._stop_requested is not None:
            self._stop_requested.set()

    async def stop(self) -> None:
        self.request_stop()
        task = self._task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        stop = self._stop_requested
        assert stop is not None
        while not stop.is_set():
            try:
                status = await asyncio.to_thread(self._probe_once)
            except Exception:
                logger.debug("Clipboard availability probe failed", exc_info=True)
                status = ClipboardAvailability.ERROR
            if stop.is_set():
                break
            self._update(status)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    def _probe_once(self) -> ClipboardAvailability:
        if self._probe is not None:
            result = self._probe()
            if not isinstance(result, ClipboardAvailability):
                raise TypeError("clipboard probe must return ClipboardAvailability")
            return result
        return probe_clipboard_image_availability(
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )

    def _update(self, status: ClipboardAvailability) -> None:
        previous = self._snapshot.status
        count = min(MAX_PROBE_COUNT, self._snapshot.probe_count + 1)
        self._snapshot = ClipboardAvailabilitySnapshot(status, self._clock(), count)
        if status == previous:
            return
        for listener in tuple(self._listeners):
            listener(self._snapshot)


def _probe_command() -> tuple[list[str], str] | None:
    if sys.platform == "darwin":
        return (["osascript", "-e", "clipboard info"], "macos")
    if not sys.platform.startswith("linux"):
        return None

    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    if (wayland or not x11) and shutil.which("wl-paste"):
        return (["wl-paste", "--list-types"], "linux")
    if (x11 or not wayland) and shutil.which("xclip"):
        return (
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            "linux",
        )
    return None


def _linux_image_media_types(output: bytes) -> frozenset[str]:
    values: set[str] = set()
    for line in output.decode("ascii", errors="ignore").splitlines():
        media_type = line.split(";", maxsplit=1)[0].strip().lower()
        if media_type in _IMAGE_MEDIA_TYPES:
            values.add(media_type)
    return frozenset(values)


__all__ = [
    "ClipboardAvailability",
    "ClipboardAvailabilitySnapshot",
    "ClipboardImageAvailabilityDetector",
    "probe_clipboard_image_availability",
]
