"""Terminal stream encoding policy for the CLI entrypoint."""

from __future__ import annotations

import io
import sys


def ensure_utf8_output() -> None:
    """Configure terminal streams for lossless rendered text and copy/paste."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
            ctypes.windll.kernel32.SetConsoleCP(65001)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


__all__ = ["ensure_utf8_output"]
