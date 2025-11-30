"""Clean error display for module validation failures."""

import re
from typing import NamedTuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


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


def display_validation_error(console: Console, error: Exception, verbose: bool = False) -> bool:
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
