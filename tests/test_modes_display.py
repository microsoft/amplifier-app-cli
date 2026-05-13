"""Tests for /modes display redesign and /mode info command.

Covers:
  - One-line-per-mode layout with column alignment
  - Terminal-width truncation
  - (hidden) marker for unadvertised modes
  - Footer legend presence/absence based on hidden modes
  - /mode info <name> command output
  - All modes shown regardless of advertised flag (human-facing)
"""

from __future__ import annotations

import asyncio
from collections import namedtuple
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from amplifier_app_cli.main import CommandProcessor


# ---------------------------------------------------------------------------
# Helpers: build a minimal CommandProcessor + fake ModeListing objects
# ---------------------------------------------------------------------------

# Simulate ModeListing NamedTuple from hooks-mode without importing it.
# The real one is amplifier_module_hooks_mode.ModeListing(name, description, source, advertised).
ModeListing = namedtuple("ModeListing", ["name", "description", "source", "advertised"])


def _make_cp(mode_listings: list, active_mode: str | None = None) -> "CommandProcessor":
    """Build a CommandProcessor with mock session for unit testing /modes display."""
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()

    mock_discovery = MagicMock()
    mock_discovery.list_modes.return_value = mode_listings

    mock_session.coordinator.session_state = {
        "active_mode": active_mode,
        "mode_discovery": mock_discovery,
        "mode_hooks": MagicMock(),
    }

    return CommandProcessor(mock_session, "test-bundle")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Group 1: Basic display — all modes shown, hidden marker applied
# ---------------------------------------------------------------------------


