"""Agent management commands for the Amplifier CLI."""

from __future__ import annotations

from typing import Any

import click

from ..console import console
from ..ui.item_renderer import ItemRenderer
from ..ui.view_policy import resolve_view, view_flags


@click.group(invoke_without_command=True)
@click.pass_context
def agents(ctx: click.Context):
    """Manage Amplifier agents."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@agents.command("list")
@click.option("--bundle", "-b", default=None, help="Bundle to list agents from")
@view_flags
def list_agents(bundle: str | None, compact: bool, detailed: bool, fmt: str):
    """List available agents from bundles.

    Agents are defined within bundles. Use --bundle to specify which bundle's
    agents to list, or omit to see agents from the default bundle.
    """
    from ..paths import create_bundle_registry

    registry = create_bundle_registry()
    well_known = registry.list_registered()
    view = resolve_view(
        ("agents", "list"), compact_flag=compact, detailed_flag=detailed
    )
    renderer = ItemRenderer(console)

    if not well_known:
        console.print("[dim]No bundles registered[/dim]")
        console.print(
            "\nUse [cyan]amplifier bundle list[/cyan] to see available bundles"
        )
        return

    items: list[dict[str, Any]] = [
        {
            "name": name,
            "enabled": True,
            "behaviors": ["bundle"],
            "config_summary": {"note": "agents defined within bundle"},
        }
        for name in well_known
    ]

    if fmt == "json":
        renderer.render_json(items)
        return

    renderer.render(items, view=view, category="agent", section_title="agent bundles")
    console.print("[dim]Use 'amplifier bundle show <name>' to see bundle details[/dim]")
    console.print(
        "[dim]Use 'amplifier run --bundle <name>' to start a session with that bundle[/dim]"
    )


@agents.command("show")
@click.argument("name")
@view_flags
def show_agent(name: str, compact: bool, detailed: bool, fmt: str):
    """Show detailed information about a specific agent.

    NAME is the agent name (e.g., 'foundation:zen-architect')

    Agents are defined within bundles and accessed via the task tool during sessions.
    """
    view = resolve_view(
        ("agents", "show"), compact_flag=compact, detailed_flag=detailed
    )
    renderer = ItemRenderer(console)

    bundle_part = None
    agent_part = name
    if ":" in name:
        bundle_part, agent_part = name.split(":", 1)

    item: dict[str, Any] = {
        "name": name,
        "enabled": True,
        "behaviors": [bundle_part] if bundle_part else [],
        "config_summary": {
            "bundle": bundle_part or "unknown",
            "agent": agent_part,
            "note": "agents are defined within bundles and loaded during sessions",
        },
    }

    if fmt == "json":
        renderer.render_json(item)
        return

    renderer.render_one(item, view=view)
    console.print(
        "[dim]Use 'amplifier bundle show <bundle>' to examine bundle configuration[/dim]"
    )


@agents.command("dirs")
@view_flags
def show_dirs(compact: bool, detailed: bool, fmt: str):
    """Show agent search directories.

    Note: Agents are now primarily defined within bundles rather than
    standalone directories.
    """
    from ..paths import get_agent_search_paths

    paths = get_agent_search_paths()
    view = resolve_view(
        ("agents", "list"), compact_flag=compact, detailed_flag=detailed
    )
    renderer = ItemRenderer(console)

    if not paths:
        console.print("[yellow]No search paths configured[/yellow]")
        console.print("\nAgents are now defined within bundles.")
        console.print(
            "Use [cyan]amplifier bundle list[/cyan] to see available bundles."
        )
        return

    items: list[dict[str, Any]] = []
    for path in reversed(paths):
        exists = path.exists()
        path_str = str(path)
        if ".amplifier/agents" in path_str:
            path_type = "user" if str(path).startswith(str(path.home())) else "project"
        elif "amplifier_app_cli" in path_str:
            path_type = "bundled"
        else:
            path_type = "other"

        items.append(
            {
                "name": path_str,
                "enabled": exists,
                "behaviors": [path_type],
                "config_summary": {
                    "type": path_type,
                    "exists": "yes" if exists else "no",
                },
            }
        )

    if fmt == "json":
        renderer.render_json(items)
        return

    renderer.render(
        items, view=view, category="agent", section_title="agent search paths"
    )
