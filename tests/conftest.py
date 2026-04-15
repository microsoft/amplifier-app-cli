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
