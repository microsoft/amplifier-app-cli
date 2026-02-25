"""Log filter to suppress duplicate LLM error lines from console output.

When an LLM error occurs, the provider and session both log the error via
``logger.error()`` before the CLI's ``except LLMError`` handler renders
a Rich panel. This filter, when attached to the console handler only,
suppresses those known duplicate patterns so the user sees just the panel.

Log file handlers are unaffected — all records are preserved for debugging.
"""

import logging


class LLMErrorLogFilter(logging.Filter):
    """Suppress known LLM error patterns from the console handler.

    Drops ERROR-level records that match:
    - ``[PROVIDER] * API error:`` — provider's error log
    - ``[PROVIDER] * response processing error:`` — provider's catch-all
    - ``Execution failed:`` — session's error log

    Everything else passes through unchanged.
    """

    _SUPPRESSED_PREFIXES = ("Execution failed:",)

    _SUPPRESSED_FRAGMENTS = (
        "API error:",
        "response processing error:",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False to suppress the record, True to let it through."""
        if record.levelno != logging.ERROR:
            return True

        message = record.getMessage()

        for prefix in self._SUPPRESSED_PREFIXES:
            if message.startswith(prefix):
                return False

        if "[PROVIDER]" in message:
            for fragment in self._SUPPRESSED_FRAGMENTS:
                if fragment in message:
                    return False

        return True
