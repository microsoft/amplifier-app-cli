"""Scripted PTY harness for reality-checking the full-screen TUI.

Boots a real terminal program under a pseudo-terminal, feeds scripted
keystrokes, waits for rendered markers, and persists auditable artifacts:

- ``raw.ansi``        the byte-faithful capture including escape sequences
- ``clean.txt``       the capture with ANSI escapes stripped
- ``transcript.html`` an HTML rendering of the clean transcript
- ``action_log.json`` every scripted step with its outcome and timing
- ``summary.json``    the same summary dict ``run_scripted_check`` returns

Optional capabilities (PNG rendering of the capture, GUI clipboard image
paste) are probed and reported as ``SKIP`` checks rather than failures when
the host cannot support them, so the harness stays honest on headless CI.

The PTY-driving loops (``_read_until``, ``_drain``, ``_wait_for_process``)
are extracted from the proven helpers in ``tests/test_tui_pty.py``; that
module intentionally keeps its own copies so it stays self-contained.
"""

from __future__ import annotations

import dataclasses
import errno
import html
import json
import os
import re
import select
import shutil
import struct
import subprocess
import time
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from amplifier_app_cli.ui.layered_repl_style import TOKENS

GUI_CLIPBOARD_BRIDGE_ENV = "AMPLIFIER_TUI_GUI_CLIPBOARD_BRIDGE"
PNG_RENDERERS = ("textimg",)
CLIPBOARD_IMAGE_TOOLS = ("pngpaste", "wl-paste", "xclip")

# CSI (including private modes like \x1b[?1049h), OSC, and other C1 escapes.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[@-Z\\-_]"
)

_TRANSCRIPT_TEMPLATE = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>TUI reality-check transcript</title>
<style>
body {{{{ background: {TOKENS["bg_term"]}; color: {TOKENS["fg"]}; }}}}
pre {{{{ font-family: monospace; white-space: pre-wrap; }}}}
</style>
</head>
<body>
<pre>{{transcript}}</pre>
</body>
</html>
"""


@dataclasses.dataclass(frozen=True)
class HarnessConfig:
    """Configuration for one scripted reality-check run."""

    command: list[str]
    cwd: Path
    output_dir: Path
    timeout_seconds: float = 30.0
    rows: int = 30
    cols: int = 100
    env: Mapping[str, str] | None = None


def run_scripted_check(
    config: HarnessConfig,
    steps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Run ``config.command`` under a PTY and drive it with ``steps``.

    Each step is a mapping with a ``type`` of ``send`` (write ``text`` as
    keystrokes), ``wait`` (block until ``text`` appears in the rendered
    output), or ``wait_exit`` (block until the process exits cleanly).
    Returns the summary dict that is also persisted to ``summary.json``.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    output = bytearray()
    action_log: list[dict[str, Any]] = []
    checks: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + config.timeout_seconds

    process, master = _spawn_under_pty(config)
    try:
        _run_steps(master, output, process, steps, deadline, action_log, checks)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _drain(master, output)
        os.close(master)

    raw_text = output.decode("utf-8", errors="replace")
    _write_artifacts(config.output_dir, raw_text, checks)
    checks["png_render"] = _png_render_check(config.output_dir, raw_text)
    checks["clipboard_image_paste"] = _clipboard_image_paste_check()

    summary: dict[str, Any] = {
        "overall_status": (
            "FAIL"
            if any(check["status"] == "FAIL" for check in checks.values())
            else "PASS"
        ),
        "command": list(config.command),
        "returncode": process.returncode,
        "checks": checks,
    }
    (config.output_dir / "action_log.json").write_text(
        json.dumps(action_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (config.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def _spawn_under_pty(config: HarnessConfig) -> tuple[subprocess.Popen[bytes], int]:
    """Start the command attached to a fresh PTY sized per the config."""
    import fcntl  # POSIX-only; deferred so this module imports anywhere
    import termios

    master, slave = os.openpty()
    size = struct.pack("HHHH", config.rows, config.cols, 0, 0)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
    env = {**os.environ, "TERM": "xterm-256color", **dict(config.env or {})}
    process = subprocess.Popen(
        config.command,
        cwd=str(config.cwd),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    return process, master


def _run_steps(
    master: int,
    output: bytearray,
    process: subprocess.Popen[bytes],
    steps: Sequence[Mapping[str, Any]],
    deadline: float,
    action_log: list[dict[str, Any]],
    checks: dict[str, dict[str, Any]],
) -> None:
    failed = False
    for index, step in enumerate(steps):
        step_type = str(step.get("type", ""))
        name = str(step.get("name", f"step_{index}"))
        entry: dict[str, Any] = {"name": name, "type": step_type}
        started = time.monotonic()
        if failed:
            entry["status"] = "SKIP"
            entry["reason"] = "skipped after earlier failure"
            if step_type in ("wait", "wait_exit"):
                checks[name] = {"status": "SKIP", "reason": entry["reason"]}
            action_log.append(entry)
            continue

        reason: str | None = None
        if step_type == "send":
            data = str(step["text"]).encode("utf-8")
            os.write(master, data)
            entry["bytes_sent"] = len(data)
        elif step_type == "wait":
            reason = _read_until(
                master, output, str(step["text"]).encode("utf-8"), deadline
            )
        elif step_type == "wait_exit":
            reason = _wait_for_process(master, output, process, deadline)
            if reason is None and process.returncode != 0:
                reason = f"process exited with code {process.returncode}"
        else:
            reason = f"unknown step type: {step_type!r}"

        entry["status"] = "PASS" if reason is None else "FAIL"
        entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if reason is not None:
            failed = True
            entry["reason"] = reason
            entry["tail"] = output[-2000:].decode("utf-8", errors="replace")
        if step_type in ("wait", "wait_exit") or reason is not None:
            checks[name] = {
                key: entry[key]
                for key in ("status", "reason", "tail", "elapsed_seconds")
                if key in entry
            }
        action_log.append(entry)


def _write_artifacts(
    output_dir: Path, raw_text: str, checks: dict[str, dict[str, Any]]
) -> None:
    clean_text = strip_ansi(raw_text)
    files = {
        "raw.ansi": raw_text,
        "clean.txt": clean_text,
        "transcript.html": _TRANSCRIPT_TEMPLATE.format(
            transcript=html.escape(clean_text)
        ),
    }
    for filename, content in files.items():
        (output_dir / filename).write_text(content, encoding="utf-8")
    missing = [name for name in files if not (output_dir / name).is_file()]
    checks["artifacts"] = (
        {"status": "FAIL", "reason": f"missing artifacts: {missing}"}
        if missing
        else {"status": "PASS", "files": sorted(files)}
    )


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences and normalize line endings."""
    clean = _ANSI_ESCAPE_RE.sub("", text).replace("\x1b", "")
    return clean.replace("\r\n", "\n").replace("\r", "\n")


