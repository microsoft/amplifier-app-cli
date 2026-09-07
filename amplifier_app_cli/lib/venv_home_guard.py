"""Guard against one Python environment being shared by two ``AMPLIFIER_HOME``s.

``AMPLIFIER_HOME`` isolates config, cache, registry and sessions. It does **not**
isolate the Python environment Amplifier is installed into. Modules and bundles
are installed *editable* (``uv pip install -e <cache path> --python
sys.executable``), so every editable ``.pth`` file in that environment points
into ``<AMPLIFIER_HOME>/cache/...``. Point ``AMPLIFIER_HOME`` somewhere else and
run again, and those pointers are silently rewritten to the new home's cache --
leaving the first home's install importing code from a directory it does not own.

That is not hypothetical. On 2026-09-07 a batch of automated lanes ran
``AMPLIFIER_HOME=/tmp/hd-*-scratch-* amplifier ...`` on a host that already had a
real install. 61 of 63 ``_editable_impl_*.pth`` files in the owner's uv tool venv
were repointed at ``/tmp/hd-android-scratch-before-bfKCxP/...`` and
``/tmp/hd-stock-fbf8ce6/...``. Nothing warned. The install had to be repaired by
hand from a backup.

This module detects that collision *before* anything is installed, from the one
piece of evidence that cannot lie: the ``.pth`` files already on disk.

Everything here is pure functions over paths plus one enforcement entry point;
the CLI layer only prints and exits.
"""

from __future__ import annotations

import os
import re
import sysconfig
from dataclasses import dataclass
from pathlib import Path

# Environment variable that lets a caller proceed anyway. Named in every
# refusal message -- a guard with no documented way past it gets worked around
# with something worse.
OVERRIDE_ENV = "AMPLIFIER_ALLOW_SHARED_VENV"

# Subcommands that must keep working even while the guard is tripped: they are
# how a user diagnoses or repairs the collision. Blocking `reset` in particular
# would make the recommended repair unreachable.
EXEMPT_COMMANDS = frozenset({"version", "reset"})

# Flags that never start a session and never install anything.
EXEMPT_FLAGS = frozenset({"--help", "-h", "--version"})

# Foundation's cache lays modules out as `<home>/cache/<repo-name>-<16 hex>`.
# The hash suffix is what distinguishes an Amplifier cache entry from an
# arbitrary directory that merely happens to sit under something called
# "cache", so a dev checkout installed editable is never mistaken for one.
_CACHE_ENTRY_SUFFIX = re.compile(r"-[0-9a-f]{16}$")

_CACHE_DIR_NAME = "cache"


@dataclass(frozen=True)
class EditableEntry:
    """One editable ``.pth`` file in the environment, and the home that owns it."""

    pth: Path
    target: Path
    home: Path


@dataclass(frozen=True)
class HomeConflict:
    """The environment's editable installs are owned by a different home."""

    site_packages: Path
    current_home: Path
    foreign: tuple[EditableEntry, ...]
    owned: tuple[EditableEntry, ...]

    @property
    def foreign_homes(self) -> tuple[Path, ...]:
        """Distinct foreign homes, most-claimed first."""
        counts: dict[Path, int] = {}
        for entry in self.foreign:
            counts[entry.home] = counts.get(entry.home, 0) + 1
        return tuple(sorted(counts, key=lambda h: (-counts[h], str(h))))

    @property
    def total(self) -> int:
        return len(self.foreign) + len(self.owned)


class SharedVenvHomeError(RuntimeError):
    """Raised when running would repoint another home's editable installs."""

    def __init__(self, conflict: HomeConflict):
        self.conflict = conflict
        super().__init__(format_conflict(conflict))


def site_packages_dir() -> Path:
    """The ``site-packages`` directory of the interpreter running Amplifier.

    This is the directory ``uv pip install -e --python sys.executable`` writes
    ``.pth`` files into -- the shared resource ``AMPLIFIER_HOME`` fails to
    isolate.
    """
    return Path(sysconfig.get_paths()["purelib"])


def home_for_target(target: Path) -> Path | None:
    """Return the Amplifier home owning ``target``, or None if it owns none.

    ``<home>/cache/<repo>-<16 hex>[/subpath]`` -> ``<home>``. Anything else --
    a dev checkout, a site-packages copy, a path with no cache segment -- is
    not claimed by any home and returns None.
    """
    parts = target.parts
    for index, part in enumerate(parts[:-1]):
        # index 0 would make the home an empty path; a real home always has at
        # least a root component before `cache/`.
        if index == 0 or part != _CACHE_DIR_NAME:
            continue
        if _CACHE_ENTRY_SUFFIX.search(parts[index + 1]):
            return Path(*parts[:index])
    return None


def _targets_in_pth(pth: Path) -> list[Path]:
    """Absolute directory targets declared by a ``.pth`` file.

    Only path lines are read. A line beginning with ``import`` is executable
    setuptools finder glue whose mapping lives in a separate module; it is
    skipped rather than half-parsed. uv -- which writes every ``.pth`` involved
    in the incident this module exists to prevent -- uses the path form.
    """
    try:
        text = pth.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    targets: list[Path] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("import "):
            continue
        candidate = Path(line)
        if candidate.is_absolute():
            targets.append(candidate)
    return targets


def read_editable_entries(site_packages: Path) -> list[EditableEntry]:
    """Every ``.pth`` entry in ``site_packages`` that an Amplifier home owns."""
    try:
        pth_files = sorted(site_packages.glob("*.pth"))
    except OSError:
        return []

    entries: list[EditableEntry] = []
    for pth in pth_files:
        for target in _targets_in_pth(pth):
            home = home_for_target(target)
            if home is not None:
                entries.append(EditableEntry(pth=pth, target=target, home=home))
    return entries


