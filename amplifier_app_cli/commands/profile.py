"""Profile management commands for the Amplifier CLI."""

from __future__ import annotations

import sys
from typing import Any

import click
from rich.table import Table

from ..console import console
from ..data.profiles import get_system_default_profile
from ..paths import create_config_manager
from ..paths import create_profile_loader


@click.group(invoke_without_command=True)
@click.pass_context
def profile(ctx: click.Context):
    """Manage Amplifier profiles."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@profile.command(name="list")
def profile_list():
    """List all available profiles."""
    loader = create_profile_loader()
    config_manager = create_config_manager()
    profiles = loader.list_profiles()
    active_profile = config_manager.get_active_profile()
    project_default = config_manager.get_project_default()

    if not profiles:
        console.print("[yellow]No profiles found.[/yellow]")
        return

    table = Table(title="Available Profiles", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Status")

    for profile_name in profiles:
        source = loader.get_profile_source(profile_name)
        source_label = source or "unknown"

        status_parts: list[str] = []
        if profile_name == active_profile:
            status_parts.append("[bold green]active[/bold green]")
        if profile_name == project_default:
            status_parts.append("[cyan]default[/cyan]")

        status = ", ".join(status_parts) if status_parts else ""

        table.add_row(profile_name, source_label, status)

    console.print(table)


@profile.command(name="current")
def profile_current():
    """Show the currently active profile and its source."""
    config_manager = create_config_manager()

    local = config_manager._read_yaml(config_manager.paths.local)
    if local and "profile" in local and "active" in local["profile"]:
        profile_name = local["profile"]["active"]
        source = "local"
    else:
        project_default = config_manager.get_project_default()
        if project_default:
            profile_name = project_default
            source = "default"
        else:
            user = config_manager._read_yaml(config_manager.paths.user)
            if user and "profile" in user and "active" in user["profile"]:
                profile_name = user["profile"]["active"]
                source = "user"
            else:
                profile_name = None
                source = None

    if profile_name:
        if source == "local":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from local settings)[/dim]")
            console.print("Source: [cyan].amplifier/settings.local.yaml[/cyan]")
        elif source == "default":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from project default)[/dim]")
            console.print("Source: [cyan].amplifier/settings.yaml[/cyan]")
        elif source == "user":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from user settings)[/dim]")
            console.print("Source: [cyan]~/.amplifier/settings.yaml[/cyan]")
    else:
        console.print("[yellow]No active profile set[/yellow]")
        console.print(f"Using system default: [bold]{get_system_default_profile()}[/bold]")
        console.print("\n[bold]To set a profile:[/bold]")
        console.print("  Local:   [cyan]amplifier profile use <name>[/cyan]")
        console.print("  Project: [cyan]amplifier profile use <name> --project[/cyan]")
        console.print("  Global:  [cyan]amplifier profile use <name> --global[/cyan]")


def build_effective_config_with_sources(chain: list[Any]):
    """Build effective configuration with source tracking."""
    effective_config: dict[str, Any] = {
        "session": {},
        "providers": {},
        "tools": {},
        "hooks": {},
        "agents": {},
    }

    sources: dict[str, Any] = {
        "session": {},
        "providers": {},
        "tools": {},
        "hooks": {},
        "agents": {},
    }

    for profile in chain:
        profile_name = profile.profile.name

        if profile.session:
            for field in ["orchestrator", "context", "max_tokens", "compact_threshold", "auto_compact"]:
                value = getattr(profile.session, field, None)
                if value is not None:
                    if field in effective_config["session"] and field in sources["session"]:
                        old_source = sources["session"][field]
                        if isinstance(old_source, tuple):
                            old_source = old_source[0]
                        sources["session"][field] = (profile_name, old_source)
                    else:
                        sources["session"][field] = profile_name
                    effective_config["session"][field] = value

        if profile.providers:
            for provider in profile.providers:
                module_name = provider.module
                if module_name in effective_config["providers"]:
                    old_source = sources["providers"][module_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["providers"][module_name] = (profile_name, old_source)
                else:
                    sources["providers"][module_name] = profile_name
                effective_config["providers"][module_name] = provider

        if profile.tools:
            for tool in profile.tools:
                module_name = tool.module
                if module_name in effective_config["tools"]:
                    old_source = sources["tools"][module_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["tools"][module_name] = (profile_name, old_source)
                else:
                    sources["tools"][module_name] = profile_name
                effective_config["tools"][module_name] = tool

        if profile.hooks:
            for hook in profile.hooks:
                module_name = hook.module
                if module_name in effective_config["hooks"]:
                    old_source = sources["hooks"][module_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["hooks"][module_name] = (profile_name, old_source)
                else:
                    sources["hooks"][module_name] = profile_name
                effective_config["hooks"][module_name] = hook

        if profile.agents:
            if "agents_config" not in effective_config:
                effective_config["agents_config"] = {}
            effective_config["agents_config"] = profile.agents.model_dump()
            sources["agents_config"] = profile_name

    return effective_config, sources


def render_effective_config(chain: list[Any], detailed: bool):
    """Render the effective configuration with source annotations."""
    config, sources = build_effective_config_with_sources(chain)

    console.print("\n[bold]Inheritance:[/bold]", end=" ")
    chain_names = " → ".join([p.profile.name for p in chain])
    console.print(chain_names)

    console.print("\n[bold]Effective Configuration:[/bold]\n")

    def format_source(source: Any) -> str:
        from rich.markup import escape

        if isinstance(source, list | tuple) and len(source) == 2:
            current, previous = source
            current_escaped = escape(str(current))
            previous_escaped = escape(str(previous))
            return f" [yellow]\\[from {current_escaped}, overrides {previous_escaped}][/yellow]"
        if source:
            source_escaped = escape(str(source))
            return f" [cyan]\\[from {source_escaped}][/cyan]"
        return ""

    if config["session"]:
        console.print("[bold]Session:[/bold]")
        for field, value in config["session"].items():
            source = sources["session"].get(field, "")
            console.print(f"  {field}: {value}{format_source(source)}")
        console.print()

    if config["providers"]:
        console.print("[bold]Providers:[/bold]")
        for module_name, provider in config["providers"].items():
            source = sources["providers"].get(module_name, "")
            console.print(f"  {module_name}{format_source(source)}")
            if detailed and provider.config:
                for key, value in provider.config.items():
                    console.print(f"    {key}: {value}")
        console.print()

    if config["tools"]:
        console.print("[bold]Tools:[/bold]")
        for module_name, tool in config["tools"].items():
            source = sources["tools"].get(module_name, "")
            console.print(f"  {module_name}{format_source(source)}")
            if detailed and tool.config:
                for key, value in tool.config.items():
                    console.print(f"    {key}: {value}")
        console.print()

    if config["hooks"]:
        console.print("[bold]Hooks:[/bold]")
        for module_name, hook in config["hooks"].items():
            source = sources["hooks"].get(module_name, "")
            console.print(f"  {module_name}{format_source(source)}")
            if detailed and hook.config:
                for key, value in hook.config.items():
                    console.print(f"    {key}: {value}")
        console.print()

    if config.get("agents_config"):
        console.print("[bold]Agents:[/bold]")
        agents_cfg = config["agents_config"]
        source = sources.get("agents_config", "")
        source_str = format_source(source)

        if agents_cfg.get("dirs"):
            console.print(f"  dirs: {agents_cfg['dirs']}{source_str}")
        if agents_cfg.get("include"):
            console.print(f"  include: {agents_cfg['include']}{source_str}")
        if agents_cfg.get("inline"):
            inline_count = len(agents_cfg["inline"])
            console.print(f"  inline: {inline_count} agent(s){source_str}")
            if detailed:
                for agent_name in agents_cfg["inline"]:
                    console.print(f"    - {agent_name}")


@profile.command(name="show")
@click.argument("name")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed configuration values")
def profile_show(name: str, detailed: bool):
    """Show details of a specific profile with inheritance chain."""
    loader = create_profile_loader()

    try:
        profile_obj = loader.load_profile(name)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[bold]Profile:[/bold] {profile_obj.profile.name}")
    console.print(f"[bold]Version:[/bold] {profile_obj.profile.version}")
    console.print(f"[bold]Description:[/bold] {profile_obj.profile.description}")

    chain = [profile_obj]
    render_effective_config(chain, detailed)


@profile.command(name="use")
@click.argument("name")
@click.option("--local", "scope_flag", flag_value="local", help="Set locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Set for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Set globally (all projects)")
def profile_use(name: str, scope_flag: str | None):
    """Set the active profile."""
    loader = create_profile_loader()
    config_manager = create_config_manager()

    try:
        loader.load_profile(name)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    scope = scope_flag or "local"

    if scope == "local":
        config_manager.set_active_profile(name)
        console.print(f"[green]✓ Using '{name}' profile locally[/green]")
        console.print("  File: .amplifier/settings.local.yaml")
    elif scope == "project":
        config_manager.set_project_default(name)
        console.print(f"[green]✓ Set '{name}' as project default[/green]")
        console.print("  File: .amplifier/settings.yaml")
        console.print("  [yellow]Remember to commit .amplifier/settings.yaml[/yellow]")
    elif scope == "global":
        from amplifier_config import Scope

        config_manager.update_settings({"profile": {"active": name}}, scope=Scope.USER)
        console.print(f"[green]✓ Set '{name}' globally[/green]")
        console.print("  File: ~/.amplifier/settings.yaml")


@profile.command(name="reset")
def profile_reset():
    """Clear the local profile choice (falls back to project default if set)."""
    from amplifier_config import Scope

    config_manager = create_config_manager()
    config_manager.clear_active_profile(scope=Scope.LOCAL)

    project_default = config_manager.get_project_default()
    if project_default:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using project default: [bold]{project_default}[/bold]")
    else:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using system default: [bold]{get_system_default_profile()}[/bold]")


@profile.command(name="default")
@click.option("--set", "set_default", metavar="NAME", help="Set project default profile")
@click.option("--clear", is_flag=True, help="Clear project default profile")
def profile_default(set_default: str | None, clear: bool):
    """Manage the project default profile."""
    config_manager = create_config_manager()

    if clear:
        config_manager.clear_project_default()
        console.print("[green]✓[/green] Cleared project default profile")
        return

    if set_default:
        loader = create_profile_loader()
        try:
            loader.load_profile(set_default)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Profile '{set_default}' not found")
            sys.exit(1)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)

        config_manager.set_project_default(set_default)
        console.print(f"[green]✓[/green] Set project default: {set_default}")
        console.print("\n[yellow]Note:[/yellow] Remember to commit .amplifier/settings.yaml")
        return

    project_default = config_manager.get_project_default()
    if project_default:
        console.print(f"[bold green]Project default:[/bold green] {project_default}")
        console.print("Source: [cyan].amplifier/settings.yaml[/cyan]")
    else:
        console.print("[yellow]No project default set[/yellow]")
        console.print(f"System default: [bold]{get_system_default_profile()}[/bold]")
        console.print("\nSet a project default with:")
        console.print("  [cyan]amplifier profile default --set <name>[/cyan]")


__all__ = ["profile"]
