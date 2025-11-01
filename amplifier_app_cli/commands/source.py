"""Module source override commands."""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.table import Table

from ..console import console
from ..paths import create_config_manager
from ..paths import create_module_resolver


@click.group(invoke_without_command=True)
@click.pass_context
def source(ctx: click.Context):
    """Manage module source overrides."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@source.command("add")
@click.argument("module_id")
@click.argument("source_uri")
@click.option("--global", "is_global", is_flag=True, help="Store override in user (~/.amplifier) settings")
def source_add(module_id: str, source_uri: str, is_global: bool):
    """Add a module source override."""
    from amplifier_config import Scope

    config_manager = create_config_manager()
    scope = Scope.USER if is_global else Scope.PROJECT
    config_manager.add_source_override(module_id, source_uri, scope=scope)

    scope_label = "global (~/.amplifier)" if is_global else "project (.amplifier)"
    console.print(f"[green]✓ Added source override for {module_id}[/green]")
    console.print(f"  Scope: {scope_label}")


@source.command("remove")
@click.argument("module_id")
@click.option("--global", "is_global", is_flag=True, help="Remove from user (~/.amplifier) settings")
def source_remove(module_id: str, is_global: bool):
    """Remove a module source override."""
    from amplifier_config import Scope

    config_manager = create_config_manager()
    scope = Scope.USER if is_global else Scope.PROJECT
    removed = config_manager.remove_source_override(module_id, scope=scope)

    if removed:
        scope_label = "global (~/.amplifier)" if is_global else "project (.amplifier)"
        console.print(f"[green]✓ Removed source override for {module_id} ({scope_label})[/green]")
    else:
        console.print(f"[yellow]Source override for {module_id} not found[/yellow]")


@source.command("list")
def source_list():
    """List all module source overrides."""
    config_manager = create_config_manager()
    sources = config_manager.get_module_sources()

    if not sources:
        console.print("[yellow]No source overrides configured[/yellow]")
        console.print("\nAdd overrides with:")
        console.print("  [cyan]amplifier source add <module> <uri>[/cyan]")
        return

    table = Table(title="Module Source Overrides", show_header=True, header_style="bold cyan")
    table.add_column("Module", style="green")
    table.add_column("Source", style="magenta")

    for module_id, source_uri in sorted(sources.items()):
        display_uri = source_uri if len(source_uri) <= 60 else source_uri[:57] + "..."
        table.add_row(module_id, display_uri)

    console.print(table)


@source.command("show")
@click.argument("module_id")
def source_show(module_id: str):
    """Show resolution path for a module."""
    resolver = create_module_resolver()

    console.print(f"[bold]Module:[/bold] {module_id}\n")
    console.print("[bold]Resolution Path:[/bold]")

    env_key = f"AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')}"
    env_val = os.getenv(env_key)
    env_display = f"[green]✓ {env_val}[/green]" if env_val else "[dim]not set[/dim]"
    console.print(f"  1. Environment ({env_key}): {env_display}")

    workspace = Path(".amplifier/modules") / module_id
    workspace_display = "[green]✓ found[/green]" if workspace.exists() else "[dim]not found[/dim]"
    console.print(f"  2. Workspace (.amplifier/modules/): {workspace_display}")

    config_manager = create_config_manager()
    merged_sources = config_manager.get_module_sources()
    project_source = merged_sources.get(module_id)
    project_display = f"[green]✓ {project_source}[/green]" if project_source else "[dim]not found[/dim]"
    console.print(f"  3. Project (.amplifier/settings.yaml): {project_display}")

    console.print("  4. User (~/.amplifier/settings.yaml): [dim](merged with project)[/dim]")
    console.print("  5. Profile: [dim](depends on active profile)[/dim]")
    console.print("  6. Package: [dim](installed packages)[/dim]")

    try:
        source_obj, layer = resolver.resolve_with_layer(module_id)
        console.print(f"\n[bold green]✓ Resolved via:[/bold green] {layer}")
        console.print(f"[bold green]Source:[/bold green] {source_obj}")
    except Exception as exc:
        console.print(f"\n[bold red]✗ Failed:[/bold red] {exc}")


__all__ = ["source"]
