"""Tool management commands for the Amplifier CLI.

Generic mechanism to list, inspect, and invoke any mounted tool.
This provides CLI access to tools from any collection without the CLI
needing to know about specific tools or collections.

Philosophy: Mechanism, not policy. CLI provides capability to invoke tools;
which tools exist is determined by the active bundle (or deprecated profile).
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
from ..paths import create_agent_loader
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..runtime.config import inject_user_providers

# ============================================================================
# Bundle/Profile Detection (mirrors run.py pattern)
# ============================================================================


def _get_active_bundle_name() -> str | None:
    """Get the active bundle name from settings (if any).

    Checks for bundle configured via 'amplifier bundle use'.
    Returns None if no bundle is explicitly configured.
    """
    config_manager = create_config_manager()
    bundle_settings = config_manager.get_merged_settings().get("bundle", {})
    if isinstance(bundle_settings, dict):
        return bundle_settings.get("active")
    return None


def _should_use_bundle() -> tuple[bool, str | None, str | None]:
    """Determine whether to use bundle or profile system.

    Returns:
        Tuple of (use_bundle: bool, bundle_name: str | None, profile_name: str | None)

    Logic (mirrors run.py):
    1. If active bundle is set → use bundle
    2. If active profile is set → use profile (deprecated path)
    3. Default to 'foundation' bundle (Phase 2 default)
    """
    config_manager = create_config_manager()

    # Check for active bundle
    bundle_name = _get_active_bundle_name()
    if bundle_name:
        return (True, bundle_name, None)

    # Check for explicit profile configuration (deprecated)
    profile_name = config_manager.get_active_profile()
    if profile_name:
        return (False, None, profile_name)

    # Default to foundation bundle (Phase 2)
    return (True, "foundation", None)


# ============================================================================
# Bundle-based Tool Loading (primary path)
# ============================================================================


async def _get_mounted_tools_from_bundle_async(bundle_name: str) -> list[dict[str, Any]]:
    """Get actual mounted tool names from a bundle.

    Uses PreparedBundle to create a session and extract mounted tools.

    Args:
        bundle_name: Name of bundle to load

    Returns:
        List of tool dicts with name, description, and callable status
    """
    from ..lib.app_settings import AppSettings
    from ..runtime.config import resolve_config_async

    # Load bundle via unified resolve_config_async (single source of truth)
    agent_loader = create_agent_loader()
    config_manager = create_config_manager()
    profile_loader = create_profile_loader()
    app_settings = AppSettings(config_manager)

    try:
        _config, prepared_bundle = await resolve_config_async(
            bundle_name=bundle_name,
            config_manager=config_manager,
            profile_loader=profile_loader,
            agent_loader=agent_loader,
            app_settings=app_settings,
            console=console,
        )
    except Exception as e:
        raise ValueError(f"Failed to load bundle '{bundle_name}': {e}") from e
    
    if prepared_bundle is None:
        raise ValueError(f"Bundle '{bundle_name}' did not produce a PreparedBundle")

    inject_user_providers(_config, prepared_bundle)

    # Create session from prepared bundle
    session = await prepared_bundle.create_session()
    await session.initialize()

    try:
        # Get mounted tools
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


async def _invoke_tool_from_bundle_async(bundle_name: str, tool_name: str, tool_args: dict[str, Any]) -> Any:
    """Invoke a tool within a bundle session context.

    Args:
        bundle_name: Bundle determining which tools are available
        tool_name: Name of tool to invoke
        tool_args: Arguments to pass to the tool

    Returns:
        Tool execution result

    Raises:
        ValueError: If tool not found
        Exception: If tool execution fails
    """
    from ..lib.app_settings import AppSettings
    from ..main import _register_session_spawning
    from ..runtime.config import resolve_config_async

    # Load bundle via unified resolve_config_async (single source of truth)
    agent_loader = create_agent_loader()
    config_manager = create_config_manager()
    profile_loader = create_profile_loader()
    app_settings = AppSettings(config_manager)

    _config, prepared_bundle = await resolve_config_async(
        bundle_name=bundle_name,
        config_manager=config_manager,
        profile_loader=profile_loader,
        agent_loader=agent_loader,
        app_settings=app_settings,
        console=console,
    )
    
    if prepared_bundle is None:
        raise ValueError(f"Bundle '{bundle_name}' did not produce a PreparedBundle")

    inject_user_providers(_config, prepared_bundle)

    # Create session from prepared bundle
    session = await prepared_bundle.create_session()
    await session.initialize()

    # Register session spawning (enables tools like recipes to spawn sub-sessions)
    _register_session_spawning(session)

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

        # Invoke the tool
        if hasattr(tool_instance, "execute"):
            result = await tool_instance.execute(tool_args)  # type: ignore[union-attr]
        else:
            raise ValueError(f"Tool '{tool_name}' does not have execute method")

        return result

    finally:
        await session.cleanup()


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

    from ..lib.legacy import compile_profile_to_mount_plan
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
@click.option("--profile", "-p", help="Profile to use (deprecated, use bundles instead)")
@click.option("--bundle", "-b", help="Bundle to use (default: active bundle)")
@click.option("--output", "-o", type=click.Choice(["table", "json"]), default="table", help="Output format")
@click.option("--modules", "-m", is_flag=True, help="Show module names instead of mounted tools")
def tool_list(profile: str | None, bundle: str | None, output: str, modules: bool):
    """List available tools from the active bundle (or profile).

    By default, shows the actual tool names that can be invoked (e.g., read_file,
    write_file). Use --modules to see tool module names instead (e.g., tool-filesystem).
    """
    # Determine whether to use bundle or profile path
    use_bundle, default_bundle, default_profile = _should_use_bundle()

    # Explicit flags override auto-detection
    if bundle:
        use_bundle = True
        default_bundle = bundle
    elif profile:
        use_bundle = False
        default_profile = profile

    if use_bundle:
        # Bundle path (primary)
        bundle_name = default_bundle or "foundation"

        if modules:
            # For bundles, --modules is not supported (bundles don't expose module-level info the same way)
            console.print("[yellow]--modules flag not supported with bundles. Showing mounted tools.[/yellow]")

        # Show actual mounted tool names
        console.print(f"[dim]Mounting tools from bundle '{bundle_name}'...[/dim]")

        try:
            tools = asyncio.run(_get_mounted_tools_from_bundle_async(bundle_name))
        except Exception as e:
            console.print(f"[red]Error mounting tools:[/red] {e}")
            sys.exit(1)

        if not tools:
            console.print(f"[yellow]No tools mounted from bundle '{bundle_name}'[/yellow]")
            return

        if output == "json":
            result = {
                "bundle": bundle_name,
                "tools": [{"name": t["name"], "description": t["description"]} for t in tools],
            }
            print(json.dumps(result, indent=2))
            return

        # Table output for humans
        table = Table(
            title=f"Mounted Tools ({len(tools)} tools from bundle '{bundle_name}')",
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
        return

    # Profile path (deprecated - will be removed)
    profile_name = default_profile or _get_active_profile_name()

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
@click.option("--profile", "-p", help="Profile to use (deprecated, use bundles instead)")
@click.option("--bundle", "-b", help="Bundle to use (default: active bundle)")
@click.option("--output", "-o", type=click.Choice(["text", "json"]), default="text", help="Output format")
@click.option("--module", "-m", is_flag=True, help="Look up by module name instead of mounted tool name")
def tool_info(tool_name: str, profile: str | None, bundle: str | None, output: str, module: bool):
    """Show detailed information about a tool.

    By default, looks up the actual mounted tool by name (e.g., read_file).
    Use --module to look up by module name instead (e.g., tool-filesystem).
    """
    # Determine whether to use bundle or profile path
    use_bundle, default_bundle, default_profile = _should_use_bundle()

    # Explicit flags override auto-detection
    if bundle:
        use_bundle = True
        default_bundle = bundle
    elif profile:
        use_bundle = False
        default_profile = profile

    if use_bundle:
        # Bundle path (primary)
        bundle_name = default_bundle or "foundation"

        if module:
            # For bundles, --module is not supported
            console.print("[yellow]--module flag not supported with bundles. Looking up mounted tool.[/yellow]")

        # Look up actual mounted tool
        console.print(f"[dim]Mounting tools to get info for '{tool_name}'...[/dim]")

        try:
            tools = asyncio.run(_get_mounted_tools_from_bundle_async(bundle_name))
        except Exception as e:
            console.print(f"[red]Error mounting tools:[/red] {e}")
            sys.exit(1)

        found_tool = next((t for t in tools if t["name"] == tool_name), None)

        if not found_tool:
            console.print(f"[red]Error:[/red] Tool '{tool_name}' not found in bundle '{bundle_name}'")
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
        return

    # Profile path (deprecated - will be removed)
    profile_name = default_profile or _get_active_profile_name()

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
@click.option("--bundle", "-b", help="Bundle to use (default: auto-detect)")
@click.option("--profile", "-p", help="Profile to use (deprecated, use --bundle)")
@click.option("--output", "-o", type=click.Choice(["text", "json"]), default="text", help="Output format")
def tool_invoke(tool_name: str, args: tuple[str, ...], bundle: str | None, profile: str | None, output: str):
    """Invoke a tool directly with provided arguments.

    Arguments are provided as key=value pairs:

        amplifier tool invoke filesystem_read path=/tmp/test.txt

    For complex values, use JSON:

        amplifier tool invoke some_tool data='{"key": "value"}'
    """
    # Parse key=value arguments first (before session creation)
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

    # Determine bundle vs profile path
    if bundle:
        # Explicit bundle flag
        use_bundle, bundle_name, profile_name = True, bundle, None
    elif profile:
        # Explicit profile flag (deprecated path)
        use_bundle, bundle_name, profile_name = False, None, profile
    else:
        # Auto-detect: bundle if configured, else profile, else foundation bundle (Phase 2 default)
        use_bundle, bundle_name, profile_name = _should_use_bundle()

    # Run the invocation
    try:
        if use_bundle:
            result = asyncio.run(_invoke_tool_from_bundle_async(bundle_name, tool_name, tool_args))  # type: ignore[arg-type]
        else:
            # Deprecated profile path
            result = asyncio.run(_invoke_tool_async(profile_name or _get_active_profile_name(), tool_name, tool_args))
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

    from ..lib.legacy import compile_profile_to_mount_plan
    from ..main import _register_session_spawning
    from ..paths import create_agent_loader
    from ..paths import create_module_resolver

    # Load profile and compile to mount plan (with agent loader for agent delegation)
    loader = create_profile_loader()
    profile = loader.load_profile(profile_name)
    agent_loader = create_agent_loader()
    mount_plan = compile_profile_to_mount_plan(profile, agent_loader=agent_loader)

    # Create session with mount plan
    session = AmplifierSession(mount_plan)

    # Mount module source resolver (app-layer policy)
    resolver = create_module_resolver()
    await session.coordinator.mount("module-source-resolver", resolver)

    # Initialize session (mounts all tools)
    await session.initialize()

    # Register session spawning capabilities (app-layer policy)
    # This enables tools like recipes to spawn agent sub-sessions
    _register_session_spawning(session)

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
