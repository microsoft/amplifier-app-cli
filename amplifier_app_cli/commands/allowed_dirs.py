"""Allowed directories management commands.

Manages allowed_write_paths for tool-filesystem, controlling which
directories the AI can write to.
"""

from pathlib import Path
from typing import cast

import click
from rich.console import Console
from rich.table import Table

from ..lib.settings import AppSettings
from ..lib.settings import Scope
from ..paths import create_config_manager
from ..paths import get_effective_scope
from ..paths import ScopeNotAvailableError
from ..utils.error_format import escape_markup

console = Console()


@click.group(name="allowed-dirs")
def allowed_dirs():
    """Manage directories the AI can write to.

    Controls allowed_write_paths for tool-filesystem. By default,
    the AI can only write to the current working directory.

    Examples:
        amplifier allowed-dirs list
        amplifier allowed-dirs add /tmp/test --global
        amplifier allowed-dirs remove /tmp/test
    """
    pass


@allowed_dirs.command("list")
@click.option(
    "--local", "scope_filter", flag_value="local", help="Show only local paths"
)
@click.option(
    "--project", "scope_filter", flag_value="project", help="Show only project paths"
)
@click.option(
    "--global", "scope_filter", flag_value="global", help="Show only global paths"
)
@click.option(
    "--session", "scope_filter", flag_value="session", help="Show only session paths"
)
def list_dirs(scope_filter: str | None):
    """List allowed write directories.

    Shows all configured allowed_write_paths and their source scope.
    """
    settings = AppSettings()
    paths = settings.get_allowed_write_paths()

    if scope_filter:
        paths = [(p, s) for p, s in paths if s == scope_filter]

    if not paths:
        if scope_filter:
            console.print(
                f"[yellow]No allowed directories at {scope_filter} scope[/yellow]"
            )
        else:
            console.print("[yellow]No allowed directories configured[/yellow]")
            console.print(
                "\n[dim]Add directories with: amplifier allowed-dirs add <path>[/dim]"
            )
        return

    table = Table(
        title="Allowed Write Directories", show_header=True, header_style="bold cyan"
    )
    table.add_column("Path", style="green")
    table.add_column("Scope", style="yellow")

    for path, scope in paths:
        table.add_row(path, scope)

    console.print(table)


@allowed_dirs.command("add")
@click.argument("path")
@click.option("--local", "scope_flag", flag_value="local", help="Add to local scope")
@click.option(
    "--project", "scope_flag", flag_value="project", help="Add to project scope"
)
@click.option(
    "--global", "scope_flag", flag_value="global", help="Add to global scope (default)"
)
def add_dir(path: str, scope_flag: str | None):
    """Add a directory to allowed write paths.

    PATH should be an absolute path or will be resolved relative to cwd.

    Examples:
        amplifier allowed-dirs add /tmp/test
        amplifier allowed-dirs add ./output --project
        amplifier allowed-dirs add ~/scratch --global
    """
    # Resolve path to absolute
    resolved = Path(path).expanduser().resolve()

    if not resolved.exists():
        console.print(f"[yellow]Warning:[/yellow] Path does not exist: {resolved}")
        console.print("[dim]Directory will be allowed once it's created.[/dim]")

    # Determine scope with validation
    config_manager = create_config_manager()
    try:
        scope, was_fallback = get_effective_scope(
            cast(Scope, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="global",  # Default to global for CLI
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope (~/.amplifier/settings.yaml)"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {escape_markup(e.message)}")
        return

    # Add the path
    settings = AppSettings()
    settings.add_allowed_write_path(str(resolved), cast(Scope, scope))

    console.print(f"[green]✓ Added {resolved}[/green]")
    console.print(f"  Scope: {scope}")


@allowed_dirs.command("remove")
@click.argument("path")
@click.option(
    "--local", "scope_flag", flag_value="local", help="Remove from local scope"
)
@click.option(
    "--project", "scope_flag", flag_value="project", help="Remove from project scope"
)
@click.option(
    "--global",
    "scope_flag",
    flag_value="global",
    help="Remove from global scope (default)",
)
def remove_dir(path: str, scope_flag: str | None):
    """Remove a directory from allowed write paths.

    PATH should match exactly as it appears in the config.

    Examples:
        amplifier allowed-dirs remove /tmp/test
        amplifier allowed-dirs remove /home/user/scratch --global
    """
    # Determine scope with validation
    config_manager = create_config_manager()
    try:
        scope, was_fallback = get_effective_scope(
            cast(Scope, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="global",  # Default to global for CLI
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope (~/.amplifier/settings.yaml)"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {escape_markup(e.message)}")
        return

    settings = AppSettings()
    removed = settings.remove_allowed_write_path(path, cast(Scope, scope))

    if removed:
        console.print(f"[green]✓ Removed {path}[/green]")
        console.print(f"  Scope: {scope}")
    else:
        console.print(f"[yellow]Path not found at {scope} scope:[/yellow] {path}")
        console.print(
            "\n[dim]Use 'amplifier allowed-dirs list' to see configured paths[/dim]"
        )


__all__ = ["allowed_dirs"]
