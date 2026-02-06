"""Bundle management commands for the Amplifier CLI.

Bundles are the configuration format for configuring Amplifier sessions.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING
from typing import cast

import click
from rich.table import Table
from rich.text import Text

from ..console import console
from ..lib.bundle_loader import AppBundleDiscovery
from ..lib.settings import AppSettings
from ..paths import ScopeNotAvailableError
from ..paths import ScopeType
from ..paths import create_bundle_registry
from ..paths import get_effective_scope
from ..utils.display import create_sha_text
from ..utils.display import create_status_symbol
from ..utils.display import print_legend

if TYPE_CHECKING:
    from amplifier_foundation import BundleStatus


def _remove_bundle_from_settings(app_settings: AppSettings, scope: ScopeType) -> bool:
    """Remove the active bundle setting from a settings file.

    Only removes bundle.active, preserving bundle.added and bundle.app.
    This allows proper inheritance from lower-priority scopes while
    keeping user-added bundles intact.

    Returns:
        True if bundle.active was removed, False if not present or error
    """
    try:
        settings = app_settings._read_scope(scope)
        if settings and "bundle" in settings and "active" in settings["bundle"]:
            del settings["bundle"]["active"]
            # Clean up empty bundle section
            if not settings["bundle"]:
                del settings["bundle"]
            app_settings._write_scope(scope, settings)
            return True
    except Exception:
        pass
    return False


@click.group(invoke_without_command=True)
@click.pass_context
def bundle(ctx: click.Context):
    """Manage Amplifier bundles (configuration format)."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@bundle.command(name="list")
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all bundles including dependencies and nested bundles",
)
def bundle_list(show_all: bool):
    """List available bundles.

    By default, shows bundles intended for user selection:
    - Well-known bundles (foundation, recipes, etc.)
    - User-added bundles (via bundle add)
    - Local bundles (in .amplifier/bundles/)

    Use --all to see everything including:
    - Dependencies loaded transitively
    - Nested bundles (behaviors, providers, etc.)
    """
    app_settings = AppSettings()
    discovery = AppBundleDiscovery()

    active_bundle = app_settings.get_active_bundle()

    if show_all:
        _show_all_bundles(discovery, active_bundle)
    else:
        _show_user_bundles(discovery, active_bundle)


