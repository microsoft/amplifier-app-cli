"""Registry command - list modules from amplifier-modules registry."""

from __future__ import annotations

import asyncio
import json

import click
from rich.table import Table

from ...console import console
from ..client import RegistryClient


def _get_installed_modules() -> set[str]:
    """Get the set of currently available module names.

    Includes modules from:
    - Installed entry points (via ModuleLoader)
    - Active profile modules
    - Cached git modules
    """
    module_names = set()

    # 1. Entry point modules
    try:
        from amplifier_core.loader import ModuleLoader

        loader = ModuleLoader()
        modules_info = asyncio.run(loader.discover())
        module_names.update(module.id for module in modules_info)
    except Exception:
        pass

    # 2. Profile modules
    try:
        from ...commands.module import _get_profile_modules
        from ...data.profiles import get_system_default_profile
        from ...paths import create_config_manager

        config_manager = create_config_manager()
        active_profile = config_manager.get_active_profile() or get_system_default_profile()
        profile_modules = _get_profile_modules(active_profile)
        module_names.update(mod["id"] for mod in profile_modules)
    except Exception:
        pass

    # 3. Cached modules
    try:
        from ...commands.module import _get_cached_modules

        cached_modules = _get_cached_modules()
        module_names.update(mod["id"] for mod in cached_modules)
    except Exception:
        pass

    return module_names


@click.command("registry")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["agent", "behavior", "provider", "bundle", "context"]),
    help="Filter by module type",
)
@click.option(
    "--verified",
    is_flag=True,
    help="Show only verified modules",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
def registry_command(type: str | None, verified: bool, output_json: bool):
    """List available modules from the amplifier-modules registry.

    This command shows modules available in the central registry.
    To see installed modules, use 'amplifier module list'.
    """
    client = RegistryClient()

    try:
        # Build filters
        filters = {}
        if type:
            filters["type"] = type
        if verified:
            filters["verified"] = True

        # Fetch modules and installed modules
        modules = client.list_modules(filters=filters if filters else None)
        installed_modules = _get_installed_modules()

        # JSON output
        if output_json:
            output = {"total": len(modules), "modules": modules}
            print(json.dumps(output, indent=2))
            return

        # Human-readable output
        if not modules:
            console.print("[yellow]No modules found matching criteria[/yellow]")
            console.print("\nTry without filters: [cyan]amplifier module registry[/cyan]")
            return

        # Build filter description
        filter_desc = []
        if type:
            filter_desc.append(f"type={type}")
        if verified:
            filter_desc.append("verified only")
        filter_str = f" ({', '.join(filter_desc)})" if filter_desc else ""

        table = Table(
            title=f"Available Modules{filter_str} ({len(modules)})",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Name \\[verified]", style="green", no_wrap=True)
        table.add_column("Installed", style="magenta", justify="center")
        table.add_column("Version", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Description")
        table.add_column("Tags", style="dim")

        for module in modules:
            # Format name with verification badge
            name = module["name"]
            if module.get("verified"):
                name = f"{name} \\[v]"

            # Check if installed
            is_installed = module["name"] in installed_modules
            installed_indicator = "Yes" if is_installed else ""

            # Get version (using 'latest' field from JSON)
            version = module.get("latest", "")

            # Get type (using 'module_type' field from JSON)
            module_type = module.get("module_type", "")

            # Truncate long descriptions
            desc = module.get("description", "")

            # Format tags
            tags = module.get("tags", [])
            tags_str = ", ".join(tags)  # Show first 3 tags
            

            table.add_row(
                name,
                installed_indicator,
                version,
                module_type,
                desc,
                tags_str,
            )

        console.print(table)
        console.print()
        console.print("[dim]Legend:[/dim]")
        console.print("[dim]  Name: \\[v] = Verified by Amplifier team[/dim]")
        console.print("[dim]  Installed: 'Yes' = Module is already installed[/dim]")
        console.print()
        console.print("[dim]To install a module:[/dim]")
        console.print("  [cyan]amplifier module add <name>[/cyan]")
        console.print()
        console.print("[dim]For more details:[/dim]")
        console.print("  [cyan]amplifier module info <name>[/cyan]")

    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to fetch registry: {e}")
        console.print("\n[dim]Possible solutions:[/dim]")
        console.print("  1. Check your internet connection")
        console.print("  2. Try again in a few moments")


def registry_command_func(type: str | None, verified: bool, output_json: bool):
    """Wrapper function for calling from code."""
    registry_command.callback(type=type, verified=verified, output_json=output_json)
