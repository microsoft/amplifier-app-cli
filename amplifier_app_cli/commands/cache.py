"""Cache management commands for the Amplifier CLI.

Provides commands to inspect, manage, and clean the unified cache directory
at ~/.amplifier/cache/. All modules (whether loaded via bundles or legacy
profiles) are cached here.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

import click
from rich.table import Table

from ..console import console
from ..utils.module_cache import get_cache_dir
from ..utils.module_cache import scan_cached_modules


def _get_dir_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    total = 0
    with contextlib.suppress(OSError):
        for entry in path.rglob("*"):
            if entry.is_file():
                with contextlib.suppress(OSError):
                    total += entry.stat().st_size
    return total


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    size_float = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size_float < 1024:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024
    return f"{size_float:.1f} TB"


@click.group(invoke_without_command=True)
@click.pass_context
def cache(ctx: click.Context):
    """Manage the Amplifier module cache.

    The cache stores downloaded modules at ~/.amplifier/cache/.
    Use these commands to inspect and manage cached content.
    """
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@cache.command(name="path")
def cache_path():
    """Show the cache directory path."""
    cache_dir = get_cache_dir()
    console.print(f"[cyan]{cache_dir}[/cyan]")

    if cache_dir.exists():
        console.print("[dim]Status: exists[/dim]")
    else:
        console.print("[dim]Status: not created yet[/dim]")


@cache.command(name="size")
def cache_size():
    """Show total cache disk usage."""
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        console.print("[dim]Cache directory does not exist yet.[/dim]")
        console.print(f"[dim]Path: {cache_dir}[/dim]")
        return

    total_size = _get_dir_size(cache_dir)
    console.print(f"[bold]Cache Size:[/bold] {_format_size(total_size)}")
    console.print(f"[dim]Path: {cache_dir}[/dim]")


@cache.command(name="list")
@click.option(
    "--type",
    "module_type",
    type=click.Choice(["all", "tool", "hook", "provider", "orchestrator", "context", "agent"]),
    default="all",
    help="Filter by module type",
)
def cache_list(module_type: str):
    """List all cached modules with sizes."""
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        console.print("[dim]Cache directory does not exist yet.[/dim]")
        console.print(f"[dim]Path: {cache_dir}[/dim]")
        return

    modules = scan_cached_modules(type_filter=module_type)

    if not modules:
        if module_type == "all":
            console.print("[dim]No cached modules found.[/dim]")
        else:
            console.print(f"[dim]No cached {module_type} modules found.[/dim]")
        return

    # Create table
    table = Table(title="Cached Modules")
    table.add_column("Module", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Ref", style="green")
    table.add_column("SHA", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Mutable", style="dim")

    total_size = 0
    for module in modules:
        # Calculate size of this module's cache
        module_size = _get_dir_size(module.cache_path)
        total_size += module_size

        table.add_row(
            module.module_id,
            module.module_type,
            module.ref,
            module.sha,
            _format_size(module_size),
            "yes" if module.is_mutable else "no",
        )

    console.print(table)
    console.print(f"\n[bold]Total:[/bold] {len(modules)} modules, {_format_size(total_size)}")


@cache.command(name="clean")
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--mutable-only",
    is_flag=True,
    help="Only clean mutable refs (branches), keep immutable (tags, SHAs)",
)
def cache_clean(force: bool, mutable_only: bool):
    """Clean the module cache.

    By default, cleans all cached modules. Use --mutable-only to only
    clean branch-based refs while keeping tagged versions and SHA-pinned
    modules intact.
    """
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        console.print("[dim]Cache directory does not exist - nothing to clean.[/dim]")
        return

    # Get info about what will be cleaned
    modules = scan_cached_modules()
    if not modules:
        console.print("[dim]No cached modules found - nothing to clean.[/dim]")
        return

    # Count what will be affected
    if mutable_only:
        to_clean = [m for m in modules if m.is_mutable]
        to_keep = [m for m in modules if not m.is_mutable]
        clean_desc = "mutable modules (branches)"
    else:
        to_clean = modules
        to_keep = []
        clean_desc = "all cached modules"

    if not to_clean:
        console.print("[dim]No modules match the criteria - nothing to clean.[/dim]")
        if mutable_only and to_keep:
            console.print(f"[dim]{len(to_keep)} immutable modules will be kept.[/dim]")
        return

    # Calculate sizes
    total_size = sum(_get_dir_size(m.cache_path) for m in to_clean)

    # Show what will be cleaned
    console.print(f"\n[bold]Will clean {clean_desc}:[/bold]")
    console.print(f"  Modules: {len(to_clean)}")
    console.print(f"  Size: {_format_size(total_size)}")

    if to_keep:
        console.print(f"\n[dim]Will keep {len(to_keep)} immutable modules.[/dim]")

    # Confirm unless --force
    if not force and not click.confirm("\nProceed with cleaning cache?"):
        console.print("[yellow]Aborted.[/yellow]")
        return

    # Clean the cache
    cleaned = 0
    errors = 0

    if mutable_only:
        # Clean individual module directories
        for module in to_clean:
            try:
                shutil.rmtree(module.cache_path)
                cleaned += 1
            except Exception as e:
                console.print(f"[red]Error cleaning {module.module_id}:[/red] {e}")
                errors += 1
    else:
        # Clean entire cache directory
        try:
            shutil.rmtree(cache_dir)
            cleaned = len(to_clean)
        except Exception as e:
            console.print(f"[red]Error cleaning cache:[/red] {e}")
            errors = len(to_clean)

    # Report results
    if errors == 0:
        console.print(f"\n[green]Cleaned {cleaned} modules ({_format_size(total_size)})[/green]")
    else:
        console.print(f"\n[yellow]Cleaned {cleaned} modules, {errors} errors[/yellow]")
