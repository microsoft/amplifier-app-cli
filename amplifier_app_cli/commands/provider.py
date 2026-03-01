"""Provider management commands."""

import time
from typing import Any

import click
from rich.console import Console
from rich.prompt import Confirm
from rich.prompt import Prompt
from rich.table import Table

from ..key_manager import KeyManager
from ..lib.settings import AppSettings
from ..paths import create_config_manager
from ..provider_config_utils import configure_provider
from ..provider_loader import get_provider_models
from ..provider_manager import ProviderManager
from ..provider_sources import ensure_provider_installed
from ..provider_sources import get_effective_provider_sources
from ..provider_sources import install_known_providers
from ..utils.error_format import escape_markup

console = Console()


def _get_settings() -> AppSettings:
    """Get AppSettings instance. Extracted for testability."""
    return AppSettings()


def _ensure_providers_ready() -> None:
    """Ensure provider modules are installed (post-update fix).

    Called by subcommands that need providers to be available.
    The 'install' subcommand skips this since it IS the install.
    """
    from .init import check_first_run

    check_first_run()


def _normalize_module_id(provider_id: str) -> str:
    """Normalize provider ID to full module ID."""
    if provider_id.startswith("provider-"):
        return provider_id
    return f"provider-{provider_id}"


def _display_name(module_id: str) -> str:
    """Get display name from module ID (strip provider- prefix)."""
    return module_id.replace("provider-", "")


