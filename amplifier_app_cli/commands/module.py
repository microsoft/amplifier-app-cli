"""Module management commands for the Amplifier CLI."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, cast

import click
from rich.panel import Panel
from rich.table import Table

from ..console import console
from ..data.profiles import get_system_default_profile
from ..module_manager import ModuleManager
from ..paths import create_config_manager, create_module_resolver, create_profile_loader


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


@module.command("refresh")
@click.argument("module_id", required=False)
@click.option("--mutable-only", is_flag=True, help="Only refresh mutable refs (branches, not tags/SHAs)")
def module_refresh(module_id: str | None, mutable_only: bool):
    """Refresh module cache.

    Clears cached git modules so they re-download on next use.
    Useful for updating modules pinned to branches (e.g., @main).
    """
    from pathlib import Path

    cache_dir = Path.home() / ".amplifier" / "module-cache"

    if not cache_dir.exists():
        console.print("[yellow]No module cache found[/yellow]")
        console.print("Modules will download on next use")
        return

    if module_id:
        # Refresh specific module - find matching cache dirs
        import json

        refreshed = 0
        for cache_hash in cache_dir.iterdir():
            if not cache_hash.is_dir():
                continue

            for ref_dir in cache_hash.iterdir():
                if not ref_dir.is_dir():
                    continue

                # Check metadata
                metadata_file = ref_dir / ".amplifier_cache_metadata.json"
                if not metadata_file.exists():
                    continue

                try:
                    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))

                    # Skip if wrong module
                    if metadata.get("url", "").split("/")[-1] != f"amplifier-module-{module_id}":
                        continue

                    # Skip if mutable-only and this is immutable
                    if mutable_only and not metadata.get("is_mutable", True):
                        continue

                    # Delete cache dir
                    import shutil

                    shutil.rmtree(ref_dir)
                    console.print(f"[green]✓ Cleared cache for {module_id}@{metadata['ref']}[/green]")
                    refreshed += 1
                except Exception as e:
                    console.print(f"[yellow]⚠ Could not clear {ref_dir}: {e}[/yellow]")

        if refreshed == 0:
            console.print(f"[yellow]No cached modules found for '{module_id}'[/yellow]")
    else:
        # Refresh all modules
        import json
        import shutil

        refreshed = 0
        skipped = 0

        for cache_hash in cache_dir.iterdir():
            if not cache_hash.is_dir():
                continue

            for ref_dir in cache_hash.iterdir():
                if not ref_dir.is_dir():
                    continue

                # Check if we should skip immutable refs
                if mutable_only:
                    metadata_file = ref_dir / ".amplifier_cache_metadata.json"
                    if metadata_file.exists():
                        try:
                            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                            if not metadata.get("is_mutable", True):
                                skipped += 1
                                continue
                        except Exception:
                            pass

                # Delete cache dir
                shutil.rmtree(ref_dir)
                refreshed += 1

        console.print(f"[green]✓ Cleared {refreshed} cached modules[/green]")
        if skipped > 0:
            console.print(f"[dim]Skipped {skipped} immutable refs (tags/SHAs)[/dim]")
        console.print("Modules will re-download on next use")


@module.command("check-updates")
def module_check_updates():
    """Check for module updates.

    Checks all sources (local files and cached git) for updates.
    """

    console.print("Checking for updates...")

    # For module check-updates, include ALL cached modules (not just active)
    from ..utils.source_status import check_all_sources

    report = asyncio.run(check_all_sources(include_all_cached=True))

    # Show git cache updates
    if report.cached_git_sources:
        console.print()
        console.print("[yellow]Cached Git Sources (updates available):[/yellow]")
        for status in report.cached_git_sources:
            console.print(f"  • {status.name}@{status.ref} ({status.layer})")
            console.print(f"    {status.cached_sha} → {status.remote_sha} ({status.age_days}d old)")
        console.print()
        console.print("Run [cyan]amplifier module refresh[/cyan] to update")

    # Show local file statuses
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

    if not report.has_updates and not report.has_local_changes:
        if report.cached_modules_checked == 0:
            console.print("[dim]No cached modules to check[/dim]")
            console.print("[dim]Modules will be cached when first used[/dim]")
        else:
            console.print(f"[green]✓ Checked {report.cached_modules_checked} cached modules - all up to date[/green]")


__all__ = ["module"]