def _normalize(path: Path) -> Path:
    """Resolve without requiring existence -- a foreign home is often deleted."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - defensive; resolve() is non-strict
        return path


def detect_home_conflict(
    current_home: Path | None = None,
    site_packages: Path | None = None,
) -> HomeConflict | None:
    """Detect that this environment's editable installs belong to another home.

    Args:
        current_home: The home in effect. Defaults to the resolved
            ``AMPLIFIER_HOME``.
        site_packages: Environment to inspect. Defaults to the running
            interpreter's.

    Returns:
        A ``HomeConflict`` when at least one editable install is owned by a
        different home, else None. A fresh environment (no Amplifier-owned
        editable installs at all) is never a conflict.
    """
    if current_home is None:
        from amplifier_foundation.paths.resolution import get_amplifier_home

        current_home = get_amplifier_home()
    if site_packages is None:
        site_packages = site_packages_dir()

    current = _normalize(current_home)
    owned: list[EditableEntry] = []
    foreign: list[EditableEntry] = []
    for entry in read_editable_entries(site_packages):
        if _normalize(entry.home) == current:
            owned.append(entry)
        else:
            foreign.append(entry)

    if not foreign:
        return None

    return HomeConflict(
        site_packages=site_packages,
        current_home=current,
        foreign=tuple(foreign),
        owned=tuple(owned),
    )


def format_conflict(conflict: HomeConflict, *, override_active: bool = False) -> str:
    """Render a conflict as a message that names the cause and the remedy."""
    homes = conflict.foreign_homes
    claimed_by = "\n".join(
        f"      {home}"
        f"  ({sum(1 for e in conflict.foreign if e.home == home)} editable install(s)"
        f"{'' if home.exists() else ', directory no longer exists'})"
        for home in homes
    )
    samples = "\n".join(
        f"      {entry.pth.name} -> {entry.target}" for entry in conflict.foreign[:5]
    )
    more = len(conflict.foreign) - 5
    if more > 0:
        samples += f"\n      ... and {more} more"

    headline = (
        "AMPLIFIER_HOME changed, but the Python environment did not."
        if override_active
        else "Refusing to run: this Python environment belongs to a different AMPLIFIER_HOME."
    )

    return f"""{headline}

  AMPLIFIER_HOME now:  {conflict.current_home}
  claimed by:
{claimed_by}
  environment:         {conflict.site_packages}
  editable installs:   {len(conflict.foreign)} foreign of {conflict.total} total

{samples}

AMPLIFIER_HOME isolates config, cache, registry and sessions. It does NOT isolate
this Python environment. Modules are installed editable into it, so continuing
here silently rewrites the pointers above to {conflict.current_home}'s cache and
leaves the other home importing code it does not own. That is how 61 of 63
_editable_impl_*.pth files in a real install were repointed at
/tmp/hd-android-scratch-before-bfKCxP and /tmp/hd-stock-fbf8ce6 on 2026-09-07.

Pick one:

  1. Give this home its own environment (do this for scratch, CI and eval homes):
       UV_TOOL_DIR={conflict.current_home}/uv-tools \\
       UV_TOOL_BIN_DIR={conflict.current_home}/bin \\
       uv tool install git+https://github.com/microsoft/amplifier
       AMPLIFIER_HOME={conflict.current_home} {conflict.current_home}/bin/amplifier ...

  2. Go back to the home this environment already belongs to:
       AMPLIFIER_HOME={homes[0]} amplifier ...

  3. Hand this environment over to {conflict.current_home} on purpose
     (this rewrites the pointers above -- the other home stops working):
       {OVERRIDE_ENV}=1 amplifier ...
     To repoint everything cleanly instead of module by module:
       {OVERRIDE_ENV}=1 amplifier reset --remove cache -y
"""


def is_exempt(argv: list[str] | None) -> bool:
    """True when the invocation is a diagnostic or repair path.

    ``amplifier version``, ``amplifier reset``, ``--help`` and ``--version``
    install nothing and are how a tripped guard gets diagnosed and cleared.
    """
    if not argv:
        return False
    for arg in argv:
        if arg in EXEMPT_FLAGS:
            return True
    for arg in argv:
        if arg.startswith("-"):
            continue
        return arg in EXEMPT_COMMANDS
    return False


def enforce_home_ownership(
    argv: list[str] | None = None,
    *,
    current_home: Path | None = None,
    site_packages: Path | None = None,
    env: dict[str, str] | None = None,
) -> HomeConflict | None:
    """Refuse to continue when this environment belongs to another home.

    Args:
        argv: Arguments after the program name. Exempt invocations skip the check.
        current_home: Override the home in effect (tests).
        site_packages: Override the environment inspected (tests).
        env: Override the environment variables consulted (tests).

    Returns:
        None when there is nothing to report, or the ``HomeConflict`` when the
        override is set (caller should warn and continue).

    Raises:
        SharedVenvHomeError: A conflict exists and the override is not set.
    """
    if is_exempt(argv):
        return None

    environ = os.environ if env is None else env
    conflict = detect_home_conflict(
        current_home=current_home, site_packages=site_packages
    )
    if conflict is None:
        return None

    if environ.get(OVERRIDE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return conflict

    raise SharedVenvHomeError(conflict)


__all__ = [
    "EXEMPT_COMMANDS",
    "OVERRIDE_ENV",
    "EditableEntry",
    "HomeConflict",
    "SharedVenvHomeError",
    "detect_home_conflict",
    "enforce_home_ownership",
    "format_conflict",
    "home_for_target",
    "is_exempt",
    "read_editable_entries",
    "site_packages_dir",
]