def _png_render_check(output_dir: Path, raw_text: str) -> dict[str, Any]:
    """Render the raw capture to PNG when a renderer exists; SKIP otherwise."""
    renderer = next((tool for tool in PNG_RENDERERS if shutil.which(tool)), None)
    if renderer is None:
        return {
            "status": "SKIP",
            "reason": (
                "png_unavailable: no ANSI-to-PNG renderer on PATH "
                f"(tried: {', '.join(PNG_RENDERERS)})"
            ),
        }
    png_path = output_dir / "render.png"
    result = subprocess.run(
        [renderer, "-o", str(png_path)],
        input=raw_text.encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not png_path.is_file():
        return {
            "status": "SKIP",
            "reason": (
                f"png_unavailable: {renderer} failed with code "
                f"{result.returncode}: "
                f"{result.stderr.decode('utf-8', errors='replace')[:500]}"
            ),
        }
    return {"status": "PASS", "renderer": renderer, "path": str(png_path)}


def _clipboard_image_paste_check() -> dict[str, Any]:
    """Report whether a GUI clipboard image paste could even be scripted.

    A PTY alone cannot inject an image paste; that needs either the
    Amplifier GUI clipboard bridge or a native clipboard tool. This check
    only probes capability -- it never fails the run.
    """
    bridge = os.environ.get(GUI_CLIPBOARD_BRIDGE_ENV)
    if bridge:
        return {
            "status": "SKIP",
            "capability": "BRIDGE",
            "reason": (
                f"clipboard bridge configured via {GUI_CLIPBOARD_BRIDGE_ENV}; "
                "image paste not exercised by this scripted run"
            ),
        }
    native = next((tool for tool in CLIPBOARD_IMAGE_TOOLS if shutil.which(tool)), None)
    if native is not None:
        return {
            "status": "SKIP",
            "capability": "NATIVE",
            "reason": (
                f"native clipboard tool {native} available; "
                "image paste not exercised by this scripted run"
            ),
        }
    return {
        "status": "SKIP",
        "capability": "UNSUPPORTED",
        "reason": (
            "clipboard_image_unsupported: no GUI clipboard bridge "
            f"({GUI_CLIPBOARD_BRIDGE_ENV}) and no native clipboard tool "
            f"(tried: {', '.join(CLIPBOARD_IMAGE_TOOLS)})"
        ),
    }


def _read_until(
    master: int,
    output: bytearray,
    needle: bytes,
    deadline: float,
) -> str | None:
    """Read PTY output until ``needle`` appears; return a reason on failure."""
    while needle not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return f"timed out waiting for {needle!r}"
        readable, _, _ = select.select([master], [], [], min(0.2, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(master, 65_536)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    if needle not in output:
        return f"pty closed before {needle!r} appeared"
    return None


def _drain(master: int, output: bytearray) -> None:
    """Consume whatever remains on the PTY without blocking."""
    while True:
        readable, _, _ = select.select([master], [], [], 0)
        if not readable:
            return
        try:
            chunk = os.read(master, 65_536)
        except OSError as error:
            if error.errno == errno.EIO:
                return
            raise
        if not chunk:
            return
        output.extend(chunk)


def _wait_for_process(
    master: int,
    output: bytearray,
    process: subprocess.Popen[bytes],
    deadline: float,
) -> str | None:
    """Wait for exit while draining the PTY; return a reason on timeout."""
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timed out waiting for the process to exit"
        readable, _, _ = select.select([master], [], [], min(0.1, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(master, 65_536)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    try:
        process.wait(timeout=max(1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return "timed out waiting for the process to exit"
    _drain(master, output)
    return None
