"""Shared display utilities for CLI output.

Single source of truth for status symbols, SHA formatting, and legends.
Used by: commands/update.py, commands/module.py
"""

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()


def create_status_symbol(local_sha: str | None, remote_sha: str | None, has_local_changes: bool = False) -> Text:
    """Create styled status symbol based on update state.

    Returns:
        ● (yellow) - update available
        ◦ (cyan) - local changes
        ✓ (green) - up to date
    """
    if has_local_changes:
        return Text("◦", style="cyan")
    if local_sha and remote_sha and local_sha != remote_sha:
        return Text("●", style="yellow")
    return Text("✓", style="green")


def create_sha_text(sha: str | None, style: str = "dim") -> Text:
    """Create styled SHA text (always dim to prevent hex color interpretation)."""
    return Text(sha[:7] if sha else "unknown", style=style)


def print_legend() -> None:
    """Print status symbol legend at the bottom of reports."""
    console.print(
        "[dim]Legend: [green]✓[/green] up to date  [yellow]●[/yellow] update available  [cyan]◦[/cyan] local changes[/dim]"
    )


def create_modules_table(cached_git_sources: list, title: str = "Modules") -> Table | None:
    """Create a table for cached git modules.

    Args:
        cached_git_sources: List of CachedGitStatus objects from source_status
        title: Table title

    Returns:
        Table if there are modules, None otherwise
    """
    if not cached_git_sources:
        return None

    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Cached", style="dim", justify="right")
    table.add_column("Remote", style="dim", justify="right")
    table.add_column("", width=1, justify="center")

    for status in sorted(cached_git_sources, key=lambda x: x.name):
        status_symbol = create_status_symbol(status.cached_sha, status.remote_sha)

        table.add_row(
            status.name,
            create_sha_text(status.cached_sha),
            create_sha_text(status.remote_sha),
            status_symbol,
        )

    return table


def create_local_modules_table(local_file_sources: list, title: str = "Modules (Local Overrides)") -> Table | None:
    """Create a table for modules with local source overrides.

    Args:
        local_file_sources: List of LocalFileStatus objects from source_status
        title: Table title

    Returns:
        Table if there are modules, None otherwise
    """
    if not local_file_sources:
        return None

    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("SHA", style="dim", justify="right")
    table.add_column("Path", style="dim")
    table.add_column("", width=1, justify="center")

    for status in sorted(local_file_sources, key=lambda x: x.name):
        has_local_changes = status.uncommitted_changes or status.unpushed_commits
        status_symbol = create_status_symbol(status.local_sha, status.local_sha, has_local_changes)

        # Truncate path for display
        path_str = str(status.path) if status.path else "-"
        if len(path_str) > 40:
            path_str = "..." + path_str[-37:]

        table.add_row(
            status.name,
            create_sha_text(status.local_sha),
            Text(path_str, style="dim"),
            status_symbol,
        )

    return table


def show_modules_report(
    cached_git_sources: list, local_file_sources: list | None = None, check_only: bool = True
) -> None:
    """Display module status in table format with legend.

    Shows both local overrides and cached modules for a complete listing.

    Args:
        cached_git_sources: List of CachedGitStatus from source_status
        local_file_sources: List of LocalFileStatus from source_status (optional)
        check_only: If True, show "run amplifier module update" hint
    """
    has_any_modules = False

    # Show local overrides first (if any)
    if local_file_sources:
        local_table = create_local_modules_table(local_file_sources)
        if local_table:
            console.print()
            console.print(local_table)
            has_any_modules = True

    # Show cached git sources (if any)
    cached_table = create_modules_table(cached_git_sources, title="Modules (Cached)")
    if cached_table:
        console.print()
        console.print(cached_table)
        has_any_modules = True

    if has_any_modules:
        console.print()
        print_legend()

        # Count updates in cached sources
        updates_available = sum(1 for s in cached_git_sources if s.has_update)
        if check_only and updates_available > 0:
            console.print()
            console.print(f"[yellow]{updates_available} update(s) available[/yellow]")
            console.print("Run [cyan]amplifier module update[/cyan] to install")
    else:
        console.print("[dim]No modules to check[/dim]")
        console.print("[dim]Modules will be cached when first used[/dim]")


def create_collections_table(collection_sources: list, title: str = "Collections") -> Table | None:
    """Create a table for installed collections.

    Args:
        collection_sources: List of CollectionStatus objects from source_status
        title: Table title

    Returns:
        Table if there are collections, None otherwise
    """
    if not collection_sources:
        return None

    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Installed", style="dim", justify="right")
    table.add_column("Remote", style="dim", justify="right")
    table.add_column("", width=1, justify="center")

    for status in sorted(collection_sources, key=lambda x: x.name):
        status_symbol = create_status_symbol(status.installed_sha, status.remote_sha)

        table.add_row(
            status.name,
            create_sha_text(status.installed_sha),
            create_sha_text(status.remote_sha),
            status_symbol,
        )

    return table


def show_collections_report(collection_sources: list, check_only: bool = True) -> None:
    """Display collection status in table format with legend.

    Args:
        collection_sources: List of CollectionStatus from source_status
        check_only: If True, show "run amplifier collection update" hint
    """
    table = create_collections_table(collection_sources)
    if table:
        console.print()
        console.print(table)
        console.print()
        print_legend()

        # Count updates
        updates_available = sum(1 for s in collection_sources if s.has_update)
        if check_only and updates_available > 0:
            console.print()
            console.print(f"[yellow]{updates_available} update(s) available[/yellow]")
            console.print("Run [cyan]amplifier collection update[/cyan] to install")
    else:
        console.print("[dim]No collections installed[/dim]")
        console.print("[dim]Install collections with 'amplifier collection add <source>'[/dim]")
