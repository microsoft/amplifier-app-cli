"""Bundle management commands for the Amplifier CLI.

Bundles are an opt-in alternative to profiles for configuring Amplifier sessions.
When a bundle is active, it takes precedence over the profile system.

Per IMPLEMENTATION_PHILOSOPHY: Bundles and profiles coexist - profiles remain
the default, bundles are explicitly opted into via `amplifier bundle use`.
"""

from __future__ import annotations

import asyncio
import sys
from typing import cast

import click
from rich.table import Table

from ..console import console
from ..lib.bundle_loader import AppBundleDiscovery
from ..paths import ScopeNotAvailableError
from ..paths import ScopeType
from ..paths import create_bundle_registry
from ..paths import create_config_manager
from ..paths import get_effective_scope


def _remove_bundle_from_settings(config_manager, scope_path) -> bool:
    """Remove the bundle key entirely from a settings file.

    This is better than setting bundle: null because it allows proper
    inheritance from lower-priority scopes.

    Returns:
        True if bundle was removed, False if not present or error
    """
    try:
        settings = config_manager._read_yaml(scope_path)
        if settings and "bundle" in settings:
            del settings["bundle"]
            config_manager._write_yaml(scope_path, settings)
            return True
    except Exception:
        pass
    return False


@click.group(invoke_without_command=True)
@click.pass_context
def bundle(ctx: click.Context):
    """Manage Amplifier bundles (opt-in alternative to profiles)."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@bundle.command(name="list")
def bundle_list():
    """List all available bundles."""
    config_manager = create_config_manager()
    discovery = AppBundleDiscovery()

    bundles = discovery.list_bundles()
    settings = config_manager.get_merged_settings() or {}
    bundle_settings = settings.get("bundle") or {}  # Handle bundle: null case
    active_bundle = bundle_settings.get("active")

    if not bundles:
        console.print("[yellow]No bundles found.[/yellow]")
        console.print("\nBundles can be found in:")
        console.print("  • .amplifier/bundles/ (project)")
        console.print("  • ~/.amplifier/bundles/ (user)")
        console.print("  • Installed packages (e.g., amplifier-foundation)")
        return

    table = Table(title="Available Bundles", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Location", style="yellow")
    table.add_column("Status")

    for bundle_name in bundles:
        uri = discovery.find(bundle_name)
        location = _format_location(uri)

        status_parts: list[str] = []
        if bundle_name == active_bundle:
            status_parts.append("[bold green]active[/bold green]")

        status = ", ".join(status_parts) if status_parts else ""
        table.add_row(bundle_name, location, status)

    console.print(table)

    # Show current mode
    if active_bundle:
        console.print(f"\n[dim]Mode: Bundle ({active_bundle})[/dim]")
    else:
        console.print("\n[dim]Mode: Profile (default)[/dim]")


def _format_location(uri: str | None) -> str:
    """Format a bundle URI for display."""
    if not uri:
        return "unknown"

    if uri.startswith("file://"):
        path = uri[7:]
        # Shorten common prefixes
        home = str(__import__("pathlib").Path.home())
        if path.startswith(home):
            return "~" + path[len(home) :]
        return path
    return uri


@bundle.command(name="show")
@click.argument("name")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed configuration")
def bundle_show(name: str, detailed: bool):
    """Show details of a specific bundle."""
    registry = create_bundle_registry()

    try:
        loaded = asyncio.run(registry.load(name))
        # registry.load() returns Bundle | dict[str, Bundle]
        if isinstance(loaded, dict):
            raise ValueError(f"Expected single bundle, got dict for '{name}'")
        bundle_obj = loaded
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Bundle '{name}' not found")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/red] Failed to load bundle: {exc}")
        sys.exit(1)

    # Basic info
    console.print(f"[bold]Bundle:[/bold] {bundle_obj.name}")
    if bundle_obj.version:
        console.print(f"[bold]Version:[/bold] {bundle_obj.version}")
    if bundle_obj.description:
        console.print(f"[bold]Description:[/bold] {bundle_obj.description}")
    console.print(f"[bold]Location:[/bold] {bundle_obj.base_path}")

    # Mount plan summary
    mount_plan = bundle_obj.to_mount_plan()

    console.print("\n[bold]Configuration:[/bold]")

    # Session
    if "session" in mount_plan:
        session = mount_plan["session"]
        console.print("\n[bold]Session:[/bold]")
        if "orchestrator" in session:
            orch = session["orchestrator"]
            if isinstance(orch, dict):
                console.print(f"  orchestrator: {orch.get('module', 'unknown')}")
            else:
                console.print(f"  orchestrator: {orch}")
        if "context" in session:
            ctx = session["context"]
            if isinstance(ctx, dict):
                console.print(f"  context: {ctx.get('module', 'unknown')}")
            else:
                console.print(f"  context: {ctx}")

    # Providers
    providers = mount_plan.get("providers", [])
    if providers:
        console.print(f"\n[bold]Providers:[/bold] ({len(providers)})")
        for p in providers:
            if isinstance(p, dict):
                module = p.get("module", "unknown")
                console.print(f"  • {module}")
                if detailed and p.get("config"):
                    for key, value in p["config"].items():
                        console.print(f"      {key}: {value}")
    else:
        console.print("\n[bold]Providers:[/bold] (none - provider-agnostic bundle)")

    # Tools
    tools = mount_plan.get("tools", [])
    if tools:
        console.print(f"\n[bold]Tools:[/bold] ({len(tools)})")
        for t in tools:
            if isinstance(t, dict):
                console.print(f"  • {t.get('module', 'unknown')}")
            else:
                console.print(f"  • {t}")

    # Hooks
    hooks = mount_plan.get("hooks", [])
    if hooks:
        console.print(f"\n[bold]Hooks:[/bold] ({len(hooks)})")
        for h in hooks:
            if isinstance(h, dict):
                console.print(f"  • {h.get('module', 'unknown')}")
            else:
                console.print(f"  • {h}")

    # Agents
    agents = mount_plan.get("agents", {})
    if agents:
        console.print(f"\n[bold]Agents:[/bold] ({len(agents)})")
        for agent_name in sorted(agents.keys()):
            console.print(f"  • {agent_name}")

    # Includes (if available)
    if bundle_obj.includes:
        console.print(f"\n[bold]Includes:[/bold] ({len(bundle_obj.includes)})")
        for inc in bundle_obj.includes:
            console.print(f"  • {inc}")


@bundle.command(name="use")
@click.argument("name")
@click.option("--local", "scope_flag", flag_value="local", help="Set locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Set for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Set globally (all projects)")
def bundle_use(name: str, scope_flag: str | None):
    """Set a bundle as active (opts out of profile system).

    When a bundle is active, it takes precedence over profiles for session
    configuration. Use 'amplifier bundle clear' to revert to profiles.
    """
    from amplifier_config import Scope

    # Verify bundle exists
    discovery = AppBundleDiscovery()
    uri = discovery.find(name)
    if not uri:
        console.print(f"[red]Error:[/red] Bundle '{name}' not found")
        console.print("\nAvailable bundles:")
        for b in discovery.list_bundles():
            console.print(f"  • {b}")
        sys.exit(1)

    config_manager = create_config_manager()

    # Validate scope availability
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="global",
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope (~/.amplifier/settings.yaml)"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        sys.exit(1)

    # Set the bundle
    if scope == "local":
        config_manager.update_settings({"bundle": {"active": name}}, scope=Scope.LOCAL)
        console.print(f"[green]✓ Using bundle '{name}' locally[/green]")
        console.print("  File: .amplifier/settings.local.yaml")
    elif scope == "project":
        config_manager.update_settings({"bundle": {"active": name}}, scope=Scope.PROJECT)
        console.print(f"[green]✓ Set bundle '{name}' as project default[/green]")
        console.print("  File: .amplifier/settings.yaml")
        console.print("  [yellow]Remember to commit .amplifier/settings.yaml[/yellow]")
    elif scope == "global":
        config_manager.update_settings({"bundle": {"active": name}}, scope=Scope.USER)
        console.print(f"[green]✓ Set bundle '{name}' globally[/green]")
        console.print("  File: ~/.amplifier/settings.yaml")

    console.print("\n[dim]Tip: Use 'amplifier bundle clear' to revert to profile mode[/dim]")


@bundle.command(name="clear")
@click.option("--local", "scope_flag", flag_value="local", help="Clear local bundle setting")
@click.option("--project", "scope_flag", flag_value="project", help="Clear project bundle setting")
@click.option("--global", "scope_flag", flag_value="global", help="Clear global bundle setting")
@click.option("--all", "clear_all", is_flag=True, help="Clear bundle settings from all scopes")
def bundle_clear(scope_flag: str | None, clear_all: bool):
    """Clear active bundle (reverts to profile mode).

    Without scope flags, auto-detects and clears the bundle from wherever it's set.
    Use --all to clear bundle settings from all scopes.
    """
    from amplifier_config import Scope

    config_manager = create_config_manager()

    if clear_all:
        # Clear from all available scopes by removing the bundle key entirely
        cleared = []
        scope_paths = [
            (Scope.LOCAL, config_manager.paths.local, "local"),
            (Scope.PROJECT, config_manager.paths.project, "project"),
            (Scope.USER, config_manager.paths.user, "user"),
        ]
        for scope, path, name in scope_paths:
            if config_manager.is_scope_available(scope) and _remove_bundle_from_settings(config_manager, path):
                cleared.append(name)

        if cleared:
            console.print(f"[green]✓ Cleared bundle settings from: {', '.join(cleared)}[/green]")
        else:
            console.print("[yellow]No bundle settings found to clear[/yellow]")

        console.print("[green]Now using profile mode[/green]")
        return

    # If no scope specified, auto-detect which scope has the bundle
    if scope_flag is None:
        detected_scope = _find_bundle_scope(config_manager)
        if detected_scope is None:
            console.print("[yellow]No active bundle found in any scope[/yellow]")
            console.print("[dim]Already using profile mode[/dim]")
            return
        scope = detected_scope
        console.print(f"[dim]Auto-detected bundle in {scope} scope[/dim]")
    else:
        # Clear from specific scope
        try:
            scope, was_fallback = get_effective_scope(
                cast(ScopeType, scope_flag),
                config_manager,
                default_scope="global",
            )
            if was_fallback:
                console.print("[yellow]Note:[/yellow] Running from home directory, using global scope")
        except ScopeNotAvailableError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            sys.exit(1)

    scope_path = {
        "local": config_manager.paths.local,
        "project": config_manager.paths.project,
        "global": config_manager.paths.user,
    }[scope]

    # Remove bundle key entirely (not set to null) to allow inheritance from lower scopes
    if not _remove_bundle_from_settings(config_manager, scope_path):
        console.print(f"[yellow]No bundle setting in {scope} scope[/yellow]")
        return

    console.print(f"[green]✓ Cleared bundle from {scope} scope[/green]")

    # Check if any bundle is still active at other scopes
    merged = config_manager.get_merged_settings()
    bundle_settings = merged.get("bundle", {})
    remaining_bundle = bundle_settings.get("active") if isinstance(bundle_settings, dict) else None
    if remaining_bundle:
        console.print(f"[dim]Bundle '{remaining_bundle}' still active from another scope[/dim]")
    else:
        console.print("[green]Now using profile mode[/green]")


@bundle.command(name="current")
def bundle_current():
    """Show the currently active bundle and configuration mode."""
    config_manager = create_config_manager()
    merged = config_manager.get_merged_settings()

    bundle_settings = merged.get("bundle", {})
    active_bundle = bundle_settings.get("active") if isinstance(bundle_settings, dict) else None

    if active_bundle:
        # Determine source scope
        source = _get_bundle_source_scope(config_manager)

        console.print(f"[bold green]Active bundle:[/bold green] {active_bundle}")
        console.print("[bold]Mode:[/bold] Bundle")
        console.print(f"[bold]Source:[/bold] {source}")

        # Show bundle info
        discovery = AppBundleDiscovery()
        uri = discovery.find(active_bundle)
        if uri:
            console.print(f"[bold]Location:[/bold] {_format_location(uri)}")

        console.print("\n[dim]Use 'amplifier bundle clear' to revert to profile mode[/dim]")
    else:
        console.print("[bold]Mode:[/bold] Profile (default)")
        console.print("[yellow]No active bundle[/yellow]")

        # Show active profile for context
        active_profile = config_manager.get_active_profile()
        if active_profile:
            console.print(f"[bold]Active profile:[/bold] {active_profile}")
        else:
            from ..data.profiles import get_system_default_profile

            console.print(f"[bold]Active profile:[/bold] {get_system_default_profile()} (system default)")

        console.print("\n[dim]Use 'amplifier bundle use <name>' to switch to bundle mode[/dim]")


def _find_bundle_scope(config_manager) -> str | None:
    """Find which scope has a bundle setting (for auto-clear)."""
    from amplifier_config import Scope

    # Check scopes in precedence order (local first, as that's what user likely wants to clear)
    scope_paths = [
        (Scope.LOCAL, config_manager.paths.local, "local"),
        (Scope.PROJECT, config_manager.paths.project, "project"),
        (Scope.USER, config_manager.paths.user, "global"),
    ]
    for scope, path, name in scope_paths:
        if not config_manager.is_scope_available(scope):  # type: ignore[attr-defined]
            continue
        try:
            settings = config_manager._read_yaml(path)
            if settings and "bundle" in settings and settings["bundle"].get("active"):
                return name
        except Exception:
            pass

    return None


def _get_bundle_source_scope(config_manager) -> str:
    """Determine which scope the active bundle comes from."""
    from amplifier_config import Scope

    # Check scopes in precedence order
    scope_paths = [
        (Scope.LOCAL, config_manager.paths.local, ".amplifier/settings.local.yaml"),
        (Scope.PROJECT, config_manager.paths.project, ".amplifier/settings.yaml"),
        (Scope.USER, config_manager.paths.user, "~/.amplifier/settings.yaml"),
    ]
    for scope, path, label in scope_paths:
        if not config_manager.is_scope_available(scope):  # type: ignore[attr-defined]
            continue
        try:
            settings = config_manager._read_yaml(path)
            if settings and "bundle" in settings and settings["bundle"].get("active"):
                return label
        except Exception:
            pass

    return "unknown"


@bundle.command(name="add")
@click.argument("name")
@click.argument("uri")
def bundle_add(name: str, uri: str):
    """Add a bundle to the registry for discovery.

    NAME is the local name to use for this bundle.
    URI is the location of the bundle (git+https://, file://, etc.).

    Examples:

        amplifier bundle add recipes git+https://github.com/microsoft/amplifier-bundle-recipes@main

        amplifier bundle add my-local file:///path/to/bundle
    """
    from ..lib.bundle_loader import user_registry

    # Check if name already exists
    existing = user_registry.get_bundle(name)
    if existing:
        console.print(f"[yellow]Warning:[/yellow] Bundle '{name}' already registered")
        console.print(f"  Current URI: {existing['uri']}")
        console.print(f"  Added: {existing['added_at']}")
        console.print("\nUpdating to new URI...")

    # Add to registry
    user_registry.add_bundle(name, uri)
    console.print(f"[green]✓ Added bundle '{name}'[/green]")
    console.print(f"  URI: {uri}")
    console.print("\n[dim]Use 'amplifier bundle list' to see all bundles[/dim]")
    console.print(f"[dim]Use 'amplifier bundle use {name}' to activate this bundle[/dim]")


@bundle.command(name="remove")
@click.argument("name")
def bundle_remove(name: str):
    """Remove a bundle from the user registry.

    This only removes the registry entry, not any cached files.
    Does not affect well-known bundles like 'foundation'.

    Example:

        amplifier bundle remove recipes
    """
    from ..lib.bundle_loader import user_registry
    from ..lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES

    # Check if this is a well-known bundle
    if name in WELL_KNOWN_BUNDLES:
        console.print(f"[red]Error:[/red] Cannot remove well-known bundle '{name}'")
        console.print("  Well-known bundles are built into amplifier")
        sys.exit(1)

    # Try to remove
    if user_registry.remove_bundle(name):
        console.print(f"[green]✓ Removed bundle '{name}' from registry[/green]")
    else:
        console.print(f"[yellow]Bundle '{name}' not found in user registry[/yellow]")
        console.print("\nUser-added bundles can be seen with 'amplifier bundle list'")


__all__ = ["bundle"]
