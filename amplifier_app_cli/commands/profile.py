"""Profile management commands for the Amplifier CLI."""

from __future__ import annotations

import sys
from typing import Any
from typing import cast

import click
from rich.table import Table

from ..console import console
from ..data.profiles import get_system_default_profile
from ..paths import ScopeNotAvailableError
from ..paths import ScopeType
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..paths import get_effective_scope


@click.group(invoke_without_command=True)
@click.pass_context
def profile(ctx: click.Context):
    """Manage Amplifier profiles."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@profile.command(name="list")
def profile_list():
    """List all available profiles."""
    loader = create_profile_loader()
    config_manager = create_config_manager()
    profiles = loader.list_profiles()
    active_profile = config_manager.get_active_profile()
    project_default = config_manager.get_project_default()

    if not profiles:
        console.print("[yellow]No profiles found.[/yellow]")
        return

    table = Table(title="Available Profiles", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Status")

    for profile_name in profiles:
        source = loader.get_profile_source(profile_name)
        source_label = source or "unknown"

        status_parts: list[str] = []
        if profile_name == active_profile:
            status_parts.append("[bold green]active[/bold green]")
        if profile_name == project_default:
            status_parts.append("[cyan]default[/cyan]")

        status = ", ".join(status_parts) if status_parts else ""

        table.add_row(profile_name, source_label, status)

    console.print(table)


@profile.command(name="current")
def profile_current():
    """Show the currently active profile and its source."""
    config_manager = create_config_manager()

    local = config_manager._read_yaml(config_manager.paths.local)
    if local and "profile" in local and "active" in local["profile"]:
        profile_name = local["profile"]["active"]
        source = "local"
    else:
        project_default = config_manager.get_project_default()
        if project_default:
            profile_name = project_default
            source = "default"
        else:
            user = config_manager._read_yaml(config_manager.paths.user)
            if user and "profile" in user and "active" in user["profile"]:
                profile_name = user["profile"]["active"]
                source = "user"
            else:
                profile_name = None
                source = None

    if profile_name:
        if source == "local":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from local settings)[/dim]")
            console.print("Source: [cyan].amplifier/settings.local.yaml[/cyan]")
        elif source == "default":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from project default)[/dim]")
            console.print("Source: [cyan].amplifier/settings.yaml[/cyan]")
        elif source == "user":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from user settings)[/dim]")
            console.print("Source: [cyan]~/.amplifier/settings.yaml[/cyan]")
    else:
        console.print("[yellow]No active profile set[/yellow]")
        console.print(f"Using system default: [bold]{get_system_default_profile()}[/bold]")
        console.print("\n[bold]To set a profile:[/bold]")
        console.print("  Local:   [cyan]amplifier profile use <name>[/cyan]")
        console.print("  Project: [cyan]amplifier profile use <name> --project[/cyan]")
        console.print("  Global:  [cyan]amplifier profile use <name> --global[/cyan]")


def build_effective_config_with_sources(chain_dicts: list[dict[str, Any]], chain_names: list[str]):
    """
    Build effective configuration with accurate source tracking.

    Merges the inheritance chain while tracking which profile contributed which values.
    This enables accurate "[from X]" annotations showing true provenance.

    Args:
        chain_dicts: List of raw profile dictionaries from root to leaf
        chain_names: List of profile names corresponding to chain_dicts

    Returns:
        Tuple of (effective_config, sources) where sources tracks provenance
    """
    from amplifier_profiles.merger import merge_profile_dicts

    effective_config: dict[str, Any] = {
        "session": {},
        "providers": {},
        "tools": {},
        "hooks": {},
        "agents": {},
    }

    sources: dict[str, Any] = {
        "session": {},
        "providers": {},
        "tools": {},
        "hooks": {},
        "agents": {},
        "config_fields": {},  # Track individual config field sources
    }

    # Track merged state incrementally to identify where each value comes from
    merged_so_far = {}

    for _i, (profile_dict, profile_name) in enumerate(zip(chain_dicts, chain_names, strict=False)):
        # Track what this profile adds/modifies

        # Session fields
        if "session" in profile_dict:
            session = profile_dict["session"]
            if isinstance(session, dict):
                for field, _value in session.items():
                    if field not in merged_so_far.get("session", {}):
                        # New field from this profile
                        sources["session"][field] = profile_name
                    else:
                        # Overriding previous value
                        old_source = sources["session"][field]
                        if isinstance(old_source, tuple):
                            old_source = old_source[0]
                        sources["session"][field] = (profile_name, old_source)

        # Module lists (providers, tools, hooks)
        for section in ["providers", "tools", "hooks"]:
            if section in profile_dict:
                items = profile_dict[section]
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "module" in item:
                            module_id = item["module"]

                            # Check if module exists in merged state
                            merged_section = merged_so_far.get(section, [])
                            existing_module = next(
                                (m for m in merged_section if isinstance(m, dict) and m.get("module") == module_id),
                                None,
                            )

                            if existing_module is None:
                                # New module from this profile
                                sources[section][module_id] = profile_name
                                sources["config_fields"][f"{section}.{module_id}"] = {}
                            else:
                                # Module exists - check what this profile is actually changing
                                old_source = sources[section][module_id]
                                if isinstance(old_source, tuple):
                                    old_source = old_source[0]

                                # If child provides source, it's redefining the module
                                if "source" in item:
                                    sources[section][module_id] = (profile_name, old_source)
                                elif "config" in item:
                                    # Only config provided - module itself is from parent
                                    # Keep parent as module source, track config modifications separately
                                    sources[section][module_id] = old_source
                                    # Mark that config was modified
                                    if section not in sources.get("config_modified_by", {}):
                                        sources.setdefault("config_modified_by", {})[section] = {}
                                    sources["config_modified_by"][section][module_id] = profile_name

                                # Track config field provenance
                                if "config" in item and isinstance(item["config"], dict):
                                    config_key = f"{section}.{module_id}"
                                    if config_key not in sources["config_fields"]:
                                        sources["config_fields"][config_key] = {}

                                    for cfg_field, _cfg_value in item["config"].items():
                                        existing_config = existing_module.get("config", {})
                                        if cfg_field not in existing_config:
                                            sources["config_fields"][config_key][cfg_field] = profile_name
                                        else:
                                            old_cfg_source = sources["config_fields"][config_key].get(
                                                cfg_field, old_source
                                            )
                                            sources["config_fields"][config_key][cfg_field] = (
                                                profile_name,
                                                old_cfg_source,
                                            )

        # Agents
        if "agents" in profile_dict:
            if "agents" not in merged_so_far:
                sources["agents"] = profile_name
            else:
                old_source = sources.get("agents", "")
                if isinstance(old_source, tuple):
                    old_source = old_source[0]
                sources["agents"] = (profile_name, old_source)

        # Merge this profile into the running total
        merged_so_far = merge_profile_dicts(merged_so_far, profile_dict)

    # Build final effective config from merged result
    effective_config["session"] = merged_so_far.get("session", {})
    effective_config["providers"] = {
        item["module"]: item
        for item in merged_so_far.get("providers", [])
        if isinstance(item, dict) and "module" in item
    }
    effective_config["tools"] = {
        item["module"]: item for item in merged_so_far.get("tools", []) if isinstance(item, dict) and "module" in item
    }
    effective_config["hooks"] = {
        item["module"]: item for item in merged_so_far.get("hooks", []) if isinstance(item, dict) and "module" in item
    }
    effective_config["agents"] = merged_so_far.get("agents", {})

    return effective_config, sources


def render_effective_config(
    chain_dicts: list[dict[str, Any]], chain_names: list[str], source_overrides: dict[str, str], detailed: bool
):
    """Render the effective configuration with source annotations and override indicators."""
    config, sources = build_effective_config_with_sources(chain_dicts, chain_names)

    console.print("\n[bold]Effective Configuration:[/bold]\n")

    def format_source(source: Any) -> str:
        """Format source attribution (concise, no 'from' prefix)."""
        from rich.markup import escape

        if isinstance(source, list | tuple) and len(source) == 2:
            current, previous = source
            current_escaped = escape(str(current))
            previous_escaped = escape(str(previous))
            return f" [yellow]\\[{current_escaped}, overrides {previous_escaped}][/yellow]"
        if source:
            source_escaped = escape(str(source))
            return f" [cyan]\\[{source_escaped}][/cyan]"
        return ""

    def format_config_field_source(section: str, module_id: str, field_name: str) -> str:
        """Get provenance for a specific config field."""
        config_key = f"{section}.{module_id}"
        field_source = sources.get("config_fields", {}).get(config_key, {}).get(field_name)
        return format_source(field_source)

    def render_source_line(module_id: str, profile_source: str | None):
        """
        Render source line with override annotation if applicable.

        Shows effective source value with [settings override] annotation when override is active.
        Pattern matches config field provenance: value [source-annotation]

        Args:
            module_id: Module identifier to check for overrides
            profile_source: Source URL from profile
        """
        if not profile_source:
            return

        override_source = source_overrides.get(module_id)

        if override_source and override_source != profile_source:
            # Override is active - show effective source with annotation
            if len(override_source) > 60:
                display_source = override_source[:57] + "..."
            else:
                display_source = override_source
            console.print(f"    source: {display_source} [yellow](settings override)[/yellow]")
        else:
            # No override - show profile source
            if len(profile_source) > 60:
                display_source = profile_source[:57] + "..."
            else:
                display_source = profile_source
            console.print(f"    source: {display_source}")

    # Session
    if config["session"]:
        console.print("[bold]Session:[/bold]")

        for field in ["orchestrator", "context"]:
            if field in config["session"]:
                value = config["session"][field]
                source = sources["session"].get(field, "")

                if isinstance(value, dict) and "module" in value:
                    module_id = value.get("module", "")

                    if detailed:
                        # Detailed: Show everything with DRY helper
                        console.print(f"  {field}:{format_source(source)}")
                        console.print(f"    module: {module_id}")
                        # Use DRY helper for source display
                        render_source_line(module_id, value.get("source"))
                        if "config" in value and value["config"]:
                            console.print("    config:")
                            for cfg_key, cfg_value in value["config"].items():
                                field_src = format_config_field_source("session", field, cfg_key)
                                console.print(f"      {cfg_key}: {cfg_value}{field_src}")
                    else:
                        # Non-detailed: Just module name and provenance
                        console.print(f"  {field}: {module_id}{format_source(source)}")

        # Other session fields (max_tokens, etc.) - only in detailed mode
        if detailed:
            other_fields = [k for k in config["session"] if k not in ["orchestrator", "context"]]
            if other_fields:
                for field in other_fields:
                    value = config["session"][field]
                    source = sources["session"].get(field, "")
                    console.print(f"  {field}: {value}{format_source(source)}")

        console.print()

    if config["providers"]:
        console.print("[bold]Providers:[/bold]")
        for module_name, provider in config["providers"].items():
            source = sources["providers"].get(module_name, "")
            console.print(f"  {module_name}{format_source(source)}")

            if detailed:
                # Use DRY helper for source display with overrides
                render_source_line(module_name, provider.get("source"))

                # Show config with per-field provenance
                if provider.get("config"):
                    console.print("    config:")
                    for key, value in provider["config"].items():
                        field_src = format_config_field_source("providers", module_name, key)
                        console.print(f"      {key}: {value}{field_src}")
        console.print()

    if config["tools"]:
        console.print("[bold]Tools:[/bold]")
        for module_name, tool in config["tools"].items():
            source = sources["tools"].get(module_name, "")
            console.print(f"  {module_name}{format_source(source)}")

            if detailed:
                # Use DRY helper for source display with overrides
                render_source_line(module_name, tool.get("source"))

                # Show config with per-field provenance
                if tool.get("config"):
                    console.print("    config:")
                    for key, value in tool["config"].items():
                        field_src = format_config_field_source("tools", module_name, key)
                        console.print(f"      {key}: {value}{field_src}")
        console.print()

    if config["hooks"]:
        console.print("[bold]Hooks:[/bold]")
        for module_name, hook in config["hooks"].items():
            source = sources["hooks"].get(module_name, "")
            console.print(f"  {module_name}{format_source(source)}")

            if detailed:
                # Use DRY helper for source display with overrides
                render_source_line(module_name, hook.get("source"))

                # Show config with per-field provenance
                if hook.get("config"):
                    console.print("    config:")
                    for key, value in hook["config"].items():
                        field_src = format_config_field_source("hooks", module_name, key)
                        console.print(f"      {key}: {value}{field_src}")
        console.print()

    if config.get("agents"):
        console.print("[bold]Agents:[/bold]")
        agents_cfg = config["agents"]
        source = sources.get("agents", "")
        source_str = format_source(source)

        # agents_cfg can be a dict (with dirs/include/inline) or other format
        if isinstance(agents_cfg, dict):
            if agents_cfg.get("dirs"):
                console.print(f"  dirs: {agents_cfg['dirs']}{source_str}")
            if agents_cfg.get("include"):
                console.print(f"  include: {agents_cfg['include']}{source_str}")
            if agents_cfg.get("inline"):
                inline_count = len(agents_cfg["inline"])
                console.print(f"  inline: {inline_count} agent(s){source_str}")
                if detailed:
                    for agent_name in agents_cfg["inline"]:
                        console.print(f"    - {agent_name}")
        else:
            # Handle non-dict agents config (e.g., string reference)
            console.print(f"  {agents_cfg}{source_str}")


@profile.command(name="show")
@click.argument("name")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed configuration values")
def profile_show(name: str, detailed: bool):
    """Show details of a specific profile with inheritance chain."""
    loader = create_profile_loader()
    config_manager = create_config_manager()

    try:
        profile_obj = loader.load_profile(name)
        chain_names = loader.get_inheritance_chain(name)
        # Load raw dicts for accurate provenance tracking
        chain_dicts = loader.load_inheritance_chain_dicts(name)
        # Get source overrides for transparency
        source_overrides = config_manager.get_module_sources()
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[bold]Profile:[/bold] {profile_obj.profile.name}")
    console.print(f"[bold]Version:[/bold] {profile_obj.profile.version}")
    console.print(f"[bold]Description:[/bold] {profile_obj.profile.description}")

    # Display real inheritance chain
    console.print("\n[bold]Inheritance:[/bold]", end=" ")
    console.print(" → ".join(chain_names))

    # Display effective configuration with source overrides
    render_effective_config(chain_dicts, chain_names, source_overrides, detailed)


@profile.command(name="use")
@click.argument("name")
@click.option("--local", "scope_flag", flag_value="local", help="Set locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Set for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Set globally (all projects)")
def profile_use(name: str, scope_flag: str | None):
    """Set the active profile."""
    from amplifier_config import Scope

    loader = create_profile_loader()
    config_manager = create_config_manager()

    try:
        loader.load_profile(name)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

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

    if scope == "local":
        config_manager.set_active_profile(name)
        console.print(f"[green]✓ Using '{name}' profile locally[/green]")
        console.print("  File: .amplifier/settings.local.yaml")
    elif scope == "project":
        config_manager.set_project_default(name)
        console.print(f"[green]✓ Set '{name}' as project default[/green]")
        console.print("  File: .amplifier/settings.yaml")
        console.print("  [yellow]Remember to commit .amplifier/settings.yaml[/yellow]")
    elif scope == "global":
        config_manager.update_settings({"profile": {"active": name}}, scope=Scope.USER)
        console.print(f"[green]✓ Set '{name}' globally[/green]")
        console.print("  File: ~/.amplifier/settings.yaml")


@profile.command(name="reset")
def profile_reset():
    """Clear the local profile choice (falls back to project default if set)."""
    from amplifier_config import Scope

    config_manager = create_config_manager()

    # Check if local scope is available
    if not config_manager.is_scope_available(Scope.LOCAL):
        console.print(
            "[yellow]Note:[/yellow] Running from home directory - no local profile to reset.\n"
            "Use [cyan]amplifier profile use <name> --global[/cyan] to set a global profile."
        )
        return

    config_manager.clear_active_profile(scope=Scope.LOCAL)

    project_default = config_manager.get_project_default()
    if project_default:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using project default: [bold]{project_default}[/bold]")
    else:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using system default: [bold]{get_system_default_profile()}[/bold]")


@profile.command(name="default")
@click.option("--set", "set_default", metavar="NAME", help="Set project default profile")
@click.option("--clear", is_flag=True, help="Clear project default profile")
def profile_default(set_default: str | None, clear: bool):
    """Manage the project default profile."""
    from amplifier_config import Scope

    config_manager = create_config_manager()

    # Check if project scope is available for write operations
    if (clear or set_default) and not config_manager.is_scope_available(Scope.PROJECT):
        console.print(
            "[red]Error:[/red] Project default can only be managed from a project directory.\n"
            "The project scope is not available when running from your home directory."
        )
        sys.exit(1)

    if clear:
        config_manager.clear_project_default()
        console.print("[green]✓[/green] Cleared project default profile")
        return

    if set_default:
        loader = create_profile_loader()
        try:
            loader.load_profile(set_default)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Profile '{set_default}' not found")
            sys.exit(1)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)

        config_manager.set_project_default(set_default)
        console.print(f"[green]✓[/green] Set project default: {set_default}")
        console.print("\n[yellow]Note:[/yellow] Remember to commit .amplifier/settings.yaml")
        return

    project_default = config_manager.get_project_default()
    if project_default:
        console.print(f"[bold green]Project default:[/bold green] {project_default}")
        console.print("Source: [cyan].amplifier/settings.yaml[/cyan]")
    else:
        console.print("[yellow]No project default set[/yellow]")
        console.print(f"System default: [bold]{get_system_default_profile()}[/bold]")
        console.print("\nSet a project default with:")
        console.print("  [cyan]amplifier profile default --set <name>[/cyan]")


__all__ = ["profile"]
