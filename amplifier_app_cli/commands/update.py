"""Update command for Amplifier CLI."""

import asyncio
import subprocess

import click
from rich.console import Console

from ..utils.umbrella_discovery import discover_umbrella_source
from ..utils.update_check import check_amplifier_updates

console = Console()


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--force", is_flag=True, help="Force update even if already latest")
def update(check_only: bool, force: bool):
    """Update Amplifier to latest version.

    Updates all Amplifier libraries to the latest commit on the tracking branch.
    """
    # Check for updates
    console.print("Checking for Amplifier updates...")

    result = asyncio.run(check_amplifier_updates())

    # Handle local dev mode
    if result.mode == "local_dev":
        console.print()
        console.print("[yellow]Local development mode detected[/yellow]")
        console.print()
        console.print("You're running editable installs from amplifier-dev/")
        console.print("To update:")
        console.print("  1. cd amplifier-dev")
        console.print("  2. git pull  (in each library repo)")
        console.print("  3. Restart amplifier")
        console.print()

        # Show current git status (items are LocalDevStatus objects)
        if result.has_updates:
            console.print("[yellow]Git status issues:[/yellow]")
            for status_item in result.updates_available:
                # In local dev mode, these are LocalDevStatus objects
                console.print(f"  • {status_item.library}")  # type: ignore

                if status_item.uncommitted_changes:  # type: ignore
                    console.print("    ⚠ Uncommitted changes")
                if status_item.behind_remote:  # type: ignore
                    console.print(f"    ⚠ Behind remote by {status_item.remote_commits} commits")  # type: ignore
                if status_item.unpushed_commits:  # type: ignore
                    console.print("    ⚠ Unpushed commits")
        return

    # Handle errors
    if result.error:
        console.print(f"[red]✗ Could not check for updates: {result.error}[/red]")
        console.print()
        console.print("Try reinstalling:")
        console.print("  uv tool install git+https://github.com/microsoft/amplifier@next")
        return

    # Check-only mode
    if check_only:
        if result.has_updates:
            console.print()
            console.print("[green]Updates available:[/green]")
            for update in result.updates_available:
                console.print(f"  • {update.library}: {update.installed_sha} → {update.remote_sha}")
                if update.commit_message:
                    console.print(f"    {update.commit_message}")
            console.print()
            console.print("Run [cyan]amplifier update[/cyan] to install")
        else:
            console.print("✓ All libraries up to date")
        return

    # No updates available
    if not result.has_updates and not force:
        console.print("✓ All libraries already up to date")
        return

    # Perform update
    if result.has_updates:
        console.print()
        console.print("[yellow]Updating Amplifier...[/yellow]")
        for update in result.updates_available:
            console.print(f"  • {update.library}: {update.installed_sha} → {update.remote_sha}")
        console.print()
    else:
        console.print()
        console.print("[yellow]Force updating Amplifier...[/yellow]")
        console.print()

    # Discover umbrella source
    umbrella_info = discover_umbrella_source()

    if not umbrella_info:
        console.print("[red]✗ Could not determine installation source[/red]")
        console.print()
        console.print("Try reinstalling:")
        console.print("  uv tool install git+https://github.com/microsoft/amplifier@next")
        return

    # Update using discovered umbrella URL (no hardcoding!)
    update_url = f"git+{umbrella_info.url}@{umbrella_info.ref}"

    console.print(f"Installing from: {update_url}")

    try:
        result = subprocess.run(
            ["uv", "tool", "install", "--force", update_url], capture_output=True, text=True, timeout=120
        )

        if result.returncode == 0:
            console.print("[green]✓ Successfully updated Amplifier[/green]")
            console.print()
            console.print("Restart amplifier to use new version")
        else:
            console.print("[red]✗ Update failed[/red]")
            console.print()
            console.print("Error output:")
            console.print(result.stderr)
            console.print()
            console.print("Try manually:")
            console.print(f"  uv tool install --force {update_url}")
            return

    except subprocess.TimeoutExpired:
        console.print("[red]✗ Update timed out[/red]")
        console.print()
        console.print("This might be due to slow network. Try again later or:")
        console.print(f"  uv tool install --force {update_url}")
        return
    except Exception as e:
        console.print(f"[red]✗ Update failed: {e}[/red]")
        return
