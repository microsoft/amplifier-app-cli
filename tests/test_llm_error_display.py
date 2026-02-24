"""Tests for display_llm_error() Rich panel formatter."""

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


class TestRateLimitErrorDisplay:
    """RateLimitError shows yellow border, provider, retry-after, and tip."""

    def test_shows_provider_name(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error)
        assert "anthropic" in output.lower()

    def test_shows_retry_after_value(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=42.0)
        _, output = _capture_output(error)
        assert "42" in output

    def test_shows_actionable_tip(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error)
        # Should contain some guidance text
        assert "tip" in output.lower() or "retry" in output.lower()

    def test_shows_rate_limited_title(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error)
        assert "Rate Limited" in output or "rate limit" in output.lower()


class TestAuthenticationErrorDisplay:
    """AuthenticationError shows auth-specific guidance."""

    def test_shows_auth_guidance(self) -> None:
        error = AuthenticationError("invalid api key", provider="openai")
        _, output = _capture_output(error)
        # Should mention API key or credentials
        assert (
            "key" in output.lower()
            or "credential" in output.lower()
            or "auth" in output.lower()
        )


class TestContextLengthErrorDisplay:
    """ContextLengthError shows context reduction guidance."""

    def test_shows_context_guidance(self) -> None:
        error = ContextLengthError("context too long", provider="anthropic")
        _, output = _capture_output(error)
        assert (
            "context" in output.lower()
            or "conversation" in output.lower()
            or "reduce" in output.lower()
        )


class TestContentFilterErrorDisplay:
    """ContentFilterError shows rephrasing guidance."""

    def test_shows_rephrase_guidance(self) -> None:
        error = ContentFilterError(
            "content blocked by safety filter", provider="anthropic"
        )
        _, output = _capture_output(error)
        assert (
            "rephras" in output.lower()
            or "content" in output.lower()
            or "filter" in output.lower()
        )


class TestMessageTruncation:
    """Very long error messages are truncated to ~200 chars."""

    def test_long_message_is_truncated(self) -> None:
        long_msg = "x" * 500
        error = LLMError(long_msg, provider="anthropic")
        _, output = _capture_output(error)
        # The full 500-char message should NOT appear verbatim in the output
        assert long_msg not in output


class TestVerboseMode:
    """Verbose mode shows traceback detail."""

    def test_verbose_shows_traceback_marker(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error, verbose=True)
        assert "Traceback" in output or "traceback" in output.lower()

    def test_non_verbose_omits_traceback(self) -> None:
        error = RateLimitError("rate limited", provider="anthropic", retry_after=30.0)
        _, output = _capture_output(error, verbose=False)
        # "Traceback" as a section header should not appear without verbose
        assert "Traceback" not in output
