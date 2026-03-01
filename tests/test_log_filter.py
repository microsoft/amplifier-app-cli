"""Tests for LLMErrorLogFilter — suppresses duplicate LLM error console lines."""

import logging
import sys

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


class TestHandlerLevelFiltering:
    """Verify the filter works when attached to a handler (not just a logger)."""

    def test_handler_filter_suppresses_child_logger_provider_error(self) -> None:
        handler = logging.StreamHandler()
        handler.addFilter(LLMErrorLogFilter())
        record = logging.LogRecord(
            name="amplifier_module_provider_anthropic",
            level=logging.ERROR,
            pathname="__init__.py",
            lineno=1,
            msg="[PROVIDER] Anthropic API error: %s",
            args=('{"type":"error","error":{"message":"Overloaded"}}',),
            exc_info=None,
        )
        assert handler.filter(record) is False

    def test_handler_filter_passes_unrelated_child_logger_error(self) -> None:
        handler = logging.StreamHandler()
        handler.addFilter(LLMErrorLogFilter())
        record = logging.LogRecord(
            name="amplifier_module_provider_anthropic",
            level=logging.ERROR,
            pathname="__init__.py",
            lineno=100,
            msg="Connection pool exhausted",
            args=(),
            exc_info=None,
        )
        # Handler.filter() returns bool on Python <3.12, LogRecord (truthy) on 3.12+
        assert handler.filter(record), (
            "Unrelated error should pass through filter "
            "(note: Handler.filter() returns LogRecord on Python 3.12+, bool on earlier)"
        )


class TestAttachLlmErrorFilter:
    """Verify that _attach_llm_error_filter targets the stderr handler at runtime."""

    def setup_method(self) -> None:
        """Save root logger state and import attachment utilities."""
        from amplifier_app_cli.main import _attach_llm_error_filter, _llm_error_filter

        self.attach = _attach_llm_error_filter
        self.llm_filter = _llm_error_filter
        self.root = logging.getLogger()
        self._orig_handlers = self.root.handlers[:]
        self._orig_filters = self.root.filters[:]

    def teardown_method(self) -> None:
        """Restore root logger state."""
        self.root.handlers = self._orig_handlers
        self.root.filters = self._orig_filters

    def test_attaches_to_stderr_handler_when_present(self) -> None:
        """When a stderr StreamHandler exists, the filter goes on it, not root."""
        stderr_handler = logging.StreamHandler(sys.stderr)
        self.root.handlers = [stderr_handler]
        self.root.filters = []

        self.attach()

        assert self.llm_filter in stderr_handler.filters, (
            "LLMErrorLogFilter must be in the stderr handler's filters"
        )
        assert self.llm_filter not in self.root.filters, (
            "LLMErrorLogFilter should not be on the root logger when stderr handler exists"
        )

    def test_falls_back_to_root_when_no_stderr_handler(self) -> None:
        """When no stderr handler exists, fallback attaches to root logger."""
        self.root.handlers = []
        self.root.filters = []

        self.attach()

        assert self.llm_filter in self.root.filters, (
            "LLMErrorLogFilter must fall back to root logger when no stderr handler"
        )

    def test_ignores_non_stderr_stream_handler(self) -> None:
        """A StreamHandler writing to stdout should NOT get the filter."""
        stdout_handler = logging.StreamHandler(sys.stdout)
        self.root.handlers = [stdout_handler]
        self.root.filters = []

        self.attach()

        assert self.llm_filter not in stdout_handler.filters, (
            "stdout handler should not receive the filter"
        )
        assert self.llm_filter in self.root.filters, (
            "LLMErrorLogFilter must fall back to root when only stdout handler exists"
        )


class TestFilterIntegrationChildLogger:
    """Integration test: filter suppresses child logger records on stderr handler.

    Simulates the production bug where a child logger (e.g. the provider module)
    emits an ERROR record matching filter patterns, and verifies the filter
    suppresses it from the stderr handler.
    """

    def setup_method(self) -> None:
        """Save root logger state, clear it, and add a fresh stderr handler."""
        self.root = logging.getLogger()
        self._orig_handlers = self.root.handlers[:]
        self._orig_filters = self.root.filters[:]
        self._orig_level = self.root.level

        self.root.handlers.clear()
        self.root.filters.clear()
        self.root.setLevel(logging.DEBUG)

        self.stderr_handler = logging.StreamHandler(sys.stderr)
        self.stderr_handler.setLevel(logging.DEBUG)
        self.root.addHandler(self.stderr_handler)

    def teardown_method(self) -> None:
        """Restore original root logger state."""
        self.root.handlers = self._orig_handlers
        self.root.filters = self._orig_filters
        self.root.setLevel(self._orig_level)

    def test_child_logger_provider_error_suppressed(self) -> None:
        from amplifier_app_cli.main import _attach_llm_error_filter

        _attach_llm_error_filter()

        child = logging.getLogger("amplifier_module_provider_anthropic")
        record = child.makeRecord(
            name=child.name,
            level=logging.ERROR,
            fn="__init__.py",
            lno=1507,
            msg='[PROVIDER] Anthropic API error: {"type":"error","error":{"type":"overloaded_error"}}',
            args=(),
            exc_info=None,
        )
        assert self.stderr_handler.filter(record) is False

    def test_child_logger_execution_failed_suppressed(self) -> None:
        from amplifier_app_cli.main import _attach_llm_error_filter

        _attach_llm_error_filter()

        child = logging.getLogger("amplifier_core.session")
        record = child.makeRecord(
            name=child.name,
            level=logging.ERROR,
            fn="session.py",
            lno=454,
            msg='Execution failed: {"type":"error","error":{"type":"overloaded_error"}}',
            args=(),
            exc_info=None,
        )
        assert self.stderr_handler.filter(record) is False

    def test_child_logger_unrelated_error_passes_through(self) -> None:
        from amplifier_app_cli.main import _attach_llm_error_filter

        _attach_llm_error_filter()

        child = logging.getLogger("amplifier_module_provider_anthropic")
        record = child.makeRecord(
            name=child.name,
            level=logging.ERROR,
            fn="__init__.py",
            lno=1507,
            msg="Connection pool exhausted",
            args=(),
            exc_info=None,
        )
        assert self.stderr_handler.filter(record)
