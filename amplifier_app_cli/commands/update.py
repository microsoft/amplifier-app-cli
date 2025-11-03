"""Update command for Amplifier CLI."""

import asyncio

import click
from rich.console import Console

from ..utils.umbrella_discovery import discover_umbrella_source
from ..utils.update_check import check_updates

console = Console()


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--force", is_flag=True, help="Force update even if already latest")
def update(check_only: bool, force: bool):
    """Update Amplifier to latest version.

    Checks all sources (local files and cached git) and provides appropriate guidance.
    """
    # Check for updates
    console.print("Checking for updates...")

    report = asyncio.run(check_updates())

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

    # Can't auto-update file sources (only cached git)
    if report.cached_git_sources:
        console.print()
        console.print("[dim]To update cached git sources, run:[/dim]")
        console.print("  [cyan]amplifier module refresh[/cyan]")

    # Note about umbrella updates (if applicable)
    umbrella_info = discover_umbrella_source()
    if umbrella_info and not report.local_file_sources:
        # All from umbrella, can suggest full update
        console.print()
        console.print("[dim]To update Amplifier installation:[/dim]")
        update_url = f"git+{umbrella_info.url}@{umbrella_info.ref}"
        console.print(f"  [cyan]uv tool install --force {update_url}[/cyan]")
