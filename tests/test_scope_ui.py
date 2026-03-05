"""Tests for ui/scope.py — Shared Scope UI Helpers.

Tests print_scope_indicator, is_scope_change_available, prompt_scope_change,
and validate_scope_cli across 4 test classes with 12 tests total.
"""

import re
from io import StringIO
from unittest.mock import patch

import click
import pytest
from rich.console import Console

from amplifier_app_cli.ui.scope import (
    is_scope_change_available,
    print_scope_indicator,
    prompt_scope_change,
    validate_scope_cli,
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for cleaner assertions."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ============================================================
# TestPrintScopeIndicator — 3 tests
# ============================================================


class TestPrintScopeIndicator:
    """Tests for print_scope_indicator() rendering."""

    def test_global_scope_renders_dim(self):
        """Global scope should render with dim treatment."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        print_scope_indicator("global", console=console)
        output = buf.getvalue()
        assert "Global" in output
        # Verify dim ANSI escape is present (SGR code 2)
        assert "\x1b[2m" in output

    def test_project_scope_renders_yellow(self):
        """Project scope should render with yellow treatment."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        print_scope_indicator("project", console=console)
        output = buf.getvalue()
        assert "Project" in output
        # Verify yellow ANSI escape is present (SGR code 33)
        assert "\x1b[33m" in output

    def test_local_scope_renders_yellow(self):
        """Local scope should render with yellow treatment."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        print_scope_indicator("local", console=console)
        output = buf.getvalue()
        assert "Local" in output
        # Verify yellow ANSI escape is present (SGR code 33)
        assert "\x1b[33m" in output


# ============================================================
# TestIsScopeChangeAvailable — 2 tests
# ============================================================


class TestIsScopeChangeAvailable:
    """Tests for is_scope_change_available()."""

    def test_returns_false_when_at_home(self):
        """Should return False when cwd is home directory."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=True
        ):
            assert is_scope_change_available() is False

    def test_returns_true_when_not_at_home(self):
        """Should return True when cwd is not the home directory."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=False
        ):
            assert is_scope_change_available() is True


# ============================================================
# TestPromptScopeChange — 5 tests
# ============================================================


class TestPromptScopeChange:
    """Tests for prompt_scope_change() interactive submenu."""

    def test_shows_numbered_scope_list(self):
        """Should display numbered list of scopes."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="1"):
            prompt_scope_change("global", console=console)
        output = _strip_ansi(buf.getvalue())
        # Should show numbered items in "N." format
        assert "1." in output
        assert "2." in output
        assert "3." in output

    def test_current_scope_has_arrow_marker(self):
        """Current scope should be marked with an arrow indicator."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="1"):
            prompt_scope_change("global", console=console)
        output = buf.getvalue()
        # The arrow marker should appear on the current scope line
        assert (
            "←" in output
            or "<-" in output
            or "◀" in output
            or "current" in output.lower()
        )

    def test_returns_selected_scope(self):
        """Should return the scope corresponding to user's choice."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        # Choose "2" which should be project
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="2"):
            result = prompt_scope_change("global", console=console)
        assert result == "project"

    def test_returns_current_scope_when_same_selected(self):
        """Selecting the already-current scope should return it unchanged."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        # Choose "1" which is global (the current)
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="1"):
            result = prompt_scope_change("global", console=console)
        assert result == "global"
        # Confirmation message should NOT appear when scope didn't change
        output = buf.getvalue()
        assert "scope changed" not in output.lower()

    def test_shows_confirmation_on_change(self):
        """Should print a confirmation message when scope actually changes."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        # Switch from global to project (choice "2")
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="2"):
            prompt_scope_change("global", console=console)
        output = buf.getvalue()
        # Should contain confirmation text about the change
        assert "scope changed" in output.lower()


# ============================================================
# TestValidateScopeCli — 4 tests
# ============================================================


class TestValidateScopeCli:
    """Tests for validate_scope_cli() CLI guard."""

    def test_global_scope_always_passes(self):
        """Global scope should pass regardless of directory."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=True
        ):
            # Should not raise
            validate_scope_cli("global")

    def test_project_scope_passes_outside_home(self):
        """Project scope should pass when not at home directory."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=False
        ):
            # Should not raise
            validate_scope_cli("project")

    def test_project_scope_raises_at_home(self):
        """Project scope from home directory should raise click.UsageError."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=True
        ):
            with pytest.raises(click.UsageError):
                validate_scope_cli("project")

    def test_local_scope_raises_at_home(self):
        """Local scope from home directory should raise click.UsageError."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=True
        ):
            with pytest.raises(click.UsageError):
                validate_scope_cli("local")
