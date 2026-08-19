"""Filesystem helpers that paper over cross-platform deletion differences."""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from pathlib import Path


def rmtree_robust(path: Path | str) -> None:
    """``shutil.rmtree`` that survives read-only files on Windows.

    Git marks files under ``.git/objects/pack`` (``*.pack``, ``*.idx``)
    read-only. POSIX only needs write permission on the *parent* directory to
    unlink a child, so read-only files delete fine there. Windows honours the
    read-only attribute on the file itself and refuses, so removing a
    git-backed cache (``~/.amplifier/cache/<bundle>/.git/...``) raises
    ``PermissionError: [WinError 5] Access is denied``.

    On the first ``PermissionError`` we clear the read-only bit across the tree
    and retry once. On POSIX a ``PermissionError`` means something else entirely
    (no write access to the parent), so it is re-raised untouched rather than
    rewriting the user's file modes.
    """
    try:
        shutil.rmtree(path)
        return
    except PermissionError:
        if os.name != "nt":
            raise

    # Windows only: S_IWRITE toggles the read-only attribute (it is not a
    # POSIX-style mode), so this is a targeted "make deletable" pass.
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE)

    shutil.rmtree(path)
