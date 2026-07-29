"""Runtime logging setup owned outside the CLI entrypoint."""

from __future__ import annotations

import logging
import sys

from amplifier_app_cli.ui.log_filter import LLMErrorLogFilter


def attach_llm_error_filter(error_filter: LLMErrorLogFilter) -> None:
    """Attach ``error_filter`` to configured terminal log handlers."""
    root = logging.getLogger()
    loggers = [root]
    loggers.extend(
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    attached = False
    for configured_logger in loggers:
        for handler in configured_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                continue
            if isinstance(handler, logging.StreamHandler):
                if getattr(handler, "stream", None) not in {
                    sys.stderr,
                    sys.__stderr__,
                }:
                    continue
            elif handler.__class__.__name__ != "RichHandler":
                continue
            if error_filter not in handler.filters:
                handler.addFilter(error_filter)
            attached = True
    if not attached and error_filter not in root.filters:
        root.addFilter(error_filter)


__all__ = ["attach_llm_error_filter"]
