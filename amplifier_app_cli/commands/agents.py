"""Agent management commands for the Amplifier CLI."""

from __future__ import annotations

import click

from ..console import console


@click.group(invoke_without_command=True)
@click.pass_context
def agents(ctx: click.Context):
    """Manage Amplifier agents."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@agents.command("list")
@click.option("--bundle", "-b", default=None, help="Bundle to list agents from")
def list_agents(bundle: str | None):
    """List available agents from bundles.

    Agents are defined within bundles. Use --bundle to specify which bundle's
    agents to list, or omit to see agents from the default bundle.
    """
    from ..paths import create_bundle_registry

    registry = create_bundle_registry()

    # Get well-known bundles
    well_known = registry.list_registered()

    if not well_known:
        console.print("[dim]No bundles registered[/dim]")
        console.print("\nUse [cyan]amplifier bundle list[/cyan] to see available bundles")
        return

    console.print("[bold]Agents are defined within bundles.[/bold]\n")
    console.print("To see agents for a specific bundle, load that bundle and check its configuration.")
    console.print("Available bundles with potential agents:\n")

    for name in well_known:
        console.print(f"  • {name}")

    console.print("\n[dim]Use 'amplifier bundle show <name>' to see bundle details[/dim]")
    console.print("[dim]Use 'amplifier run --bundle <name>' to start a session with that bundle[/dim]")


@agents.command("show")
@click.argument("name")
def show_agent(name: str):
    """Show detailed information about a specific agent.

    NAME is the agent name (e.g., 'foundation:zen-architect')

    Agents are defined within bundles and accessed via the task tool during sessions.
    """
    console.print(f"\n[bold cyan]{name}[/bold cyan]\n")
    console.print("Agents are defined within bundles and loaded during sessions.")
    console.print("To see an agent's configuration, examine the bundle that defines it.\n")

    # Parse bundle:agent format
    if ":" in name:
        bundle_part, agent_part = name.split(":", 1)
        console.print(f"This agent appears to be from bundle: [cyan]{bundle_part}[/cyan]")
        console.print(f"Agent name within bundle: [green]{agent_part}[/green]\n")
    else:
        console.print("Agent names typically use the format: [cyan]bundle:agent-name[/cyan]\n")

    console.print("[dim]Use 'amplifier bundle show <bundle>' to examine bundle configuration[/dim]")


@agents.command("dirs")
def show_dirs():
    """Show agent search directories.

    Note: Agents are now primarily defined within bundles rather than
    standalone directories.
    """
    from ..paths import get_agent_search_paths

    paths = get_agent_search_paths()

    console.print("\n[bold]Agent Search Paths[/bold]")
    console.print("[dim](legacy - agents are now primarily defined within bundles)[/dim]\n")

    if not paths:
        console.print("[yellow]No search paths configured[/yellow]")
        console.print("\nAgents are now defined within bundles.")
        console.print("Use [cyan]amplifier bundle list[/cyan] to see available bundles.")
        return

    for path in reversed(paths):
        exists = path.exists()
        status = "[green]✓[/green]" if exists else "[dim]✗[/dim]"

        # Determine path type
        path_str = str(path)
        if ".amplifier/agents" in path_str:
            if str(path).startswith(str(path.home())):
                label = "[cyan]user[/cyan]"
            else:
                label = "[cyan]project[/cyan]"
        elif "amplifier_app_cli" in path_str:
            label = "[yellow]bundled[/yellow]"
        else:
            label = "[dim]other[/dim]"

        console.print(f"  {status} {label:20} {path}")

    console.print()
