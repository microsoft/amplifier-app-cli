"""Test package shim for the checked-in TUI reality-check harness."""

from __future__ import annotations

from pathlib import Path

# Pytest runs this directory as the ``reality_check`` package under
# ``--import-mode=importlib``. Add the checked-in harness package directory to
# this package path so ``from reality_check import tui_harness`` resolves there.
__path__.append(str(Path(__file__).with_name("reality_check")))
