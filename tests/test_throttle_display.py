"""Tests for format_throttle_warning() provider:throttle event formatter."""

from io import StringIO

from rich.console import Console

from amplifier_app_cli.ui.display import CLIDisplaySystem, format_throttle_warning


class TestFormatThrottleWarningFullPayload:
    """Full payload with all fields produces percentage-based message."""

    def test_formats_full_payload_with_percentage(self) -> None:
        payload = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "reason": "approaching rate limit",
            "dimension": "input_tokens",
            "remaining": 50000,
            "limit": 1000000,
            "delay": 5,
        }
        result = format_throttle_warning(payload)
        assert "anthropic" in result
        assert "input_tokens" in result
        assert "5%" in result
        assert "5s" in result
        assert "throttling" in result.lower()

    def test_correct_percentage_calculation(self) -> None:
        """50000 / 1000000 = 5%."""
        payload = {
            "provider": "anthropic",
            "dimension": "input_tokens",
            "remaining": 50000,
            "limit": 1000000,
            "delay": 5,
        }
        result = format_throttle_warning(payload)
        assert "5%" in result

    def test_percentage_rounds_to_integer(self) -> None:
        """33333 / 100000 = 33.333...% rounds to 33%."""
        payload = {
            "provider": "anthropic",
            "dimension": "requests",
            "remaining": 33333,
            "limit": 100000,
            "delay": 2,
        }
        result = format_throttle_warning(payload)
        assert "33%" in result


class TestFormatThrottleWarningMissingFields:
    """Missing fields are handled gracefully."""

    def test_missing_remaining_skips_percentage(self) -> None:
        payload = {
            "provider": "anthropic",
            "dimension": "input_tokens",
            "limit": 1000000,
            "delay": 5,
        }
        result = format_throttle_warning(payload)
        assert "anthropic" in result
        assert "5s" in result
        assert "%" not in result

    def test_missing_limit_skips_percentage(self) -> None:
        payload = {
            "provider": "anthropic",
            "dimension": "input_tokens",
            "remaining": 50000,
            "delay": 5,
        }
        result = format_throttle_warning(payload)
        assert "anthropic" in result
        assert "5s" in result
        assert "%" not in result

    def test_zero_limit_skips_percentage(self) -> None:
        payload = {
            "provider": "anthropic",
            "dimension": "input_tokens",
            "remaining": 0,
            "limit": 0,
            "delay": 5,
        }
        result = format_throttle_warning(payload)
        assert "%" not in result

    def test_missing_provider_shows_unknown(self) -> None:
        payload = {
            "dimension": "input_tokens",
            "remaining": 50000,
            "limit": 1000000,
            "delay": 5,
        }
        result = format_throttle_warning(payload)
        assert "unknown" in result

    def test_missing_delay_shows_question_mark(self) -> None:
        payload = {
            "provider": "anthropic",
            "dimension": "input_tokens",
        }
        result = format_throttle_warning(payload)
        assert "?s" in result


class TestShowMessageWithThrottleWarning:
    """CLIDisplaySystem.show_message() can display throttle warning strings."""

    def test_show_message_renders_throttle_warning(self) -> None:
        payload = {
            "provider": "anthropic",
            "dimension": "input_tokens",
            "remaining": 50000,
            "limit": 1000000,
            "delay": 5,
        }
        warning = format_throttle_warning(payload)

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        display = CLIDisplaySystem()
        display.console = console

        display.show_message(warning, level="warning", source="provider:throttle")

        buf.seek(0)
        output = buf.read()
        assert "throttle" in output.lower()
        assert "anthropic" in output.lower()
