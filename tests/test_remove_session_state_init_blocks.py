"""Tests for task-13: Remove session_state initialization blocks from app-cli.

Verifies that CommandProcessor.__init__ no longer contains the
session_state initialization block:
    if not hasattr(self.session.coordinator, "session_state"):
        self.session.coordinator.session_state = {}

Also confirms hooks-mode has no such block (already removed in task-9).
"""

import inspect
import os
import subprocess


def test_no_session_state_init_block_in_command_processor():
    """CommandProcessor.__init__ must NOT contain a session_state init block.

    The block:
        if not hasattr(self.session.coordinator, "session_state"):
            self.session.coordinator.session_state = {}
    should be absent.
    """
    from amplifier_app_cli.main import CommandProcessor

    source = inspect.getsource(CommandProcessor.__init__)

    assert 'coordinator, "session_state"' not in source, (
        "session_state initialization block found in CommandProcessor.__init__.\n"
        "The pattern 'coordinator, \"session_state\"' must not appear.\n"
        "Remove the 'if not hasattr(coordinator, \"session_state\")' init block."
    )


def test_grep_no_session_state_init_pattern_in_app_cli():
    """grep confirms zero session_state init patterns in app-cli production code."""
    # Find the app-cli source directory
    here = os.path.dirname(__file__)
    app_cli_src = os.path.join(here, "..", "amplifier_app_cli")

    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            "if not hasattr.*session_state",
            app_cli_src,
        ],
        capture_output=True,
        text=True,
    )

    matches = result.stdout.strip()
    assert matches == "", (
        f"Found session_state init patterns in app-cli source:\n{matches}\n"
        "All 'if not hasattr(..., \"session_state\")' init blocks must be removed."
    )
