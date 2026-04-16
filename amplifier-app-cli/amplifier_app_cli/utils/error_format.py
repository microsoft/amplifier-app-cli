"""Safe error message formatting utilities.

Ensures exceptions always have useful display messages, even when
their str() representation is empty (e.g., TimeoutError, CancelledError).

This module addresses a common issue where certain Python exceptions
have empty string representations, leading to unhelpful error messages
like "Error: " with nothing after the colon.
"""

from __future__ import annotations

import asyncio
from rich.markup import escape as _escape_markup


# Friendly messages for specific exception types known to have empty str()
# These provide user-actionable context when the exception itself has none
FRIENDLY_MESSAGES: dict[type, str] = {
    TimeoutError: "Request timed out. This may indicate network issues or a slow API response.",
    asyncio.CancelledError: "Operation was cancelled.",
    ConnectionResetError: "Connection was reset by the server.",
    BrokenPipeError: "Connection was closed unexpectedly.",
    KeyboardInterrupt: "Operation interrupted by user.",
}


def format_error_message(e: BaseException, *, include_type: bool = True) -> str:
    """Format an exception into a useful display message.

    Handles exceptions with empty str() representations by falling back
    to type name and/or friendly messages.

    Args:
        e: The exception to format
        include_type: Whether to include the exception type name

    Returns:
        A non-empty, user-friendly error message

    Examples:
        >>> format_error_message(TimeoutError())
        'TimeoutError: Request timed out. This may indicate network issues or a slow API response.'

        >>> format_error_message(ValueError("invalid input"))
        'ValueError: invalid input'

        >>> format_error_message(ValueError("invalid input"), include_type=False)
        'invalid input'
    """
    error_str = str(e)
    error_type = type(e).__name__

    # If we have a message, use it
    if error_str:
        if include_type and error_type not in error_str:
            return f"{error_type}: {error_str}"
        return error_str

    # No message - check for friendly fallback
    for exc_type, friendly_msg in FRIENDLY_MESSAGES.items():
        if isinstance(e, exc_type):
            return f"{error_type}: {friendly_msg}"

    # Last resort: just the type name with indicator
    return f"{error_type}: (no additional details)"


def escape_markup(value: object) -> str:
    """Escape a value for safe interpolation into Rich markup strings.

    Prevents Rich from interpreting brackets in exception messages,
    file paths, or other dynamic content as markup tags.

    Args:
        value: Any value to escape (will be converted to str)

    Returns:
        String safe for interpolation into Rich markup f-strings
    """
    return _escape_markup(str(value))
