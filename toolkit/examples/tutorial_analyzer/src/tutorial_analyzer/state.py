"""State management for tutorial_analyzer.

Simple dict to JSON pattern - tool-specific, no framework.
"""

import json
from pathlib import Path

STATE_FILE = ".tutorial_analyzer_state.json"


def save_state(state: dict):
    """Save state to file (checkpoint).

    Called after EVERY stage completion.
    """
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def load_state() -> dict:
    """Load state if exists.

    Returns empty dict if no saved state.
    """
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}


def clear_state():
    """Clear saved state (for fresh run)."""
    if Path(STATE_FILE).exists():
        Path(STATE_FILE).unlink()
