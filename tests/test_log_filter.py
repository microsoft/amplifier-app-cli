"""Tests for LLMErrorLogFilter — suppresses duplicate LLM error console lines."""

import logging

from amplifier_app_cli.ui.log_filter import LLMErrorLogFilter


def _make_record(message: str, level: int = logging.ERROR) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=level,
        pathname="test.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestLLMErrorLogFilter:
    def setup_method(self) -> None:
        self.f = LLMErrorLogFilter()

    def test_suppresses_provider_api_error(self) -> None:
        record = _make_record(
            '[PROVIDER] Anthropic API error: {"type":"error","error":{"type":"rate_limit_error"}}'
        )
        assert self.f.filter(record) is False

    def test_suppresses_execution_failed(self) -> None:
        record = _make_record(
            'Execution failed: {"type":"error","error":{"type":"overloaded_error"}}'
        )
        assert self.f.filter(record) is False

    def test_passes_unrelated_error(self) -> None:
        record = _make_record("Connection pool exhausted")
        assert self.f.filter(record) is True

    def test_passes_info_level_provider_message(self) -> None:
        record = _make_record(
            "[PROVIDER] Anthropic API call started", level=logging.INFO
        )
        assert self.f.filter(record) is True

    def test_passes_non_api_error_provider_message(self) -> None:
        record = _make_record("[PROVIDER] Received response from Anthropic API")
        assert self.f.filter(record) is True

    def test_suppresses_response_processing_error(self) -> None:
        record = _make_record(
            "[PROVIDER] Anthropic response processing error: unexpected field"
        )
        assert self.f.filter(record) is False

    def test_passes_warning_level_through(self) -> None:
        record = _make_record(
            '[PROVIDER] Anthropic API error: {"type":"error"}',
            level=logging.WARNING,
        )
        assert self.f.filter(record) is True
