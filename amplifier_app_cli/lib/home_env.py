"""A Python environment per ``AMPLIFIER_HOME``, so editable installs stop colliding.

``AMPLIFIER_HOME`` isolates config, cache, registry and sessions. It does **not**
isolate the Python environment. Modules are installed editable into the
interpreter running Amplifier (``uv pip install -e <cache path> --python
sys.executable``), which under ``uv tool install`` is one venv per machine. Two
homes therefore write the same ``.pth`` filenames into the same directory, and
the second run silently repoints the first home's installs at its own cache.
``lib.venv_home_guard`` refuses to run when that has already happened; this
module removes the shared resource so there is nothing to refuse.

**The mechanism is an overlay, not a second Amplifier install.** Each home gets
``<AMPLIFIER_HOME>/env/py<major>.<minor>`` -- a bare venv holding *only* the
packages Amplifier installs at runtime. Amplifier itself, and every dependency
it shipped with, keep living in the base environment; the overlay carries a
single ``.pth`` pointing back at the base ``site-packages`` so its own
interpreter can still import them. At startup the running process calls
``site.addsitedir()`` on the overlay, which is how modules installed there become
importable *in this process* -- no re-exec, no second copy of the stack.

Measured on the host that filed this (uv 0.12.6, CPython 3.13.11, all offline):
``uv venv`` 0.374 s; an editable install of a module whose dependency is already
in the base environment 0.794 s wall / "Installed 5 packages in 6 ms"; base
environment ``.pth`` count 75 before and 75 after -- zero cross-contamination.

Two consequences worth knowing before reading the code:

* **Dependencies already present in the base environment are re-resolved into
  the overlay.** ``uv`` reads a venv's own ``dist-info``; it does not follow the
  base ``.pth``. Measured: ``uv pip list`` on a fresh overlay reports 0 packages
  while the base has 156 ``dist-info`` directories. The copies come from uv's
  cache as hardlinks, so the cost is milliseconds and near-zero disk -- but they
  are real entries.
* **The base environment wins at import time**, because ``site.addsitedir()``
  appends. A dependency resolved to a *different* version in the overlay would
  therefore be installed and then silently ignored. To make that impossible,
  every install is passed a constraints file pinning each base distribution to
  its installed version, so an incompatible requirement fails loudly at install
  time instead of quietly at import time.

While the foundation half of this change is unshipped (foundation's
``ModuleActivator`` performs most editable installs and still targets
``sys.executable``), the behavior here is **opt-in**: with
``AMPLIFIER_HOME_ENV`` unset, every entry point below is a no-op and installs
target ``sys.executable`` exactly as before.
"""

from __future__ import annotations

import logging
import os
import site
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Opt-in switch. Off by default: app-cli owns only the provider installs, so
# isolating them alone does not make a home isolated -- it makes it *partly*
# isolated, which is harder to reason about than today's honest collision plus
# the guard. Flip the default when foundation's ModuleActivator also targets
# ``install_python()``.
ENABLE_ENV = "AMPLIFIER_HOME_ENV"

# Directory layout under the home. Version-keyed so upgrading the base
# interpreter starts a fresh overlay rather than importing 3.13 builds into 3.14.
ENV_DIR_NAME = "env"

# Points the overlay's own interpreter back at the base environment, so
# ``<overlay>/bin/python`` can import Amplifier and everything it shipped with.
BASE_PTH_NAME = "_amplifier_base.pth"

# Pins every base distribution during an overlay install (see module docstring).
CONSTRAINTS_NAME = "base-constraints.txt"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def is_enabled(env: dict[str, str] | None = None) -> bool:
    """True when per-home environments are switched on."""
    environ = os.environ if env is None else env
    return environ.get(ENABLE_ENV, "").strip().lower() in _TRUTHY


def _resolved_home(home: Path | None) -> Path:
    if home is not None:
        return home
    from amplifier_foundation.paths.resolution import get_amplifier_home

    return get_amplifier_home()


def home_env_root(home: Path | None = None) -> Path:
    """``<AMPLIFIER_HOME>/env/py<major>.<minor>`` -- this home's overlay.

    Version-keyed on the *running* interpreter: an overlay built for 3.13 is
    never handed to 3.14, and neither has to be torn down for the other.
    """
    tag = f"py{sys.version_info.major}.{sys.version_info.minor}"
    return _resolved_home(home) / ENV_DIR_NAME / tag


def env_python(root: Path) -> Path:
    """The overlay interpreter, whether or not it exists yet."""
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def env_site_packages(root: Path) -> Path | None:
    """The overlay's ``site-packages``, or None when the overlay is absent."""
    if os.name == "nt":
        candidate = root / "Lib" / "site-packages"
        return candidate if candidate.is_dir() else None
    try:
        return next(iter(sorted(root.glob("lib/python*/site-packages"))), None)
    except OSError:
        return None


