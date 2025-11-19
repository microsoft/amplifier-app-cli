"""Update command for Amplifier CLI."""

import asyncio

import click
from rich.console import Console

from ..utils.settings_manager import save_update_last_check
from ..utils.source_status import check_all_sources
from ..utils.update_executor import execute_updates

console = Console()


async def _get_umbrella_dependency_details(umbrella_info) -> list[dict]:
    """Get details of Amplifier dependencies (libs with their SHAs).

    Returns:
        List of dicts with {name, current_sha, remote_sha, source_url}
    """
    import importlib.metadata
    import json

    from ..utils.umbrella_discovery import fetch_umbrella_dependencies

    if not umbrella_info:
        return []

    try:
        # Get dependency definitions from umbrella
        umbrella_deps = await fetch_umbrella_dependencies(umbrella_info)

        details = []
        for lib_name, dep_info in umbrella_deps.items():
            # Get current installed SHA
            current_sha = None
            try:
                dist = importlib.metadata.distribution(lib_name)
                if hasattr(dist, "read_text"):
                    direct_url_text = dist.read_text("direct_url.json")
                    if direct_url_text:
                        direct_url = json.loads(direct_url_text)
                        if "vcs_info" in direct_url:
                            current_sha = direct_url["vcs_info"].get("commit_id", "")[:7]
            except Exception:
                current_sha = "unknown"

            # Get remote SHA
            from ..utils.source_status import _get_github_commit_sha

            try:
                remote_sha_full = await _get_github_commit_sha(dep_info["url"], dep_info["branch"])
                remote_sha = remote_sha_full[:7]
            except Exception:
                remote_sha = "unknown"

            details.append(
                {
                    "name": lib_name,
                    "current_sha": current_sha,
                    "remote_sha": remote_sha,
                    "source_url": dep_info["url"],
                    "has_update": current_sha != remote_sha,
                }
            )

        return details
    except Exception:
        return []


def _show_concise_report(report, check_only: bool, has_umbrella_updates: bool, umbrella_deps=None) -> None:
    """Show concise one-line format for all sources.

    Organized by type: Amplifier → Libraries → Modules → Collections
    Simple format: name SHA → SHA (age if relevant)
    """
    console.print()

    # === AMPLIFIER (Always show with status and dependency details) ===
    if has_umbrella_updates:
        console.print("Amplifier:            (update available)")
    else:
        console.print("Amplifier:            (up to date)")

    # Show Amplifier dependencies if available
    if umbrella_deps:
        console.print()
        console.print("[cyan]Amplifier Dependencies:[/cyan]")
        for dep in umbrella_deps:
            sha_display = f"{dep['current_sha']}  →  {dep['remote_sha']}"
            console.print(f"  {dep['name']:20s}  {sha_display}")

    # === LIBRARIES (Local file sources - amplifier-core, amplifier-app-cli, etc.) ===
    libraries = [s for s in report.local_file_sources if "amplifier-" in s.name or "amplifier_" in s.name]
    if libraries:
        console.print()
        console.print("[cyan]Libraries:[/cyan]")
        for status in libraries:
            sha = status.local_sha or "unknown"
            if status.uncommitted_changes or status.unpushed_commits:
                console.print(f"  {status.name:20s}  local  (uncommitted)")
            elif status.has_remote and status.remote_sha and status.remote_sha != status.local_sha:
                console.print(f"  {status.name:20s}  {sha}  →  {status.remote_sha}")
            else:
                console.print(f"  {status.name:20s}  {sha}  →  {sha}")

    # === MODULES (Cached git sources - providers, tools, hooks, etc.) ===
    if report.cached_git_sources:
        console.print()
        console.print("[cyan]Modules:[/cyan]")
        for status in report.cached_git_sources:
            age_str = f" ({status.age_days}d old)" if status.age_days > 0 else ""
            console.print(f"  {status.name:20s}  {status.cached_sha}  →  {status.remote_sha}{age_str}")

    # === COLLECTIONS ===
    if report.collection_sources:
        console.print()
        console.print("[cyan]Collections:[/cyan]")
        for status in report.collection_sources:
            installed = status.installed_sha or "<none>"
            console.print(f"  {status.name:20s}  {installed}  →  {status.remote_sha}")

    console.print()
    if not check_only and (report.has_updates or has_umbrella_updates):
        console.print("Run [cyan]amplifier update[/cyan] to install")


