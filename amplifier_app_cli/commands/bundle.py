"""Bundle management commands for the Amplifier CLI.

Bundles are an opt-in alternative to profiles for configuring Amplifier sessions.
When a bundle is active, it takes precedence over the profile system.

Per IMPLEMENTATION_PHILOSOPHY: Bundles and profiles coexist - profiles remain
the default, bundles are explicitly opted into via `amplifier bundle use`.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING
from typing import cast

import click
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..console import console
from ..lib.bundle_loader import AppBundleDiscovery
from ..paths import ScopeNotAvailableError
from ..paths import ScopeType
from ..paths import create_bundle_registry
from ..paths import create_config_manager
from ..paths import get_effective_scope
from ..utils.display import create_sha_text
from ..utils.display import create_status_symbol
from ..utils.display import print_legend

if TYPE_CHECKING:
    from amplifier_foundation import BundleStatus


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


def _remove_profile_from_settings(config_manager, scope_path) -> bool:
    """Remove the profile key entirely from a settings file.

    This ensures that after bundle clear, the system defaults to the
    foundation bundle (Phase 2 behavior) rather than falling back to
    a previously-set profile.

    Returns:
        True if profile was removed, False if not present or error
    """
    try:
        settings = config_manager._read_yaml(scope_path)
        if settings and "profile" in settings:
            del settings["profile"]
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
    from amplifier_app_cli.lib.legacy import Scope

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

    console.print("\n[dim]Tip: Use 'amplifier bundle clear' to revert to default (foundation bundle)[/dim]")


@bundle.command(name="clear")
@click.option("--local", "scope_flag", flag_value="local", help="Clear local settings")
@click.option("--project", "scope_flag", flag_value="project", help="Clear project settings")
@click.option("--global", "scope_flag", flag_value="global", help="Clear global settings")
@click.option("--all", "clear_all", is_flag=True, help="Clear settings from all scopes")
def bundle_clear(scope_flag: str | None, clear_all: bool):
    """Clear bundle and profile settings (reverts to default foundation bundle).

    Clears both bundle.active and profile.active settings, so the system
    defaults to the foundation bundle (Phase 2 behavior).

    Without scope flags, auto-detects and clears from wherever settings are found.
    Use --all to clear from all scopes.
    """
    from amplifier_app_cli.lib.legacy import Scope

    config_manager = create_config_manager()

    if clear_all:
        # Clear from all available scopes by removing bundle and profile keys entirely
        # This ensures the system defaults to foundation bundle (Phase 2 behavior)
        bundle_cleared = []
        profile_cleared = []
        scope_paths = [
            (Scope.LOCAL, config_manager.paths.local, "local"),
            (Scope.PROJECT, config_manager.paths.project, "project"),
            (Scope.USER, config_manager.paths.user, "user"),
        ]
        for scope, path, name in scope_paths:
            if config_manager.is_scope_available(scope):
                if _remove_bundle_from_settings(config_manager, path):
                    bundle_cleared.append(name)
                if _remove_profile_from_settings(config_manager, path):
                    profile_cleared.append(name)

        if bundle_cleared:
            console.print(f"[green]✓ Cleared bundle settings from: {', '.join(bundle_cleared)}[/green]")
        if profile_cleared:
            console.print(f"[green]✓ Cleared profile settings from: {', '.join(profile_cleared)}[/green]")
        if not bundle_cleared and not profile_cleared:
            console.print("[yellow]No bundle or profile settings found to clear[/yellow]")

        console.print("[green]Now using default: foundation bundle[/green]")
        return

    # If no scope specified, auto-detect which scope has bundle or profile settings
    if scope_flag is None:
        detected_scope = _find_bundle_or_profile_scope(config_manager)
        if detected_scope is None:
            console.print("[yellow]No bundle or profile settings found in any scope[/yellow]")
            console.print("[dim]Already using default: foundation bundle[/dim]")
            return
        scope = detected_scope
        console.print(f"[dim]Auto-detected settings in {scope} scope[/dim]")
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

    # Remove bundle and profile keys entirely to default to foundation bundle (Phase 2)
    bundle_removed = _remove_bundle_from_settings(config_manager, scope_path)
    profile_removed = _remove_profile_from_settings(config_manager, scope_path)

    if not bundle_removed and not profile_removed:
        console.print(f"[yellow]No bundle or profile setting in {scope} scope[/yellow]")
        return

    if bundle_removed:
        console.print(f"[green]✓ Cleared bundle from {scope} scope[/green]")
    if profile_removed:
        console.print(f"[green]✓ Cleared profile from {scope} scope[/green]")

    # Check if any bundle or profile is still active at other scopes
    merged = config_manager.get_merged_settings()
    bundle_settings = merged.get("bundle", {})
    remaining_bundle = bundle_settings.get("active") if isinstance(bundle_settings, dict) else None
    remaining_profile = config_manager.get_active_profile()

    if remaining_bundle:
        console.print(f"[dim]Bundle '{remaining_bundle}' still active from another scope[/dim]")
    elif remaining_profile:
        console.print(f"[dim]Profile '{remaining_profile}' still active from another scope[/dim]")
        console.print("[dim]Use --all to clear from all scopes[/dim]")
    else:
        console.print("[green]Now using default: foundation bundle[/green]")


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

        console.print("\n[dim]Use 'amplifier bundle clear' to revert to default (foundation bundle)[/dim]")
    else:
        # Check if a profile is explicitly set (backward compatibility)
        active_profile = config_manager.get_active_profile()
        if active_profile:
            console.print("[bold]Mode:[/bold] Profile (deprecated)")
            console.print(f"[bold]Active profile:[/bold] {active_profile}")

            warning_text = (
                "[yellow bold]⚠ Profiles are deprecated.[/yellow bold]\n\n"
                "Use [cyan]amplifier bundle clear[/cyan] to switch to bundles.\n\n"
                "[dim]Migration guide for developers:[/dim]\n"
                "[link=https://github.com/microsoft/amplifier/blob/main/docs/MIGRATION_COLLECTIONS_TO_BUNDLES.md]"
                "https://github.com/microsoft/amplifier/blob/main/docs/MIGRATION_COLLECTIONS_TO_BUNDLES.md[/link]"
            )
            console.print()
            console.print(Panel(warning_text, border_style="yellow", title="Deprecated", title_align="left"))
        else:
            # No explicit bundle or profile - will default to foundation bundle
            console.print("[bold]Mode:[/bold] Bundle (default)")
            console.print("[bold]Active bundle:[/bold] foundation (default)")
            console.print("\n[dim]Use 'amplifier bundle use <name>' to switch to a different bundle[/dim]")


def _find_bundle_or_profile_scope(config_manager) -> str | None:
    """Find which scope has a bundle or profile setting (for auto-clear).

    Checks for both bundle.active and profile.active settings.
    Returns the first scope where either is found.
    """
    from amplifier_app_cli.lib.legacy import Scope

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
            if settings:
                # Check for bundle.active
                if "bundle" in settings and settings["bundle"].get("active"):
                    return name
                # Check for profile.active
                if "profile" in settings and settings["profile"].get("active"):
                    return name
        except Exception:
            pass

    return None


def _get_bundle_source_scope(config_manager) -> str:
    """Determine which scope the active bundle comes from."""
    from amplifier_app_cli.lib.legacy import Scope

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
@click.argument("uri")
@click.option("--name", "-n", "name_override", help="Custom name for the bundle (default: from bundle metadata)")
def bundle_add(uri: str, name_override: str | None):
    """Add a bundle to the registry for discovery.

    URI is the location of the bundle (git+https://, file://, etc.).
    The bundle name is automatically extracted from the bundle's metadata.
    Use --name to specify a custom alias instead.

    Examples:

        \b
        # Auto-derives name from bundle metadata
        amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-recipes@main

        \b
        # Use custom alias
        amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-recipes@main --name my-recipes

        \b
        # Local bundle
        amplifier bundle add file:///path/to/bundle
    """
    from amplifier_foundation import load_bundle

    from ..lib.bundle_loader import user_registry

    # Fetch and parse bundle to extract name from metadata
    console.print(f"[dim]Fetching bundle from {uri}...[/dim]")

    try:
        # Use load_bundle to resolve URI and load bundle metadata
        bundle = asyncio.run(load_bundle(uri, auto_include=False))
        bundle_name = bundle.name
        bundle_version = bundle.version

        if not bundle_name:
            console.print("[red]Error:[/red] Bundle has no name in its metadata")
            console.print("  Use --name to specify a name manually")
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to fetch bundle: {e}")
        console.print("  Check the URI and try again")
        sys.exit(1)

    # Use override name if provided, otherwise use name from metadata
    name = name_override or bundle_name

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
    if bundle_version:
        console.print(f"  Version: {bundle_version}")
    if name_override and name_override != bundle_name:
        console.print(f"  [dim](Bundle's canonical name: {bundle_name})[/dim]")
    console.print("\n[dim]Use 'amplifier bundle list' to see all bundles[/dim]")
    console.print(f"[dim]Use 'amplifier bundle use {name}' to activate this bundle[/dim]")


@bundle.command(name="remove")
@click.argument("name")
def bundle_remove(name: str):
    """Remove a bundle from the user registry.

    This removes the bundle from both the user registry and the persisted
    foundation registry. Does not delete cached files.
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

    # Remove from user registry (CLI layer)
    removed_from_user = user_registry.remove_bundle(name)

    # Remove from foundation registry (persistence layer)
    registry = create_bundle_registry()
    removed_from_foundation = registry.unregister(name)

    if removed_from_user or removed_from_foundation:
        console.print(f"[green]✓ Removed bundle '{name}'[/green]")
    else:
        console.print(f"[yellow]Bundle '{name}' not found[/yellow]")
        console.print("\nUser-added bundles can be seen with 'amplifier bundle list'")


@bundle.command(name="update")
@click.argument("name", required=False)
@click.option("--all", "update_all", is_flag=True, help="Update all discovered bundles")
@click.option("--check", "check_only", is_flag=True, help="Only check for updates, don't apply")
@click.option("--yes", "-y", "auto_confirm", is_flag=True, help="Auto-confirm update without prompting")
@click.option("--source", "specific_source", help="Update only a specific source URI")
def bundle_update(
    name: str | None, update_all: bool, check_only: bool, auto_confirm: bool, specific_source: str | None
):
    """Check for and apply updates to bundle sources.

    By default, checks and updates the currently active bundle.
    Specify a bundle name to check a different bundle.
    Use --all to check/update all discovered bundles.

    The update process has two phases:
    1. Check status (no side effects) - shows what updates are available
    2. Refresh (side effects) - downloads updates from remote sources

    Examples:

        amplifier bundle update              # Check and update active bundle
        amplifier bundle update --check      # Only check, don't update
        amplifier bundle update foundation   # Check specific bundle
        amplifier bundle update -y           # Update without prompting
        amplifier bundle update --all        # Check and update all bundles
        amplifier bundle update --all --check # Check all bundles without updating
    """
    if update_all:
        asyncio.run(_bundle_update_all_async(check_only, auto_confirm))
    else:
        asyncio.run(_bundle_update_async(name, check_only, auto_confirm, specific_source))


async def _bundle_update_async(
    name: str | None, check_only: bool, auto_confirm: bool, specific_source: str | None
) -> None:
    """Async implementation of bundle update command."""
    from amplifier_foundation import check_bundle_status
    from amplifier_foundation import update_bundle

    config_manager = create_config_manager()
    registry = create_bundle_registry()

    # Determine which bundle to check
    if name:
        bundle_name = name
    else:
        # Use active bundle
        merged = config_manager.get_merged_settings()
        bundle_settings = merged.get("bundle", {})
        bundle_name = bundle_settings.get("active") if isinstance(bundle_settings, dict) else None

        if not bundle_name:
            console.print("[yellow]No active bundle.[/yellow]")
            console.print("\nEither specify a bundle name or set an active bundle:")
            console.print("  amplifier bundle update <name>")
            console.print("  amplifier bundle use <name>")
            sys.exit(1)

    # Load the bundle
    console.print(f"[bold]Checking bundle:[/bold] {bundle_name}")
    try:
        loaded = await registry.load(bundle_name)
        if isinstance(loaded, dict):
            console.print(f"[red]Error:[/red] Expected single bundle, got dict for '{bundle_name}'")
            sys.exit(1)
        bundle_obj = loaded
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Bundle '{bundle_name}' not found")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/red] Failed to load bundle: {exc}")
        sys.exit(1)

    # Check status
    console.print("\n[dim]Checking for updates...[/dim]")
    status: BundleStatus = await check_bundle_status(bundle_obj)

    # Display status table
    _display_bundle_status(status)

    # Summary
    console.print(f"\n{status.summary}")

    if not status.has_updates:
        console.print("\n[green]All sources are up to date.[/green]")
        return

    if check_only:
        console.print("\n[dim](--check flag: skipping refresh)[/dim]")
        return

    # Confirm update
    if not auto_confirm:
        update_count = len(status.updateable_sources)
        if specific_source:
            console.print(f"\n[yellow]Update specific source:[/yellow] {specific_source}")
        else:
            console.print(f"\n[yellow]Ready to update {update_count} source(s)[/yellow]")

        confirm = click.confirm("Proceed with update?", default=True)
        if not confirm:
            console.print("[dim]Update cancelled.[/dim]")
            return

    # Perform refresh
    console.print("\n[bold]Refreshing sources...[/bold]")
    try:
        if specific_source:
            await update_bundle(bundle_obj, selective=[specific_source])
            console.print(f"[green]✓ Updated:[/green] {specific_source}")
        else:
            await update_bundle(bundle_obj)
            console.print(f"[green]✓ Updated {len(status.updateable_sources)} source(s)[/green]")
    except Exception as exc:
        console.print(f"[red]Error during update:[/red] {exc}")
        sys.exit(1)

    console.print("\n[green]Bundle update complete![/green]")