def base_site_packages() -> Path:
    """The base environment's ``site-packages`` -- the resource being unshared."""
    return Path(sysconfig.get_paths()["purelib"])


def base_constraints() -> list[str]:
    """``name==version`` for every distribution importable in the base environment.

    Pinning these during an overlay install is what turns a silently-ignored
    version skew (the overlay resolves a newer dependency, the base copy wins at
    import) into a loud install-time resolution failure naming both versions.
    """
    import importlib.metadata

    pins: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        version = dist.version
        if not name or not version:
            continue
        pins.setdefault(name, version)
    return [f"{name}=={version}" for name, version in sorted(pins.items())]


def write_constraints(root: Path) -> Path:
    """Write the base pins into the overlay and return the file path.

    Rewritten on every ensure: the base environment changes under
    ``amplifier update``, and a stale pin file would pin to a version that is no
    longer there.
    """
    path = root / CONSTRAINTS_NAME
    path.write_text("\n".join(base_constraints()) + "\n", encoding="utf-8")
    return path


def ensure_home_env(
    home: Path | None = None,
    *,
    runner: Runner | None = None,
) -> Path | None:
    """Create this home's overlay if needed and return its interpreter.

    Returns None when per-home environments are off, when ``uv`` is missing, or
    when creation fails. Every one of those is a *fallback*, not an error: the
    caller keeps using ``sys.executable``, which is exactly today's behavior,
    and ``lib.venv_home_guard`` still converts the resulting cross-home
    collision into a refusal on the next run. Failing loudly here would make a
    missing ``uv`` fatal for a CLI that is otherwise perfectly able to run.

    Args:
        home: Override the home (tests). Defaults to the resolved AMPLIFIER_HOME.
        runner: Override the subprocess runner (tests).

    Returns:
        Path to the overlay interpreter, or None to fall back.
    """
    if not is_enabled():
        return None

    root = home_env_root(home)
    python = env_python(root)
    run = subprocess.run if runner is None else runner

    if not python.exists():
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
            result = run(
                ["uv", "venv", "--python", sys.executable, str(root)],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logger.debug("per-home environment skipped: uv is not installed")
            return None
        except OSError as e:
            logger.debug(f"per-home environment skipped: {e}")
            return None
        if result.returncode != 0:
            logger.debug(
                f"per-home environment skipped: uv venv exited "
                f"{result.returncode}: {(result.stderr or '').strip()}"
            )
            return None
        if not python.exists():
            logger.debug(f"per-home environment skipped: {python} was not created")
            return None

    site_packages = env_site_packages(root)
    if site_packages is None:
        logger.debug(f"per-home environment skipped: no site-packages under {root}")
        return None

    try:
        (site_packages / BASE_PTH_NAME).write_text(
            f"{base_site_packages()}\n", encoding="utf-8"
        )
        write_constraints(root)
    except OSError as e:
        logger.debug(f"per-home environment skipped: {e}")
        return None

    return python


def uv_target_args(
    home: Path | None = None,
    *,
    runner: Runner | None = None,
) -> list[str]:
    """The ``uv pip install`` arguments that select where an install lands.

    ``["--python", <overlay>, "--constraint", <pins>]`` when this home has its
    own environment, and ``["--python", sys.executable]`` -- today's behavior --
    whenever it does not. Call sites splice this in and need to know nothing
    else.
    """
    python = ensure_home_env(home, runner=runner)
    if python is None:
        return ["--python", sys.executable]
    return [
        "--python",
        str(python),
        "--constraint",
        str(home_env_root(home) / CONSTRAINTS_NAME),
    ]


def activate_home_env(home: Path | None = None) -> Path | None:
    """Put this home's overlay on ``sys.path`` and return the directory added.

    ``site.addsitedir`` *appends*, and that ordering is deliberate: the base
    environment keeps winning for any package present in both, so the one copy
    of a shared dependency that actually gets imported is the one Amplifier
    shipped with. Modules only ever exist in the overlay, so nothing they need
    is shadowed.

    Returns None when there is nothing to activate (off, or not created yet).
    """
    if not is_enabled():
        return None
    site_packages = env_site_packages(home_env_root(home))
    if site_packages is None:
        return None
    site.addsitedir(str(site_packages))
    return site_packages


__all__ = [
    "BASE_PTH_NAME",
    "CONSTRAINTS_NAME",
    "ENABLE_ENV",
    "ENV_DIR_NAME",
    "activate_home_env",
    "base_constraints",
    "base_site_packages",
    "ensure_home_env",
    "env_python",
    "env_site_packages",
    "home_env_root",
    "is_enabled",
    "uv_target_args",
    "write_constraints",
]
