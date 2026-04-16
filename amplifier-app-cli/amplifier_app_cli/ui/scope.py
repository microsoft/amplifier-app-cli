"""Shared scope UI helpers for interactive dashboards and CLI commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
from rich.prompt import Prompt

from ..lib.settings import Scope
from ..paths import is_running_from_home

if TYPE_CHECKING:
    from rich.console import Console

    from ..lib.settings import AppSettings

_SCOPE_INFO: dict[str, tuple[str, str, str, str]] = {
    "global": (
        "global",
        "~/.amplifier/settings.yaml",
        "Your defaults across all projects",
        "",
    ),
    "project": (
        "project",
        ".amplifier/settings.yaml",
        "Team settings, committed to git",
        "(team-shared, committed)",
    ),
    "local": (
        "local",
        ".amplifier/settings.local.yaml",
        "This machine only, gitignored",
        "(this machine only, gitignored)",
    ),
}

_SCOPE_ORDER: list[str] = ["global", "project", "local"]

_HOME_DIR_ERROR = (
    "Your home directory is your global scope — project and local scopes "
    "apply to specific project directories. Run this command from a project "
    "folder to use those scopes."
)


def print_scope_indicator(
    console: Console,
    settings: AppSettings,
    current_scope: Scope,
) -> None:
    _name, file_hint, _desc, parenthetical = _SCOPE_INFO[current_scope]
    if current_scope == "global":
        console.print(
            f"  [dim]Saving to:[/dim] [bold]{current_scope}[/bold]"
            f"  [dim]{file_hint}[/dim]"
        )
    else:
        console.print(
            f"  [yellow]Saving to:[/yellow] [bold yellow]{current_scope}[/bold yellow]"
            f"  [dim]{file_hint}[/dim]"
            f"  [yellow]{parenthetical}[/yellow]"
        )


def is_scope_change_available() -> bool:
    return not is_running_from_home()


def prompt_scope_change(
    console: Console,
    settings: AppSettings,
    current_scope: Scope,
) -> Scope:
    console.print("\n  Write scope:")
    for i, scope_name in enumerate(_SCOPE_ORDER, 1):
        _name, file_hint, description, _paren = _SCOPE_INFO[scope_name]
        marker = "\u25b8" if scope_name == current_scope else " "
        default_tag = " (default)" if scope_name == "global" else ""
        console.print(
            f"  {marker} [{i}] {scope_name:<8} {file_hint:<40} {description}{default_tag}"
        )
    console.print()

    scope_map = {str(i): name for i, name in enumerate(_SCOPE_ORDER, 1)}
    current_number = str(_SCOPE_ORDER.index(current_scope) + 1)

    try:
        choice = Prompt.ask(
            "  Scope",
            choices=list(scope_map.keys()),
            default=current_number,
        )
    except (EOFError, KeyboardInterrupt):
        return current_scope

    new_scope: Scope = scope_map[choice]  # type: ignore[assignment]

    if new_scope != current_scope:
        _name, file_hint, _desc, _paren = _SCOPE_INFO[new_scope]
        if new_scope == "project":
            extra = " (shared via git)"
        elif new_scope == "local":
            extra = " (gitignored)"
        else:
            extra = ""
        console.print(
            f"  [green]\u2713 Switched to {new_scope} scope. "
            f"Changes save to {file_hint}{extra}.[/green]"
        )

    return new_scope


def validate_scope_cli(scope: str) -> None:
    if scope != "global" and is_running_from_home():
        raise click.UsageError(_HOME_DIR_ERROR)
