"""Tests for _configure_console_logging() -- the fix for the traceback-leak
class of onboarding defects.

amplifier-app-cli never calls logging.basicConfig()/dictConfig()/
addHandler() anywhere. With zero handlers on the root logger, Python's
logging.lastResort dumps bare messages AND full tracebacks from any
module's `logger.warning(..., exc_info=True)` straight to stderr. This is
exactly how the owner's raw AuthenticationError traceback leaked during
`provider add openai-chatgpt`'s model fetch.

_configure_console_logging() installs a real stderr handler so:
1. Tracebacks are suppressed by default (unless verbose/debug requested).
2. _attach_llm_error_filter()'s primary "attach to existing stderr
   handler" path actually fires, instead of silently falling back to the
   inert root-logger-filter path (a logger-level filter never fires for
   records emitted by a *child* logger -- see that function's docstring).

These tests use an ISOLATED Logger instance (patched in place of
logging.getLogger()) rather than the true root logger, because pytest's
own log-capture plugin keeps a handler on the real root logger for the
duration of every test -- manipulating root.handlers directly is not
reliable under pytest.
"""

import io
import logging
import sys
from unittest.mock import patch

import pytest

from amplifier_app_cli.main import _configure_console_logging


def _isolated_logger(name: str) -> logging.Logger:
    """A fresh, unpropagated Logger standing in for "the root logger" for
    a single test, so pytest's own log-capture handler never interferes."""
    logger = logging.Logger(name)
    logger.handlers = []
    logger.filters = []
    return logger


class TestConfigureConsoleLogging:
    """Verify handler installation and no-op-when-already-configured."""

    def setup_method(self) -> None:
        self._orig_argv = sys.argv[:]

    def teardown_method(self) -> None:
        sys.argv = self._orig_argv

    def test_installs_stderr_handler_when_none_exists(self) -> None:
        fake_root = _isolated_logger("test-installs-handler")
        sys.argv = ["amplifier", "provider", "add", "openai-chatgpt"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        assert len(fake_root.handlers) == 1
        handler = fake_root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr
        assert handler.level == logging.WARNING

    def test_noop_when_handler_already_present(self) -> None:
        fake_root = _isolated_logger("test-noop")
        existing = logging.StreamHandler(sys.stderr)
        fake_root.handlers = [existing]
        sys.argv = ["amplifier", "run"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        assert fake_root.handlers == [existing], (
            "Must never override an existing logging setup"
        )

    def test_suppresses_traceback_by_default(self) -> None:
        """The installed handler must strip exc_info/exc_text from a
        record unless verbose/debug was requested on the command line."""
        fake_root = _isolated_logger("test-suppress-default")
        sys.argv = ["amplifier", "provider", "add", "openai-chatgpt"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        handler = fake_root.handlers[0]
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.getLogger(
                "amplifier_module_provider_openai_chatgpt"
            ).makeRecord(
                name="amplifier_module_provider_openai_chatgpt",
                level=logging.WARNING,
                fn="provider.py",
                lno=1,
                msg="Could not fetch models: %s",
                args=("boom",),
                exc_info=sys.exc_info(),
            )
        assert record.exc_info is not None  # sanity: it started populated
        assert handler.filter(record)
        assert record.exc_info is None, (
            "Traceback info must be stripped from the record by default"
        )
        assert record.exc_text is None

    @pytest.mark.parametrize("flag", ["--verbose", "-v", "--debug"])
    def test_preserves_traceback_when_verbose_requested(self, flag: str) -> None:
        fake_root = _isolated_logger(f"test-preserve-{flag}")
        sys.argv = ["amplifier", "provider", "add", "openai-chatgpt", flag]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        handler = fake_root.handlers[0]
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.getLogger(
                "amplifier_module_provider_openai_chatgpt"
            ).makeRecord(
                name="amplifier_module_provider_openai_chatgpt",
                level=logging.WARNING,
                fn="provider.py",
                lno=1,
                msg="Could not fetch models: %s",
                args=("boom",),
                exc_info=sys.exc_info(),
            )
        assert handler.filter(record)
        assert record.exc_info is not None, (
            "Traceback info must be preserved when --verbose/-v/--debug is requested"
        )

    def test_message_only_formatter(self) -> None:
        fake_root = _isolated_logger("test-formatter")
        sys.argv = ["amplifier"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        handler = fake_root.handlers[0]
        assert handler.formatter is not None
        assert handler.formatter._fmt == "%(message)s"


class TestConfigureConsoleLoggingEnablesLlmErrorFilterAttachment:
    """Integration: once a real handler exists, _attach_llm_error_filter()
    must take its primary path (attach to the handler), not the inert
    root-logger fallback."""

    def setup_method(self) -> None:
        self._orig_argv = sys.argv[:]

    def teardown_method(self) -> None:
        sys.argv = self._orig_argv

    def test_llm_error_filter_attaches_to_handler_not_root(self) -> None:
        from amplifier_app_cli.main import _attach_llm_error_filter, _llm_error_filter

        fake_root = _isolated_logger("test-llm-filter-attach")
        sys.argv = ["amplifier", "provider", "add", "openai-chatgpt"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()
            _attach_llm_error_filter()

        assert len(fake_root.handlers) == 1
        handler = fake_root.handlers[0]
        assert _llm_error_filter in handler.filters, (
            "LLMErrorLogFilter must land on the real stderr handler "
            "installed by _configure_console_logging(), not fall back "
            "to the inert root-logger filter path"
        )
        assert _llm_error_filter not in fake_root.filters


class TestTracebackSuppressionEndToEnd:
    """A record actually written through the handler must not contain a
    traceback in the emitted output, by default."""

    def setup_method(self) -> None:
        self._orig_argv = sys.argv[:]

    def teardown_method(self) -> None:
        sys.argv = self._orig_argv

    def test_logged_exception_reaches_stderr_without_traceback(self) -> None:
        fake_root = _isolated_logger("test-e2e-no-verbose")
        sys.argv = ["amplifier", "provider", "add", "openai-chatgpt"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        # Redirect the installed handler's stream to a buffer we can inspect.
        buf = io.StringIO()
        fake_root.handlers[0].stream = buf

        logger = logging.Logger("amplifier_module_provider_openai_chatgpt")
        logger.handlers = []
        logger.parent = fake_root
        logger.setLevel(logging.WARNING)
        try:
            raise RuntimeError("AuthenticationError: invalid api key")
        except RuntimeError:
            logger.warning("Could not fetch models: %s", "auth failed", exc_info=True)

        output = buf.getvalue()
        assert "Could not fetch models: auth failed" in output
        assert "Traceback" not in output
        assert "RuntimeError" not in output

    def test_logged_exception_reaches_stderr_with_traceback_when_verbose(
        self,
    ) -> None:
        fake_root = _isolated_logger("test-e2e-verbose")
        sys.argv = ["amplifier", "provider", "add", "openai-chatgpt", "--verbose"]

        with patch("amplifier_app_cli.main.logging.getLogger", return_value=fake_root):
            _configure_console_logging()

        buf = io.StringIO()
        fake_root.handlers[0].stream = buf

        logger = logging.Logger("amplifier_module_provider_openai_chatgpt")
        logger.handlers = []
        logger.parent = fake_root
        logger.setLevel(logging.WARNING)
        try:
            raise RuntimeError("AuthenticationError: invalid api key")
        except RuntimeError:
            logger.warning("Could not fetch models: %s", "auth failed", exc_info=True)

        output = buf.getvalue()
        assert "Traceback" in output