async def _bundle_update_all_async(check_only: bool, auto_confirm: bool) -> None:
    """Check and update all discovered bundles."""
    from amplifier_foundation import check_bundle_status
    from amplifier_foundation import update_bundle

    discovery = AppBundleDiscovery()
    registry = create_bundle_registry()

    # Get all bundles
    bundle_names = discovery.list_bundles()

    if not bundle_names:
        console.print("[yellow]No bundles found.[/yellow]")
        return

    console.print("Checking for updates...")
    console.print("  Checking bundles...")

    # Track results
    results: dict[str, BundleStatus] = {}
    errors: dict[str, str] = {}
    bundles_with_updates: list[str] = []

    # Check each bundle
    for bundle_name in bundle_names:
        try:
            loaded = await registry.load(bundle_name)
            if isinstance(loaded, dict):
                errors[bundle_name] = "Expected single bundle, got dict"
                continue
            bundle_obj = loaded

            status: BundleStatus = await check_bundle_status(bundle_obj)
            results[bundle_name] = status

            if status.has_updates:
                bundles_with_updates.append(bundle_name)

        except FileNotFoundError:
            errors[bundle_name] = "Bundle not found"
        except Exception as exc:
            errors[bundle_name] = str(exc)

    # Display sources table for each bundle (matching amplifier update style)
    for bundle_name in sorted(bundle_names):
        if bundle_name in results:
            status = results[bundle_name]
            if status.sources:
                table = Table(title=f"Bundle: {bundle_name}", show_header=True, header_style="bold cyan")
                table.add_column("Source", style="green")
                table.add_column("Cached", style="dim", justify="right")
                table.add_column("Remote", style="dim", justify="right")
                table.add_column("", width=1, justify="center")

                for source in sorted(status.sources, key=lambda x: x.source_uri):
                    # Extract module name from source URI for cleaner display
                    source_name = source.source_uri
                    if "/" in source_name:
                        # Get last path component, strip common prefixes
                        source_name = source_name.split("/")[-1]
                        if source_name.startswith("amplifier-module-"):
                            source_name = source_name[17:]  # Remove "amplifier-module-"
                        elif "@" in source_name:
                            source_name = source_name.split("@")[0]

                    status_symbol = create_status_symbol(source.cached_commit, source.remote_commit)

                    table.add_row(
                        source_name,
                        create_sha_text(source.cached_commit),
                        create_sha_text(source.remote_commit),
                        status_symbol,
                    )

                console.print()
                console.print(table)

        elif bundle_name in errors:
            console.print()
            console.print(f"[red]Bundle: {bundle_name}[/red]")
            console.print(f"  [red]Error:[/red] {errors[bundle_name]}")

    console.print()
    print_legend()

    # Show errors if any
    if errors:
        console.print()
        for name, error in errors.items():
            console.print(f"[red]Error checking {name}:[/red] {error}")

    # Summary
    total_updates = len(bundles_with_updates)
    if total_updates == 0:
        console.print("[green]✓ All bundles up to date[/green]")
        return

    if check_only:
        console.print()
        console.print(f"[yellow]{total_updates} bundle(s) have updates available[/yellow]")
        console.print("Run [cyan]amplifier bundle update --all[/cyan] to install")
        return

    # Show what will be updated
    console.print()
    console.print("Run amplifier bundle update --all to install")
    console.print()
    for name in bundles_with_updates:
        status = results[name]
        console.print(f"  • Update {name} ({len(status.updateable_sources)} source(s))")

    # Confirm update
    console.print()
    if not auto_confirm:
        confirm = click.confirm("Proceed with update?", default=True)
        if not confirm:
            console.print("[dim]Update cancelled.[/dim]")
            return

    # Perform updates
    console.print()
    console.print("Updating...")
    console.print()

    updated_count = 0
    update_errors: dict[str, str] = {}

    for bundle_name in bundles_with_updates:
        try:
            loaded = await registry.load(bundle_name)
            if isinstance(loaded, dict):
                update_errors[bundle_name] = "Expected single bundle, got dict"
                continue
            bundle_obj = loaded

            await update_bundle(bundle_obj)
            updated_count += 1
            console.print(f"[green]✓[/green] {bundle_name}")
        except Exception as exc:
            update_errors[bundle_name] = str(exc)
            console.print(f"[red]✗[/red] {bundle_name}: {exc}")

    # Final summary
    console.print()
    if update_errors:
        console.print(f"[yellow]✓ Update complete ({updated_count} updated, {len(update_errors)} failed)[/yellow]")
    else:
        console.print("[green]✓ Update complete[/green]")
        for name in bundles_with_updates:
            console.print(f"  [green]✓[/green] {name}")


