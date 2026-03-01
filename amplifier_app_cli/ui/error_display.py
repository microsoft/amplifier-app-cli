"""Clean error display for module validation and LLM errors."""

import json
import re
from typing import NamedTuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from amplifier_core.llm_errors import (
    AuthenticationError,
    ContentFilterError,
    ContextLengthError,
    LLMError,
    RateLimitError,
)


class ParsedValidationError(NamedTuple):
    """Parsed components of a ModuleValidationError."""

    module_id: str
    summary: str
    errors: list[tuple[str, str]]  # List of (check_name, message) tuples
    raw_message: str


def parse_validation_error(error: Exception) -> ParsedValidationError | None:
    """
    Parse a ModuleValidationError message into structured components.

    Also handles RuntimeError that wraps a ModuleValidationError
    (e.g., "Cannot initialize without orchestrator: Module '...' failed validation: ...")

    Returns None if the error doesn't match expected format.
    """
    message = str(error)

    # Pattern 1: Full validation error with checks (can be embedded in wrapper message)
    # "Module 'provider-anthropic' failed validation: 2 passed, 3 failed. Errors: check1: msg1; check2: msg2"
    full_pattern = r"Module '([^']+)' failed validation: ([^.]+)\. Errors: (.+)"
    match = re.search(full_pattern, message)  # search anywhere in message

    if match:
        module_id = match.group(1)
        summary = match.group(2)
        errors_str = match.group(3)

        # Parse individual errors (semicolon separated "name: message" pairs)
        errors = []
        for error_part in errors_str.split("; "):
            if ": " in error_part:
                name, msg = error_part.split(": ", 1)
                errors.append((name.strip(), msg.strip()))
            else:
                errors.append(("unknown", error_part.strip()))

        return ParsedValidationError(
            module_id=module_id,
            summary=summary,
            errors=errors,
            raw_message=message,
        )

    # Pattern 2: No valid package error (can be embedded in wrapper message)
    # "Module 'xyz' has no valid Python package at /path/to/module"
    package_pattern = r"Module '([^']+)' has no valid Python package at (.+)"
    match = re.search(package_pattern, message)  # search anywhere in message

    if match:
        module_id = match.group(1)
        path = match.group(2)

        return ParsedValidationError(
            module_id=module_id,
            summary="No valid Python package found",
            errors=[("package_structure", f"Expected package at: {path}")],
            raw_message=message,
        )

    return None


