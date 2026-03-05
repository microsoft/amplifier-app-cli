"""Shared Scope UI helpers for interactive commands.

Provides reusable functions for scope indicator display, scope change prompts,
scope availability checks, and CLI scope validation guards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Literal

    ScopeValue = Literal["global", "project", "local"]

import click
from rich.console import Console
from rich.prompt import Prompt

from amplifier_app_cli.paths import is_running_from_home

# Scope metadata: display_name, file_hint, description, parenthetical
_SCOPE_INFO: dict[str, dict[str, str]] = {
    "global": {
        "display_name": "Global",
        "file_hint": "~/.amplifier/settings.yaml",
        "description": "User-wide defaults",
        "parenthetical": "all projects",
    },
    "project": {
        "display_name": "Project",
        "file_hint": ".amplifier/settings.yaml",
        "description": "Team-shared project settings",
        "parenthetical": "committed",
    },
    "local": {
        "display_name": "Local",
        "file_hint": ".amplifier/settings.local.yaml",
        "description": "Machine-specific overrides",
        "parenthetical": "gitignored",
    },
}

_SCOPE_ORDER: list[ScopeValue] = ["global", "project", "local"]


def print_scope_indicator(
    scope: ScopeValue,
    *,
    console: Console | None = None,
) -> None:
    """Render a 'Saving to: ...' line with scope-appropriate styling.

    Global scope gets dim treatment; project and local scopes get yellow treatment.
    """
    if console is None:
        console = Console()

    # Fallback to global for unknown scopes
    info = _SCOPE_INFO.get(scope, _SCOPE_INFO["global"])
    label = info["display_name"]
    hint = info["file_hint"]

    if scope == "global":
        console.print(f"[dim]Saving to: {label} ({hint})[/dim]")
    else:
        console.print(f"[yellow]Saving to: {label} ({hint})[/yellow]")


def is_scope_change_available() -> bool:
    """Return whether scope change is available.

    Returns False when cwd is the home directory (only global scope makes sense).
    """
    return not is_running_from_home()


def prompt_scope_change(
    current_scope: ScopeValue,
    *,
    console: Console | None = None,
) -> ScopeValue:
    """Interactive submenu for switching scope.

    Shows a numbered list of scopes with an arrow marker on the current one.
    Uses Prompt.ask() with choices validation. Shows confirmation on change.

    Returns the selected scope name.
    """
    if console is None:
        console = Console()

    console.print()
    console.print("[bold]Select scope:[/bold]")

    for idx, scope_key in enumerate(_SCOPE_ORDER, start=1):
        info = _SCOPE_INFO[scope_key]
        marker = " ← current" if scope_key == current_scope else ""
        console.print(
            f"  {idx}. {info['display_name']}"
            f" — {info['description']} ({info['parenthetical']}){marker}"
        )

    console.print()
    choices = [str(i) for i in range(1, len(_SCOPE_ORDER) + 1)]
    choice = Prompt.ask("Choice", choices=choices, default="1")
    selected = _SCOPE_ORDER[int(choice) - 1]

    if selected != current_scope:
        new_info = _SCOPE_INFO[selected]
        console.print(
            f"\n[green]Scope changed to {new_info['display_name']}"
            f" ({new_info['file_hint']})[/green]"
        )

    return selected


def validate_scope_cli(scope: ScopeValue) -> None:
    """Guard for --scope CLI flags.

    Raises click.UsageError if a non-global scope is requested from the
    home directory.
    """
    if scope == "global":
        return

    if is_running_from_home():
        raise click.UsageError(
            f"The '{scope}' scope is not available from your home directory. "
            f"Use --scope=global or cd into a project directory."
        )