def _display_bundle_status(status: BundleStatus) -> None:
    """Display bundle status in a formatted table."""
    if not status.sources:
        console.print("[dim]No sources to check.[/dim]")
        return

    table = Table(title=f"Bundle Sources: {status.bundle_name}", show_header=True, header_style="bold cyan")
    table.add_column("Source", style="dim", no_wrap=False, max_width=60)
    table.add_column("Status", justify="center")
    table.add_column("Details", style="dim")

    for source in status.sources:
        # Truncate long URIs
        uri = source.source_uri
        if len(uri) > 57:
            uri = uri[:54] + "..."

        # Status indicator
        if source.has_update:
            status_text = Text("🔄 Update", style="yellow")
        elif source.has_update is False:
            status_text = Text("✅ Current", style="green")
        else:
            status_text = Text("❓ Unknown", style="dim")

        # Details
        details_parts = []
        if source.cached_commit and source.remote_commit:
            local_short = source.cached_commit[:7]
            remote_short = source.remote_commit[:7]
            if source.has_update:
                details_parts.append(f"{local_short} → {remote_short}")
        elif source.is_pinned:
            details_parts.append("pinned")
        elif source.error:
            details_parts.append(f"error: {source.error[:30]}")

        details = " ".join(details_parts) if details_parts else source.summary[:40]

        table.add_row(uri, status_text, details)

    console.print(table)


__all__ = ["bundle"]
