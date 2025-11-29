"""Tool management commands for the Amplifier CLI.

Generic mechanism to list, inspect, and invoke any mounted tool.
This provides CLI access to tools from any collection without the CLI
needing to know about specific tools or collections.

Philosophy: Mechanism, not policy. CLI provides capability to invoke tools;
which tools exist is determined by the active profile.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click
from rich.panel import Panel
from rich.table import Table

from ..console import console
from ..data.profiles import get_system_default_profile
from ..paths import create_config_manager
from ..paths import create_profile_loader


@click.group(invoke_without_command=True)
@click.pass_context
def tool(ctx: click.Context):
    """Invoke tools from the active profile.

    Generic mechanism to list, inspect, and invoke any mounted tool.
    Tools are determined by the active profile's mount plan.

    Examples:
        amplifier tool list                    List available tools
        amplifier tool info filesystem_read    Show tool schema
        amplifier tool invoke filesystem_read path=/tmp/test.txt
    """
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


def _get_active_profile_name() -> str:
    """Get the active profile name from config hierarchy."""
    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile()
    if active_profile:
        return active_profile

    project_default = config_manager.get_project_default()
    if project_default:
        return project_default

    return get_system_default_profile()


def _get_tools_from_profile(profile_name: str) -> list[dict[str, Any]]:
    """Extract tool MODULE information from a profile's mount plan.

    This returns module-level info (e.g., 'tool-filesystem'), NOT individual tools.
    For actual mounted tool names, use _get_mounted_tools_async().

    Args:
        profile_name: Name of profile to load

    Returns:
        List of tool module dicts with module, source, config, etc.
    """
    loader = create_profile_loader()
    try:
        profile = loader.load_profile(profile_name)
    except (FileNotFoundError, ValueError):
        return []

    tools: list[dict[str, Any]] = []
    for tool_entry in profile.tools:
        tools.append(
            {
                "module": tool_entry.module,
                "source": tool_entry.source or "profile",
                "config": tool_entry.config or {},
                "description": getattr(tool_entry, "description", "No description"),
            }
        )
    return tools


async def _get_mounted_tools_async(profile_name: str) -> list[dict[str, Any]]:
    """Get actual mounted tool names by initializing a session.

    Modules like 'tool-filesystem' expose multiple tools like 'read_file',
    'write_file', 'edit_file'. This function returns the actual tool names
    that can be invoked.

    Args:
        profile_name: Profile determining which tools are available

    Returns:
        List of tool dicts with name, module (if determinable), and callable status
    """
    from amplifier_core import AmplifierSession
    from amplifier_profiles import compile_profile_to_mount_plan

    from ..paths import create_module_resolver

    # Load profile and compile to mount plan
    loader = create_profile_loader()
    try:
        profile = loader.load_profile(profile_name)
    except (FileNotFoundError, ValueError):
        return []

    mount_plan = compile_profile_to_mount_plan(profile)

    # Create session with mount plan
    session = AmplifierSession(mount_plan)

    # Mount module source resolver (app-layer policy)
    resolver = create_module_resolver()
    await session.coordinator.mount("module-source-resolver", resolver)

    # Initialize session (mounts all tools)
    await session.initialize()

    try:
        # Get mounted tools - these are the actual invokable tool names
        tools = session.coordinator.get("tools")
        if not tools:
            return []

        result = []
        for tool_name, tool_instance in tools.items():
            # Get description from tool if available
            description = "No description"
            if hasattr(tool_instance, "description"):
                description = tool_instance.description
            elif hasattr(tool_instance, "__doc__") and tool_instance.__doc__:
                # Use first line of docstring
                description = tool_instance.__doc__.strip().split("\n")[0]

            result.append(
                {
                    "name": tool_name,
                    "description": description,
                    "has_execute": hasattr(tool_instance, "execute"),
                }
            )

        return sorted(result, key=lambda t: t["name"])

    finally:
        await session.cleanup()


@tool.command(name="list")
@click.option("--profile", "-p", help="Profile to use (default: active profile)")
@click.option("--output", "-o", type=click.Choice(["table", "json"]), default="table", help="Output format")
@click.option("--modules", "-m", is_flag=True, help="Show module names instead of mounted tools")
def tool_list(profile: str | None, output: str, modules: bool):
    """List available tools from the active profile.

    By default, shows the actual tool names that can be invoked (e.g., read_file,
    write_file). Use --modules to see tool module names instead (e.g., tool-filesystem).
    """
    profile_name = profile or _get_active_profile_name()

    if modules:
        # Show module-level info (fast, no session needed)
        tool_modules = _get_tools_from_profile(profile_name)

        if not tool_modules:
            console.print(f"[yellow]No tool modules found in profile '{profile_name}'[/yellow]")
            return

        if output == "json":
            result = {
                "profile": profile_name,
                "modules": [{"name": t["module"], "source": t["source"]} for t in tool_modules],
            }
            print(json.dumps(result, indent=2))
            return

        # Table output for humans
        table = Table(title=f"Tool Modules in profile '{profile_name}'", show_header=True, header_style="bold cyan")
        table.add_column("Module", style="green")
        table.add_column("Source", style="yellow")

        for t in tool_modules:
            source_str = str(t["source"])
            if len(source_str) > 50:
                source_str = source_str[:47] + "..."
            table.add_row(t["module"], source_str)

        console.print(table)
        console.print("\n[dim]These are module names. Run without --modules to see actual tool names.[/dim]")
        return

    # Default: show actual mounted tool names (requires session initialization)
    console.print(f"[dim]Mounting tools from profile '{profile_name}'...[/dim]")

    try:
        tools = asyncio.run(_get_mounted_tools_async(profile_name))
    except Exception as e:
        console.print(f"[red]Error mounting tools:[/red] {e}")
        console.print("[dim]Try 'amplifier tool list --modules' to see tool modules without mounting.[/dim]")
        sys.exit(1)

    if not tools:
        console.print(f"[yellow]No tools mounted from profile '{profile_name}'[/yellow]")
        return

    if output == "json":
        result = {
            "profile": profile_name,
            "tools": [{"name": t["name"], "description": t["description"]} for t in tools],
        }
        print(json.dumps(result, indent=2))
        return

    # Table output for humans
    table = Table(
        title=f"Mounted Tools ({len(tools)} tools from profile '{profile_name}')",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name", style="green")
    table.add_column("Description", style="yellow")

    for t in tools:
        desc = t["description"]
        if len(desc) > 60:
            desc = desc[:57] + "..."
        table.add_row(t["name"], desc)

    console.print(table)
    console.print("\n[dim]Use 'amplifier tool invoke <name> key=value ...' to invoke a tool[/dim]")


@tool.command(name="info")
@click.argument("tool_name")
@click.option("--profile", "-p", help="Profile to use (default: active profile)")
@click.option("--output", "-o", type=click.Choice(["text", "json"]), default="text", help="Output format")
@click.option("--module", "-m", is_flag=True, help="Look up by module name instead of mounted tool name")
def tool_info(tool_name: str, profile: str | None, output: str, module: bool):
    """Show detailed information about a tool.

    By default, looks up the actual mounted tool by name (e.g., read_file).
    Use --module to look up by module name instead (e.g., tool-filesystem).
    """
    profile_name = profile or _get_active_profile_name()

    if module:
        # Module lookup (fast, no session needed)
        tool_modules = _get_tools_from_profile(profile_name)
        found_tool = next((t for t in tool_modules if t["module"] == tool_name), None)

        if not found_tool:
            console.print(f"[red]Error:[/red] Module '{tool_name}' not found in profile '{profile_name}'")
            console.print("\nAvailable modules:")
            for t in tool_modules:
                console.print(f"  - {t['module']}")
            sys.exit(1)

        if output == "json":
            print(json.dumps(found_tool, indent=2))
            return

        panel_content = f"""[bold]Module:[/bold] {found_tool["module"]}
