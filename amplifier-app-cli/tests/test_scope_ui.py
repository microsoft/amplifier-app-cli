"""Tests for ui/scope.py — Shared Scope UI Helpers.

Tests print_scope_indicator, is_scope_change_available, prompt_scope_change,
and validate_scope_cli across 5 test classes with 15 tests total.
"""

import re
from io import StringIO
from unittest.mock import MagicMock, patch

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


def _make_settings() -> MagicMock:
    """Create a minimal mock AppSettings for testing."""
    return MagicMock()


# ============================================================
# TestPrintScopeIndicator — 3 tests
# ============================================================


class TestPrintScopeIndicator:
    """Tests for print_scope_indicator() rendering."""

    def test_global_scope_renders_dim(self):
        """Global scope should render with 'Saving to' text and dim treatment."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        print_scope_indicator(console, settings, "global")
        output = buf.getvalue()
        plain = _strip_ansi(output)
        assert "Saving to" in plain
        # Verify dim ANSI escape is present (SGR code 2)
        assert "\x1b[2m" in output

    def test_project_scope_renders_yellow(self):
        """Project scope should render with yellow treatment and 'team-shared'."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        print_scope_indicator(console, settings, "project")
        output = buf.getvalue()
        plain = _strip_ansi(output)
        assert "team-shared" in plain
        # Verify yellow ANSI escape is present (SGR code 33)
        assert "\x1b[33m" in output

    def test_local_scope_renders_yellow(self):
        """Local scope should render with yellow treatment and 'gitignored'."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        print_scope_indicator(console, settings, "local")
        output = buf.getvalue()
        plain = _strip_ansi(output)
        assert "gitignored" in plain
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
# TestPromptScopeChange — 6 tests
# ============================================================


class TestPromptScopeChange:
    """Tests for prompt_scope_change() interactive submenu."""

    def test_shows_numbered_scope_list(self):
        """Should display numbered list of scopes in [N] format."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="1"):
            prompt_scope_change(console, settings, "global")
        output = _strip_ansi(buf.getvalue())
        # Should show numbered items in "[N]" format
        assert "[1]" in output
        assert "[2]" in output
        assert "[3]" in output

    def test_current_scope_has_arrow_marker(self):
        """Current scope should be marked with the ▸ indicator."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="1"):
            prompt_scope_change(console, settings, "global")
        output = _strip_ansi(buf.getvalue())
        # The ▸ marker should appear for the current scope
        assert "▸" in output

    def test_returns_selected_scope(self):
        """Should return the scope corresponding to user's choice."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        # Choose "2" which should be project
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="2"):
            result = prompt_scope_change(console, settings, "global")
        assert result == "project"

    def test_returns_current_scope_when_same_selected(self):
        """Selecting the already-current scope should return it unchanged."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        # Choose "1" which is global (the current)
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="1"):
            result = prompt_scope_change(console, settings, "global")
        assert result == "global"
        # Confirmation message should NOT appear when scope didn't change
        output = _strip_ansi(buf.getvalue())
        assert "Switched to" not in output

    def test_shows_confirmation_on_change(self):
        """Should print a 'Switched to' confirmation when scope actually changes."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        # Switch from global to project (choice "2")
        with patch("amplifier_app_cli.ui.scope.Prompt.ask", return_value="2"):
            prompt_scope_change(console, settings, "global")
        output = _strip_ansi(buf.getvalue())
        assert "Switched to" in output

    def test_default_choice_matches_current_scope(self):
        """Prompt.ask default should be the number of the current scope."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        settings = _make_settings()
        # When current scope is "project" (position 2), default should be "2"
        captured_kwargs: dict = {}

        def fake_ask(prompt: str, **kwargs: object) -> str:
            captured_kwargs.update(kwargs)
            return "2"

        with patch("amplifier_app_cli.ui.scope.Prompt.ask", side_effect=fake_ask):
            prompt_scope_change(console, settings, "project")
        assert captured_kwargs.get("default") == "2"


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
        """Project scope from home directory should raise with 'home directory' message."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=True
        ):
            with pytest.raises(click.UsageError) as exc_info:
                validate_scope_cli("project")
            assert "home directory" in str(exc_info.value)

    def test_local_scope_raises_at_home(self):
        """Local scope from home directory should raise with 'home directory' message."""
        with patch(
            "amplifier_app_cli.ui.scope.is_running_from_home", return_value=True
        ):
            with pytest.raises(click.UsageError) as exc_info:
                validate_scope_cli("local")
            assert "home directory" in str(exc_info.value)
