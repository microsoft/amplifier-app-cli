"""Routing matrix management commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import click
import yaml
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..lib.settings import AppSettings, Scope
from ..provider_loader import get_provider_info, get_provider_models
from ..ui.scope import (
    is_scope_change_available,
    print_scope_indicator,
    prompt_scope_change,
    validate_scope_cli,
)

console = Console()

INFRASTRUCTURE_CONFIG_FIELDS = frozenset(
    {
        "base_url",
        "api_key",
        "host",
        "azure_endpoint",
        "api_version",
        "deployment_name",
        "managed_identity_client_id",
        "use_managed_identity",
        "use_default_credential",
    }
)


def _get_routing_config_fields(provider_id: str) -> list[dict[str, Any]]:
    """Get config fields from a provider that are relevant for routing matrix candidates.

    Filters out secrets (API keys) and infrastructure fields (base_url, endpoints).
    Returns only model-behavior fields (reasoning_effort, 1M context, prompt caching, etc.).

    Returns empty list if provider info is unavailable.
    """
    info = get_provider_info(provider_id)
    config_fields = info.get("config_fields", []) if info else []
    return [
        field
        for field in config_fields
        if field.get("field_type") != "secret"
        and field.get("id") not in INFRASTRUCTURE_CONFIG_FIELDS
    ]


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
    validate_scope_cli(scope)
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
@click.option(
    "--detailed",
    is_flag=True,
    default=False,
    help="Show full candidate waterfall instead of resolved view.",
)
def routing_show(matrix_name: str | None, detailed: bool):
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
    if detailed:
        _show_matrix_details(matrix_data, settings)
    else:
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


def _show_matrix_details(matrix_data: dict[str, Any], settings: AppSettings) -> None:
    """Display the full candidate waterfall for a routing matrix.

    Shows every candidate for each role with ★/✓/✗ indicators based on
    whether the provider is configured, and highlights the active winner.
    """
    name = matrix_data.get("name", "unknown")
    description = matrix_data.get("description", "")
    updated = str(matrix_data.get("updated", ""))

    # Header
    console.print(f"\n  Matrix: [bold]{name}[/bold]")
    if description:
        console.print(f"  {description}")
    if updated:
        console.print(f"  Updated: {updated}")

    provider_types = _get_configured_provider_types(settings)
    roles = matrix_data.get("roles", {})

    for role_name, role_config in roles.items():
        role_desc = role_config.get("description", "")
        header = f"\n  [bold cyan]{role_name}[/bold cyan]"
        if role_desc:
            header += f" — {role_desc}"
        console.print(header)

        candidates = role_config.get("candidates", [])
        winner_found = False

        for candidate in candidates:
            provider = candidate.get("provider", "")
            model = candidate.get("model", "?")
            config = candidate.get("config", {})

            # Build inline config string if present
            # Use \[ to escape the literal bracket from Rich's markup parser
            config_str = ""
            if config:
                pairs = ", ".join(f"{k}: {v}" for k, v in config.items())
                config_str = f"  [dim]\\[{pairs}][/dim]"

            is_configured = provider in provider_types

            if is_configured and not winner_found:
                winner_found = True
                line = (
                    f"    [green]★ {provider} / {model}[/green]"
                    f"{config_str}  [green]← active[/green]"
                )
            elif is_configured:
                line = f"    [dim]✓ {provider} / {model}[/dim]{config_str}"
            else:
                line = (
                    f"    [dim]✗ {provider} / {model}[/dim]"
                    f"{config_str}  [dim]not configured[/dim]"
                )

            console.print(line)

        if not winner_found:
            console.print(
                "    [yellow]⚠ No configured provider can serve this role[/yellow]"
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
        console.print("    \\[v] View full details of a matrix")
        console.print("    \\[c] Create / edit custom matrix")
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
            _routing_edit_matrix(settings)


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
            _show_matrix_details(matrices[name], settings)
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
    from .provider import _ensure_providers_ready

    try:
        _ensure_providers_ready()
    except SystemExit:
        pass
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
    """Get list of unique configured provider type names."""
    providers = settings.get_provider_overrides()
    seen: set[str] = set()
    names: list[str] = []
    for p in providers:
        module = p.get("module", "")
        name = (
            module.removeprefix("provider-")
            if module.startswith("provider-")
            else module
        )
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _get_provider_config(
    provider_name: str, settings: AppSettings
) -> dict[str, Any] | None:
    """Look up the stored config dict for a provider by type name or module name.

    Accepts either the short type name (e.g. ``"anthropic"``) or the full module
    name (e.g. ``"provider-anthropic"``).  Returns the provider's ``config``
    sub-dict, or ``None`` if no matching provider is found.
    """
    for p in settings.get_provider_overrides():
        p_module = p.get("module", "")
        p_type = (
            p_module.removeprefix("provider-")
            if p_module.startswith("provider-")
            else p_module
        )
        if p_type == provider_name or p_module == provider_name:
            return p.get("config", {})
    return None


def _build_model_cache(
    provider_names: list[str], settings: AppSettings
) -> dict[str, list]:
    """Fetch models for all providers upfront. Returns provider_name → [ModelInfo]."""
    model_cache: dict[str, list] = {}
    with console.status(
        "[dim]Fetching models for all providers...[/dim]", spinner="dots"
    ):
        for pname in provider_names:
            try:
                cfg = _get_provider_config(pname, settings)
                models = get_provider_models(pname, collected_config=cfg)
                model_cache[pname] = models
            except Exception:
                model_cache[pname] = []
    for pname in provider_names:
        count = len(model_cache[pname])
        if count:
            console.print(f"  [green]✓[/green] {pname}: {count} model(s)")
        else:
            console.print(f"  [yellow]✗[/yellow] {pname}: could not fetch models")
    console.print()
    return model_cache


def _list_models_for_provider(
    provider_name: str, settings: AppSettings | None = None
) -> list[str]:
    """List available models for a provider. Returns model name strings.

    If settings is provided, looks up the provider's config (API keys, base_url, etc.)
    and passes it to get_provider_models for authenticated model listing.
    """
    try:
        from ..provider_loader import get_provider_models

        collected_config = (
            _get_provider_config(provider_name, settings) if settings else None
        )
        models = get_provider_models(provider_name, collected_config=collected_config)
        return [str(getattr(m, "name", m)) for m in models]
    except Exception:
        return []


def _prompt_provider_and_model(
    role_name: str,
    role_desc: str,
    provider_names: list[str],
    settings: AppSettings | None = None,
    model_cache: dict[str, list] | None = None,
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

    # Get cached models (raw ModelInfo objects) or None
    cached_models = model_cache.get(provider) if model_cache else None

    # Look up provider config from settings for authenticated model fetching
    provider_config = _get_provider_config(provider, settings) if settings else None

    from ..provider_config_utils import _prompt_model_selection

    provider_id = (
        f"provider-{provider}" if not provider.startswith("provider-") else provider
    )
    selected = _prompt_model_selection(
        provider_id,
        default_model=None,
        collected_config=provider_config,
        models=cached_models,
    )

    if selected is None:  # Ctrl-C or cancel
        return None

    model = selected

    if not model:
        return None

    return provider, model


def _edit_role(
    role_name: str,
    role_desc: str,
    provider_names: list[str],
    settings: AppSettings,
    model_cache: dict[str, list] | None = None,
    current_candidate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Edit a routing matrix role: pick provider, model, and config.

    Args:
        role_name: Name of the role being edited (e.g., "security-audit")
        role_desc: Description of the role
        provider_names: List of configured provider type names
        settings: AppSettings instance for config lookup
        model_cache: Pre-fetched ModelInfo objects per provider (from upfront cache)
        current_candidate: Existing candidate dict if editing (for defaults).
            Has keys: provider, model, config

    Returns:
        Dict with {provider, model, config} for the candidate, or None if cancelled/skipped.
    """
    # Step 1 — Print role header
    console.print(f"\n  [bold cyan]{role_name}[/bold cyan]: {role_desc}")

    # Step 2 — Provider selection
    for i, pname in enumerate(provider_names, 1):
        marker = (
            "→ "
            if current_candidate and pname == current_candidate.get("provider")
            else "  "
        )
        console.print(f"  {marker}[{i}] {pname}")
    console.print(
        "    [s] Skip (keep current)" if current_candidate else "    [s] Skip"
    )

    try:
        default = "s"
        if current_candidate:
            for i, pname in enumerate(provider_names, 1):
                if pname == current_candidate.get("provider"):
                    default = str(i)
                    break
        choice = Prompt.ask("    Provider", default=default).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice == "s":
        return current_candidate  # Keep existing or skip (None if no current)

    try:
        idx = int(choice)
        if idx < 1 or idx > len(provider_names):
            console.print("    [red]Invalid choice.[/red]")
            return None
        provider = provider_names[idx - 1]
    except ValueError:
        console.print("    [red]Invalid choice.[/red]")
        return None

    # Step 3 — Model selection
    cached_models = model_cache.get(provider) if model_cache else None

    # Look up provider config from settings
    provider_config = _get_provider_config(provider, settings)

    from ..provider_config_utils import _prompt_model_selection

    default_model = (
        current_candidate.get("model")
        if current_candidate and current_candidate.get("provider") == provider
        else None
    )

    provider_id = (
        f"provider-{provider}" if not provider.startswith("provider-") else provider
    )
    selected_model = _prompt_model_selection(
        provider_id,
        default_model=default_model,
        collected_config=provider_config,
        models=cached_models,
    )

    if selected_model is None:  # Ctrl-C
        return None

    # Step 4 — Config field prompting (simplified: direct Prompt/Confirm, no env-var wrapping)
    config_fields = _get_routing_config_fields(provider_id)
    candidate_config: dict[str, Any] = {}

    if config_fields:
        console.print(f"\n  [bold]Config for {provider} / {selected_model}:[/bold]")
        existing_config = (
            current_candidate.get("config", {}) if current_candidate else {}
        )

        for field_dict in config_fields:
            field_id = field_dict.get("id", "")
            display = field_dict.get("display_name", field_id)
            field_type = field_dict.get("field_type", "text")
            default = existing_config.get(field_id) or field_dict.get("default")
            choices = field_dict.get("choices")
            description = field_dict.get("description", "")

            # Check show_when conditions (simple key=value evaluation)
            show_when = field_dict.get("show_when")
            if show_when:
                should_show = True
                for sw_key, sw_val in show_when.items():
                    current_val = str(candidate_config.get(sw_key, ""))
                    if sw_val.startswith("contains:"):
                        if sw_val[9:] not in current_val:
                            should_show = False
                    elif sw_val.startswith("not_contains:"):
                        if sw_val[13:] in current_val:
                            should_show = False
                    elif current_val != sw_val:
                        should_show = False
                if not should_show:
                    continue

            try:
                if description:
                    console.print(f"  [dim]{description}[/dim]")

                if field_type == "boolean":
                    bool_default = default in ("true", "True", True, "yes")
                    value = Confirm.ask(f"  {display}", default=bool_default)
                    candidate_config[field_id] = str(value).lower()
                elif field_type == "choice" and choices:
                    for i, c in enumerate(choices, 1):
                        marker = " (current)" if str(c) == str(default) else ""
                        console.print(f"  [{i}] {c}{marker}")
                    default_idx = None
                    if default:
                        for i, c in enumerate(choices, 1):
                            if str(c) == str(default):
                                default_idx = str(i)
                                break
                    choice_str = Prompt.ask(
                        f"  {display}",
                        choices=[str(i) for i in range(1, len(choices) + 1)],
                        default=default_idx,
                    )
                    if choice_str is not None:
                        candidate_config[field_id] = choices[int(choice_str) - 1]
                else:
                    value = Prompt.ask(
                        f"  {display}", default=str(default) if default else ""
                    )
                    if value:
                        candidate_config[field_id] = value
            except (EOFError, KeyboardInterrupt):
                return None

    # Step 5 — Return result
    return {
        "provider": provider,
        "model": selected_model,
        "config": candidate_config,
    }