def _show_verbose_report(report, check_only: bool, umbrella_deps=None) -> None:
    """Show detailed multi-line format for each source (unified format)."""

    # Show Amplifier dependencies if available
    if umbrella_deps:
        console.print()
        console.print("[cyan]Amplifier Dependencies:[/cyan]")
        for dep in umbrella_deps:
            console.print(f"  • {dep['name']}")
            console.print(f"    {dep['current_sha']} → {dep['remote_sha']}")
            console.print(f"    Source: {dep['source_url']}")

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

    # Show cached modules (unified format with source URL)
    if report.cached_git_sources:
        console.print()
        console.print("[cyan]Modules:[/cyan]")
        for status in report.cached_git_sources:
            console.print(f"  • {status.name}@{status.ref}")
            console.print(f"    {status.cached_sha} → {status.remote_sha} ({status.age_days}d old)")
            console.print(f"    Source: {status.url}")

    # Show collections (unified format with source URL and days instead of timestamp)
    if report.collection_sources:
        console.print()
        console.print("[cyan]Collections:[/cyan]")
        for status in report.collection_sources:
            console.print(f"  • {status.name}")
            console.print(f"    {status.installed_sha or '<none>'} → {status.remote_sha}")
            console.print(f"    Source: {status.source}")


@click.command()
@click.option("--check-only", is_flag=True, help="Check for updates without installing")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmations")
@click.option("--force", is_flag=True, help="Force update even if already latest")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed multi-line output per source")
def update(check_only: bool, yes: bool, force: bool, verbose: bool):
    """Update Amplifier to latest version.

    Checks all sources (local files and cached git) and executes updates.
    """
    # Check for updates with status messages
    if force:
        console.print("Force update mode - skipping update detection...")
    else:
        console.print("Checking for updates...")

    # Check umbrella first
    from ..utils.umbrella_discovery import discover_umbrella_source
    from ..utils.update_executor import check_umbrella_dependencies_for_updates

    umbrella_info = discover_umbrella_source()
    has_umbrella_updates = False

    if umbrella_info:
        if force:
            has_umbrella_updates = True  # Force update umbrella
        else:
            console.print("  Checking Amplifier dependencies...")
            has_umbrella_updates = asyncio.run(check_umbrella_dependencies_for_updates(umbrella_info))

    # Check modules and collections
    if not force:
        console.print("  Checking modules...")
        console.print("  Checking collections...")

    report = asyncio.run(check_all_sources(include_all_cached=True, force=force))

    # Get Amplifier dependency details
    umbrella_deps = asyncio.run(_get_umbrella_dependency_details(umbrella_info)) if umbrella_info else []

    # Display results based on verbosity
    if verbose:
        _show_verbose_report(report, check_only, umbrella_deps=umbrella_deps)
    else:
        _show_concise_report(report, check_only, has_umbrella_updates, umbrella_deps=umbrella_deps)

    # Check if anything actually needs updating
    nothing_to_update = not report.has_updates and not has_umbrella_updates and not force

    # Exit early if nothing to update
    if nothing_to_update:
        console.print("[green]✓ All sources up to date[/green]")
        return

    # Check-only mode (we know there ARE updates if we got here)
    if check_only:
        console.print("\n[yellow]Updates available:[/yellow]")
        if has_umbrella_updates:
            console.print("  • Amplifier (umbrella dependencies have updates)")
        if report.has_updates:
            console.print("  • Modules and/or collections")
        console.print("\nRun [cyan]amplifier update[/cyan] to install")
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