def _find_provider_entry(
    providers: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    """Find a provider entry by name/id.

    Matches against:
    - 'id' field (for multi-instance providers)
    - 'module' field stripped of 'provider-' prefix
    - full 'module' field
    """
    for p in providers:
        # Match by id field
        if p.get("id") == name:
            return p
        # Match by module name (with or without prefix)
        module = p.get("module", "")
        if (
            module == name
            or module == f"provider-{name}"
            or _display_name(module) == name
        ):
            return p
    return None


def _get_max_priority(providers: list[dict[str, Any]]) -> int:
    """Get the maximum priority value from configured providers."""
    max_pri = 0
    for p in providers:
        config = p.get("config", {})
        if isinstance(config, dict):
            pri = config.get("priority", 0)
            if isinstance(pri, int) and pri > max_pri:
                max_pri = pri
    return max_pri


@click.group()
def provider():
    """Manage AI providers."""
    pass


# ============================================================
# provider install (kept from original)
# ============================================================


@provider.command("install")
@click.argument("provider_ids", nargs=-1)
@click.option("--all", "install_all", is_flag=True, help="Install all known providers")
@click.option(
    "--quiet", "-q", is_flag=True, help="Suppress progress output (for CI/CD)"
)
@click.option("--force", is_flag=True, help="Reinstall even if already installed")
@click.pass_context
def provider_install(
    ctx: click.Context,
    provider_ids: tuple[str, ...],
    install_all: bool,
    quiet: bool,
    force: bool,
) -> None:
    """Install provider modules.

    Downloads and installs provider modules without configuring them.
    Useful for CI/CD, pre-init setup, or recovering after updates.

    If no PROVIDER_IDs are specified, installs all known providers.

    Examples:
      amplifier provider install              # Install all providers
      amplifier provider install anthropic    # Install just Anthropic
      amplifier provider install anthropic openai  # Install specific providers
      amplifier provider install -q           # Silent install (CI/CD)
      amplifier provider install --force      # Reinstall even if installed
    """
    config_manager = create_config_manager()
    sources = get_effective_provider_sources(config_manager)

    # Determine which providers to install
    if provider_ids:
        # Validate and normalize provider IDs
        normalized_ids: list[str] = []
        for pid in provider_ids:
            module_id = pid if pid.startswith("provider-") else f"provider-{pid}"
            if module_id not in sources:
                console.print(f"[red]Error:[/red] Unknown provider '{pid}'")
                console.print("\nKnown providers:")
                for known_id in sorted(sources.keys()):
                    console.print(f"  - {known_id.replace('provider-', '')}")
                ctx.exit(1)
            normalized_ids.append(module_id)

        # Install specific providers
        failed: list[str] = []
        for module_id in normalized_ids:
            display = module_id.replace("provider-", "")

            # Check if already installed (unless --force)
            if not force:
                try:
                    import importlib.metadata

                    eps = importlib.metadata.entry_points(group="amplifier.modules")
                    already_installed = any(ep.name == module_id for ep in eps)
                    if already_installed:
                        if not quiet:
                            console.print(
                                f"[dim]{display} already installed (use --force to reinstall)[/dim]"
                            )
                        continue
                except Exception:
                    pass  # Continue with installation if check fails

            success = ensure_provider_installed(
                module_id,
                config_manager=config_manager,
                console=None if quiet else console,
            )
            if not success:
                failed.append(display)

        if failed:
            if not quiet:
                console.print(f"\n[red]Failed to install: {', '.join(failed)}[/red]")
            ctx.exit(1)
        elif not quiet:
            console.print("\n[green]✓ Provider installation complete[/green]")

    else:
        # Install all known providers (default behavior or --all flag)
        if not quiet:
            console.print("[bold]Installing all known providers...[/bold]")

        installed = install_known_providers(
            config_manager=config_manager,
            console=None if quiet else console,
            verbose=not quiet,
        )

        if not installed and not quiet:
            console.print("[red]No providers were installed[/red]")
            ctx.exit(1)
        elif not quiet:
            console.print(f"\n[green]✓ Installed {len(installed)} provider(s)[/green]")


# ============================================================
# Task 7: provider add [type]
# ============================================================


@provider.command("add")
@click.argument("provider_type", required=False)
def provider_add(provider_type: str | None) -> None:
    """Add and configure a provider.

    If PROVIDER_TYPE is not specified, shows an interactive picker.

    Examples:
      amplifier provider add anthropic
      amplifier provider add openai
      amplifier provider add          # interactive picker
    """
    _ensure_providers_ready()

    settings = _get_settings()
    provider_mgr = ProviderManager(settings)

    # If type not provided, show picker
    if provider_type is None:
        available = provider_mgr.list_providers()
        if not available:
            console.print(
                "[red]No providers available. Run: amplifier provider install[/red]"
            )
            return

        console.print("\n[bold]Available providers:[/bold]")
        provider_map: dict[str, str] = {}
        for idx, (module_id, name, _desc) in enumerate(available, 1):
            provider_map[str(idx)] = module_id
            console.print(f"  [{idx}] {name}")

        choice = Prompt.ask("Which provider?", choices=list(provider_map.keys()))
        module_id = provider_map[choice]
    else:
        module_id = _normalize_module_id(provider_type)

    display = _display_name(module_id)

    # Check for multi-instance: is there already a provider with this module?
    existing_providers = settings.get_provider_overrides()
    same_module = [p for p in existing_providers if p.get("module") == module_id]
    instance_id: str | None = None

    if same_module:
        console.print(f"\n[yellow]A {display} provider is already configured.[/yellow]")
        suggested_id = f"{display}-{len(same_module) + 1}"
        instance_id = Prompt.ask("Enter an ID for this instance", default=suggested_id)

    # Run the configuration wizard
    key_manager = KeyManager()
    config = configure_provider(module_id, key_manager)

    if config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

    # Determine priority: first provider = 1, subsequent = max_existing + 1
    if not existing_providers:
        priority = 1
    else:
        priority = _get_max_priority(existing_providers) + 1

    config_with_priority = {**config, "priority": priority}

    # Build provider entry
    provider_entry: dict[str, Any] = {
        "module": module_id,
        "config": config_with_priority,
    }
    if instance_id:
        provider_entry["id"] = instance_id

    # Determine source
    effective_sources = get_effective_provider_sources(settings)
    source = effective_sources.get(module_id)
    if source:
        provider_entry["source"] = source

    # Save to settings — use raw write to preserve priority properly
    # We append to the existing providers list rather than using set_provider_override
    # which would demote existing providers
    scope_providers = settings.get_scope_provider_overrides("global")

    # For multi-instance, just append. For single instance, replace matching module.
    if instance_id:
        scope_providers.append(provider_entry)
    else:
        # Replace any existing entry with same module
        scope_providers = [p for p in scope_providers if p.get("module") != module_id]
        scope_providers.append(provider_entry)

    # Write back
    scope_settings = settings._read_scope("global")
    if "config" not in scope_settings:
        scope_settings["config"] = {}
    scope_settings["config"]["providers"] = scope_providers
    settings._write_scope("global", scope_settings)

    # Show confirmation
    model = config.get("default_model", "")
    model_display = f" ({model})" if model else ""
    name_display = instance_id or display
    console.print(f"\n[green]✓ Provider added: {name_display}{model_display}[/green]")


# ============================================================
# Task 8: provider list (redesigned)
# ============================================================


@provider.command("list")
def provider_list() -> None:
    """List configured providers.

    Shows all configured providers with their type, model, priority, and status.
    The primary provider (lowest priority) is marked with ★.
    """
    _ensure_providers_ready()

    settings = _get_settings()
    providers = settings.get_provider_overrides()

    if not providers:
        console.print("[yellow]No providers configured.[/yellow]")
        console.print("Run: [cyan]amplifier provider add[/cyan]")
        return

    table = Table(title="Configured Providers")
    table.add_column("Name/ID", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Default Model")
    table.add_column("Priority", justify="right")
    table.add_column("Status")

    # Find min priority for star marker
    priorities = []
    for p in providers:
        config = p.get("config", {})
        pri = config.get("priority", 100) if isinstance(config, dict) else 100
        priorities.append(pri)
    min_priority = min(priorities) if priorities else 0

    for idx, p in enumerate(providers):
        module = p.get("module", "unknown")
        display = p.get("id") or _display_name(module)
        ptype = _display_name(module)
        config = p.get("config", {})
        model = config.get("default_model", "-") if isinstance(config, dict) else "-"
        pri = config.get("priority", 100) if isinstance(config, dict) else 100

        # Star marker for primary (lowest priority)
        is_primary = pri == min_priority
        name_col = f"★ {display}" if is_primary else f"  {display}"
        status = "configured"

        table.add_row(name_col, ptype, model, str(pri), status)

    console.print(table)


# ============================================================
# Task 9: provider remove and provider edit
# ============================================================


@provider.command("remove")
@click.argument("name")
def provider_remove(name: str) -> None:
    """Remove a configured provider.

    NAME can be a provider type (e.g., 'anthropic') or instance ID.

    Examples:
      amplifier provider remove anthropic
      amplifier provider remove anthropic-2
    """
    _ensure_providers_ready()

    settings = _get_settings()
    providers = settings.get_provider_overrides()

    entry = _find_provider_entry(providers, name)
    if entry is None:
        console.print(f"[red]Provider '{name}' not found.[/red]")
        if providers:
            console.print("\nConfigured providers:")
            for p in providers:
                display = p.get("id") or _display_name(p.get("module", ""))
                console.print(f"  • {display}")
        return

    # Show what will be removed
    module = entry.get("module", "unknown")
    display = entry.get("id") or _display_name(module)
    config = entry.get("config", {})
    model = config.get("default_model", "") if isinstance(config, dict) else ""
    console.print(f"\nWill remove: [bold]{display}[/bold]")
    if model:
        console.print(f"  Model: {model}")

    if not Confirm.ask("Continue?", default=False):
        console.print("[dim]Cancelled.[/dim]")
        return

    # Remove from settings — remove from all scopes
    for scope in ("local", "project", "global"):
        scope_providers = settings.get_scope_provider_overrides(scope)  # type: ignore[arg-type]
        original_len = len(scope_providers)

        # Filter out the matching entry
        filtered = []
        for p in scope_providers:
            if p.get("id") == entry.get("id") and entry.get("id"):
                continue  # Remove by id match
            if p.get("module") == entry.get("module") and not entry.get("id"):
                continue  # Remove by module match (no id = first instance)
            filtered.append(p)

        if len(filtered) < original_len:
            scope_settings = settings._read_scope(scope)  # type: ignore[arg-type]
            config_section = scope_settings.get("config", {})
            if filtered:
                config_section["providers"] = filtered
            else:
                config_section.pop("providers", None)
            if config_section:
                scope_settings["config"] = config_section
            elif "config" in scope_settings:
                scope_settings.pop("config", None)
            settings._write_scope(scope, scope_settings)  # type: ignore[arg-type]

    console.print(f"\n[green]✓ Removed provider: {display}[/green]")


@provider.command("edit")
@click.argument("name")
def provider_edit(name: str) -> None:
    """Re-configure an existing provider.

    Opens the configuration wizard with current values as defaults.

    Examples:
      amplifier provider edit anthropic
      amplifier provider edit anthropic-2
    """
    _ensure_providers_ready()

    settings = _get_settings()
    providers = settings.get_provider_overrides()

    entry = _find_provider_entry(providers, name)
    if entry is None:
        console.print(f"[red]Provider '{name}' not found.[/red]")
        if providers:
            console.print("\nConfigured providers:")
            for p in providers:
                display = p.get("id") or _display_name(p.get("module", ""))
                console.print(f"  • {display}")
        return

    module_id = entry.get("module", "")
    existing_config = entry.get("config", {})
    display = entry.get("id") or _display_name(module_id)

    # Run configure_provider with existing config as defaults
    key_manager = KeyManager()
    new_config = configure_provider(
        module_id, key_manager, existing_config=existing_config
    )

    if new_config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

    # Preserve priority from existing config
    priority = (
        existing_config.get("priority", 1) if isinstance(existing_config, dict) else 1
    )
    new_config_with_priority = {**new_config, "priority": priority}

    # Build updated entry
    updated_entry: dict[str, Any] = {
        "module": module_id,
        "config": new_config_with_priority,
    }
    if entry.get("id"):
        updated_entry["id"] = entry["id"]
    if entry.get("source"):
        updated_entry["source"] = entry["source"]

    # Update in settings — replace the matching entry in global scope
    scope_providers = settings.get_scope_provider_overrides("global")
    new_list = []
    replaced = False
    for p in scope_providers:
        if not replaced and _find_provider_entry([p], name) is not None:
            new_list.append(updated_entry)
            replaced = True
        else:
            new_list.append(p)

    if not replaced:
        new_list.append(updated_entry)

    scope_settings = settings._read_scope("global")
    if "config" not in scope_settings:
        scope_settings["config"] = {}
    scope_settings["config"]["providers"] = new_list
    settings._write_scope("global", scope_settings)

    model = new_config.get("default_model", "")
    model_display = f" ({model})" if model else ""
    console.print(f"\n[green]✓ Provider updated: {display}{model_display}[/green]")


# ============================================================
# Task 10: provider test [name]
# ============================================================


@provider.command("test")
@click.argument("name", required=False)
def provider_test(name: str | None) -> None:
    """Test provider connectivity.

    Tests one or all configured providers by calling list_models().

    Examples:
      amplifier provider test              # test all
      amplifier provider test anthropic    # test specific
    """
    _ensure_providers_ready()

    settings = _get_settings()
    providers = settings.get_provider_overrides()

    if not providers:
        console.print("[yellow]No providers configured.[/yellow]")
        console.print("Run: [cyan]amplifier provider add[/cyan]")
        return

    # Determine which providers to test
    if name:
        entry = _find_provider_entry(providers, name)
        if entry is None:
            console.print(f"[red]Provider '{name}' not found.[/red]")
            return
        to_test = [entry]
    else:
        to_test = providers

    table = Table(title="Provider Test Results")
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Latency", justify="right")
    table.add_column("Details")

    for p in to_test:
        module_id = p.get("module", "unknown")
        display = p.get("id") or _display_name(module_id)
        config = p.get("config", {})

        start = time.time()
        try:
            models = get_provider_models(module_id, collected_config=config)
            elapsed = time.time() - start
            latency = f"{elapsed:.1f}s"
            model_count = len(models)
            table.add_row(
                display,
                "[green]✓[/green]",
                latency,
                f"{model_count} model(s) available",
            )
        except Exception as e:
            elapsed = time.time() - start
            latency = f"{elapsed:.1f}s"
            error_msg = f"{type(e).__name__}: {e}"
            if len(error_msg) > 60:
                error_msg = error_msg[:57] + "..."
            table.add_row(
                display,
                "[red]✗[/red]",
                latency,
                escape_markup(error_msg),
            )

    console.print(table)


# ============================================================
# provider models (kept from original)
# ============================================================


@provider.command("models")
@click.argument("provider_id", required=False)
@click.pass_context
def provider_models(ctx: click.Context, provider_id: str | None) -> None:
    """List available models for a provider.

    If PROVIDER_ID is omitted, uses the currently active provider.

    Examples:
      amplifier provider models anthropic
      amplifier provider models openai
      amplifier provider models  # uses current provider
    """
    # Ensure providers are installed (post-update fix)
    _ensure_providers_ready()

    config_manager = create_config_manager()

    # Determine which provider to query
    if provider_id is None:
        manager = ProviderManager(config_manager)
        current = manager.get_current_provider()
        if current is None:
            console.print(
                "[yellow]No active provider. Run 'amplifier provider add' first "
                "or specify a provider ID.[/]"
            )
            ctx.exit(1)
        provider_id = current.module_id

    # Normalize provider ID (handle both "anthropic" and "provider-anthropic")
    module_id = _normalize_module_id(provider_id)
    display = _display_name(module_id)

    # Get stored provider config (for credentials/endpoints)
    manager = ProviderManager(config_manager)
    stored_config = manager.get_provider_config(module_id)

    # Fetch models
    try:
        model_list = get_provider_models(
            module_id, config_manager, collected_config=stored_config
        )
    except Exception as e:
        console.print(
            f"[red]Failed to load provider '{display}': {escape_markup(str(e))}[/]"
        )
        # Provide helpful next steps
        if stored_config:
            console.print(
                f"\nRe-configure with: [cyan]amplifier provider edit {display}[/]"
            )
        else:
            console.print(
                f"\nConfigure first with: [cyan]amplifier provider add {display}[/]"
            )
        ctx.exit(1)

    # Handle empty list
    if not model_list:
        console.print(f"[yellow]No models available for provider '{display}'.[/]")
        console.print("This provider may require manual model specification.")
        return

    # Build and display table
    table = Table(title=f"Models for {display}")
    table.add_column("Model ID", style="cyan")
    table.add_column("Display Name")
    table.add_column("Context", justify="right")
    table.add_column("Max Output", justify="right")
    table.add_column("Capabilities")

    for model in model_list:
        table.add_row(
            model.id,
            model.display_name or model.id,
            f"{model.context_window:,}" if model.context_window else "-",
            f"{model.max_output_tokens:,}" if model.max_output_tokens else "-",
            ", ".join(model.capabilities) if model.capabilities else "-",
        )

    console.print(table)


# ============================================================
# Task 1: provider manage — interactive dashboard
# ============================================================


def provider_manage_loop(settings: AppSettings) -> None:
    """Interactive provider management loop.

    Callable from CLI command or from init dashboard.
    """
    while True:
        # 1. Display provider table
        providers = settings.get_provider_overrides()
        if not providers:
            console.print("\n  [dim]No providers configured.[/dim]\n")
        else:
            table = Table(title="Configured Providers")
            table.add_column("#", justify="right", width=3)
            table.add_column("Name/ID", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Default Model")
            table.add_column("Priority", justify="right")

            # Find min priority for star marker
            priorities = []
            for p in providers:
                config = p.get("config", {})
                pri = config.get("priority", 100) if isinstance(config, dict) else 100
                priorities.append(pri)
            min_priority = min(priorities) if priorities else 0

            for i, p in enumerate(providers, 1):
                module = p.get("module", "unknown")
                display = p.get("id") or _display_name(module)
                ptype = _display_name(module)
                config = p.get("config", {})
                model = (
                    config.get("default_model", "-")
                    if isinstance(config, dict)
                    else "-"
                )
                pri = config.get("priority", 100) if isinstance(config, dict) else 100

                is_primary = pri == min_priority
                name_col = f"★ {display}" if is_primary else f"  {display}"

                table.add_row(str(i), name_col, ptype, model, str(pri))

            console.print(table)

        # 2. Show actions menu
        console.print("  Actions:")
        console.print("    [a] Add a provider")
        console.print("    [e] Edit a provider (enter number)")
        console.print("    [r] Remove a provider (enter number)")
        console.print("    [p] Reorder priorities")
        console.print("    [t] Test connections")
        console.print("    [d] Done")
        console.print()

        try:
            choice = Prompt.ask("  Choice", default="d").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "d":
            break
        elif choice == "a":
            _manage_add_provider(settings)
        elif choice.startswith("e"):
            _manage_edit_provider(settings, choice, providers)
        elif choice.startswith("r"):
            _manage_remove_provider(settings, choice, providers)
        elif choice == "p":
            _manage_reorder_providers(settings, providers)
        elif choice == "t":
            _manage_test_providers(settings, providers)


def _parse_number_from_choice(choice: str, prefix: str, count: int) -> int | None:
    """Parse a provider number from a choice string like 'e2' or 'r 3'.

    Returns 0-based index or None if invalid.
    """
    num_str = choice[len(prefix) :].strip()
    if not num_str:
        try:
            num_str = Prompt.ask("  Enter number").strip()
        except (EOFError, KeyboardInterrupt):
            return None
    try:
        num = int(num_str)
        if 1 <= num <= count:
            return num - 1
        console.print(f"  [red]Invalid number. Enter 1-{count}.[/red]")
        return None
    except ValueError:
        console.print("  [red]Invalid input. Enter a number.[/red]")
        return None


def _manage_add_provider(settings: AppSettings) -> None:
    """Add a provider from the manage loop."""
    try:
        _ensure_providers_ready()
    except SystemExit:
        return

    provider_mgr = ProviderManager(settings)
    available = provider_mgr.list_providers()
    if not available:
        console.print(
            "  [red]No providers available. Run: amplifier provider install[/red]"
        )
        return

    console.print("\n  [bold]Available providers:[/bold]")
    provider_map: dict[str, str] = {}
    for idx, (module_id, name, _desc) in enumerate(available, 1):
        provider_map[str(idx)] = module_id
        console.print(f"    [{idx}] {name}")

    try:
        choice = Prompt.ask("  Which provider?", choices=list(provider_map.keys()))
    except (EOFError, KeyboardInterrupt):
        return

    module_id = provider_map[choice]
    display = _display_name(module_id)

    # Check for multi-instance
    existing_providers = settings.get_provider_overrides()
    same_module = [p for p in existing_providers if p.get("module") == module_id]
    instance_id: str | None = None

    if same_module:
        console.print(
            f"\n  [yellow]A {display} provider is already configured.[/yellow]"
        )
        try:
            suggested_id = f"{display}-{len(same_module) + 1}"
            instance_id = Prompt.ask(
                "  Enter an ID for this instance", default=suggested_id
            )
        except (EOFError, KeyboardInterrupt):
            return

    # Run configuration wizard
    key_manager = KeyManager()
    config = configure_provider(module_id, key_manager)

    if config is None:
        console.print("  [red]Configuration cancelled.[/red]")
        return

    # Determine priority
    if not existing_providers:
        priority = 1
    else:
        priority = _get_max_priority(existing_providers) + 1

    config_with_priority = {**config, "priority": priority}

    # Build provider entry
    provider_entry: dict[str, Any] = {
        "module": module_id,
        "config": config_with_priority,
    }
    if instance_id:
        provider_entry["id"] = instance_id

    # Determine source
    effective_sources = get_effective_provider_sources(settings)
    source = effective_sources.get(module_id)
    if source:
        provider_entry["source"] = source

    # Save to settings
    scope_providers = settings.get_scope_provider_overrides("global")
    if instance_id:
        scope_providers.append(provider_entry)
    else:
        scope_providers = [p for p in scope_providers if p.get("module") != module_id]
        scope_providers.append(provider_entry)

    scope_settings = settings._read_scope("global")
    if "config" not in scope_settings:
        scope_settings["config"] = {}
    scope_settings["config"]["providers"] = scope_providers
    settings._write_scope("global", scope_settings)

    model = config.get("default_model", "")
    model_display = f" ({model})" if model else ""
    name_display = instance_id or display
    console.print(f"\n  [green]✓ Provider added: {name_display}{model_display}[/green]")


def _manage_edit_provider(
    settings: AppSettings, choice: str, providers: list[dict[str, Any]]
) -> None:
    """Edit a provider from the manage loop."""
    if not providers:
        console.print("  [yellow]No providers to edit.[/yellow]")
        return
    idx = _parse_number_from_choice(choice, "e", len(providers))
    if idx is None:
        return

    entry = providers[idx]
    module_id = entry.get("module", "")
    existing_config = entry.get("config", {})
    display = entry.get("id") or _display_name(module_id)

    key_manager = KeyManager()
    new_config = configure_provider(
        module_id, key_manager, existing_config=existing_config
    )

    if new_config is None:
        console.print("  [red]Configuration cancelled.[/red]")
        return

    priority = (
        existing_config.get("priority", 1) if isinstance(existing_config, dict) else 1
    )
    new_config_with_priority = {**new_config, "priority": priority}

    updated_entry: dict[str, Any] = {
        "module": module_id,
        "config": new_config_with_priority,
    }
    if entry.get("id"):
        updated_entry["id"] = entry["id"]
    if entry.get("source"):
        updated_entry["source"] = entry["source"]

    name = entry.get("id") or _display_name(module_id)
    scope_providers = settings.get_scope_provider_overrides("global")
    new_list = []
    replaced = False
    for p in scope_providers:
        if not replaced and _find_provider_entry([p], name) is not None:
            new_list.append(updated_entry)
            replaced = True
        else:
            new_list.append(p)
    if not replaced:
        new_list.append(updated_entry)

    scope_settings = settings._read_scope("global")
    if "config" not in scope_settings:
        scope_settings["config"] = {}
    scope_settings["config"]["providers"] = new_list
    settings._write_scope("global", scope_settings)

    model = new_config.get("default_model", "")
    model_display = f" ({model})" if model else ""
    console.print(f"\n  [green]✓ Provider updated: {display}{model_display}[/green]")


def _manage_remove_provider(
    settings: AppSettings, choice: str, providers: list[dict[str, Any]]
) -> None:
    """Remove a provider from the manage loop."""
    if not providers:
        console.print("  [yellow]No providers to remove.[/yellow]")
        return
    idx = _parse_number_from_choice(choice, "r", len(providers))
    if idx is None:
        return

    entry = providers[idx]
    module = entry.get("module", "unknown")
    display = entry.get("id") or _display_name(module)

    try:
        if not Confirm.ask(f"  Remove {display}?", default=False):
            console.print("  [dim]Cancelled.[/dim]")
            return
    except (EOFError, KeyboardInterrupt):
        return

    # Remove from all scopes
    for scope in ("local", "project", "global"):
        scope_providers = settings.get_scope_provider_overrides(scope)  # type: ignore[arg-type]
        original_len = len(scope_providers)

        filtered = []
        for p in scope_providers:
            if p.get("id") == entry.get("id") and entry.get("id"):
                continue
            if p.get("module") == entry.get("module") and not entry.get("id"):
                continue
            filtered.append(p)

        if len(filtered) < original_len:
            scope_settings = settings._read_scope(scope)  # type: ignore[arg-type]
            config_section = scope_settings.get("config", {})
            if filtered:
                config_section["providers"] = filtered
            else:
                config_section.pop("providers", None)
            if config_section:
                scope_settings["config"] = config_section
            elif "config" in scope_settings:
                scope_settings.pop("config", None)
            settings._write_scope(scope, scope_settings)  # type: ignore[arg-type]

    console.print(f"\n  [green]✓ Removed provider: {display}[/green]")


def _manage_reorder_providers(
    settings: AppSettings, providers: list[dict[str, Any]]
) -> None:
    """Reorder provider priorities from the manage loop."""
    if len(providers) < 2:
        console.print("  [dim]Need at least 2 providers to reorder.[/dim]")
        return

    console.print("\n  Current order:")
    for i, p in enumerate(providers, 1):
        module = p.get("module", "unknown")
        display = p.get("id") or _display_name(module)
        console.print(f"    [{i}] {display}")

    try:
        order_str = Prompt.ask("  Enter new order (e.g., 2 1 3)").strip()
    except (EOFError, KeyboardInterrupt):
        return

    try:
        new_order = [int(x) for x in order_str.split()]
    except ValueError:
        console.print("  [red]Invalid input. Enter numbers separated by spaces.[/red]")
        return

    if sorted(new_order) != list(range(1, len(providers) + 1)):
        console.print(
            f"  [red]Please enter all numbers from 1 to {len(providers)}.[/red]"
        )
        return

    # Reorder and reassign priorities
    reordered = []
    for priority, num in enumerate(new_order, 1):
        entry = dict(providers[num - 1])  # shallow copy
        config = dict(entry.get("config", {}))
        config["priority"] = priority
        entry["config"] = config
        reordered.append(entry)

    scope_settings = settings._read_scope("global")
    if "config" not in scope_settings:
        scope_settings["config"] = {}
    scope_settings["config"]["providers"] = reordered
    settings._write_scope("global", scope_settings)

    console.print("\n  [green]✓ Priorities updated.[/green]")


def _manage_test_providers(
    settings: AppSettings, providers: list[dict[str, Any]]
) -> None:
    """Test provider connections from the manage loop."""
    if not providers:
        console.print("  [yellow]No providers to test.[/yellow]")
        return

    table = Table(title="Provider Test Results")
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Latency", justify="right")
    table.add_column("Details")

    for p in providers:
        module_id = p.get("module", "unknown")
        display = p.get("id") or _display_name(module_id)
        config = p.get("config", {})

        start = time.time()
        try:
            models = get_provider_models(module_id, collected_config=config)
            elapsed = time.time() - start
            latency = f"{elapsed:.1f}s"
            model_count = len(models)
            table.add_row(
                display,
                "[green]✓[/green]",
                latency,
                f"{model_count} model(s) available",
            )
        except Exception as e:
            elapsed = time.time() - start
            latency = f"{elapsed:.1f}s"
            error_msg = f"{type(e).__name__}: {e}"
            if len(error_msg) > 60:
                error_msg = error_msg[:57] + "..."
            table.add_row(
                display,
                "[red]✗[/red]",
                latency,
                escape_markup(error_msg),
            )

    console.print(table)


@provider.command("manage")
def provider_manage():
    """Interactive provider management dashboard."""
    _ensure_providers_ready()
    settings = _get_settings()
    provider_manage_loop(settings)
