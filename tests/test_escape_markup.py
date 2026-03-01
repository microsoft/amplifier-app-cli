"""Tests for Rich markup escaping in CLI error handlers.

Covers the two layers of the fix for Rich markup injection:
1. escape_markup() helper — prevents brackets from being parsed as markup
2. Console.print safety net — catches MarkupError as defense-in-depth

See: https://github.com/microsoft/amplifier-app-cli/pull/101
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from amplifier_app_cli.utils.error_format import escape_markup


# ---------------------------------------------------------------------------
# Layer 1: escape_markup() unit tests
# ---------------------------------------------------------------------------


class TestEscapeMarkup:
    """Unit tests for the escape_markup() helper."""

    def test_path_with_closing_tag_pattern(self):
        """The crash case: [/Users/salil/file] looks like a closing tag."""
        result = escape_markup("[/Users/salil/file.txt]")
        # Escaped brackets should not be parsed as markup
        assert "/Users/salil/file.txt" in result

    def test_path_with_bracket_content(self):
        """The silent-loss case: [config/file.yaml] eaten as markup."""
        result = escape_markup("[config/file.yaml]")
        assert "config/file.yaml" in result

    def test_preserves_plain_text(self):
        """Normal error messages pass through unchanged."""
        assert escape_markup("Connection refused") == "Connection refused"

    def test_handles_non_string_input(self):
        """Exceptions and other objects are str()-converted first."""
        assert escape_markup(ValueError("boom")) == "boom"
        assert escape_markup(42) == "42"
        assert escape_markup(None) == "None"

    def test_empty_string(self):
        assert escape_markup("") == ""

    def test_renders_correctly_in_rich(self):
        """Escaped content renders as literal text in Rich markup."""
        buf = StringIO()
        c = Console(file=buf, force_terminal=False, no_color=True)
        escaped = escape_markup("[/Users/salil/file.txt]")
        c.print(f"[red]Error:[/red] {escaped}")
        output = buf.getvalue()
        assert "[/Users/salil/file.txt]" in output

    def test_silent_loss_renders_correctly(self):
        """Escaped bracket content is not silently consumed."""
        buf = StringIO()
        c = Console(file=buf, force_terminal=False, no_color=True)
        escaped = escape_markup("Missing [config/file.yaml]")
        c.print(f"[red]Error:[/red] {escaped}")
        output = buf.getvalue()
        assert "[config/file.yaml]" in output


# ---------------------------------------------------------------------------
# Layer 2: Console.print safety net
# ---------------------------------------------------------------------------


class TestConsoleSafetyNet:
    """Tests for the Console.print monkey-patch in console.py."""

    def test_markup_error_does_not_crash(self):
        """Console.print with bad markup retries with markup=False."""
        import amplifier_app_cli.console  # noqa: F401 — triggers the monkey-patch

        buf = StringIO()
        c = Console(file=buf, force_terminal=False, no_color=True)
        # This would raise MarkupError without the safety net
        c.print("[/Users/salil/file.txt]")
        output = buf.getvalue()
        assert "/Users/salil/file.txt" in output

    def test_valid_markup_still_works(self):
        """Intentional markup is not broken by the safety net."""
        import amplifier_app_cli.console  # noqa: F401

        buf = StringIO()
        c = Console(file=buf, force_terminal=False, no_color=True)
        c.print("[bold]hello[/bold]")
        assert "hello" in buf.getvalue()

