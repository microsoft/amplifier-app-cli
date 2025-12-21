"""Source override commands - unified for modules and collections.

Auto-detects whether identifier is a module or collection:
- Module: Has amplifier.modules entry point or name matches amplifier-module-*
- Collection: Has profiles/, agents/, context/, scenario-tools/, or modules/ directories

Per IMPLEMENTATION_PHILOSOPHY:
- Ruthless simplicity: Single unified command, not separate hierarchies
- User-friendly: Auto-detect removes need for users to know the distinction
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import cast

import click
from rich.table import Table

from ..console import console
from ..paths import ScopeNotAvailableError
from ..paths import ScopeType
from ..paths import create_config_manager
from ..paths import create_module_resolver
from ..paths import get_effective_scope
from ..provider_sources import DEFAULT_PROVIDER_SOURCES
from ..provider_sources import is_local_path


def _is_module_path(path: Path) -> bool:
    """Check if path looks like a module (not a collection).

    Module indicators:
    - Has amplifier.modules entry point in pyproject.toml
    - Name matches amplifier-module-* pattern

    Args:
        path: Path to check

    Returns:
        True if path looks like a module, False otherwise
    """
    # Check pyproject.toml for amplifier.modules entry point
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            entry_points = data.get("project", {}).get("entry-points", {})
            if "amplifier.modules" in entry_points:
                return True
        except Exception:
            pass

    # Check name pattern
    return path.name.startswith("amplifier-module-")


def _is_collection_path(path: Path) -> bool:
    """Check if path looks like a collection.

    Collection indicators:
    - Has profiles/, agents/, context/, scenario-tools/, or modules/ directories
    - Does NOT have amplifier.modules entry point

    Args:
        path: Path to check

    Returns:
        True if path looks like a collection, False otherwise
    """
    # First, exclude modules
    if _is_module_path(path):
        return False

    # Check for collection resource directories
    collection_dirs = ["profiles", "agents", "context", "scenario-tools", "modules"]
    return any((path / dirname).is_dir() for dirname in collection_dirs)


def _detect_source_type(identifier: str, source_uri: str) -> str:
    """Detect whether identifier/source is a module or collection.

    Detection strategy:
    1. If source_uri is a local path, inspect directory structure
    2. If identifier matches naming conventions, use those
    3. Check existing overrides to see if already configured

    Args:
        identifier: Module ID or collection name
        source_uri: Source path or URI

    Returns:
        'module' or 'collection'
    """
    # Try to inspect source path if it's local
    source_path = Path(source_uri).expanduser()
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path

    if source_path.exists() and source_path.is_dir():
        if _is_module_path(source_path):
            return "module"
        if _is_collection_path(source_path):
            return "collection"

    # Fall back to identifier naming conventions
    if (
        identifier.startswith("amplifier-module-")
        or identifier.startswith("provider-")
        or identifier.startswith("tool-")
        or identifier.startswith("hooks-")
        or identifier.startswith("loop-")
        or identifier.startswith("context-")
    ):
        return "module"

    # Default to collection (collections are more common in user space)
    return "collection"


@click.group(invoke_without_command=True)
@click.pass_context
def source(ctx: click.Context):
    """Manage source overrides for modules and collections.

    Automatically detects whether the identifier is a module or collection
    based on directory structure and naming conventions.

    Examples:

        \b
        # Add module source override
        amplifier source add provider-anthropic ~/dev/provider-anthropic

        \b
        # Add collection source override
        amplifier source add foundation ~/dev/foundation

        \b
        # Force type if auto-detect is wrong
        amplifier source add my-thing ~/dev/my-thing --collection
    """
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@source.command("add")
@click.argument("identifier")
@click.argument("source_uri")
@click.option(
    "--local", "scope_flag", flag_value="local", help="Store in local settings (.amplifier/settings.local.yaml)"
)
@click.option(
    "--project", "scope_flag", flag_value="project", help="Store in project settings (.amplifier/settings.yaml)"
)
@click.option("--global", "scope_flag", flag_value="global", help="Store in user settings (~/.amplifier/settings.yaml)")
@click.option("--module", "force_module", is_flag=True, help="Force treating as module (skip auto-detect)")
@click.option("--collection", "force_collection", is_flag=True, help="Force treating as collection (skip auto-detect)")
def source_add(
    identifier: str,
    source_uri: str,
    scope_flag: str | None,
    force_module: bool,
    force_collection: bool,
):
    """Add a source override for a module or collection.

    IDENTIFIER is the module ID or collection name.
    SOURCE_URI is the local path or git URL to use.

    Auto-detects whether identifier is a module or collection.
    Use --module or --collection flags to override auto-detection.

    Examples:

        \b
        # Module source override (auto-detected)
        amplifier source add provider-anthropic ~/dev/provider-anthropic

        \b
        # Collection source override (auto-detected)
        amplifier source add foundation ~/dev/foundation

        \b
        # Force collection type
        amplifier source add my-bundle ~/dev/my-bundle --collection
    """
    from amplifier_app_cli.lib.legacy import Scope

    # Handle conflicting flags
    if force_module and force_collection:
        console.print("[red]Cannot specify both --module and --collection[/red]")
        raise click.Abort()

    # Determine type
    if force_module:
        source_type = "module"
    elif force_collection:
        source_type = "collection"
    else:
        source_type = _detect_source_type(identifier, source_uri)

    config_manager = create_config_manager()

    # Determine scope with validation (defaults to global when running from home)
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
        return

    scope_enum = {"local": Scope.LOCAL, "project": Scope.PROJECT, "global": Scope.USER}[scope]

    if source_type == "module":
        config_manager.add_source_override(identifier, source_uri, scope=scope_enum)
    else:
        config_manager.add_collection_source_override(identifier, source_uri, scope=scope_enum)  # pyright: ignore[reportAttributeAccessIssue]

    scope_labels = {
        "local": "local (.amplifier/settings.local.yaml)",
        "project": "project (.amplifier/settings.yaml)",
        "global": "global (~/.amplifier/settings.yaml)",
    }
    console.print(f"[green]✓ Added {source_type} source override for {identifier}[/green]")
    console.print(f"  Source: {source_uri}")
    console.print(f"  Scope: {scope_labels[scope]}")


@source.command("remove")
@click.argument("identifier")
@click.option(
    "--local", "scope_flag", flag_value="local", help="Remove from local settings (.amplifier/settings.local.yaml)"
)
@click.option(
    "--project", "scope_flag", flag_value="project", help="Remove from project settings (.amplifier/settings.yaml)"
)
@click.option(
    "--global", "scope_flag", flag_value="global", help="Remove from user settings (~/.amplifier/settings.yaml)"
)
@click.option("--module", "force_module", is_flag=True, help="Force treating as module (skip auto-detect)")
@click.option("--collection", "force_collection", is_flag=True, help="Force treating as collection (skip auto-detect)")
def source_remove(
    identifier: str,
    scope_flag: str | None,
    force_module: bool,
    force_collection: bool,
):
    """Remove a source override for a module or collection.

    IDENTIFIER is the module ID or collection name to remove.

    Tries to remove from both module and collection overrides by default.
    Use --module or --collection flags to target specifically.

    Also cleans up any provider config entries that use a local source path
    for this module, replacing them with the default git source.

    Examples:

        \b
        # Remove override (auto-detect type)
        amplifier source remove provider-anthropic

        \b
        # Remove global override
        amplifier source remove foundation --global
    """
    from amplifier_app_cli.lib.legacy import Scope

    # Handle conflicting flags
    if force_module and force_collection:
        console.print("[red]Cannot specify both --module and --collection[/red]")
        raise click.Abort()

    config_manager = create_config_manager()

    # Determine scope with validation (defaults to global when running from home)
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
        return

    scope_enum = {"local": Scope.LOCAL, "project": Scope.PROJECT, "global": Scope.USER}[scope]
    scope_labels = {
        "local": "local (.amplifier/settings.local.yaml)",
        "project": "project (.amplifier/settings.yaml)",
        "global": "global (~/.amplifier/settings.yaml)",
    }

    removed_module = False
    removed_collection = False

    # Try to remove based on flags or both
    if force_module or not force_collection:
        removed_module = config_manager.remove_source_override(identifier, scope=scope_enum)
    if force_collection or not force_module:
        removed_collection = config_manager.remove_collection_source_override(identifier, scope=scope_enum)  # pyright: ignore[reportAttributeAccessIssue]

    # Also clean up any provider config entries with local source paths for this module
    provider_cleaned = False
    if removed_module or not force_collection:
        provider_cleaned = _cleanup_provider_config_source(config_manager, identifier, scope_enum)

    if removed_module:
        console.print(f"[green]✓ Removed module source override for {identifier} ({scope_labels[scope]})[/green]")
    if removed_collection:
        console.print(f"[green]✓ Removed collection source override for {identifier} ({scope_labels[scope]})[/green]")
    if provider_cleaned:
        console.print("[green]✓ Updated provider config to use default source[/green]")
    if not removed_module and not removed_collection and not provider_cleaned:
        console.print(f"[yellow]Source override for {identifier} not found[/yellow]")


def _cleanup_provider_config_source(config_manager, module_id: str, scope) -> bool:
    """Clean up local source paths in provider config entries.

    When a source override is removed, also check if there's a provider config entry
    (in config.providers[]) that has a local source path for this module. If so,
    update it to use the default git source.

    Args:
        config_manager: ConfigManager instance
        module_id: Module ID to clean up (e.g., "provider-anthropic")
        scope: Scope enum value

    Returns:
        True if a provider config was updated, False otherwise
    """
    # Read settings at this scope
    scope_path = config_manager._scope_to_path(scope)
    if scope_path is None:
        return False

    settings = config_manager._read_yaml(scope_path) or {}
    config_section = settings.get("config", {})
    providers = config_section.get("providers", [])

    if not isinstance(providers, list):
        return False

    updated = False
    new_providers = []

    for provider in providers:
        if not isinstance(provider, dict):
            new_providers.append(provider)
            continue

        if provider.get("module") == module_id:
            source = provider.get("source", "")
            if is_local_path(source):
                # Replace with default git source if available
                default_source = DEFAULT_PROVIDER_SOURCES.get(module_id)
                if default_source:
                    provider = {**provider, "source": default_source}
                    updated = True
                else:
                    # No default source, just remove the source field
                    provider = {k: v for k, v in provider.items() if k != "source"}
                    updated = True

        new_providers.append(provider)

    if updated:
        config_section["providers"] = new_providers
        settings["config"] = config_section
        config_manager._write_yaml(scope_path, settings)

    return updated


@source.command("list")
def source_list():
    """List all source overrides (modules and collections).

    Shows merged overrides from all scopes (project + user).

    Examples:

        \b
        # List all source overrides
        amplifier source list
    """
    config_manager = create_config_manager()
    module_sources = config_manager.get_module_sources()
    collection_sources = config_manager.get_collection_sources()  # pyright: ignore[reportAttributeAccessIssue]

    if not module_sources and not collection_sources:
        console.print("[yellow]No source overrides configured[/yellow]")
        console.print("\nAdd overrides with:")
        console.print("  [cyan]amplifier source add <identifier> <uri>[/cyan]")
        return

    # Show module overrides
    if module_sources:
        table = Table(title="Module Source Overrides", show_header=True, header_style="bold cyan")
        table.add_column("Module", style="green")
        table.add_column("Source", style="magenta")

        for module_id, source_uri in sorted(module_sources.items()):
            display_uri = source_uri if len(source_uri) <= 60 else source_uri[:57] + "..."
            table.add_row(module_id, display_uri)

        console.print(table)

    # Show collection overrides
    if collection_sources:
        if module_sources:
            console.print()  # Separator
        table = Table(title="Collection Source Overrides", show_header=True, header_style="bold cyan")
        table.add_column("Collection", style="green")
        table.add_column("Source", style="magenta")

        for collection_name, source_uri in sorted(collection_sources.items()):
            display_uri = source_uri if len(source_uri) <= 60 else source_uri[:57] + "..."
            table.add_row(collection_name, display_uri)

        console.print(table)


@source.command("show")
@click.argument("module_id")
def source_show(module_id: str):
    """Show resolution path for a module."""
    resolver = create_module_resolver()

    console.print(f"[bold]Module:[/bold] {module_id}\n")
    console.print("[bold]Resolution Path:[/bold]")

    env_key = f"AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')}"
    env_val = os.getenv(env_key)
    env_display = f"[green]✓ {env_val}[/green]" if env_val else "[dim]not set[/dim]"
    console.print(f"  1. Environment ({env_key}): {env_display}")

    workspace = Path(".amplifier/modules") / module_id
    workspace_display = "[green]✓ found[/green]" if workspace.exists() else "[dim]not found[/dim]"
    console.print(f"  2. Workspace (.amplifier/modules/): {workspace_display}")

    config_manager = create_config_manager()
    merged_sources = config_manager.get_module_sources()
    project_source = merged_sources.get(module_id)
    project_display = f"[green]✓ {project_source}[/green]" if project_source else "[dim]not found[/dim]"
    console.print(f"  3. Project (.amplifier/settings.yaml): {project_display}")

    console.print("  4. User (~/.amplifier/settings.yaml): [dim](merged with project)[/dim]")
    console.print("  5. Profile: [dim](depends on active profile)[/dim]")
    console.print("  6. Package: [dim](installed packages)[/dim]")

    try:
        source_obj, layer = resolver.resolve_with_layer(module_id)
        console.print(f"\n[bold green]✓ Resolved via:[/bold green] {layer}")
        console.print(f"[bold green]Source:[/bold green] {source_obj}")
    except Exception as exc:
        console.print(f"\n[bold red]✗ Failed:[/bold red] {exc}")


__all__ = ["source"]
