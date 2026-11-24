"""Opt-in location-only observations after successful CLI native persistence."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import stat
import uuid

logger = logging.getLogger(__name__)
ENVIRONMENT = 'AMPLIFIER_SESSION_CATALOG_HINT_DIRECTORY'
MAX_BYTES = 8192


def announce_saved_session(session_directory: Path) -> bool:
    """Best effort only; never affect canonical save success or start an agent.

    The configured private consumer directory must already exist. Do not create
    directories, read history, enumerate the spool, or select a native owner.
    """
    configured = os.environ.get(ENVIRONMENT)
    if not configured:
        return False
    directory_fd = file_fd = None
    temporary = None
    try:
        if os.name != 'posix':
            raise ValueError('Private catalog notices require POSIX')
        inbox = Path(configured).expanduser()
        if not inbox.is_absolute() or inbox.resolve(strict=True) != inbox:
            raise ValueError('Exact existing private notice directory required')
        source = Path(session_directory).resolve(strict=True)
        if not source.is_dir():
            raise ValueError('Saved session directory required')
        raw = json.dumps({'version': 1, 'sessionDirectory': str(source)}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_BYTES:
            raise ValueError('Catalog location notice exceeds 8 KiB')
        directory_fd = os.open(inbox, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(directory_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError('Same-owner private notice directory required')
        # Hold the validated directory descriptor through publication so a path
        # replacement cannot redirect this write into an unverified directory.
        name = hashlib.sha256(str(source).encode('utf-8')).hexdigest() + '.json'
        temporary = '.catalog-notice-' + uuid.uuid4().hex
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
        offset = 0
        while offset < len(raw):
            count = os.write(file_fd, raw[offset:])
            if count <= 0:
                raise OSError('Catalog notice write did not advance')
            offset += count
        os.fsync(file_fd)
        os.close(file_fd); file_fd = None
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
        return True
    except Exception as exc:
        # No configured paths, metadata, exception payloads or credentials in
        # diagnostics. A notice failure cannot roll back native persistence.
        logger.debug('Catalog notice not confirmed (%s)', type(exc).__name__)
        return False
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        if temporary is not None and directory_fd is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                pass
