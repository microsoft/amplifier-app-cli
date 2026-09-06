"""Atomic file replacement for config and registry files.

Every file under ``~/.amplifier/`` is read by any Amplifier process that
happens to start, at any moment, on a host that may be running hundreds of
them. A writer that truncates in place therefore has a window in which a
reader sees a *partial* file -- and a partial ``settings.yaml`` parses fine,
it just silently lacks whatever had not been written yet.

That is not hypothetical. On a shared host, a bundle registering a
``model_role_resolver`` capability vanished from ``~/.amplifier/settings.yaml``
for ~10 minutes and then came back, with no change to any command. Recipe
steps carrying ``model_role: reasoning`` hard-failed with
``provider_roles=session-default-fallback`` -- a confident, well-worded error
pointing at the *recipe*, which is the wrong place to look.

The remedy is the standard one, and this module is the single place it lives:
write a temp file in the *same directory* (so ``os.replace`` stays within one
filesystem and is therefore atomic), ``fsync`` it, then ``os.replace`` it over
the target. A concurrent reader observes the whole old file or the whole new
file. There is no third state.

Atomicity is not the same as serialization: this closes the torn-read window,
not the lost-update window. A read-modify-write sequence must additionally
hold the file's lock across *both* the read and the write -- see
``lib/settings.py::_scope_lock`` and ``utils/settings_manager.py``.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

__all__ = ["atomic_write_text", "atomic_write_yaml", "atomic_write_json"]

# Windows only: ``MoveFileEx`` (what ``os.replace`` calls) fails with
# ``PermissionError`` while another process has the DESTINATION open, even for
# reading. POSIX never raises this here. Readers of these files open, read and
# close in one breath, so the collision window is microseconds -- but on a host
# running hundreds of Amplifier processes it will happen, and losing a
# ``bundle add`` to it would be its own version of this bug. Retry briefly,
# then fail loud; never silently drop the write.
REPLACE_RETRY_SECONDS = 5.0
_REPLACE_RETRY_INITIAL_SLEEP = 0.002
_REPLACE_RETRY_MAX_SLEEP = 0.05


def _replace_with_retry(src: str, dst: Path) -> None:
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    sleep_for = _REPLACE_RETRY_INITIAL_SLEEP
    attempts = 0
    while True:
        attempts += 1
        try:
            os.replace(src, dst)
            if attempts > 1:
                logger.debug(
                    "Replaced %s after %d attempts (destination was held open).",
                    dst,
                    attempts,
                )
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                logger.warning(
                    "Could not replace %s after %.1fs of retries -- another "
                    "process is holding it open.",
                    dst,
                    REPLACE_RETRY_SECONDS,
                )
                raise
            time.sleep(sleep_for)
            sleep_for = min(sleep_for * 2, _REPLACE_RETRY_MAX_SLEEP)


def atomic_write_text(path: Path | str, content: str, *, encoding: str = "utf-8") -> None:
    """Replace *path*'s contents with *content* atomically.

    Creates parent directories as needed. On any failure the temp file is
    removed and the original file is left exactly as it was.

    On Windows the final rename is retried briefly if another process is
    holding the destination open -- see :func:`_replace_with_retry`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

    # Best effort: make the rename itself durable. Not supported on Windows,
    # and never load-bearing for the atomicity guarantee above.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write_yaml(path: Path | str, data: Any, **dump_kwargs: Any) -> None:
    """Serialize *data* as YAML and write it via :func:`atomic_write_text`.

    ``sort_keys=False`` and ``default_flow_style=False`` are the repo's
    existing defaults for settings files; override per call as needed.
    """
    dump_kwargs.setdefault("default_flow_style", False)
    dump_kwargs.setdefault("sort_keys", False)
    atomic_write_text(path, yaml.safe_dump(data, **dump_kwargs))


def atomic_write_json(path: Path | str, data: Any, **dump_kwargs: Any) -> None:
    """Serialize *data* as JSON and write it via :func:`atomic_write_text`."""
    import json

    dump_kwargs.setdefault("indent", 2)
    dump_kwargs.setdefault("default", str)
    atomic_write_text(path, json.dumps(data, **dump_kwargs))
