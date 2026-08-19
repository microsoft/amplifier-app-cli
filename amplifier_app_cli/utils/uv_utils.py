"""Shared utilities for interacting with uv."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from ..console import console
from .error_format import escape_markup


def remove_stale_uv_lock() -> bool:
    """Remove an orphaned uv.lock file if no uv process is running.

    uv uses a lock file in its cache directory to prevent concurrent access.
    If a previous uv process was killed (Ctrl+C, OOM, etc.), the lock file
    can be left behind, causing subsequent uv commands to hang indefinitely
    waiting to acquire it.

    Returns:
        True if a stale lock was found and removed, False otherwise.
    """
    # Ask uv where its cache lives rather than hardcoding ~/.cache/uv
    # (macOS uses ~/Library/Caches/uv, UV_CACHE_DIR overrides both)
    try:
        result = subprocess.run(
            ["uv", "cache", "dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        cache_dir = Path(result.stdout.strip()).resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    if not cache_dir.is_dir():
        return False

    # uv.lock is uv's internal advisory lock (observed behavior, not public API)
    lock_path = cache_dir / "uv.lock"

    # Check for both real files and broken symlinks (exists() returns False
    # for broken symlinks, but they still block uv cache clean)
    if not lock_path.exists() and not lock_path.is_symlink():
        return False

    # Check if any uv process is actually running — if so, the lock is legit.
    # pgrep -x matches the process name exactly (avoids false matches on uvicorn etc).
    #
    # NOTE: There is an inherent TOCTOU race between the pgrep check and unlink().
    # A new uv process could start in this window. This is best-effort: removing
    # an orphaned lock is far safer than leaving one that causes an indefinite hang.
    # The worst case of a false removal is uv recreating its lock file immediately.
    try:
        result = subprocess.run(
            ["pgrep", "-x", "uv"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            # uv is running, lock is legitimate
            return False
    except FileNotFoundError:
        # pgrep not available (Windows, minimal containers) — we cannot
        # determine if uv is running. Fail closed: don't remove the lock.
        # The existing 60s timeout in callers will handle this.
        return False
    except subprocess.TimeoutExpired:
        # System too busy to answer in 5s — treat as uv possibly running.
        return False

    try:
        lock_path.unlink()
        console.print("    [dim]Removed stale uv.lock[/dim]")
        return True
    except OSError as e:
        import errno

        if e.errno != errno.ENOENT:
            # Permission denied, read-only FS, etc — warn the user so they
            # have context if a subsequent uv command hangs.
            console.print(f"    [dim]Could not remove stale uv.lock: {e}[/dim]")
        return False


class UvStep(NamedTuple):
    """One deferred uv command in a Windows post-exit swap script.

    Attributes:
        command: Full command line, e.g. ``uv tool install git+https://...``.
        label: Human-readable line echoed before the step runs.
        attempts: How many times to retry while files are still locked.
        required: When True, exhausting ``attempts`` aborts to the failure
            block. When False the step is best-effort and the script continues.
    """

    command: str
    label: str
    attempts: int = 10
    required: bool = True


# cmd.exe metacharacters. A command containing any of these cannot be embedded
# in a parenthesised for-loop body without escaping, and a mis-escaped script
# would fail inside a new console window the user cannot easily debug. We refuse
# to generate a script instead, and the caller prints manual instructions.
_BATCH_UNSAFE = set('()^&|<>%!"\r\n')


def _batch_safe(text: str) -> bool:
    return not (_BATCH_UNSAFE & set(text))


def defer_uv_tool_swap(
    steps: Sequence[UvStep],
    *,
    operation: str,
    intro_lines: Sequence[str],
    success_message: str,
    recovery_commands: Sequence[str],
) -> bool:
    """Run uv tool commands from a script that starts AFTER this process exits.

    Windows locks a running program's own files: while ``amplifier.exe`` is
    alive, the ``python3xx.dll`` / ``.pyd`` files it loaded from
    ``%APPDATA%\\uv\\tools\\amplifier\\Lib\\`` cannot be deleted, so an
    in-process ``uv tool install``/``uninstall`` against our own tool
    environment fails with "Access is denied (os error 5)". POSIX allows
    unlinking open files, which is why the direct path works there.

    The workaround: write a throw-away ``.cmd`` that polls until this PID is
    gone (releasing the lock), runs the steps with retries, then either
    self-deletes on success or stops with the literal recovery commands on
    screen. Launch it in a new console and exit.

    Args:
        steps: Commands to run, in order.
        operation: Short slug for the temp filename, e.g. ``reset``.
        intro_lines: Lines echoed at the top explaining what is happening.
        success_message: Line echoed when every required step succeeded.
        recovery_commands: Commands printed verbatim if the script gives up, so
            the user is never left with only a temp-file path to a tool they may
            no longer have installed.

    Returns:
        True if the script was written and launched. False if generation or
        launch failed -- the caller must then print manual instructions.
    """
    import tempfile

    if any(not _batch_safe(step.command) for step in steps):
        # Never emit a script we cannot prove parses correctly.
        console.print(
            "[yellow]Warning:[/yellow] Cannot safely script the deferred uv commands."
        )
        return False

    pid = os.getpid()
    lines: list[str] = [
        "@echo off",
        "setlocal enabledelayedexpansion",
        "echo(",
    ]
    lines += [f"echo {line}" for line in intro_lines]
    lines += [
        "echo(",
        f"echo Waiting for Amplifier PID {pid} to exit...",
        ":waitloop",
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL',
        "if not errorlevel 1 (",
        "    ping -n 2 127.0.0.1 >NUL",
        "    goto waitloop",
        ")",
        "echo(",
    ]

    for index, step in enumerate(steps):
        # A successful attempt jumps past the remaining retries: to the next
        # step's label, or to :done for the final step. The :done label lives in
        # the footer, so it must NOT also be emitted here -- a duplicate label
        # would make `goto done` land on a `goto done` and spin forever.
        is_last = index == len(steps) - 1
        nxt = "done" if is_last else f"step{index + 2}"
        lines.append(f"echo {step.label}")
        lines.append(f"for /L %%i in (1,1,{step.attempts}) do (")
        if step.required:
            lines.append(f"    {step.command}")
            lines.append(f"    if !errorlevel! EQU 0 goto {nxt}")
            lines.append(
                f"    echo   files still locked, attempt %%i of {step.attempts}; retrying in 3s..."
            )
            lines.append("    ping -n 4 127.0.0.1 >NUL")
            lines.append(")")
            # Out of attempts on a step we cannot skip.
            lines.append("goto locked")
        else:
            # Best-effort steps stay quiet; their failure is not the story, and
            # exhausting attempts simply falls through to the next step.
            lines.append(f"    {step.command} >NUL 2>&1")
            lines.append(f"    if !errorlevel! EQU 0 goto {nxt}")
            lines.append("    ping -n 3 127.0.0.1 >NUL")
            lines.append(")")
        if not is_last:
            lines.append(f":{nxt}")

    lines += [
        # Reached only when the final step was best-effort and exhausted its
        # attempts; unreachable (and harmless) after a required final step.
        "goto done",
        "",
        ":locked",
        "echo(",
        "echo Could not finish: Amplifier's files are still locked by another",
        "echo program - another Amplifier or terminal window, an antivirus scan,",
        "echo or a file indexer.",
        "echo(",
        "echo Close any other Amplifier windows, then run these commands yourself:",
    ]
    # The literal commands, not just a path to this script: %TEMP% gets swept,
    # and after a successful uninstall the user may have no amplifier at all.
    lines += [f"echo     {cmd}" for cmd in recovery_commands]
    lines += [
        "echo(",
        "echo Or re-run this script while it still exists:",
        'echo     "%~f0"',
        "echo If it keeps failing, reboot and run it once more.",
        "echo(",
        "pause",
        "exit /b 1",
        "",
        ":done",
        "echo(",
        f"echo {success_message}",
        "echo(",
        "echo This window closes in 5 seconds.",
        "ping -n 6 127.0.0.1 >NUL",
        # Standard self-delete idiom: (goto) aborts batch parsing, releasing the
        # file handle so del can remove the script. No orphan left in %TEMP%,
        # and no keypress needed on the happy path.
        '(goto) 2>NUL & del "%~f0"',
    ]

    # cmd.exe reads batch files as ANSI/OEM: ASCII + CRLF only.
    script = "\r\n".join(lines) + "\r\n"

    try:
        fd, script_path = tempfile.mkstemp(
            prefix=f"amplifier-{operation}-", suffix=".cmd"
        )
        with os.fdopen(fd, "w", encoding="ascii", newline="") as handle:
            handle.write(script)
    except OSError as e:
        console.print(
            f"[yellow]Warning:[/yellow] Could not write the finisher script: {escape_markup(str(e))}"
        )
        return False

    console.print(
        "\n[bold]>>>[/bold] Windows can't replace Amplifier while it's running."
    )
    console.print(f"    The {operation} will finish in a new window after this exits.")
    console.print(f"    Script: [cyan]{script_path}[/cyan]")

    try:
        subprocess.Popen(
            ["cmd", "/c", script_path],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            close_fds=True,
        )
    except OSError as e:
        console.print(
            f"[yellow]Warning:[/yellow] Could not auto-launch the finisher: {escape_markup(str(e))}"
        )
        return False

    console.print("    [green]Launched.[/green] Watch the new window for progress.")
    return True
