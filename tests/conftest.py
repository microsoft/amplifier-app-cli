"""Shared test fixtures for amplifier-app-cli tests.

With --import-mode=importlib (set in pyproject.toml), test files cannot import
directly from conftest.py. Shared helper functions live in tests/helpers.py,
which is made importable by adding the tests/ directory to sys.path below.
"""

import sys
from pathlib import Path

import pytest

# Make tests/ importable as a directory so test files can do:
#   from helpers import make_command_processor
sys.path.insert(0, str(Path(__file__).parent))

# Make the local amplifier-foundation package importable for integration tests.
# The caveman-test amplifier-foundation (one level up from amplifier-app-cli)
# contains the configurator subpackage used in integration tests.
# Insert before any other amplifier-foundation source so the local development
# version is preferred.
_local_foundation = Path(__file__).parent.parent.parent / "amplifier-foundation"
if _local_foundation.exists() and str(_local_foundation) not in sys.path:
    sys.path.insert(0, str(_local_foundation))

from amplifier_app_cli.main import CommandProcessor  # noqa: E402


# ---------------------------------------------------------------------------
# Autouse fixture — reset class-level SKILL_SHORTCUTS between tests to
# prevent state leaking from one test into another via the shared class dict.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_skill_shortcuts():
    """Clear SKILL_SHORTCUTS before and after every test in this suite."""
    CommandProcessor.SKILL_SHORTCUTS.clear()
    yield
    CommandProcessor.SKILL_SHORTCUTS.clear()


# ---------------------------------------------------------------------------
# Home-directory isolation that actually holds on Windows.
#
# ``Path.home()`` is ``os.path.expanduser("~")``. On POSIX that reads HOME; on
# Windows ``ntpath.expanduser`` reads USERPROFILE (then HOMEDRIVE+HOMEPATH) and
# NEVER consults HOME. So ``monkeypatch.setenv("HOME", tmp_path)`` alone -- the
# idiom every SessionStore-touching test used -- isolated nothing on Windows:
# tests wrote real records into the runner's (or a developer's) actual
# ``~/.amplifier/projects/<slug>/sessions/``, and two tests asserting that a
# record did NOT exist found one left by a previous test. Set both.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path, monkeypatch) -> Path:
    """Point ``Path.home()`` at ``tmp_path`` on every platform; returns it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path
