"""Shared utilities for interacting with uv."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..console import console


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