def _show_user_bundles(discovery: AppBundleDiscovery, active_bundle: str | None):
    """Show bundles intended for user selection (default view)."""
    bundles = discovery.list_bundles(show_all=False)
    app_settings = AppSettings()
    app_bundles = app_settings.get_app_bundles()

    if not bundles and not app_bundles:
        console.print("[yellow]No bundles found.[/yellow]")
        console.print("\nBundles can be found in:")
        console.print("  • .amplifier/bundles/ (project)")
        console.print("  • ~/.amplifier/bundles/ (user)")
        console.print("  • Installed packages (e.g., amplifier-foundation)")
        console.print(
            "\n[dim]Use --all to see all discovered bundles including dependencies.[/dim]"
        )
        return

    table = Table(title="Available Bundles", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Location", style="yellow")
    table.add_column("Status")

    # Show regular bundles
    for bundle_name in bundles:
        uri = discovery.find(bundle_name)
        location = _format_location(uri)

        status_parts: list[str] = []
        if bundle_name == active_bundle:
            status_parts.append("[bold green]active[/bold green]")

        status = ", ".join(status_parts) if status_parts else ""
        table.add_row(bundle_name, location, status)

    # Show app bundles (always composed onto sessions)
    for app_uri in app_bundles:
        # Extract name from URI for display
        app_name = _extract_bundle_name_from_uri(app_uri)
        location = _format_location(app_uri)
        table.add_row(app_name, location, "[cyan]app[/cyan]")

    console.print(table)

    # Show current mode
    if active_bundle:
        console.print(f"\n[dim]Mode: Bundle ({active_bundle})[/dim]")
    else:
        console.print("\n[dim]Mode: No bundle active (default)[/dim]")

    console.print(
        "[dim]Use --all to see all bundles including dependencies and nested bundles.[/dim]"
    )


def _show_all_bundles(discovery: AppBundleDiscovery, active_bundle: str | None):
    """Show all bundles categorized by type (--all view)."""
    categories = discovery.get_bundle_categories()

    # Well-known bundles (built-in, always available)
    if categories["well_known"]:
        table = Table(
            title="Built-in Bundles (always available)",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Name", style="green")
        table.add_column("Location", style="yellow")
        table.add_column("In Default List", style="dim")
        table.add_column("Status")

        for bundle_info in sorted(categories["well_known"], key=lambda x: x["name"]):
            name = bundle_info["name"]
            status = "[bold green]active[/bold green]" if name == active_bundle else ""
            show = "✓" if bundle_info.get("show_in_list") == "True" else "✗"
            table.add_row(name, _format_location(bundle_info["uri"]), show, status)

        console.print(table)
        console.print()

    # User-added bundles
    if categories["user_added"]:
        table = Table(
            title="User-Added Bundles", show_header=True, header_style="bold cyan"
        )
        table.add_column("Name", style="green")
        table.add_column("Location", style="yellow")
        table.add_column("Status")

        for bundle_info in sorted(categories["user_added"], key=lambda x: x["name"]):
            name = bundle_info["name"]
            status = "[bold green]active[/bold green]" if name == active_bundle else ""
            table.add_row(name, _format_location(bundle_info["uri"]), status)

        console.print(table)
        console.print()

    # Discovered root bundles (loaded via includes/namespaces)
    if categories["dependencies"]:
        table = Table(
            title="Discovered Root Bundles (loaded via includes/namespaces)",
            show_header=True,
            header_style="bold yellow",
        )
        table.add_column("Name", style="green")
        table.add_column("Location", style="yellow")
        table.add_column("Loaded By", style="dim")

        for bundle_info in sorted(categories["dependencies"], key=lambda x: x["name"]):
            table.add_row(
                bundle_info["name"],
                _format_location(bundle_info["uri"]),
                bundle_info.get("included_by", ""),
            )

        console.print(table)
        console.print()

    # Nested bundles (behaviors, providers - part of a root bundle)
    if categories["nested_bundles"]:
        table = Table(
            title="Nested Bundles (behaviors, providers - part of a root bundle)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Name", style="dim green")
        table.add_column("Root Bundle", style="dim cyan")
        table.add_column("Location", style="dim yellow")

        for bundle_info in sorted(
            categories["nested_bundles"], key=lambda x: x["name"]
        ):
            table.add_row(
                bundle_info["name"],
                bundle_info.get("root", ""),
                _format_location(bundle_info["uri"]),
            )

        console.print(table)
        console.print()

    # Show current mode
    if active_bundle:
        console.print(f"[dim]Mode: Bundle ({active_bundle})[/dim]")
    else:
        console.print("[dim]Mode: No bundle active (default)[/dim]")

    # Footer note explaining relationship to `amplifier update`
    console.print()
    console.print(
        "[dim]ℹ️  Root bundles (Built-in + Discovered) are checked by `amplifier update`.[/dim]"
    )
    console.print(
        "[dim]   Nested bundles are updated when their root bundle is updated.[/dim]"
    )


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


def _extract_bundle_name_from_uri(uri: str) -> str:
    """Extract a display name from a bundle URI.

    Examples:
        git+https://github.com/microsoft/amplifier-bundle-modes@main -> modes
        git+https://github.com/org/my-bundle@main -> my-bundle
        file:///path/to/demo-app-bundle -> demo-app-bundle
    """
    # Handle file:// URIs
    if uri.startswith("file://"):
        path = uri[7:]
        return path.rstrip("/").split("/")[-1]

    # Handle git+ URIs: extract repo name, strip common prefixes
    if "github.com" in uri or "gitlab.com" in uri:
        # Extract repo name from URL
        # git+https://github.com/microsoft/amplifier-bundle-modes@main
        parts = uri.split("/")
        for i, part in enumerate(parts):
            if "github.com" in part or "gitlab.com" in part:
                if i + 2 < len(parts):
                    repo_name = parts[i + 2].split("@")[0].split("#")[0]
                    # Strip common prefixes
                    for prefix in ["amplifier-bundle-", "amplifier-", "bundle-"]:
                        if repo_name.startswith(prefix):
                            return repo_name[len(prefix) :]
                    return repo_name

    # Fallback: return last path segment
    return uri.split("/")[-1].split("@")[0].split("#")[0]


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
@click.option(
    "--local", "scope_flag", flag_value="local", help="Set locally (just you)"
)
@click.option(
    "--project", "scope_flag", flag_value="project", help="Set for project (team)"
)
@click.option(
    "--global", "scope_flag", flag_value="global", help="Set globally (all projects)"
)
def bundle_use(name: str, scope_flag: str | None):
    """Set a bundle as active."""
    # Verify bundle exists
    discovery = AppBundleDiscovery()
    uri = discovery.find(name)
    if not uri:
        console.print(f"[red]Error:[/red] Bundle '{name}' not found")
        console.print("\nAvailable bundles:")
        for b in discovery.list_bundles():
            console.print(f"  • {b}")
        sys.exit(1)

    app_settings = AppSettings()

    # Validate scope availability
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            app_settings,
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
    app_settings.set_active_bundle(name, scope=scope)

    if scope == "local":
        console.print(f"[green]✓ Using bundle '{name}' locally[/green]")
        console.print("  File: .amplifier/settings.local.yaml")
    elif scope == "project":
        console.print(f"[green]✓ Set bundle '{name}' as project default[/green]")
        console.print("  File: .amplifier/settings.yaml")
        console.print("  [yellow]Remember to commit .amplifier/settings.yaml[/yellow]")
    elif scope == "global":
        console.print(f"[green]✓ Set bundle '{name}' globally[/green]")
        console.print("  File: ~/.amplifier/settings.yaml")

    console.print(
        "\n[dim]Tip: Use 'amplifier bundle clear' to revert to default (foundation bundle)[/dim]"
    )


@bundle.command(name="clear")
@click.option("--local", "scope_flag", flag_value="local", help="Clear local settings")
@click.option(
    "--project", "scope_flag", flag_value="project", help="Clear project settings"
)
@click.option(
    "--global", "scope_flag", flag_value="global", help="Clear global settings"
)
@click.option("--all", "clear_all", is_flag=True, help="Clear settings from all scopes")
def bundle_clear(scope_flag: str | None, clear_all: bool):
    """Clear bundle settings (reverts to default foundation bundle).

    Without scope flags, auto-detects and clears from wherever settings are found.
    Use --all to clear from all scopes.
    """
    app_settings = AppSettings()

    if clear_all:
        bundle_cleared: list[str] = []
        scopes: list[tuple[ScopeType, str]] = [
            ("local", "local"),
            ("project", "project"),
            ("global", "global"),
        ]
        for scope, name in scopes:
            if app_settings.is_scope_available(scope):
                if _remove_bundle_from_settings(app_settings, scope):
                    bundle_cleared.append(name)

        if bundle_cleared:
            console.print(
                f"[green]✓ Cleared bundle settings from: {', '.join(bundle_cleared)}[/green]"
            )
        else:
            console.print("[yellow]No bundle settings found to clear[/yellow]")

        console.print("[green]Now using default: foundation bundle[/green]")
        return

    if scope_flag is None:
        detected_scope = _find_bundle_scope(app_settings)
        if detected_scope is None:
            console.print("[yellow]No bundle settings found in any scope[/yellow]")
            console.print("[dim]Already using default: foundation bundle[/dim]")
            return
        scope = detected_scope
        console.print(f"[dim]Auto-detected settings in {scope} scope[/dim]")
    else:
        try:
            scope, was_fallback = get_effective_scope(
                cast(ScopeType, scope_flag),
                app_settings,
                default_scope="global",
            )
            if was_fallback:
                console.print(
                    "[yellow]Note:[/yellow] Running from home directory, using global scope"
                )
        except ScopeNotAvailableError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            sys.exit(1)

    bundle_removed = _remove_bundle_from_settings(app_settings, scope)

    if not bundle_removed:
        console.print(f"[yellow]No bundle setting in {scope} scope[/yellow]")
        return

    console.print(f"[green]✓ Cleared bundle from {scope} scope[/green]")

    remaining_bundle = app_settings.get_active_bundle()

    if remaining_bundle:
        console.print(
            f"[dim]Bundle '{remaining_bundle}' still active from another scope[/dim]"
        )
        console.print("[dim]Use --all to clear from all scopes[/dim]")
    else:
        console.print("[green]Now using default: foundation bundle[/green]")


@bundle.command(name="current")
def bundle_current():
    """Show the currently active bundle and configuration mode."""
    app_settings = AppSettings()

    active_bundle = app_settings.get_active_bundle()

    if active_bundle:
        source = _get_bundle_source_scope(app_settings)

        console.print(f"[bold green]Active bundle:[/bold green] {active_bundle}")
        console.print("[bold]Mode:[/bold] Bundle")
        console.print(f"[bold]Source:[/bold] {source}")

        discovery = AppBundleDiscovery()
        uri = discovery.find(active_bundle)
        if uri:
            console.print(f"[bold]Location:[/bold] {_format_location(uri)}")

        console.print(
            "\n[dim]Use 'amplifier bundle clear' to revert to default (foundation bundle)[/dim]"
        )
    else:
        console.print("[bold]Mode:[/bold] Bundle (default)")
        console.print("[bold]Active bundle:[/bold] foundation (default)")
        console.print(
            "\n[dim]Use 'amplifier bundle use <name>' to switch to a different bundle[/dim]"
        )


def _find_bundle_scope(app_settings: AppSettings) -> ScopeType | None:
    """Find which scope has a bundle setting (for auto-clear).

    Returns the first scope where bundle.active is found.
    """
    scopes: list[ScopeType] = ["local", "project", "global"]
    for scope in scopes:
        if not app_settings.is_scope_available(scope):
            continue
        try:
            settings = app_settings._read_scope(scope)
            if settings and "bundle" in settings and settings["bundle"].get("active"):
                return scope
        except Exception:
            pass

    return None


def _get_bundle_source_scope(app_settings: AppSettings) -> str:
    """Determine which scope the active bundle comes from."""
    # Check scopes in precedence order
    scope_labels: list[tuple[ScopeType, str]] = [
        ("local", ".amplifier/settings.local.yaml"),
        ("project", ".amplifier/settings.yaml"),
        ("global", "~/.amplifier/settings.yaml"),
    ]
    for scope, label in scope_labels:
        if not app_settings.is_scope_available(scope):
            continue
        try:
            settings = app_settings._read_scope(scope)
            if settings and "bundle" in settings and settings["bundle"].get("active"):
                return label
        except Exception:
            pass

    return "unknown"


@bundle.command(name="add")
@click.argument("uri")
@click.option(
    "--name",
    "-n",
    "name_override",
    help="Custom name for the bundle (default: from bundle metadata)",
)
@click.option(
    "--app",
    is_flag=True,
    help="Add as app bundle (automatically composed with all sessions)",
)
def bundle_add(uri: str, name_override: str | None, app: bool):
    """Add a bundle to the registry for discovery.

    URI is the location of the bundle (git+https://, file://, etc.).
    The bundle name is automatically extracted from the bundle's metadata.
    Use --name to specify a custom alias instead.

    Use --app to add as an "app bundle" that is automatically composed onto
    every session, regardless of which primary bundle is used. This is useful
    for team-wide behaviors, support bundles, or personal preferences.

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

        \b
        # Add as app bundle (always active)
        amplifier bundle add git+https://github.com/org/team-bundle@main --app
    """
    from amplifier_foundation import load_bundle

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

    # All bundles are now stored in settings.yaml under bundle.added
    # App bundles additionally go in bundle.app for composition policy
    app_settings = AppSettings()

    if app:
        # Add as app bundle (always composed onto sessions)
        existing_app_bundles = app_settings.get_app_bundles()

        if uri in existing_app_bundles:
            console.print(
                "[yellow]Warning:[/yellow] Bundle already registered as app bundle"
            )
            console.print(f"  URI: {uri}")
            return

        # Add to bundle.app (composition policy) AND bundle.added (for updates)
        app_settings.add_app_bundle(uri)
        app_settings.add_bundle(name, uri)
        console.print(f"[green]✓ Added app bundle '{name}'[/green]")
        console.print(f"  URI: {uri}")
        if bundle_version:
            console.print(f"  Version: {bundle_version}")
        console.print(
            "\n[dim]App bundles are automatically composed with all sessions[/dim]"
        )
        console.print("[dim]Use 'amplifier bundle list' to see all bundles[/dim]")
    else:
        # Add to bundle.added in settings.yaml
        existing = app_settings.get_added_bundles()
        if name in existing:
            console.print(
                f"[yellow]Warning:[/yellow] Bundle '{name}' already registered"
            )
            console.print(f"  Current URI: {existing[name]}")
            console.print("\nUpdating to new URI...")

        app_settings.add_bundle(name, uri)
        console.print(f"[green]✓ Added bundle '{name}'[/green]")
        console.print(f"  URI: {uri}")
        if bundle_version:
            console.print(f"  Version: {bundle_version}")
        if name_override and name_override != bundle_name:
            console.print(f"  [dim](Bundle's canonical name: {bundle_name})[/dim]")
        console.print("\n[dim]Use 'amplifier bundle list' to see all bundles[/dim]")
        console.print(
            f"[dim]Use 'amplifier bundle use {name}' to activate this bundle[/dim]"
        )


@bundle.command(name="remove")
@click.argument("name")
@click.option(
    "--app",
    is_flag=True,
    help="Remove an app bundle by name or URI",
)
def bundle_remove(name: str, app: bool):
    """Remove a bundle from all registries.

    Removes the bundle from both the user registry and foundation registry.
    Does not delete cached files.
    Does not affect well-known bundles like 'foundation'.

    Use --app to remove an app bundle. The NAME argument can be either:
    - The bundle name (will search app bundles for matching URI)
    - The full URI of the app bundle

    Examples:

        \b
        amplifier bundle remove recipes

        \b
        # Remove app bundle by name
        amplifier bundle remove modes --app

        \b
        # Remove app bundle by URI
        amplifier bundle remove git+https://github.com/org/bundle@main --app
    """
    from ..lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES

    app_settings = AppSettings()

    if app:
        # Remove app bundle
        app_bundles = app_settings.get_app_bundles()

        # Check if name is a URI directly in the list
        if name in app_bundles:
            app_settings.remove_app_bundle(name)
            # Also remove from bundle.added (keeps settings in sync)
            app_settings.remove_added_bundle(name)
            console.print("[green]✓ Removed app bundle[/green]")
            console.print(f"  URI: {name}")
            return

        # Otherwise, search for a bundle with matching name in URI
        matching_uri = None
        for uri in app_bundles:
            if name in uri:
                matching_uri = uri
                break

        if matching_uri:
            app_settings.remove_app_bundle(matching_uri)
            # Also remove from bundle.added (keeps settings in sync)
            app_settings.remove_added_bundle(name)
            console.print(f"[green]✓ Removed app bundle '{name}'[/green]")
            console.print(f"  URI: {matching_uri}")
        else:
            console.print(f"[yellow]App bundle '{name}' not found[/yellow]")
            if app_bundles:
                console.print("\nCurrently registered app bundles:")
                for uri in app_bundles:
                    console.print(f"  - {uri}")
            else:
                console.print("\nNo app bundles registered")
        return

    # Check if this is a well-known bundle
    if name in WELL_KNOWN_BUNDLES:
        console.print(f"[red]Error:[/red] Cannot remove well-known bundle '{name}'")
        console.print("  Well-known bundles are built into amplifier")
        sys.exit(1)

    # Look up the bundle URI before removal (needed for app bundle cleanup)
    added_bundles = app_settings.get_added_bundles()
    bundle_uri = added_bundles.get(name)

    # Remove from settings.yaml bundle.added
    settings_removed = app_settings.remove_added_bundle(name)

    # Also remove from bundle.app if this bundle was registered as an app bundle
    # This prevents "ghost" bundles that continue running hooks after removal
    app_removed = False
    if bundle_uri:
        app_bundles = app_settings.get_app_bundles()
        if bundle_uri in app_bundles:
            app_removed = app_settings.remove_app_bundle(bundle_uri)
    else:
        # No URI in bundle.added - try matching by name in app bundle URIs
        app_bundles = app_settings.get_app_bundles()
        for uri in app_bundles:
            if name in uri:
                app_removed = app_settings.remove_app_bundle(uri)
                break

    # Remove from foundation registry (foundation-layer cache)
    foundation_removed = False
    try:
        registry = create_bundle_registry()
        if registry.unregister(name):
            registry.save()
            foundation_removed = True
    except Exception as e:
        # Log but don't fail - settings removal is primary concern
        console.print(
            f"[yellow]Warning:[/yellow] Failed to remove from foundation registry: {e}"
        )

    if settings_removed or foundation_removed or app_removed:
        console.print(f"[green]✓ Removed bundle '{name}' from registry[/green]")
        locations = []
        if settings_removed:
            locations.append("settings")
        if app_removed:
            locations.append("app bundles")
        if foundation_removed:
            locations.append("cache registry")
        console.print(f"  [dim](Removed from: {', '.join(locations)})[/dim]")
    else:
        console.print(f"[yellow]Bundle '{name}' not found in any registry[/yellow]")
        console.print("\nUser-added bundles can be seen with 'amplifier bundle list'")


@bundle.command(name="update")
@click.argument("name", required=False)
@click.option("--all", "update_all", is_flag=True, help="Update all discovered bundles")
@click.option(
    "--check", "check_only", is_flag=True, help="Only check for updates, don't apply"
)
@click.option(
    "--yes",
    "-y",
    "auto_confirm",
    is_flag=True,
    help="Auto-confirm update without prompting",
)
@click.option("--source", "specific_source", help="Update only a specific source URI")
def bundle_update(
    name: str | None,
    update_all: bool,
    check_only: bool,
    auto_confirm: bool,
    specific_source: str | None,
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
        asyncio.run(
            _bundle_update_async(name, check_only, auto_confirm, specific_source)
        )


async def _bundle_update_async(
    name: str | None, check_only: bool, auto_confirm: bool, specific_source: str | None
) -> None:
    """Async implementation of bundle update command."""
    from amplifier_foundation import check_bundle_status
    from amplifier_foundation import update_bundle

    app_settings = AppSettings()
    registry = create_bundle_registry()

    # Determine which bundle to check
    if name:
        bundle_name = name
    else:
        # Use active bundle
        bundle_name = app_settings.get_active_bundle()

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
            console.print(
                f"[red]Error:[/red] Expected single bundle, got dict for '{bundle_name}'"
            )
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
            console.print(
                f"\n[yellow]Update specific source:[/yellow] {specific_source}"
            )
        else:
            console.print(
                f"\n[yellow]Ready to update {update_count} source(s)[/yellow]"
            )

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
            console.print(
                f"[green]✓ Updated {len(status.updateable_sources)} source(s)[/green]"
            )
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
                table = Table(
                    title=f"Bundle: {bundle_name}",
                    show_header=True,
                    header_style="bold cyan",
                )
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

                    status_symbol = create_status_symbol(
                        source.cached_commit, source.remote_commit
                    )

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
        console.print(
            f"[yellow]{total_updates} bundle(s) have updates available[/yellow]"
        )
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
        console.print(
            f"[yellow]✓ Update complete ({updated_count} updated, {len(update_errors)} failed)[/yellow]"
        )
    else:
        console.print("[green]✓ Update complete[/green]")
        for name in bundles_with_updates:
            console.print(f"  [green]✓[/green] {name}")


def _display_bundle_status(status: BundleStatus) -> None:
    """Display bundle status in a formatted table."""
    if not status.sources:
        console.print("[dim]No sources to check.[/dim]")
        return

    table = Table(
        title=f"Bundle Sources: {status.bundle_name}",
        show_header=True,
        header_style="bold cyan",
    )
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