def _pick_base_matrix(
    settings: AppSettings,
    matrices: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Let user pick a matrix to clone and customize.

    Shows all available matrices with indicators for active (→) and custom (custom).
    Returns a deep copy of the selected matrix data, or None if cancelled.
    """
    import copy

    routing_config = settings.get_routing_config()
    active_matrix = routing_config.get("matrix", "balanced")

    # Custom matrices live under ~/.amplifier/routing/
    custom_dir = Path.home() / ".amplifier" / "routing"

    matrix_names = sorted(matrices.keys())

    console.print("\n  Choose a matrix to customize:")

    default_idx = 1
    for i, name in enumerate(matrix_names, 1):
        is_active = name == active_matrix
        is_custom = (custom_dir / f"{name}.yaml").exists()

        if is_active:
            default_idx = i

        prefix = "→ " if is_active else "  "
        suffixes = []
        if is_active:
            suffixes.append("(active)")
        if is_custom:
            suffixes.append("(custom)")
        suffix_str = "  " + "  ".join(suffixes) if suffixes else ""
        console.print(f"    [{i}] {prefix}{name}{suffix_str}")

    try:
        choices = [str(i) for i in range(1, len(matrix_names) + 1)]
        num_str = Prompt.ask("  Matrix", choices=choices, default=str(default_idx))
        idx = int(num_str) - 1
        selected_name = matrix_names[idx]
        return copy.deepcopy(matrices[selected_name])
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
        return None


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

    # Fetch models for all providers upfront
    model_cache = _build_model_cache(provider_names, settings)

    # Walk through each role
    assignments: dict[str, dict[str, str]] = {}
    for role_name, role_desc in roles.items():
        result = _prompt_provider_and_model(
            role_name,
            role_desc,
            provider_names,
            settings=settings,
            model_cache=model_cache,
        )
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
                required,
                roles.get(required, ""),
                provider_names,
                settings=settings,
                model_cache=model_cache,
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
            result = _prompt_provider_and_model(
                new_name,
                new_desc,
                provider_names,
                settings=settings,
                model_cache=model_cache,
            )
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
                result = _prompt_provider_and_model(
                    edit_name,
                    desc,
                    provider_names,
                    settings=settings,
                    model_cache=model_cache,
                )
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

    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", matrix_name):
        console.print(
            "[red]Matrix name must start with a letter or digit and contain only "
            "letters, numbers, hyphens, and underscores (max 64 chars).[/red]"
        )
        return

    output_dir = Path.home() / ".amplifier" / "routing"
    saved = save_custom_matrix(matrix_name, assignments, output_dir)
    console.print(f"\n[green]\u2713 Saved to {saved}[/green]")


def _routing_edit_matrix(settings: AppSettings) -> None:
    """Interactive matrix editor — clone an existing matrix and customize it."""
    import datetime

    # Phase 1 — Setup
    provider_names = _get_provider_names(settings)
    if not provider_names:
        console.print(
            "[yellow]No providers configured. Run: amplifier provider add[/yellow]"
        )
        return

    matrix_files = _discover_matrix_files()
    matrices = _load_all_matrices(matrix_files)

    if not matrices:
        console.print("[yellow]No routing matrices found.[/yellow]")
        console.print(
            "[dim]Run 'amplifier update' to fetch the routing-matrix bundle.[/dim]"
        )
        return

    base_matrix = _pick_base_matrix(settings, matrices)
    if base_matrix is None:
        return

    working_copy = base_matrix  # already a deep copy from _pick_base_matrix

    # Phase 2 — Upfront model fetch
    model_cache = _build_model_cache(provider_names, settings)

    # Phase 3 — Edit loop
    changed = False
    roles = working_copy.get("roles", {})

    while True:
        # Show numbered role index then waterfall view
        role_list = list(roles.keys())
        console.print("  Roles:")
        for i, rname in enumerate(role_list, 1):
            console.print(f"  [{i}] {rname}")
        console.print()
        _show_matrix_details(working_copy, settings)

        console.print("\n  Actions:")
        console.print("    [e<N>] Edit a role (e.g., e1)")
        console.print("    [a] Add a new role")
        console.print("    [r<N>] Remove a role")
        console.print("    [s] Save")
        console.print("    [q] Quit without saving")
        console.print()

        try:
            action = Prompt.ask("  Action", default="s").strip().lower()
        except (EOFError, KeyboardInterrupt):
            if changed:
                try:
                    if not Confirm.ask("  Unsaved changes. Quit?", default=False):
                        continue
                except (EOFError, KeyboardInterrupt):
                    pass
            return

        if action == "q":
            if changed:
                try:
                    if not Confirm.ask("  Unsaved changes. Quit?", default=False):
                        continue
                except (EOFError, KeyboardInterrupt):
                    pass
            return

        elif action == "s":
            break  # Go to save phase

        elif action == "a":
            # Add a new role
            try:
                new_name = Prompt.ask("  Role name").strip()
                new_desc = Prompt.ask("  Description").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not new_name:
                console.print("  [red]Name cannot be empty.[/red]")
                continue
            if new_name in roles:
                console.print(
                    f"  [yellow]Role '{new_name}' already exists. "
                    f"Use [e] to edit it.[/yellow]"
                )
                continue
            result = _edit_role(
                new_name, new_desc, provider_names, settings, model_cache=model_cache
            )
            if result:
                roles[new_name] = {
                    "description": new_desc,
                    "candidates": [result],
                }
                changed = True
                console.print(
                    f"  [green]\u2713 Added {new_name} \u2192 "
                    f"{result['provider']} / {result['model']}[/green]"
                )

        elif action.startswith("r"):
            # Remove a role
            num_str = action[1:].strip()
            if not num_str:
                try:
                    num_str = Prompt.ask("  Role number to remove").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
            try:
                num = int(num_str)
                if num < 1 or num > len(role_list):
                    console.print(f"  [red]Invalid. Enter 1-{len(role_list)}.[/red]")
                    continue
                role_name = role_list[num - 1]
            except ValueError:
                console.print("  [red]Invalid number.[/red]")
                continue

            if role_name in ("general", "fast"):
                console.print(
                    f"  [red]Cannot remove required role '{role_name}'.[/red]"
                )
                continue

            try:
                if not Confirm.ask(f"  Remove '{role_name}'?", default=False):
                    continue
            except (EOFError, KeyboardInterrupt):
                continue

            del roles[role_name]
            changed = True
            console.print(f"  [green]\u2713 Removed {role_name}[/green]")

        elif action.startswith("e"):
            # Edit a role
            num_str = action[1:].strip()
            if not num_str:
                try:
                    num_str = Prompt.ask("  Role number to edit").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
            try:
                num = int(num_str)
                if num < 1 or num > len(role_list):
                    console.print(f"  [red]Invalid. Enter 1-{len(role_list)}.[/red]")
                    continue
                role_name = role_list[num - 1]
            except ValueError:
                console.print("  [red]Invalid number.[/red]")
                continue

            role_data = roles[role_name]
            role_desc = role_data.get("description", "")
            candidates = role_data.get("candidates", [])
            current_candidate = candidates[0] if candidates else None

            result = _edit_role(
                role_name,
                role_desc,
                provider_names,
                settings,
                model_cache=model_cache,
                current_candidate=current_candidate,
            )
            if result and result != current_candidate:
                roles[role_name]["candidates"] = [result]
                changed = True
                config_str = ""
                if result.get("config"):
                    pairs = ", ".join(f"{k}: {v}" for k, v in result["config"].items())
                    config_str = f"  [{pairs}]"
                console.print(
                    f"  [green]\u2713 {role_name} \u2192 "
                    f"{result['provider']} / {result['model']}{config_str}[/green]"
                )

    # Phase 4 — Save
    try:
        default_name = working_copy.get("name", "custom")
        matrix_name = Prompt.ask("  Matrix name", default=default_name).strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
        return

    if not matrix_name:
        console.print("[red]Name cannot be empty.[/red]")
        return

    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", matrix_name):
        console.print(
            "[red]Matrix name must start with a letter or digit and contain only "
            "letters, numbers, hyphens, and underscores (max 64 chars).[/red]"
        )
        return

    output_dir = Path.home() / ".amplifier" / "routing"
    output_file = output_dir / f"{matrix_name}.yaml"

    # Check for overwrite
    if output_file.exists():
        try:
            if not Confirm.ask(
                f"  '{matrix_name}' already exists. Overwrite?", default=False
            ):
                return
        except (EOFError, KeyboardInterrupt):
            return

    # Build matrix dict — write directly to support config in candidates
    matrix_roles: dict[str, Any] = {}
    for role_name, role_data in roles.items():
        candidates_out: list[dict[str, Any]] = []
        for cand in role_data.get("candidates", []):
            cand_entry: dict[str, Any] = {
                "provider": cand.get("provider", ""),
                "model": cand.get("model", ""),
            }
            if cand.get("config"):
                cand_entry["config"] = cand["config"]
            candidates_out.append(cand_entry)
        matrix_roles[role_name] = {
            "description": role_data.get("description", ""),
            "candidates": candidates_out,
        }

    matrix_data: dict[str, Any] = {
        "name": matrix_name,
        "description": f"Custom matrix: {matrix_name}",
        "updated": datetime.date.today().isoformat(),
        "roles": matrix_roles,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(matrix_data, f, default_flow_style=False, sort_keys=False)

    console.print(f"\n[green]\u2713 Saved to {output_file}[/green]")


@routing_group.command("create")
def routing_create():
    """Interactively create a custom routing matrix."""
    settings = _get_settings()
    _routing_create_interactive(settings)
