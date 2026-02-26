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


def _run_filter_attachment_logic() -> tuple[LLMErrorLogFilter, bool]:
    """Execute the same attachment logic as main.py and return (filter, attached_to_handler).

    This is an exact copy of the logic from main.py lines 73-88, used to verify
    the algorithm works correctly under controlled conditions.

    NOTE: Keep in sync with main.py's module-level attachment code.
    If the production logic changes, update this helper to match.
    test_main_module_has_correct_attachment_code() guards against drift
    by checking key patterns in the source, but this helper must also be
    updated manually.
    """
    root = logging.getLogger()
    _llm_error_filter = LLMErrorLogFilter()
    _filter_attached = False
    for _handler in root.handlers:
        if (
            isinstance(_handler, logging.StreamHandler)
            and hasattr(_handler, "stream")
            and _handler.stream is sys.stderr
        ):
            _handler.addFilter(_llm_error_filter)
            _filter_attached = True
            break
    if not _filter_attached:
        root.addFilter(_llm_error_filter)
    return _llm_error_filter, _filter_attached


class TestFilterAttachmentLogic:
    """Verify that the filter attachment logic targets the stderr handler."""

    def setup_method(self) -> None:
        """Save root logger state and clean up any existing LLMErrorLogFilter."""
        self.root = logging.getLogger()
        self._orig_handlers = self.root.handlers[:]
        self._orig_filters = self.root.filters[:]

    def teardown_method(self) -> None:
        """Restore root logger state."""
        self.root.handlers = self._orig_handlers
        self.root.filters = self._orig_filters

    def test_filter_attaches_to_stderr_handler_when_present(self) -> None:
        """When a stderr StreamHandler exists, the filter goes on it, not root."""
        # Set up a stderr StreamHandler on root
        stderr_handler = logging.StreamHandler(sys.stderr)
        self.root.handlers = [stderr_handler]
        self.root.filters = []

        filt, attached_to_handler = _run_filter_attachment_logic()

        # Filter must be on the handler
        assert attached_to_handler, "Filter should have been attached to handler"
        assert filt in stderr_handler.filters, (
            "LLMErrorLogFilter must be in the stderr handler's filters"
        )
        # Filter must NOT be on root logger
        assert filt not in self.root.filters, (
            "LLMErrorLogFilter should not be on the root logger when stderr handler exists"
        )

    def test_filter_falls_back_to_root_when_no_stderr_handler(self) -> None:
        """When no stderr handler exists, fallback attaches to root logger."""
        self.root.handlers = []
        self.root.filters = []

        filt, attached_to_handler = _run_filter_attachment_logic()

        assert not attached_to_handler, "No handler to attach to"
        assert filt in self.root.filters, (
            "LLMErrorLogFilter must fall back to root logger when no stderr handler"
        )

    def test_filter_ignores_non_stderr_stream_handler(self) -> None:
        """A StreamHandler writing to stdout should NOT get the filter."""
        stdout_handler = logging.StreamHandler(sys.stdout)
        self.root.handlers = [stdout_handler]
        self.root.filters = []

        filt, attached_to_handler = _run_filter_attachment_logic()

        assert not attached_to_handler, "stdout handler should not receive the filter"
        assert filt not in stdout_handler.filters
        # Falls back to root instead
        assert filt in self.root.filters

    def test_main_module_has_correct_attachment_code(self) -> None:
        """Verify main.py contains handler-level attachment, not root-level."""
        from pathlib import Path

        main_mod = sys.modules["amplifier_app_cli.main"]
        main_file = main_mod.__file__
        assert main_file is not None, "main module must have a __file__"
        source = Path(main_file).read_text(encoding="utf-8")

        # The new code should iterate handlers and check for stderr
        assert "_llm_error_filter = LLMErrorLogFilter()" in source, (
            "main.py must create a named filter instance"
        )
        assert "_handler.addFilter(_llm_error_filter)" in source, (
            "main.py must attach filter to handler"
        )
        assert "_handler.stream is sys.stderr" in source, (
            "main.py must check for stderr stream"
        )
