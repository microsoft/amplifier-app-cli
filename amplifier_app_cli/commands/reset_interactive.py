"""Interactive terminal UI components for reset command.

Provides a checklist interface for user selection using raw terminal codes.
Zero external dependencies beyond stdlib - keeps the reset command self-contained
even during self-uninstall scenarios.

Example:
    >>> from .reset_interactive import run_checklist, ChecklistItem
    >>> items = [
    ...     ChecklistItem("projects", "Session transcripts", True),
    ...     ChecklistItem("cache", "Downloaded bundles", False),
    ... ]
    >>> selected = run_checklist(items, title="Select to preserve")
    >>> print(selected)  # {"projects"}
"""

from __future__ import annotations

import sys
import tty
import termios
from dataclasses import dataclass


@dataclass
class ChecklistItem:
    """A single item in the checklist.

    Attributes:
        key: Unique identifier for this item
        description: Human-readable description
        selected: Whether item is currently selected
    """

    key: str
    description: str
    selected: bool = False


def _get_key() -> str:
    """Read a single keypress from stdin.

    Returns:
        The key pressed, or special strings for arrow keys:
        - "UP" for up arrow
        - "DOWN" for down arrow
        - "ENTER" for enter/return
        - Single character for other keys
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)

        # Handle escape sequences (arrow keys)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return "UP"
                elif ch3 == "B":
                    return "DOWN"
                elif ch3 == "C":
                    return "RIGHT"
                elif ch3 == "D":
                    return "LEFT"
            return "ESC"

        # Handle enter
        if ch in ("\r", "\n"):
            return "ENTER"

        # Handle ctrl+c
        if ch == "\x03":
            raise KeyboardInterrupt

        return ch

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _clear_lines(n: int) -> None:
    """Clear n lines above cursor and move cursor up."""
    for _ in range(n):
        sys.stdout.write("\x1b[A")  # Move up
        sys.stdout.write("\x1b[2K")  # Clear line
    sys.stdout.flush()


def _hide_cursor() -> None:
    """Hide the terminal cursor."""
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()


def _show_cursor() -> None:
    """Show the terminal cursor."""
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def _render_checklist(
    items: list[ChecklistItem],
    cursor: int,
    title: str,
    will_remove: list[str],
    will_preserve: list[str],
) -> int:
    """Render the checklist UI and return number of lines printed.

    Args:
        items: List of checklist items
        cursor: Current cursor position (0-indexed)
        title: Title to display
        will_remove: List of category names that will be removed
        will_preserve: List of category names that will be preserved

    Returns:
        Number of lines printed (for clearing on re-render)
    """
    lines = []

    # Title (blank line for spacing, then title)
    lines.append("")
    lines.append(title)
    lines.append("=" * len(title))
    lines.append("")
    lines.append("Select what to PRESERVE (↑↓ navigate, space toggle, enter confirm):")
    lines.append("")

    # Items
    max_key_len = max(len(item.key) for item in items)
    for i, item in enumerate(items):
        prefix = ">" if i == cursor else " "
        checkbox = "[x]" if item.selected else "[ ]"
        key_padded = item.key.ljust(max_key_len)
        lines.append(f"  {prefix} {checkbox} {key_padded}  - {item.description}")

    # Separator
    lines.append("")
    lines.append("─" * 60)

    # Summary
    remove_str = ", ".join(will_remove) if will_remove else "(nothing)"
    preserve_str = ", ".join(will_preserve) if will_preserve else "(nothing)"
    lines.append(f"Will REMOVE: {remove_str}")
    lines.append(f"Will PRESERVE: {preserve_str}")
    lines.append("")
    lines.append("[Enter] Continue  [a] Preserve all  [n] Preserve none  [q] Quit")

    # Print all lines
    output = "\n".join(lines)
    print(output)

    return len(lines)


def run_checklist(
    items: list[ChecklistItem],
    title: str = "Amplifier Reset",
) -> set[str] | None:
    """Run an interactive checklist and return selected items.

    Args:
        items: List of ChecklistItem objects
        title: Title to display above checklist

    Returns:
        Set of selected item keys, or None if user quit/cancelled
    """
    if not items:
        return set()

    cursor = 0
    lines_printed = 0

    _hide_cursor()
    try:
        while True:
            # Calculate what will be removed/preserved
            will_preserve = [item.key for item in items if item.selected]
            will_remove = [item.key for item in items if not item.selected]

            # Clear previous render
            if lines_printed > 0:
                _clear_lines(lines_printed)

            # Render
            lines_printed = _render_checklist(
                items, cursor, title, will_remove, will_preserve
            )

            # Get input
            key = _get_key()

            if key == "UP":
                cursor = (cursor - 1) % len(items)
            elif key == "DOWN":
                cursor = (cursor + 1) % len(items)
            elif key == " ":
                # Toggle current item
                items[cursor].selected = not items[cursor].selected
            elif key == "ENTER":
                print()  # Newline after UI
                return {item.key for item in items if item.selected}
            elif key.lower() == "a":
                # Select all
                for item in items:
                    item.selected = True
            elif key.lower() == "n":
                # Select none
                for item in items:
                    item.selected = False
            elif key.lower() == "q" or key == "ESC":
                print()  # Newline after UI
                return None

    except KeyboardInterrupt:
        print()
        return None
    finally:
        _show_cursor()


__all__ = [
    "ChecklistItem",
    "run_checklist",
]
