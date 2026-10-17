"""Optional location-only notices for an independently running session catalog.

The native save remains authoritative. No catalog dependency, process, network call,
transcript read, or directory inventory belongs on this writer path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import stat
import uuid

logger = logging.getLogger(__name__)
HINT_DIRECTORY_ENV = "AMPLIFIER_SESSION_CATALOG_HINT_DIRECTORY"
MAX_HINT_BYTES = 8192


def notify_session_saved(session_directory: Path) -> bool:
    """Coalesce a best-effort v1 notice after the caller's successful native save."""
    configured = os.environ.get(HINT_DIRECTORY_ENV)
    if not configured or os.name != "posix":
        return False
    temporary = None
    try:
        inbox = Path(configured).expanduser()
        if not inbox.is_absolute() or inbox.is_symlink():
            raise ValueError("Catalog hint directory must be absolute and not a symlink")
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = inbox.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Catalog hint directory must be private")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError("Catalog hint directory has a different owner")
        directory = str(session_directory.resolve(strict=True))
        payload = json.dumps({"version": 1, "sessionDirectory": directory}, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_HINT_BYTES:
            raise ValueError("Catalog hint exceeds its location-only bound")
        key = hashlib.sha256(directory.encode("utf-8")).hexdigest()
        temporary = inbox / ("." + key + "." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, inbox / (key + ".json"))
        return True
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("Optional session catalog hint unavailable: %s", type(exc).__name__)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
