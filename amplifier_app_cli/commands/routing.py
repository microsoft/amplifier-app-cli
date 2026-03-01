"""Routing matrix management commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..lib.settings import AppSettings

console = Console()


def _get_settings() -> AppSettings:
    """Get AppSettings instance. Extracted for testability."""
    return AppSettings()


def _discover_matrix_files() -> list[Path]:
    """Discover available routing matrix YAML files from the bundle cache.

    Looks in ~/.amplifier/cache/amplifier-bundle-routing-matrix-*/routing/*.yaml
    """
    cache_base = Path.home() / ".amplifier" / "cache"
    if not cache_base.exists():
        return []

    files: list[Path] = []
    for bundle_dir in cache_base.glob("amplifier-bundle-routing-matrix-*"):
        routing_dir = bundle_dir / "routing"
        if routing_dir.is_dir():
            files.extend(routing_dir.glob("*.yaml"))
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

    settings.set_routing_matrix(matrix_name, scope=scope)  # type: ignore[arg-type]
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


def routing_manage_loop(settings: AppSettings) -> None:
    """Interactive routing management loop.

    Callable from CLI command or from init dashboard.
    """
    while True:
        # 1. Show active matrix name
        routing_config = settings.get_routing_config()
        active_matrix = routing_config.get("matrix", "balanced")
        console.print(f"\n  Active Routing Matrix: [bold]{active_matrix}[/bold]\n")

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
        console.print("    \\[d] Done")
        console.print()

        try:
            choice = Prompt.ask("  Choice", default="d").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "d":
            break
        elif choice.startswith("s"):
            _manage_select_matrix(settings, choice, matrices)
        elif choice.startswith("v"):
            _manage_view_matrix(settings, choice, matrices)


def _manage_select_matrix(
    settings: AppSettings,
    choice: str,
    matrices: dict[str, dict[str, Any]],
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
            settings.set_routing_matrix(name, scope="global")
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
def routing_manage():
    """Interactive routing matrix management dashboard."""
    settings = _get_settings()
    routing_manage_loop(settings)