def display_validation_error(
    console: Console, error: Exception, verbose: bool = False
) -> bool:
    """
    Display a ModuleValidationError with clean Rich formatting.

    Args:
        console: Rich console for output
        error: The error to display
        verbose: If True, also print traceback

    Returns:
        True if error was handled as validation error, False if not (caller should handle)
    """
    parsed = parse_validation_error(error)

    if parsed is None:
        return False

    # Build the error panel content
    content = Text()

    # Module info
    content.append("Module: ", style="dim")
    content.append(parsed.module_id, style="bold cyan")
    content.append("\n")

    # Infer module type from ID
    module_type = _infer_module_type(parsed.module_id)
    content.append("Type: ", style="dim")
    content.append(module_type, style="yellow")
    content.append("\n\n")

    # Validation summary
    content.append("Result: ", style="dim")
    content.append(parsed.summary, style="red")
    content.append("\n\n")

    # Create table for errors
    error_table = Table(show_header=False, box=None, padding=(0, 1))
    error_table.add_column("Status", style="red", width=3)
    error_table.add_column("Check", style="bold")
    error_table.add_column("Message", style="dim")

    for check_name, message in parsed.errors:
        error_table.add_row("✗", check_name, message)

    # Print the panel
    console.print()
    console.print(
        Panel(
            content,
            title="[bold red]Module Validation Failed[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )

    # Print error details table
    console.print(error_table)
    console.print()

    # Actionable tip
    tip = _get_actionable_tip(parsed)
    console.print(f"[dim]Tip: {tip}[/dim]")
    console.print()

    # Verbose mode: show traceback
    if verbose:
        console.print("[dim]─── Traceback ───[/dim]")
        console.print_exception()

    return True


def _infer_module_type(module_id: str) -> str:
    """Infer module type from module ID prefix."""
    prefixes = {
        "provider-": "Provider",
        "tool-": "Tool",
        "hooks-": "Hook",
        "loop-": "Orchestrator",
        "context-": "Context",
    }

    for prefix, module_type in prefixes.items():
        if module_id.startswith(prefix):
            return module_type

    return "Unknown"


def _get_actionable_tip(parsed: ParsedValidationError) -> str:
    """Generate an actionable tip based on the error."""
    # Check for common patterns
    error_names = [name.lower() for name, _ in parsed.errors]

    if "mount_function" in error_names or "package_structure" in error_names:
        return "Check that the module has a valid mount() function in __init__.py"

    if any("export" in name for name in error_names):
        return "Ensure required exports are present in the module's __init__.py"

    if any("signature" in name for name in error_names):
        return "Check that function signatures match the expected module contract"

    # Default tip
    return f"Review the module at: amplifier-module-{parsed.module_id}"


# ---- Maximum message length before truncation ----
_MAX_MESSAGE_LEN = 200


def _truncate(message: str, limit: int = _MAX_MESSAGE_LEN) -> str:
    """Truncate a long error message, adding an ellipsis if shortened."""
    if len(message) <= limit:
        return message
    return message[:limit] + "…"


def _extract_message(raw: str) -> str:
    """Extract a human-readable message from a raw error string.

    Tries to parse *raw* as JSON and looks for a ``message`` field
    (nested under ``error`` first, then top-level).  Falls back to
    a truncated version of the raw string when JSON parsing fails.
    """
    try:
        parsed = json.loads(raw)
        nested = parsed.get("error", {})
        if isinstance(nested, dict):
            msg = nested.get("message")
            if msg:
                return str(msg)
        top = parsed.get("message")
        if top:
            return str(top)
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return _truncate(raw)


def display_llm_error(
    console: Console, error: Exception, verbose: bool = False
) -> bool:
    """Display an LLMError with clean Rich formatting.

    Args:
        console: Rich console for output.
        error: The error to display.
        verbose: If True, also print traceback.

    Returns:
        True if error was handled as an LLMError, False if not (caller should handle).
    """
    if not isinstance(error, LLMError):
        return False

    # Determine title, border colour, and actionable tip based on error type.
    if isinstance(error, RateLimitError):
        title = "Rate Limited"
        border_style = "yellow"
    elif isinstance(error, AuthenticationError):
        title = "Authentication Failed"
        border_style = "red"
    elif isinstance(error, ContextLengthError):
        title = "Context Length Exceeded"
        border_style = "red"
    elif isinstance(error, ContentFilterError):
        title = "Content Filtered"
        border_style = "red"
    else:
        title = "LLM Error"
        border_style = "red"

    tip = _get_llm_error_tip(error)

    # Build the panel content
    content = Text()

    # Compact provider / model line (no labels)
    provider_model_parts: list[str] = []
    if error.provider:
        provider_model_parts.append(error.provider)
    if error.model:
        provider_model_parts.append(error.model)
    if provider_model_parts:
        content.append(" / ".join(provider_model_parts), style="bold cyan")
        content.append("\n")

    # Extracted human-readable message
    raw = str(error)
    content.append("\n")
    content.append(_extract_message(raw), style="white")
    content.append("\n")

    # Raw Details section
    content.append("\n")
    content.append("── Raw Details ──", style="dim")
    content.append("\n")
    content.append(raw, style="dim")

    # Print the panel
    console.print()
    console.print(
        Panel(
            content,
            title=f"[bold {border_style}]{title}[/bold {border_style}]",
            border_style=border_style,
            padding=(1, 2),
        )
    )

    # Actionable tip
    console.print(f"[dim]Tip: {tip}[/dim]")
    console.print()

    # Verbose mode: show traceback
    if verbose:
        import sys

        console.print("[dim]——— Traceback ———[/dim]")
        if sys.exc_info()[0] is not None:
            console.print_exception()

    return True


def _get_llm_error_tip(error: LLMError) -> str:
    """Return an actionable tip based on the LLM error type."""
    if isinstance(error, RateLimitError):
        if error.retry_after is not None:
            return (
                f"The provider will accept requests again in ~{error.retry_after:.0f}s. "
                "Retry automatically or wait and try again."
            )
        return "You've hit a rate limit. Wait a moment and retry, or reduce request frequency."

    if isinstance(error, AuthenticationError):
        provider = error.provider or "your provider"
        return f"Check that your API key or credentials for {provider} are valid and not expired."

    if isinstance(error, ContextLengthError):
        return "Reduce conversation length or context size. Try starting a new conversation."

    if isinstance(error, ContentFilterError):
        return "Your request was blocked by the provider's content filter. Try rephrasing your message."

    # Generic LLMError
    return "An unexpected LLM error occurred. Check the error details above."
