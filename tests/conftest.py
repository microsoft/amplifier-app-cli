"""Pytest configuration for Amplifier CLI tests."""

import sys
from pathlib import Path

# Add the amplifier_app_cli package to sys.path
# This allows tests to import submodules directly without going through __init__.py
cli_root = Path(__file__).parent.parent / "amplifier_app_cli"
sys.path.insert(0, str(cli_root))
