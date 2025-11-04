"""Update command for Amplifier CLI."""

import asyncio

import click
from rich.console import Console

from ..utils.settings_manager import save_update_last_check
from ..utils.source_status import check_all_sources
from ..utils.update_executor import execute_updates

console = Console()


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmations")
@click.option("--force", is_flag=True, help="Force update even if already latest")
def update(check_only: bool, yes: bool, force: bool):
    """Update Amplifier to latest version.

    Checks all sources (local files and cached git) and executes updates.
    """
    # Check for updates
    console.print("Checking for updates...")

    report = asyncio.run(check_all_sources(include_all_cached=True))

    # Show local file sources
    if report.local_file_sources:
        console.print()
        console.print("[cyan]Local Sources:[/cyan]")
        for status in report.local_file_sources:
            console.print(f"  • {status.name} ({status.layer})")
            console.print(f"    Path: {status.path}")

            if status.uncommitted_changes:
                console.print("    ⚠ Uncommitted changes")
            if status.unpushed_commits:
                console.print("    ⚠ Unpushed commits")

            if status.has_remote and status.remote_sha and status.remote_sha != status.local_sha:
                console.print(f"    ℹ Remote ahead: {status.local_sha} → {status.remote_sha}")
                if status.commits_behind > 0:
                    console.print(f"      {status.commits_behind} commits behind")
                console.print(f"      To update: git pull in {status.path}")

        console.print()
        console.print("[dim]For local sources: Use git to manage updates manually[/dim]")

    # Show cached git sources with updates
    if report.cached_git_sources:
        console.print()
        console.print("[yellow]Cached Git Sources (updates available):[/yellow]")
        for status in report.cached_git_sources:
            console.print(f"  • {status.name}@{status.ref} ({status.layer})")
            console.print(f"    {status.cached_sha} → {status.remote_sha} ({status.age_days}d old)")

        if not check_only:
            console.print()
            console.print("Run [cyan]amplifier module refresh[/cyan] to update cached modules")

    # Check-only mode
    if check_only:
        if not report.has_updates and not report.has_local_changes:
            console.print("[green]✓ All sources up to date[/green]")
        return

    # No updates available
    if not report.has_updates and not force:
        console.print("[green]✓ All sources up to date[/green]")
        return

    # Execute updates
    console.print()

    # Confirm unless --yes flag
    if not yes:
        # Show what will be updated
        if report.cached_git_sources:
            count = len(report.cached_git_sources)
            console.print(f"  • Refresh {count} cached module{'s' if count != 1 else ''}")
        if report.has_updates:
            console.print("  • Update Amplifier to latest version")

        console.print()
        response = input("Proceed with update? [Y/n]: ").strip().lower()
        if response not in ("", "y", "yes"):
            console.print("[dim]Update cancelled[/dim]")
            return

    # Execute updates with progress
    console.print()
    console.print("Updating...")

    result = asyncio.run(execute_updates(report))

    # Show results
    console.print()
    if result.success:
        console.print("[green]✓ Update complete[/green]")
        for item in result.updated:
            console.print(f"  [green]✓[/green] {item}")
        for msg in result.messages:
            console.print(f"  {msg}")
    else:
        console.print("[yellow]⚠ Update completed with errors[/yellow]")
        for item in result.updated:
            console.print(f"  [green]✓[/green] {item}")
        for item in result.failed:
            error = result.errors.get(item, "Unknown error")
            console.print(f"  [red]✗[/red] {item}: {error}")

    # Update last check timestamp
    from datetime import datetime

    save_update_last_check(datetime.now())
