"""Routing matrix management commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import click
import yaml
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..lib.settings import AppSettings, Scope
from ..ui.scope import (
    is_scope_change_available,
    print_scope_indicator,
    prompt_scope_change,
    validate_scope_cli,
)

console = Console()


def _get_settings() -> AppSettings:
    """Get AppSettings instance. Extracted for testability."""
    return AppSettings()


def _discover_matrix_files() -> list[Path]:
    """Discover available routing matrix YAML files.

    Looks in:
    1. ~/.amplifier/cache/amplifier-bundle-routing-matrix-*/routing/*.yaml (bundle)
    2. ~/.amplifier/routing/*.yaml (custom user matrices)
    """
    home = Path.home()
    files: list[Path] = []

    # Bundle cache matrices
    cache_base = home / ".amplifier" / "cache"
    if cache_base.exists():
        for bundle_dir in cache_base.glob("amplifier-bundle-routing-matrix-*"):
            routing_dir = bundle_dir / "routing"
            if routing_dir.is_dir():
                files.extend(routing_dir.glob("*.yaml"))

    # Custom user matrices
    custom_dir = home / ".amplifier" / "routing"
    if custom_dir.is_dir():
        files.extend(custom_dir.glob("*.yaml"))

    return sorted(files)


def _load_matrix(path: Path) -> dict[str, Any] | None:
    """Load and parse a matrix YAML file."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None


def _load_all_matrices(matrix_files: list[Path]) -> dict[str, dict[str, Any]]:
    """Load all matrix files into a name -> data dict."""
    matrices: dict[str, dict[str, Any]] = {}
    for path in matrix_files:
        data = _load_matrix(path)
        if data and "name" in data:
            matrices[data["name"]] = data
    return matrices


def _get_configured_provider_types(settings: AppSettings) -> set[str]:
    """Get the set of configured provider type names (without 'provider-' prefix).

    E.g., {'anthropic', 'openai', 'github-copilot'}
    """
    providers = settings.get_provider_overrides()
    types: set[str] = set()
    for p in providers:
        module = p.get("module", "")
        if module.startswith("provider-"):
            types.add(module.removeprefix("provider-"))
        else:
            types.add(module)
    return types


def _check_compatibility(
    matrix_data: dict[str, Any], provider_types: set[str]
) -> tuple[int, int]:
    """Check how many roles have at least one matching provider.

    Returns (covered_count, total_count).
    """
    roles = matrix_data.get("roles", {})
    total = len(roles)
    covered = 0
    for _role_name, role_config in roles.items():
        candidates = role_config.get("candidates", [])
        for candidate in candidates:
            if candidate.get("provider") in provider_types:
                covered += 1
                break
    return covered, total


def _resolve_role(
    role_config: dict[str, Any], provider_types: set[str]
) -> tuple[str | None, str | None]:
    """Resolve a role to its first matching candidate.

    Returns (model_pattern, provider_type) or (None, None) if unresolvable.
    """
    candidates = role_config.get("candidates", [])
    for candidate in candidates:
        provider = candidate.get("provider", "")
        if provider in provider_types:
            return candidate.get("model", "?"), provider
    return None, None


# ============================================================
# Command group
# ============================================================


@click.group("routing")
def routing_group():
    """Manage model routing matrices."""
    pass


# ============================================================
# Task 13: routing list
# ============================================================


@routing_group.command("list")
def routing_list():
    """List available routing matrices with compatibility indicators."""
    settings = _get_settings()
    matrix_files = _discover_matrix_files()

    if not matrix_files:
        console.print("[yellow]No routing matrices found.[/yellow]")
        console.print(
            "[dim]Run 'amplifier update' to fetch the routing-matrix bundle.[/dim]"
        )
        return

    matrices = _load_all_matrices(matrix_files)
    if not matrices:
        console.print("[yellow]No valid routing matrices found.[/yellow]")
        return

    # Get active matrix from settings
    routing_config = settings.get_routing_config()
    active_matrix = routing_config.get("matrix", "balanced")

    # Get configured provider types for compatibility check
    provider_types = _get_configured_provider_types(settings)

    table = Table(title="Routing Matrices")
    table.add_column("", width=2)  # Arrow indicator
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Compatibility", justify="right")
    table.add_column("Updated")

    for name, data in sorted(matrices.items()):
        is_active = name == active_matrix
        indicator = "→" if is_active else ""

        description = data.get("description", "")
        updated = str(data.get("updated", ""))

        if provider_types:
            covered, total = _check_compatibility(data, provider_types)
            if covered == total:
                compat = f"[green]✓ {covered}/{total} roles[/green]"
            elif covered > 0:
                compat = f"[yellow]~ {covered}/{total} roles[/yellow]"
            else:
                compat = f"[red]✗ {covered}/{total} roles[/red]"
        else:
            compat = "[dim]no providers[/dim]"

        name_style = "bold cyan" if is_active else "cyan"
        table.add_row(
            indicator,
            f"[{name_style}]{name}[/{name_style}]",
            description,
            compat,
            updated,
        )

    console.print(table)


# ============================================================
# Task 14: routing use
# ============================================================


@routing_group.command("use")
@click.argument("matrix_name")
@click.option(
    "--scope",
    default="global",
    type=click.Choice(["global", "project", "local"]),
    help="Settings scope to write to.",
)
def routing_use(matrix_name: str, scope: str):
    """Select a routing matrix."""
    settings = _get_settings()
    matrix_files = _discover_matrix_files()
    matrices = _load_all_matrices(matrix_files)

    if matrix_name not in matrices:
        available = ", ".join(sorted(matrices.keys())) if matrices else "none"
        console.print(
            f"[red]Matrix '{matrix_name}' not found.[/red] Available: {available}"
        )
        return

    settings.set_routing_matrix(matrix_name, scope=cast(Scope, scope))
    console.print(
        f"[green]✓ Routing matrix set to '{matrix_name}' ({scope} scope)[/green]"
    )

    # Show the effective resolution as a preview
    _show_matrix_resolution(matrices[matrix_name], settings)


# ============================================================
# Task 15: routing show
# ============================================================


@routing_group.command("show")
@click.argument("matrix_name", required=False)
def routing_show(matrix_name: str | None):
    """Show effective model routing for each role."""
    settings = _get_settings()
    matrix_files = _discover_matrix_files()
    matrices = _load_all_matrices(matrix_files)

    if not matrices:
        console.print("[yellow]No routing matrices found.[/yellow]")
        return

    # Determine which matrix to show
    if matrix_name is None:
        routing_config = settings.get_routing_config()
        matrix_name = routing_config.get("matrix", "balanced")

    if matrix_name not in matrices:
        available = ", ".join(sorted(matrices.keys()))
        console.print(
            f"[red]Matrix '{matrix_name}' not found.[/red] Available: {available}"
        )
        return

    matrix_data = matrices[matrix_name]
    _show_matrix_resolution(matrix_data, settings)


def _show_matrix_resolution(matrix_data: dict[str, Any], settings: AppSettings) -> None:
    """Display a role-by-role resolution table for a matrix."""
    matrix_name = matrix_data.get("name", "unknown")
    provider_types = _get_configured_provider_types(settings)

    roles = matrix_data.get("roles", {})
    if not roles:
        console.print(f"[yellow]Matrix '{matrix_name}' has no roles defined.[/yellow]")
        return

    table = Table(title=f"Routing: {matrix_name}")
    table.add_column("Role", style="cyan")
    table.add_column("Model", style="green")
    table.add_column("Provider")

    for role_name, role_config in roles.items():
        model, provider_type = _resolve_role(role_config, provider_types)
        if model and provider_type:
            table.add_row(role_name, model, provider_type)
        else:
            table.add_row(role_name, "[yellow]⚠ (no provider)[/yellow]", "[dim]-[/dim]")

    console.print(table)

    # Show provider summary
    if provider_types:
        # Find primary provider (first in the list)
        providers = settings.get_provider_overrides()
        primary_module = providers[0].get("module", "") if providers else ""
        primary_type = primary_module.removeprefix("provider-")

        provider_display = []
        for pt in sorted(provider_types):
            if pt == primary_type:
                provider_display.append(f"{pt} (★)")
            else:
                provider_display.append(pt)
        console.print(f"\n[dim]Providers: {', '.join(provider_display)}[/dim]")
    else:
        console.print(
            "\n[yellow]No providers configured. Run: amplifier provider add[/yellow]"
        )


# ============================================================
# Task 2: routing manage — interactive dashboard
# ============================================================


def routing_manage_loop(settings: AppSettings, scope: Scope = "global") -> Scope:
    """Interactive routing management loop.

    Callable from CLI command or from init dashboard.
    Tracks current_scope internally, returns it when done.
    """
    current_scope: Scope = scope
    while True:
        # 1. Show active matrix name
        routing_config = settings.get_routing_config()
        active_matrix = routing_config.get("matrix", "balanced")
        console.print(f"\n  Active Routing Matrix: [bold]{active_matrix}[/bold]\n")
        print_scope_indicator(console, settings, current_scope)
        console.print()

        # 2. Show available matrices table
        matrix_files = _discover_matrix_files()
        matrices = _load_all_matrices(matrix_files)

        if not matrices:
            console.print("  [yellow]No routing matrices found.[/yellow]")
            console.print(
                "  [dim]Run 'amplifier update' to fetch the routing-matrix bundle.[/dim]\n"
            )
        else:
            provider_types = _get_configured_provider_types(settings)

            table = Table(title="Available Matrices")
            table.add_column("#", justify="right", width=3)
            table.add_column("", width=2)  # Arrow indicator
            table.add_column("Name", style="cyan")
            table.add_column("Description")
            table.add_column("Compatibility", justify="right")

            matrix_names = sorted(matrices.keys())
            for i, name in enumerate(matrix_names, 1):
                data = matrices[name]
                is_active = name == active_matrix
                indicator = "→" if is_active else ""
                description = data.get("description", "")

                if provider_types:
                    covered, total = _check_compatibility(data, provider_types)
                    if covered == total:
                        compat = f"[green]✓ {covered}/{total} roles[/green]"
                    elif covered > 0:
                        compat = f"[yellow]~ {covered}/{total} roles[/yellow]"
                    else:
                        compat = f"[red]✗ {covered}/{total} roles[/red]"
                else:
                    compat = "[dim]no providers[/dim]"

                name_style = "bold cyan" if is_active else "cyan"
                table.add_row(
                    str(i),
                    indicator,
                    f"[{name_style}]{name}[/{name_style}]",
                    description,
                    compat,
                )

            console.print(table)

            # 3. Show current resolution table
            if active_matrix in matrices:
                _show_matrix_resolution(matrices[active_matrix], settings)

        # 4. Actions menu
        console.print("\n  Actions:")
        console.print("    \\[s] Select a different matrix (enter number)")
        console.print("    \\[v] View resolution for a specific matrix")
        console.print("    \\[c] Create a custom matrix")
        if is_scope_change_available():
            console.print("    \\[w] Change write scope")
        console.print("    \\[d] Done")
        console.print()

        try:
            choice = Prompt.ask("  Choice", default="d").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return current_scope

        if choice == "d":
            return current_scope
        elif choice == "w" and is_scope_change_available():
            current_scope = prompt_scope_change(console, settings, current_scope)
        elif choice.startswith("s"):
            _manage_select_matrix(settings, choice, matrices, scope=current_scope)
        elif choice.startswith("v"):
            _manage_view_matrix(settings, choice, matrices)
        elif choice == "c":
            _routing_create_interactive(settings)


def _manage_select_matrix(
    settings: AppSettings,
    choice: str,
    matrices: dict[str, dict[str, Any]],
    scope: Scope = "global",
) -> None:
    """Select a routing matrix from the manage loop."""
    if not matrices:
        console.print("  [yellow]No matrices available.[/yellow]")
        return

    matrix_names = sorted(matrices.keys())
    num_str = choice[len("s") :].strip()
    if not num_str:
        try:
            num_str = Prompt.ask("  Enter number").strip()
        except (EOFError, KeyboardInterrupt):
            return

    try:
        num = int(num_str)
        if 1 <= num <= len(matrix_names):
            name = matrix_names[num - 1]
            settings.set_routing_matrix(name, scope=scope)
            console.print(f"\n  [green]✓ Routing matrix set to '{name}'[/green]")
        else:
            console.print(f"  [red]Invalid number. Enter 1-{len(matrix_names)}.[/red]")
    except ValueError:
        console.print("  [red]Invalid input. Enter a number.[/red]")


def _manage_view_matrix(
    settings: AppSettings,
    choice: str,
    matrices: dict[str, dict[str, Any]],
) -> None:
    """View resolution for a specific matrix from the manage loop."""
    if not matrices:
        console.print("  [yellow]No matrices available.[/yellow]")
        return

    matrix_names = sorted(matrices.keys())
    num_str = choice[len("v") :].strip()
    if not num_str:
        try:
            num_str = Prompt.ask("  Enter number").strip()
        except (EOFError, KeyboardInterrupt):
            return

    try:
        num = int(num_str)
        if 1 <= num <= len(matrix_names):
            name = matrix_names[num - 1]
            _show_matrix_resolution(matrices[name], settings)
        else:
            console.print(f"  [red]Invalid number. Enter 1-{len(matrix_names)}.[/red]")
    except ValueError:
        console.print("  [red]Invalid input. Enter a number.[/red]")


@routing_group.command("manage")
@click.option(
    "--scope",
    default="global",
    type=click.Choice(["global", "project", "local"]),
    help="Initial write scope for settings.",
)
def routing_manage(scope: str):
    """Interactive routing matrix management dashboard."""
    validate_scope_cli(scope)
    settings = _get_settings()
    routing_manage_loop(settings, scope=cast(Scope, scope))


# ============================================================
# Helpers: role discovery + custom matrix saving
# ============================================================


def discover_roles_from_matrices(matrix_files: list[Path]) -> dict[str, str]:
    """Discover all unique roles and descriptions from matrix files.

    Loads each YAML file, extracts role names and descriptions.
    First description wins when a role appears in multiple matrices.

    Returns:
        Dict mapping role_name -> description.
    """
    roles: dict[str, str] = {}
    for path in matrix_files:
        data = _load_matrix(path)
        if not data:
            continue
        for role_name, role_config in data.get("roles", {}).items():
            if role_name not in roles:
                desc = role_config.get("description", "")
                roles[role_name] = desc
    return roles


def save_custom_matrix(
    name: str,
    assignments: dict[str, dict[str, str]],
    output_dir: Path,
) -> Path:
    """Save a custom routing matrix to YAML.

    Args:
        name: Matrix name (used as filename and in YAML).
        assignments: Dict of role_name -> {description, provider, model}.
        output_dir: Directory to write the YAML file.

    Returns:
        Path to the saved file.
    """
    import datetime

    output_dir.mkdir(parents=True, exist_ok=True)

    roles: dict[str, Any] = {}
    for role_name, info in assignments.items():
        roles[role_name] = {
            "description": info["description"],
            "candidates": [
                {
                    "provider": info["provider"],
                    "model": info["model"],
                },
            ],
        }

    matrix_data = {
        "name": name,
        "description": f"Custom matrix: {name}",
        "updated": datetime.date.today().isoformat(),
        "roles": roles,
    }

    output_path = output_dir / f"{name}.yaml"
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(matrix_data, f, default_flow_style=False, sort_keys=False)

    return output_path


# ============================================================
# routing create — interactive matrix creator
# ============================================================


def _get_provider_names(settings: AppSettings) -> list[str]:
    """Get list of configured provider type names."""
    providers = settings.get_provider_overrides()
    names: list[str] = []
    for p in providers:
        module = p.get("module", "")
        if module.startswith("provider-"):
            names.append(module.removeprefix("provider-"))
        else:
            names.append(module)
    return names


def _list_models_for_provider(provider_name: str) -> list[str]:
    """List available models for a provider. Returns model name strings."""
    try:
        from ..provider_loader import get_provider_models

        models = get_provider_models(provider_name)
        return [str(getattr(m, "name", m)) for m in models]
    except Exception:
        return []


def _prompt_provider_and_model(
    role_name: str,
    role_desc: str,
    provider_names: list[str],
) -> tuple[str, str] | None:
    """Prompt user to select a provider and model for a role.

    Returns (provider, model) or None if skipped.
    """
    console.print(f"\n  [bold cyan]{role_name}[/bold cyan]: {role_desc}")

    # Show providers as numbered list + skip option
    for i, pname in enumerate(provider_names, 1):
        console.print(f"    [{i}] {pname}")
    console.print("    [s] Skip")

    try:
        choice = Prompt.ask("    Provider", default="s").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice == "s":
        return None

    try:
        idx = int(choice)
        if idx < 1 or idx > len(provider_names):
            console.print("    [red]Invalid choice.[/red]")
            return None
        provider = provider_names[idx - 1]
    except ValueError:
        console.print("    [red]Invalid choice.[/red]")
        return None

    # Try listing models from the provider
    console.print(f"    [dim]Loading models for {provider}...[/dim]")
    models = _list_models_for_provider(provider)

    if models:
        for i, m in enumerate(models, 1):
            console.print(f"      [{i}] {m}")
        try:
            model_choice = Prompt.ask("    Model number or name").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        try:
            midx = int(model_choice)
            if 1 <= midx <= len(models):
                model = models[midx - 1]
            else:
                model = model_choice
        except ValueError:
            model = model_choice
    else:
        console.print(
            "    [dim]Could not list models. Enter model name manually.[/dim]"
        )
        try:
            model = Prompt.ask("    Model name").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    if not model:
        return None

    return provider, model


def _routing_create_interactive(settings: AppSettings) -> None:
    """Interactive custom matrix creation. Callable from CLI or manage loop."""
    provider_names = _get_provider_names(settings)

    if not provider_names:
        console.print(
            "[yellow]No providers configured. Run: amplifier provider add[/yellow]"
        )
        return

    # Discover roles from existing matrices
    matrix_files = _discover_matrix_files()
    roles = discover_roles_from_matrices(matrix_files)

    if not roles:
        # Minimal default roles
        roles = {
            "general": "Balanced catch-all for unspecialized tasks",
            "fast": "Quick parsing, classification, utility work",
        }

    console.print("\n[bold]Create Custom Routing Matrix[/bold]")
    console.print(f"[dim]Providers: {', '.join(provider_names)}[/dim]\n")

    # Walk through each role
    assignments: dict[str, dict[str, str]] = {}
    for role_name, role_desc in roles.items():
        result = _prompt_provider_and_model(role_name, role_desc, provider_names)
        if result:
            provider, model = result
            assignments[role_name] = {
                "description": role_desc,
                "provider": provider,
                "model": model,
            }
            console.print(
                f"    [green]\u2713 {role_name} \u2192 {provider} / {model}[/green]"
            )

    # Ensure required roles
    for required in ("general", "fast"):
        if required not in assignments:
            console.print(
                f"\n[yellow]Required role '{required}' was skipped. "
                f"Please assign it.[/yellow]"
            )
            result = _prompt_provider_and_model(
                required, roles.get(required, ""), provider_names
            )
            if result:
                provider, model = result
                assignments[required] = {
                    "description": roles.get(required, ""),
                    "provider": provider,
                    "model": model,
                }
                console.print(
                    f"    [green]\u2713 {required} \u2192 {provider} / {model}[/green]"
                )
            else:
                console.print("[red]Cannot create matrix without required roles.[/red]")
                return

    # Summary table
    console.print("\n")
    summary = Table(title="Matrix Summary")
    summary.add_column("Role", style="cyan")
    summary.add_column("Provider")
    summary.add_column("Model", style="green")
    for rname, rinfo in assignments.items():
        summary.add_row(rname, rinfo["provider"], rinfo["model"])
    console.print(summary)

    # Post-summary menu loop
    while True:
        console.print("\n  [a] Add a custom role")
        console.print("  [r] Remove a custom-added role")
        console.print("  [e] Edit a role's assignment")
        console.print("  [s] Save")
        console.print("  [q] Quit without saving")

        try:
            action = Prompt.ask("  Action", default="s").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Cancelled.[/dim]")
            return

        if action == "q":
            console.print("[dim]Cancelled.[/dim]")
            return
        elif action == "s":
            break
        elif action == "a":
            try:
                new_name = Prompt.ask("  Role name").strip()
                new_desc = Prompt.ask("  Description").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            result = _prompt_provider_and_model(new_name, new_desc, provider_names)
            if result:
                provider, model = result
                assignments[new_name] = {
                    "description": new_desc,
                    "provider": provider,
                    "model": model,
                }
                console.print(
                    f"    [green]\u2713 {new_name} \u2192 {provider} / {model}[/green]"
                )
        elif action == "r":
            try:
                rm_name = Prompt.ask("  Role to remove").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if rm_name in ("general", "fast"):
                console.print(f"  [red]Cannot remove required role '{rm_name}'.[/red]")
            elif rm_name in assignments:
                del assignments[rm_name]
                console.print(f"  [green]Removed '{rm_name}'.[/green]")
            else:
                console.print(f"  [yellow]Role '{rm_name}' not found.[/yellow]")
        elif action == "e":
            try:
                edit_name = Prompt.ask("  Role to edit").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if edit_name in assignments:
                desc = assignments[edit_name]["description"]
                result = _prompt_provider_and_model(edit_name, desc, provider_names)
                if result:
                    provider, model = result
                    assignments[edit_name]["provider"] = provider
                    assignments[edit_name]["model"] = model
                    console.print(
                        f"    [green]\u2713 {edit_name} \u2192 {provider} / {model}[/green]"
                    )
            else:
                console.print(f"  [yellow]Role '{edit_name}' not found.[/yellow]")

    # Save
    try:
        matrix_name = Prompt.ask("  Matrix name").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
        return

    if not matrix_name:
        console.print("[red]Name cannot be empty.[/red]")
        return

    output_dir = Path.home() / ".amplifier" / "routing"
    saved = save_custom_matrix(matrix_name, assignments, output_dir)
    console.print(f"\n[green]\u2713 Saved to {saved}[/green]")


@routing_group.command("create")
def routing_create():
    """Interactively create a custom routing matrix."""
    settings = _get_settings()
    _routing_create_interactive(settings)
