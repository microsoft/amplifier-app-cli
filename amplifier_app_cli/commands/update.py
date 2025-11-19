"""Update command for Amplifier CLI."""

import asyncio

import click
from rich.console import Console

from ..utils.settings_manager import save_update_last_check
from ..utils.source_status import check_all_sources
from ..utils.update_executor import execute_updates

console = Console()


def _show_concise_report(report, check_only: bool, has_umbrella_updates: bool) -> None:
    """Show concise one-line format for all sources.

    Organized by type: Amplifier → Libraries → Modules → Collections
    Simple format: name SHA → SHA (age if relevant)
    """
    console.print()

    # === AMPLIFIER (Always show) ===
    if has_umbrella_updates:
        console.print("Amplifier:            [update available]")
    else:
        console.print("Amplifier:            [up to date]")

    # === LIBRARIES (Local file sources - amplifier-core, amplifier-app-cli, etc.) ===
    libraries = [s for s in report.local_file_sources if "amplifier-" in s.name or "amplifier_" in s.name]
    if libraries:
        console.print()
        for status in libraries:
            sha = status.local_sha or "unknown"
            if status.uncommitted_changes or status.unpushed_commits:
                console.print(f"{status.name:20s}  local  [uncommitted]")
            elif status.has_remote and status.remote_sha and status.remote_sha != status.local_sha:
                console.print(f"{status.name:20s}  {sha}  →  {status.remote_sha}")
            else:
                console.print(f"{status.name:20s}  {sha}  →  {sha}")

    # === MODULES (Cached git sources - providers, tools, hooks, etc.) ===
    if report.cached_git_sources:
        console.print()
        for status in report.cached_git_sources:
            age_str = f" ({status.age_days}d old)" if status.age_days > 0 else ""
            console.print(f"{status.name:20s}  {status.cached_sha}  →  {status.remote_sha}{age_str}")

    # === COLLECTIONS ===
    if report.collection_sources:
        console.print()
        for status in report.collection_sources:
            installed = status.installed_sha or "<none>"
            console.print(f"{status.name:20s}  {installed}  →  {status.remote_sha}")

    console.print()
    if not check_only and (report.has_updates or has_umbrella_updates):
        console.print("Run [cyan]amplifier update[/cyan] to install")


def _show_verbose_report(report, check_only: bool) -> None:
    """Show detailed multi-line format for each source."""
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

    # Show collections with updates
    if report.collection_sources:
        console.print()
        console.print("[yellow]Collections (updates available):[/yellow]")
        for status in report.collection_sources:
            console.print(f"  • {status.name}")
            console.print(f"    {status.installed_sha} → {status.remote_sha}")
            console.print(f"    Installed: {status.installed_at}")

        if not check_only:
            console.print()
            console.print("Run [cyan]amplifier collection refresh[/cyan] to update")


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmations")
@click.option("--force", is_flag=True, help="Force update even if already latest")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed multi-line output per source")
def update(check_only: bool, yes: bool, force: bool, verbose: bool):
    """Update Amplifier to latest version.

    Checks all sources (local files and cached git) and executes updates.
    """
    # Check for updates
    if force:
        console.print("Force update mode - skipping update detection...")
    else:
        console.print("Checking for updates...")

    report = asyncio.run(check_all_sources(include_all_cached=True, force=force))

    # Check umbrella dependencies for updates
    from ..utils.umbrella_discovery import discover_umbrella_source
    from ..utils.update_executor import check_umbrella_dependencies_for_updates

    umbrella_info = discover_umbrella_source()
    has_umbrella_updates = False

    if umbrella_info:
        if force:
            has_umbrella_updates = True  # Force update umbrella
        else:
            console.print("Checking umbrella dependencies...")
            has_umbrella_updates = asyncio.run(check_umbrella_dependencies_for_updates(umbrella_info))

    # Display results based on verbosity
    if verbose:
        _show_verbose_report(report, check_only)
    else:
        _show_concise_report(report, check_only, has_umbrella_updates)

    # Check-only mode
    if check_only:
        if not report.has_updates and not report.has_local_changes and not has_umbrella_updates:
            console.print("[green]✓ All sources up to date[/green]")
        elif has_umbrella_updates:
            console.print("\n[yellow]Updates available:[/yellow]")
            console.print("  • Amplifier (umbrella dependencies have updates)")
            console.print("\nRun [cyan]amplifier update[/cyan] to install")
        return

    # No updates available
    if not report.has_updates and not has_umbrella_updates and not force:
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
        if report.collection_sources:
            count = len(report.collection_sources)
            console.print(f"  • Refresh {count} collection{'s' if count != 1 else ''}")
        if has_umbrella_updates:
            console.print("  • Update Amplifier to latest version (dependencies have updates)")

        console.print()
        response = input("Proceed with update? [Y/n]: ").strip().lower()
        if response not in ("", "y", "yes"):
            console.print("[dim]Update cancelled[/dim]")
            return

    # Execute updates with progress
    console.print()
    console.print("Updating...")

    result = asyncio.run(execute_updates(report, umbrella_info=umbrella_info if has_umbrella_updates else None))

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