[bold]Source:[/bold] {found_tool["source"]}
[bold]Description:[/bold] {found_tool.get("description", "No description")}"""

        if found_tool.get("config"):
            panel_content += "\n[bold]Config:[/bold]"
            for key, value in found_tool["config"].items():
                panel_content += f"\n  {key}: {value}"

        console.print(Panel(panel_content, title=f"Module: {tool_name}", border_style="cyan"))
        console.print("\n[dim]This is a module. Run 'amplifier tool list' to see actual tool names.[/dim]")
        return

    # Default: look up actual mounted tool
    console.print(f"[dim]Mounting tools to get info for '{tool_name}'...[/dim]")

    try:
        tools = asyncio.run(_get_mounted_tools_async(profile_name))
    except Exception as e:
        console.print(f"[red]Error mounting tools:[/red] {e}")
        console.print("[dim]Try 'amplifier tool info --module <name>' to look up module info.[/dim]")
        sys.exit(1)

    found_tool = next((t for t in tools if t["name"] == tool_name), None)

    if not found_tool:
        console.print(f"[red]Error:[/red] Tool '{tool_name}' not found in profile '{profile_name}'")
        console.print("\nAvailable tools:")
        for t in tools:
            console.print(f"  - {t['name']}")
        sys.exit(1)

    if output == "json":
        print(json.dumps(found_tool, indent=2))
        return

    panel_content = f"""[bold]Name:[/bold] {found_tool["name"]}
