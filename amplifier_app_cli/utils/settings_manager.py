"""Central settings management for Amplifier.

Philosophy: One settings file prevents proliferation, maintains simplicity.

Concurrency
-----------
``~/.amplifier/settings.yaml`` is shared by every Amplifier process on the
host, and this module's only writer -- the update-check timestamp -- fires at
*startup* of every one of them. Two hazards follow, and both are handled here:

1. **Torn reads.** The write used to truncate the file in place, so a
   concurrently-starting session could read a half-written settings.yaml. A
   partial YAML document parses fine; it just silently lacks whatever had not
   been written yet. Writes now go through :mod:`.atomic_write`.

2. **Lost updates.** Writing a timestamp is a read-modify-write of the
   *entire* file. Another process registering a bundle (``amplifier bundle
   add``, via ``lib/settings.py``) between this module's read and its write
   was silently clobbered -- measured as a registered ``model_role_resolver``
   bundle vanishing for ~10 minutes and then reappearing when the next
   ``bundle add`` re-added it. Atomicity alone does not fix that; the whole
   read-modify-write must hold the same advisory lock ``lib/settings.py``
   uses for the global scope (``settings.yaml.lock``).
"""

import logging
from datetime import datetime

import yaml
from filelock import BaseFileLock
from filelock import FileLock
from filelock import Timeout

from amplifier_foundation.paths.resolution import get_amplifier_home
from .atomic_write import atomic_write_yaml

logger = logging.getLogger(__name__)

SETTINGS_FILE = get_amplifier_home() / "settings.yaml"

# Must match lib/settings.py::AppSettings._scope_lock for the "global" scope,
# or the two writers lock against nothing.
LOCK_TIMEOUT_SECONDS = 10

DEFAULT_SETTINGS = {
    "updates": {
        "check_frequency_hours": 4,
        "auto_prompt": True,
        "last_check": None,
    }
}


def _settings_lock() -> BaseFileLock:
    """Advisory lock guarding a read-modify-write of the global settings file.

    Callers must hold this across the *entire* read-check-write sequence, not
    just the write -- locking only the write does not close the lost-update
    race. Same lock file as ``lib/settings.py``'s global scope.
    """
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(SETTINGS_FILE) + ".lock", timeout=LOCK_TIMEOUT_SECONDS)


def _read_settings_file() -> tuple[dict, bool]:
    """Return ``(settings, trustworthy)``.

    ``trustworthy`` is False when the file exists but could not be read or
    parsed. In that case the returned dict is only good enough to *read*
    defaults from -- writing it back would replace a real settings file with a
    defaults-only stub, destroying every other process's registrations.
    """
    if not SETTINGS_FILE.exists():
        return {}, True

    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}), True
    except Exception as e:
        logger.warning(f"Could not load settings from {SETTINGS_FILE}: {e}")
        return {}, False


def load_settings() -> dict:
    """Load settings from ~/.amplifier/settings.yaml.

    Creates the file with defaults if it doesn't exist.
    """
    if not SETTINGS_FILE.exists():
        try:
            with _settings_lock():
                # Re-check under the lock: another process may have created it
                # while we were waiting, and its content is not ours to erase.
                if not SETTINGS_FILE.exists():
                    atomic_write_yaml(SETTINGS_FILE, DEFAULT_SETTINGS)
                    return DEFAULT_SETTINGS.copy()
        except Timeout:
            logger.warning(
                f"Timed out waiting for the {SETTINGS_FILE} lock; "
                "using in-memory defaults without writing."
            )
            return DEFAULT_SETTINGS.copy()
        except Exception as e:
            logger.error(f"Could not create settings at {SETTINGS_FILE}: {e}")
            return DEFAULT_SETTINGS.copy()

    settings, _trustworthy = _read_settings_file()
    if not settings:
        return DEFAULT_SETTINGS.copy()

    # Ensure updates section exists
    if "updates" not in settings:
        settings["updates"] = DEFAULT_SETTINGS["updates"].copy()

    return settings


def save_settings(settings: dict):
    """Save settings to ~/.amplifier/settings.yaml.

    Atomic (temp file in the same directory, fsync, ``os.replace``) so a
    concurrently-starting session sees the whole old file or the whole new
    file -- never a partial one.

    This does NOT take the lock: a caller performing a read-modify-write must
    hold :func:`_settings_lock` across both halves itself, because locking
    only the write leaves the lost-update race wide open.
    """
    try:
        atomic_write_yaml(SETTINGS_FILE, settings)
    except Exception as e:
        logger.error(f"Could not save settings to {SETTINGS_FILE}: {e}")


def get_update_settings() -> dict:
    """Get just the updates section from settings."""
    settings = load_settings()
    return settings.get("updates", DEFAULT_SETTINGS["updates"].copy())


def save_update_last_check(timestamp: datetime):
    """Update last_check timestamp in settings.

    Read and write happen under one lock, so this cannot clobber a bundle
    registration written by another process in between.
    """
    try:
        with _settings_lock():
            settings, trustworthy = _read_settings_file()
            if not trustworthy:
                # The file is there but unreadable. Writing our in-memory view
                # back would replace someone's real settings with a stub. A
                # missed update-check timestamp is the cheaper failure.
                logger.warning(
                    f"Not rewriting {SETTINGS_FILE}: it exists but could not be "
                    "parsed, and overwriting it would discard its contents."
                )
                return

            updates = settings.get("updates")
            if not isinstance(updates, dict):
                updates = DEFAULT_SETTINGS["updates"].copy()
                settings["updates"] = updates
            updates["last_check"] = timestamp.isoformat()

            save_settings(settings)
    except Timeout:
        logger.warning(
            f"Timed out waiting for the {SETTINGS_FILE} lock; "
            "skipping the update-check timestamp write."
        )
