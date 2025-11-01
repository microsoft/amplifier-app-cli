"""Module management commands for the Amplifier CLI."""

from __future__ import annotations

import asyncio
from typing import Any
from typing import Literal
from typing import cast

import click
from rich.panel import Panel
from rich.table import Table

from ..console import console
from ..data.profiles import get_system_default_profile
from ..module_manager import ModuleManager
from ..paths import create_config_manager
from ..paths import create_module_resolver
from ..paths import create_profile_loader


@click.group(invoke_without_command=True)
@click.pass_context
def module(ctx: click.Context):
    """Manage Amplifier modules."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@module.command("list")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["all", "orchestrator", "provider", "tool", "agent", "context", "hook"]),
    default="all",
    help="Module type to list",
)
def list_modules(type: str):
    """List installed modules and those provided by the active profile."""
    from amplifier_core.loader import ModuleLoader

    loader = ModuleLoader()
    modules_info = asyncio.run(loader.discover())
    resolver = create_module_resolver()

    if modules_info:
        table = Table(title="Installed Modules (via entry points)", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Source", style="magenta")
        table.add_column("Origin", style="cyan")
        table.add_column("Description")

        for module_info in modules_info:
            if type != "all" and type != module_info.type:
                continue

            try:
                source_obj, origin = resolver.resolve_with_layer(module_info.id)
                source_str = str(source_obj)
                if len(source_str) > 40:
                    source_str = source_str[:37] + "..."
            except Exception:
                source_str = "unknown"
                origin = "unknown"

            table.add_row(module_info.id, module_info.type, source_str, origin, module_info.description)

        console.print(table)
    else:
        console.print("[dim]No installed modules found[/dim]")

    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile() or get_system_default_profile()

    local = config_manager._read_yaml(config_manager.paths.local)
    if local and "profile" in local and "active" in local["profile"]:
        source_label = "active"
    elif config_manager.get_project_default():
        source_label = "project default"
    else:
        source_label = "system default"

    profile_modules = _get_profile_modules(active_profile)
    if profile_modules:
        filtered = [m for m in profile_modules if type == "all" or m["type"] == type]

        if filtered:
            console.print()
            table = Table(
                title=f"Profile Modules (from profile '{active_profile}' ({source_label}))",
                show_header=True,
                header_style="bold green",
            )
            table.add_column("Name", style="green")
            table.add_column("Type", style="yellow")
            table.add_column("Source", style="magenta")

            for mod in filtered:
                source_str = str(mod["source"])
                if len(source_str) > 60:
                    source_str = source_str[:57] + "..."
                table.add_row(mod["id"], mod["type"], source_str)

            console.print(table)


@module.command("show")
@click.argument("module_name")
def module_show(module_name: str):
    """Show detailed information about a module."""
    from amplifier_core.loader import ModuleLoader

    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile() or get_system_default_profile()

    profile_modules = _get_profile_modules(active_profile)
    found_in_profile = next((m for m in profile_modules if m["id"] == module_name), None)

    if found_in_profile:
        source = found_in_profile["source"]
        description = found_in_profile.get("description", "No description provided")
        mount_point = found_in_profile.get("mount_point", "unknown")

        panel_content = f"""[bold]Name:[/bold] {module_name}
[bold]Type:[/bold] {found_in_profile["type"]}
[bold]Source:[/bold] {source}
[bold]Description:[/bold] {description}
[bold]Mount Point:[/bold] {mount_point}"""
        console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="cyan"))
        return

    loader = ModuleLoader()
    modules_info = asyncio.run(loader.discover())
    found_module = next((m for m in modules_info if m.id == module_name), None)

    if not found_module:
        console.print(f"[red]Module '{module_name}' not found in profile or installed modules[/red]")
        return

    panel_content = f"""[bold]Name:[/bold] {found_module.id}
