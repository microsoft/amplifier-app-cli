"""Concise typed rendering for interactive execution failures."""

from __future__ import annotations

from amplifier_core import ModuleValidationError  # pyright: ignore[reportAttributeAccessIssue]
from amplifier_core.llm_errors import LLMError

from .error_display import concise_llm_error
from .transcript_blocks import BlockedBlock
from .transcript_blocks import DebugBlock
from .ui_events import UiEventDispatcher


def render_execution_error(
    error: Exception,
    *,
    events: UiEventDispatcher,
    verbose: bool,
) -> None:
    if isinstance(error, LLMError):
        title, message = concise_llm_error(error)
        events.emit(BlockedBlock(title, message))
        return
    message = " ".join(str(error).split())[:500]
    if isinstance(error, ModuleValidationError):
        events.emit(BlockedBlock("Module validation failed", message))
        if verbose:
            events.emit(DebugBlock((message,), label="Validation detail"))
        return
    events.emit(BlockedBlock("Execution failed", message))
    if verbose:
        events.emit(DebugBlock((message,), label=type(error).__name__))


__all__ = ["render_execution_error"]
