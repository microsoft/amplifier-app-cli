"""Tool management commands for the Amplifier CLI.

Generic mechanism to list, inspect, and invoke any mounted tool.
This provides CLI access to tools from any bundle without the CLI
needing to know about specific tools or bundles.

Philosophy: Mechanism, not policy. CLI provides capability to invoke tools;
which tools exist is determined by the active bundle.
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
from ..paths import create_config_manager
from ..runtime.config import inject_user_providers

# ============================================================================
# Bundle Detection (mirrors run.py pattern)
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
    """Determine which bundle to use.

    Returns:
        Tuple of (use_bundle: bool, bundle_name: str | None, _unused)

    Logic (mirrors run.py):
    1. If active bundle is set → use bundle
    2. Always use bundle system
    3. Default to 'foundation' bundle
    """
    # Check for active bundle
    bundle_name = _get_active_bundle_name()
    if bundle_name:
        return (True, bundle_name, None)

    # Default to foundation bundle
    return (True, "foundation", None)


# ============================================================================
# Bundle-based Tool Loading (primary path)
# ============================================================================


async def _get_mounted_tools_from_bundle_async(
    bundle_name: str,
) -> list[dict[str, Any]]:
    """Get actual mounted tool names from a bundle.

    Uses PreparedBundle to create a session and extract mounted tools.

    Args:
        bundle_name: Name of bundle to load

    Returns:
        List of tool dicts with name, description, and callable status
    """
    from ..lib.settings import AppSettings
    from ..runtime.config import resolve_config_async

    # Load bundle via unified resolve_config_async (single source of truth)

    app_settings = AppSettings()

    try:
        _config, prepared_bundle = await resolve_config_async(
            bundle_name=bundle_name,
            agent_loader=None,
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


async def _invoke_tool_from_bundle_async(
    bundle_name: str, tool_name: str, tool_args: dict[str, Any]
) -> Any:
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
    from ..lib.settings import AppSettings
    from ..lib.bundle_loader import AppModuleResolver
    from ..paths import create_foundation_resolver
    from ..session_runner import register_session_spawning
    from ..runtime.config import resolve_config_async

    # Load bundle via unified resolve_config_async (single source of truth)

    app_settings = AppSettings()

    _config, prepared_bundle = await resolve_config_async(
        bundle_name=bundle_name,
        agent_loader=None,
        app_settings=app_settings,
        console=console,
    )

    if prepared_bundle is None:
        raise ValueError(f"Bundle '{bundle_name}' did not produce a PreparedBundle")

    # CRITICAL: Wrap bundle resolver with app-layer fallback (mirrors session_runner.py)
    # This enables fallback to installed providers when they're not in the bundle.
    # Without this wrapper, provider modules fail to load even after `amplifier provider install`.
    fallback_resolver = create_foundation_resolver()
    prepared_bundle.resolver = AppModuleResolver(  # type: ignore[assignment]
        bundle_resolver=prepared_bundle.resolver,
        settings_resolver=fallback_resolver,
    )

    inject_user_providers(_config, prepared_bundle)

    # Create session from prepared bundle
    session = await prepared_bundle.create_session()
    await session.initialize()

    # Register session spawning (enables tools like recipes to spawn sub-sessions)
    register_session_spawning(session)

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
    """Invoke tools from a bundle.

    Generic mechanism to list, inspect, and invoke any mounted tool.
    Tools are determined by the active bundle's mount plan.

    Examples:
        amplifier tool list                    List available tools
        amplifier tool info filesystem_read    Show tool schema
        amplifier tool invoke filesystem_read path=/tmp/test.txt
    """
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@tool.command(name="list")
@click.option("--bundle", "-b", help="Bundle to use (default: active bundle)")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.option(
    "--modules", "-m", is_flag=True, help="Show module names instead of mounted tools"
)
def tool_list(bundle: str | None, output: str, modules: bool):
    """List available tools from the active bundle.

    By default, shows the actual tool names that can be invoked (e.g., read_file,
    write_file). Use --modules to see tool module names instead (e.g., tool-filesystem).
    """
    # Determine bundle to use
    use_bundle, default_bundle, _unused = _should_use_bundle()

    # Explicit flags override auto-detection
    if bundle:
        use_bundle = True
        default_bundle = bundle

    if use_bundle:
        # Bundle path (primary)
        bundle_name = default_bundle or "foundation"

        if modules:
            # For bundles, --modules is not supported (bundles don't expose module-level info the same way)
            console.print(
                "[yellow]--modules flag not supported with bundles. Showing mounted tools.[/yellow]"
            )

        # Show actual mounted tool names
        console.print(f"[dim]Mounting tools from bundle '{bundle_name}'...[/dim]")

        try:
            tools = asyncio.run(_get_mounted_tools_from_bundle_async(bundle_name))
        except Exception as e:
            console.print(f"[red]Error mounting tools:[/red] {e}")
            sys.exit(1)

        if not tools:
            console.print(
                f"[yellow]No tools mounted from bundle '{bundle_name}'[/yellow]"
            )
            return

        if output == "json":
            result = {
                "bundle": bundle_name,
                "tools": [
                    {"name": t["name"], "description": t["description"]} for t in tools
                ],
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
        console.print(
            "\n[dim]Use 'amplifier tool invoke <name> key=value ...' to invoke a tool[/dim]"
        )


@tool.command(name="info")
@click.argument("tool_name")
@click.option("--bundle", "-b", help="Bundle to use (default: active bundle)")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
@click.option(
    "--module",
    "-m",
    is_flag=True,
    help="Look up by module name instead of mounted tool name",
)
def tool_info(tool_name: str, bundle: str | None, output: str, module: bool):
    """Show detailed information about a tool.

    By default, looks up the actual mounted tool by name (e.g., read_file).
    Use --module to look up by module name instead (e.g., tool-filesystem).
    """
    # Determine bundle to use
    use_bundle, default_bundle, _unused = _should_use_bundle()

    # Explicit flags override auto-detection
    if bundle:
        use_bundle = True
        default_bundle = bundle

    if use_bundle:
        # Bundle path (primary)
        bundle_name = default_bundle or "foundation"

        if module:
            # For bundles, --module is not supported
            console.print(
                "[yellow]--module flag not supported with bundles. Looking up mounted tool.[/yellow]"
            )

        # Look up actual mounted tool
        console.print(f"[dim]Mounting tools to get info for '{tool_name}'...[/dim]")

        try:
            tools = asyncio.run(_get_mounted_tools_from_bundle_async(bundle_name))
        except Exception as e:
            console.print(f"[red]Error mounting tools:[/red] {e}")
            sys.exit(1)

        found_tool = next((t for t in tools if t["name"] == tool_name), None)

        if not found_tool:
            console.print(
                f"[red]Error:[/red] Tool '{tool_name}' not found in bundle '{bundle_name}'"
            )
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

        console.print(
            Panel(panel_content, title=f"Tool: {tool_name}", border_style="cyan")
        )
        console.print(
            "\n[dim]Usage: amplifier tool invoke " + tool_name + " key=value ...[/dim]"
        )


@tool.command(name="invoke")
@click.argument("tool_name")
@click.argument("args", nargs=-1)
@click.option("--bundle", "-b", help="Bundle to use (default: auto-detect)")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
def tool_invoke(tool_name: str, args: tuple[str, ...], bundle: str | None, output: str):
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

    # Determine bundle
    if bundle:
        bundle_name = bundle
    else:
        _, bundle_name, _ = _should_use_bundle()

    # Run the invocation
    try:
        result = asyncio.run(
            _invoke_tool_from_bundle_async(bundle_name, tool_name, tool_args)
        )  # type: ignore[arg-type]
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


__all__ = ["tool"]
