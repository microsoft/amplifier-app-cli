"""Tests for display_llm_error() Rich panel formatter."""

import json
from io import StringIO

from rich.console import Console

from amplifier_core.llm_errors import (
    AuthenticationError,
    ContentFilterError,
    ContextLengthError,
    LLMError,
    RateLimitError,
)

from amplifier_app_cli.ui.error_display import display_llm_error


def _capture_output(error: Exception, verbose: bool = False) -> tuple[bool, str]:
    """Helper: call display_llm_error and capture the Rich output."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    result = display_llm_error(console, error, verbose=verbose)
    buf.seek(0)
    output = buf.read()
    return result, output


# ── Return value tests ───────────────────────────────────────────────


class TestDisplayLlmErrorReturnValue:
    """display_llm_error returns True for LLMError subtypes, False otherwise."""

    def test_returns_true_for_rate_limit_error(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        result, _ = _capture_output(error)
        assert result is True

    def test_returns_true_for_authentication_error(self) -> None:
        error = AuthenticationError("invalid key", provider="openai")
        result, _ = _capture_output(error)
        assert result is True

    def test_returns_true_for_context_length_error(self) -> None:
        error = ContextLengthError("too long", provider="anthropic")
        result, _ = _capture_output(error)
        assert result is True

    def test_returns_true_for_content_filter_error(self) -> None:
        error = ContentFilterError("blocked", provider="anthropic")
        result, _ = _capture_output(error)
        assert result is True

    def test_returns_true_for_generic_llm_error(self) -> None:
        error = LLMError("something went wrong", provider="anthropic")
        result, _ = _capture_output(error)
        assert result is True

    def test_returns_false_for_runtime_error(self) -> None:
        error = RuntimeError("not an LLM error")
        result, _ = _capture_output(error)
        assert result is False

    def test_returns_false_for_value_error(self) -> None:
        error = ValueError("bad value")
        result, _ = _capture_output(error)
        assert result is False


# ── Provider / model line tests ──────────────────────────────────────


class TestProviderModelLine:
    """Compact provider/model line: no labels, slash-separated."""

    def test_shows_provider_and_model(self) -> None:
        error = LLMError("boom", provider="anthropic", model="claude-opus-4-6")
        _, output = _capture_output(error)
        assert "anthropic / claude-opus-4-6" in output

    def test_shows_only_provider_when_no_model(self) -> None:
        error = LLMError("boom", provider="anthropic")
        _, output = _capture_output(error)
        assert "anthropic" in output
        # Must NOT have a trailing " / " when model is absent
        assert "anthropic / " not in output

    def test_skips_line_when_neither_provider_nor_model(self) -> None:
        error = LLMError("boom")
        _, output = _capture_output(error)
        # Neither "Provider" label nor a slash-separated line should appear
        assert "Provider" not in output
        assert " / " not in output


# ── Extracted message tests ──────────────────────────────────────────


class TestExtractedMessage:
    """_extract_message pulls human-readable text from JSON error bodies."""

    def test_extracts_nested_json_message(self) -> None:
        raw = json.dumps(
            {
                "type": "error",
                "error": {
                    "message": "Overloaded",
                    "type": "overloaded_error",
                    "details": None,
                },
                "request_id": "req_011CYWrZR1v1VKRA5jpGruM9",
            }
        )
        error = LLMError(raw, provider="anthropic", model="claude-opus-4-6")
        _, output = _capture_output(error)
        assert "Overloaded" in output
        assert "req_011CYWrZR1v1VKRA5jpGruM9" in output

    def test_extracts_openai_style_json_message(self) -> None:
        raw = json.dumps(
            {
                "error": {
                    "message": "Rate limit reached for model",
                    "type": "tokens",
                    "param": None,
                    "code": "rate_limit_exceeded",
                }
            }
        )
        error = LLMError(raw, provider="openai", model="gpt-4")
        _, output = _capture_output(error)
        assert "Rate limit reached for model" in output

    def test_extracts_top_level_json_message(self) -> None:
        raw = json.dumps({"message": "Something went wrong"})
        error = LLMError(raw, provider="anthropic")
        _, output = _capture_output(error)
        assert "Something went wrong" in output

    def test_shows_raw_when_not_json(self) -> None:
        error = LLMError("plain text error", provider="anthropic")
        _, output = _capture_output(error)
        assert "plain text error" in output

    def test_truncates_long_non_json_message(self) -> None:
        long_msg = "x" * 500
        error = LLMError(long_msg, provider="anthropic")
        _, output = _capture_output(error)
        # Full 500-char string should NOT appear verbatim (truncated to ~200)
        assert long_msg not in output
        # But the truncated prefix should appear
        assert "x" * 50 in output


# ── Raw details tests ────────────────────────────────────────────────


class TestRawDetails:
    """Raw Details section shows full error string below a separator."""

    def test_shows_raw_details_separator(self) -> None:
        raw = json.dumps(
            {
                "type": "error",
                "error": {"message": "Overloaded", "type": "overloaded_error"},
                "request_id": "req_011CYUvo1pVm9nBQDESemmf5",
            }
        )
        error = LLMError(raw, provider="anthropic")
        _, output = _capture_output(error)
        assert "Raw Details" in output

    def test_shows_full_error_string(self) -> None:
        raw = json.dumps(
            {
                "type": "error",
                "error": {"message": "Overloaded", "type": "overloaded_error"},
                "request_id": "req_011CYUvo1pVm9nBQDESemmf5",
            }
        )
        error = LLMError(raw, provider="anthropic")
        _, output = _capture_output(error)
        assert "req_011CYUvo1pVm9nBQDESemmf5" in output


# ── Title and tip tests ──────────────────────────────────────────────


class TestTitleAndTip:
    """Panel title matches error type; tip line provides guidance."""

    def test_rate_limit_title(self) -> None:
        error = RateLimitError("limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error)
        assert "Rate Limited" in output

    def test_rate_limit_tip_includes_retry_after(self) -> None:
        error = RateLimitError("limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error)
        assert "30" in output
        assert "Tip" in output

    def test_auth_error_title(self) -> None:
        error = AuthenticationError("bad key", provider="openai")
        _, output = _capture_output(error)
        assert "Authentication Failed" in output

    def test_auth_error_tip_mentions_credentials(self) -> None:
        error = AuthenticationError("bad key", provider="openai")
        _, output = _capture_output(error)
        assert "key" in output.lower() or "credential" in output.lower()

    def test_context_length_title(self) -> None:
        error = ContextLengthError("too long", provider="anthropic")
        _, output = _capture_output(error)
        assert "Context Length Exceeded" in output

    def test_content_filter_title(self) -> None:
        error = ContentFilterError("blocked", provider="anthropic")
        _, output = _capture_output(error)
        assert "Content Filtered" in output

    def test_generic_llm_error_title(self) -> None:
        error = LLMError("unexpected", provider="anthropic")
        _, output = _capture_output(error)
        assert "LLM Error" in output


# ── Verbose mode tests ───────────────────────────────────────────────


class TestVerboseMode:
    """Verbose mode shows traceback detail."""

    def test_verbose_shows_traceback_marker(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error, verbose=True)
        assert "Traceback" in output or "traceback" in output.lower()

    def test_non_verbose_omits_traceback(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error, verbose=False)
        assert "Traceback" not in output
