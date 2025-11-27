"""Agent management commands for the Amplifier CLI."""

from __future__ import annotations

import click
from rich.table import Table

from ..console import console
from ..paths import create_agent_loader
from ..paths import get_agent_search_paths


@click.group(invoke_without_command=True)
@click.pass_context
def agents(ctx: click.Context):
    """Manage Amplifier agents."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@agents.command("list")
def list_agents():
    """List available agents from all configured directories."""
    loader = create_agent_loader()
    agent_names = loader.list_agents()

    if not agent_names:
        console.print("[dim]No agents found in search paths[/dim]")
        console.print("\nUse [cyan]amplifier agents dirs[/cyan] to see search paths")
        return

    table = Table(title=f"Available Agents ({len(agent_names)})", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Description")

    for name in agent_names:
        # Get source
        source = loader.get_agent_source(name) or "unknown"

        # Try to get description
        try:
            agent = loader.load_agent(name)
            description = agent.meta.description
            # Truncate long descriptions
            first_line = description.split("\n")[0]
            if len(first_line) > 60:
                first_line = first_line[:57] + "..."
        except Exception:
            first_line = "[dim]<failed to load>[/dim]"

        table.add_row(name, source, first_line)

    console.print(table)


@agents.command("show")
@click.argument("name")
def show_agent(name: str):
    """Show detailed information about a specific agent.

    NAME is the agent name (e.g., 'zen-architect' or 'developer-expertise:bug-hunter')
    """
    loader = create_agent_loader()

    try:
        agent = loader.load_agent(name)
    except Exception as e:
        console.print(f"[red]Error loading agent '{name}': {e}[/red]")
        return

    source = loader.get_agent_source(name) or "unknown"

    # Header
    console.print(f"\n[bold cyan]{name}[/bold cyan]")
    console.print(f"[dim]Source:[/dim] {source}")
    console.print()

    # Full description
    console.print("[bold]Description[/bold]")
    console.print(agent.meta.description)
    console.print()

    # Providers
    if agent.providers:
        console.print("[bold]Providers[/bold]")
        for p in agent.providers:
            module_name = p.module
            config_info = ""
            if p.config:
                # Show key config items
                config_items = []
                if "model" in p.config:
                    config_items.append(f"model={p.config['model']}")
                if "default_model" in p.config:
                    config_items.append(f"model={p.config['default_model']}")
                if config_items:
                    config_info = f" ({', '.join(config_items)})"
            console.print(f"  {module_name}{config_info}")
        console.print()

    # Tools
    if agent.tools:
        console.print("[bold]Tools[/bold]")
        for t in agent.tools:
            console.print(f"  {t.module}")
        console.print()

    # Hooks
    if agent.hooks:
        console.print("[bold]Hooks[/bold]")
        for h in agent.hooks:
            console.print(f"  {h.module}")
        console.print()

    # Session overrides
    if agent.session:
        console.print("[bold]Session Overrides[/bold]")
        for key, value in agent.session.items():
            console.print(f"  {key}: {value}")
        console.print()

    # System instruction preview (first few lines)
    if agent.system and agent.system.instruction:
        instruction = agent.system.instruction
        lines = instruction.split("\n")
        preview_lines = lines[:5]
        has_more = len(lines) > 5

        console.print("[bold]Instruction Preview[/bold]")
        for line in preview_lines:
            # Truncate long lines
            if len(line) > 100:
                line = line[:97] + "..."
            console.print(f"  {line}")
        if has_more:
            console.print(f"  [dim]... ({len(lines) - 5} more lines)[/dim]")
        console.print()


@agents.command("dirs")
def show_dirs():
    """Show agent search directories."""
    paths = get_agent_search_paths()

    console.print("\n[bold]Agent Search Paths[/bold]")
    console.print("[dim](in order of precedence, highest first)[/dim]\n")

    if not paths:
        console.print("[yellow]No search paths configured[/yellow]")
        return

    for path in reversed(paths):
        exists = path.exists()
        status = "[green]\u2713[/green]" if exists else "[dim]\u2717[/dim]"

        # Determine path type
        path_str = str(path)
        if ".amplifier/agents" in path_str:
            if str(path).startswith(str(path.home())):
                label = "[cyan]user[/cyan]"
            else:
                label = "[cyan]project[/cyan]"
        elif "/collections/" in path_str:
            label = "[magenta]collection[/magenta]"
        elif "amplifier_app_cli" in path_str:
            label = "[yellow]bundled[/yellow]"
        else:
            label = "[dim]other[/dim]"

        console.print(f"  {status} {label:20} {path}")

    console.print()