[bold]Description:[/bold] {found_tool.get("description", "No description")}
[bold]Invokable:[/bold] {"Yes" if found_tool.get("has_execute") else "No"}"""

    console.print(Panel(panel_content, title=f"Tool: {tool_name}", border_style="cyan"))
    console.print("\n[dim]Usage: amplifier tool invoke " + tool_name + " key=value ...[/dim]")


@tool.command(name="invoke")
@click.argument("tool_name")
@click.argument("args", nargs=-1)
@click.option("--profile", "-p", help="Profile to use (default: active profile)")
@click.option("--output", "-o", type=click.Choice(["text", "json"]), default="text", help="Output format")
def tool_invoke(tool_name: str, args: tuple[str, ...], profile: str | None, output: str):
    """Invoke a tool directly with provided arguments.

    Arguments are provided as key=value pairs:

        amplifier tool invoke filesystem_read path=/tmp/test.txt

    For complex values, use JSON:

        amplifier tool invoke some_tool data='{"key": "value"}'
    """
    profile_name = profile or _get_active_profile_name()

    # Parse key=value arguments
    tool_args: dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            console.print(f"[red]Error:[/red] Invalid argument format: '{arg}'")
            console.print("Arguments must be in key=value format")
            sys.exit(1)

        key, value = arg.split("=", 1)

        # Try to parse as JSON for complex values
        try:
            tool_args[key] = json.loads(value)
        except json.JSONDecodeError:
            # Use as plain string
            tool_args[key] = value

    # Run the invocation
    try:
        result = asyncio.run(_invoke_tool_async(profile_name, tool_name, tool_args))
    except Exception as e:
        if output == "json":
            error_output = {"status": "error", "error": str(e), "tool": tool_name}
            print(json.dumps(error_output, indent=2))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Output result
    if output == "json":
        success_output = {"status": "success", "tool": tool_name, "result": result}
        print(json.dumps(success_output, indent=2, default=str))
    else:
        console.print(f"[bold green]Result from {tool_name}:[/bold green]")
        if isinstance(result, dict):
            for key, value in result.items():
                console.print(f"  {key}: {value}")
        elif isinstance(result, list):
            for item in result:
                console.print(f"  - {item}")
        else:
            console.print(f"  {result}")


async def _invoke_tool_async(profile_name: str, tool_name: str, tool_args: dict[str, Any]) -> Any:
    """Invoke a tool within a session context.

    Creates a minimal session to mount tools and invoke the specified tool.

    Args:
        profile_name: Profile determining which tools are available
        tool_name: Name of tool to invoke
        tool_args: Arguments to pass to the tool

    Returns:
        Tool execution result

    Raises:
        ValueError: If tool not found
        Exception: If tool execution fails
    """
    from amplifier_core import AmplifierSession
    from amplifier_profiles import compile_profile_to_mount_plan

    from ..paths import create_module_resolver

    # Load profile and compile to mount plan
    loader = create_profile_loader()
    profile = loader.load_profile(profile_name)
    mount_plan = compile_profile_to_mount_plan(profile)

    # Create session with mount plan
    session = AmplifierSession(mount_plan)

    # Mount module source resolver (app-layer policy)
    resolver = create_module_resolver()
    await session.coordinator.mount("module-source-resolver", resolver)

    # Initialize session (mounts all tools)
    await session.initialize()

    try:
        # Get mounted tools
        tools = session.coordinator.get("tools")
        if not tools:
            raise ValueError("No tools mounted in session")

        # Find the tool
        if tool_name not in tools:
            available = ", ".join(tools.keys())
            raise ValueError(f"Tool '{tool_name}' not found. Available: {available}")

        tool_instance = tools[tool_name]

        # Invoke the tool - tools have async execute() method
        if hasattr(tool_instance, "execute"):
            result = await tool_instance.execute(tool_args)  # type: ignore[union-attr]
        else:
            raise ValueError(f"Tool '{tool_name}' does not have execute method")

        return result

    finally:
        await session.cleanup()


__all__ = ["tool"]