[bold]Type:[/bold] {found_module.type}
[bold]Description:[/bold] {found_module.description}
[bold]Mount Point:[/bold] {found_module.mount_point}
[bold]Version:[/bold] {found_module.version}
[bold]Origin:[/bold] Installed (entry point)"""

    console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="cyan"))


@module.command("add")
@click.argument("module_id")
@click.option("--local", "scope_flag", flag_value="local", help="Add locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Add for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Add globally (all projects)")
def module_add(module_id: str, scope_flag: str | None):
    """Add a module override to settings."""

    if module_id.startswith("tool-"):
        module_type: Literal["tool", "hook", "agent"] = "tool"
    elif module_id.startswith("hooks-"):
        module_type = "hook"
    elif module_id.startswith("agent-"):
        module_type = "agent"
    else:
        console.print("[red]Error:[/red] Module ID must start with tool-, hooks-, or agent-")
        console.print("\nExamples:")
        console.print("  amplifier module add tool-jupyter")
        console.print("  amplifier module add hooks-logging")
        console.print("  amplifier module add agent-custom")
        return

    if not scope_flag:
        console.print("\nAdd for:")
        console.print("  [1] Just you (local)")
        console.print("  [2] Whole team (project)")
        console.print("  [3] All your projects (global)")
        choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")
        scope_map: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
        scope = scope_map[choice]
    else:
        scope = cast(Literal["local", "project", "global"], scope_flag)

    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)
    result = module_mgr.add_module(module_id, module_type, scope)  # type: ignore[arg-type]

    console.print(f"[green]✓ Added {module_id}[/green]")
    console.print(f"  Scope: {scope}")
    console.print(f"  File: {result.file}")


@module.command("remove")
@click.argument("module_id")
@click.option("--local", "scope_flag", flag_value="local", help="Remove from local")
@click.option("--project", "scope_flag", flag_value="project", help="Remove from project")
@click.option("--global", "scope_flag", flag_value="global", help="Remove from global")
def module_remove(module_id: str, scope_flag: str | None):
    """Remove a module override from settings."""

    if not scope_flag:
        console.print("\nRemove from:")
        console.print("  [1] Just you (local)")
        console.print("  [2] Whole team (project)")
        console.print("  [3] All your projects (global)")
        choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")
        scope_map: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
        scope = scope_map[choice]
    else:
        scope = cast(Literal["local", "project", "global"], scope_flag)

    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)
    module_mgr.remove_module(module_id, scope)  # type: ignore[arg-type]

    console.print(f"[green]✓ Removed {module_id} from {scope}[/green]")


@module.command("current")
def module_current():
    """Display modules configured in settings overrides."""
    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)
    modules = module_mgr.get_current_modules()

    if not modules:
        console.print("[yellow]No modules configured in settings[/yellow]")
        console.print("\nAdd modules with:")
        console.print("  [cyan]amplifier module add <module-id>[/cyan]")
        return

    table = Table(title="Currently Configured Modules (from settings)")
    table.add_column("Module", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Source", style="cyan")

    for mod in modules:
        table.add_row(mod.module_id, mod.module_type, mod.source)

    console.print(table)
    console.print("\n[dim]Note: This shows modules added via settings.[/dim]")
    console.print("[dim]For all installed modules, use: amplifier module list[/dim]")


def _get_profile_modules(profile_name: str) -> list[dict[str, Any]]:
    """Return module metadata for a profile."""
    loader = create_profile_loader()
    try:
        profile = loader.load_profile(profile_name)
    except Exception:
        return []

    modules: list[dict[str, Any]] = []

    def add_module(module, module_type: str):
        if module is None:
            return
        modules.append(
            {
                "id": module.module,
                "type": module_type,
                "source": module.source or "profile",
                "config": module.config or {},
                "description": getattr(module, "description", "No description"),
                "mount_point": getattr(module, "mount_point", "unknown"),
            }
        )

    for provider in profile.providers:
        add_module(provider, "provider")
    for tool in profile.tools:
        add_module(tool, "tool")
    for hook in profile.hooks:
        add_module(hook, "hook")
    if profile.session:
        add_module(profile.session.orchestrator, "orchestrator")
        add_module(profile.session.context, "context")

    return modules


__all__ = ["module"]