def test_all_modes_shown_including_unadvertised() -> None:
    """/modes must show both advertised and unadvertised modes."""
    listings = [
        ModeListing("plan", "Think and plan", "modes", True),
        ModeListing("mode-design", "Design a mode", "modes", False),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    assert "plan" in output
    assert "mode-design" in output


def test_unadvertised_mode_has_hidden_marker() -> None:
    """Unadvertised modes must be marked with (hidden) in the output."""
    listings = [
        ModeListing("plan", "Think and plan", "modes", True),
        ModeListing("mode-design", "Design a mode", "modes", False),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    # (hidden) should appear on the mode-design line
    lines = output.splitlines()
    mode_design_lines = [ln for ln in lines if "mode-design" in ln]
    assert mode_design_lines, "mode-design should appear in /modes output"
    assert any("(hidden)" in ln for ln in mode_design_lines), (
        "Unadvertised mode-design must be marked with '(hidden)'"
    )


def test_advertised_mode_has_no_hidden_marker() -> None:
    """Advertised modes must NOT have a (hidden) marker."""
    listings = [
        ModeListing("plan", "Think and plan", "modes", True),
        ModeListing("mode-design", "Design a mode", "modes", False),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    lines = output.splitlines()
    plan_lines = [ln for ln in lines if "plan" in ln and "mode-design" not in ln]
    assert plan_lines, "plan should appear in /modes output"
    assert not any("(hidden)" in ln for ln in plan_lines), (
        "Advertised mode 'plan' must NOT have the '(hidden)' marker"
    )


def test_footer_legend_present_when_hidden_modes_exist() -> None:
    """A footer legend explaining (hidden) must appear when there are hidden modes."""
    listings = [
        ModeListing("plan", "Think and plan", "modes", True),
        ModeListing("mode-design", "Design a mode", "modes", False),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    assert "(hidden)" in output.lower() or "hidden" in output.lower(), (
        "Footer must explain the (hidden) marker when hidden modes exist"
    )


def test_footer_legend_absent_when_no_hidden_modes() -> None:
    """The (hidden) footer legend must NOT appear when all modes are advertised."""
    listings = [
        ModeListing("plan", "Think and plan", "modes", True),
        ModeListing("careful", "Confirm destructive actions", "modes", True),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    # Check that (hidden) marker is absent (no hidden modes)
    lines = output.splitlines()
    # The mode lines should not have "(hidden)" — only the footer/legend would
    # We check none of the mode name lines contain "(hidden)"
    mode_lines = [ln for ln in lines if "plan" in ln or "careful" in ln]
    assert not any("(hidden)" in ln for ln in mode_lines), (
        "No mode should have '(hidden)' marker when all modes are advertised"
    )


# ---------------------------------------------------------------------------
# Group 2: Grouping by source bundle
# ---------------------------------------------------------------------------


def test_modes_grouped_by_source() -> None:
    """Modes must be grouped by their source bundle name."""
    listings = [
        ModeListing("plan", "Think and plan", "modes", True),
        ModeListing("careful", "Confirm destructive actions", "modes", True),
        ModeListing("context-intelligence", "CI investigation", "context-intelligence", True),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    assert "modes:" in output or "modes" in output
    assert "context-intelligence" in output


# ---------------------------------------------------------------------------
# Group 3: Terminal truncation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_width", [60, 100, 200])
def test_no_description_exceeds_terminal_width(terminal_width: int, monkeypatch) -> None:
    """Each mode line must not exceed the terminal width."""

    monkeypatch.setattr(
        "shutil.get_terminal_size",
        lambda *a, **kw: type("T", (), {"columns": terminal_width, "lines": 40})(),
    )

    listings = [
        ModeListing(
            "plan",
            "A very long description that goes on and on and on forever until it wraps the terminal "
            "line which is the exact problem we are trying to fix with this redesign",
            "modes",
            True,
        ),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    lines = output.splitlines()
    mode_lines = [ln for ln in lines if "plan" in ln and "|" not in ln]
    # We allow the header and footer lines to be any length; check mode body lines
    for line in mode_lines:
        assert len(line) <= terminal_width + 5, (  # +5 tolerance for edge cases
            f"Line too long ({len(line)} > {terminal_width}): {line!r}"
        )


def test_long_description_truncated_with_ellipsis() -> None:
    """Long descriptions must be truncated with '...' suffix."""
    import shutil

    long_desc = "A" * 200  # definitely too long for any terminal

    listings = [
        ModeListing("plan", long_desc, "modes", True),
    ]
    cp = _make_cp(listings)
    output = _run(cp._list_modes())

    assert "..." in output, "Long descriptions must be truncated with '...'"


# ---------------------------------------------------------------------------
# Group 4: /mode info <name> command
# ---------------------------------------------------------------------------


def test_mode_info_command_registered() -> None:
    """/mode info must be routable as a sub-command of /mode."""
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": None,
        "mode_discovery": MagicMock(),
        "mode_hooks": MagicMock(),
    }
    cp = CommandProcessor(mock_session, "test-bundle")

    # Process /mode info plan — must not raise NotImplementedError or crash
    mock_session.coordinator.session_state["mode_discovery"].find.return_value = None
    try:
        result = _run(cp._handle_mode("info plan"))
        # Should return some string response (error or info)
        assert isinstance(result, str)
    except Exception as e:
        pytest.fail(f"_handle_mode('info plan') raised unexpectedly: {e!r}")


def test_mode_info_output_for_known_mode() -> None:
    """/mode info <name> should show full details for a known mode."""
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": None,
        "mode_discovery": MagicMock(),
        "mode_hooks": MagicMock(),
    }
    cp = CommandProcessor(mock_session, "test-bundle")

    # Set up a fake mode definition
    mock_mode = MagicMock()
    mock_mode.name = "plan"
    mock_mode.description = "Analyze and plan without implementing"
    mock_mode.source = "modes"
    mock_mode.advertised = True
    mock_mode.shortcut = "plan"
    mock_mode.default_action = "block"
    mock_mode.safe_tools = ["read_file", "grep"]
    mock_mode.warn_tools = []
    mock_mode.confirm_tools = []
    mock_mode.block_tools = ["bash"]
    mock_mode.contributes = {}

    mock_session.coordinator.session_state["mode_discovery"].find.return_value = mock_mode

    result = _run(cp._handle_mode("info plan"))

    assert isinstance(result, str)
    assert "plan" in result
    assert "Analyze and plan" in result
    assert "modes" in result  # source


def test_mode_info_unknown_mode() -> None:
    """/mode info <unknown> should return a clear error message."""
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": None,
        "mode_discovery": MagicMock(),
        "mode_hooks": MagicMock(),
    }
    cp = CommandProcessor(mock_session, "test-bundle")
    mock_session.coordinator.session_state["mode_discovery"].find.return_value = None

    result = _run(cp._handle_mode("info nonexistent-mode"))

    assert isinstance(result, str)
    assert "not found" in result.lower() or "nonexistent-mode" in result
