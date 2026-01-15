"""Provider management commands."""

from typing import Literal
from typing import cast

import click
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..key_manager import KeyManager
from ..paths import ScopeNotAvailableError
from ..paths import create_config_manager
from ..paths import get_effective_scope
from ..provider_config_utils import configure_provider
from ..provider_manager import ProviderManager
from ..provider_manager import ScopeType
from ..provider_sources import ensure_provider_installed
from ..provider_sources import get_effective_provider_sources
from ..provider_sources import install_known_providers

console = Console()


def _ensure_providers_ready() -> None:
    """Ensure provider modules are installed (post-update fix).

    Called by subcommands that need providers to be available.
    The 'install' subcommand skips this since it IS the install.
    """
    from .init import check_first_run

    check_first_run()


@click.group()
def provider():
    """Manage AI providers."""
    # Note: check_first_run() is called by subcommands that need providers.
    # The 'install' subcommand skips this since it IS the install.
    pass


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
            display_name = module_id.replace("provider-", "")

            # Check if already installed (unless --force)
            if not force:
                try:
                    import importlib.metadata

                    eps = importlib.metadata.entry_points(group="amplifier.modules")
                    already_installed = any(ep.name == module_id for ep in eps)
                    if already_installed:
                        if not quiet:
                            console.print(
                                f"[dim]{display_name} already installed (use --force to reinstall)[/dim]"
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
                failed.append(display_name)

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


@provider.command("use")
@click.argument("provider_id")
@click.option("--model", help="Model name (Anthropic/OpenAI/Ollama)")
@click.option("--deployment", help="Deployment name (Azure OpenAI)")
@click.option("--endpoint", help="Azure endpoint URL")
@click.option("--use-azure-cli", is_flag=True, help="Use Azure CLI auth (Azure OpenAI)")
@click.option(
    "--local", "scope_flag", flag_value="local", help="Configure locally (just you)"
)
@click.option(
    "--project", "scope_flag", flag_value="project", help="Configure for project (team)"
)
@click.option(
    "--global",
    "scope_flag",
    flag_value="global",
    help="Configure globally (all projects)",
)
@click.option(
    "--yes",
    "-y",
    "non_interactive",
    is_flag=True,
    help="Non-interactive mode: use CLI values and env vars, skip prompts",
)
def provider_use(
    provider_id: str,
    model: str | None,
    deployment: str | None,
    endpoint: str | None,
    use_azure_cli: bool,
    scope_flag: str | None,
    non_interactive: bool = False,
):
    """Configure provider.

    Examples:
      amplifier provider use anthropic --model claude-opus-4-1 --local
      amplifier provider use openai --model gpt-5.1 --project
      amplifier provider use azure-openai --endpoint https://... --deployment gpt-5.1-codex --use-azure-cli
      amplifier provider use ollama --model llama3

    Use --yes/-y for non-interactive mode (CI/CD, shadow containers).
    In non-interactive mode, credentials are read from environment variables.
    """
    import sys

    # Check for TTY if interactive mode requested
    if not non_interactive and not sys.stdin.isatty():
        console.print(
            "[red]Error:[/red] Interactive mode requires a TTY. "
            "Use --yes flag for non-interactive setup."
        )
        console.print("\nExample:")
        console.print(f"  amplifier provider use {provider_id} --model <model> --yes")
        return
    # Ensure providers are installed (post-update fix)
    _ensure_providers_ready()

    # Build module ID (handle both "anthropic" and "provider-anthropic")
    module_id = (
        provider_id
        if provider_id.startswith("provider-")
        else f"provider-{provider_id}"
    )

    # Validate provider exists
    config_manager = create_config_manager()
    provider_mgr = ProviderManager(config_manager)

    valid_providers = {p[0]: p[1] for p in provider_mgr.list_providers()}
    if module_id not in valid_providers:
        console.print(f"[red]Error:[/red] Unknown provider '{provider_id}'")
        console.print("\nAvailable providers:")
        for pid, name, _ in provider_mgr.list_providers():
            console.print(f"  • {pid.replace('provider-', '')} ({name})")
        return

    # Use unified configuration dispatcher
    key_manager = KeyManager()
    config = configure_provider(
        module_id,
        key_manager,
        model=model,
        endpoint=endpoint,
        deployment=deployment,
        use_azure_cli=use_azure_cli if use_azure_cli else None,
        non_interactive=non_interactive,
    )

    if config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

    # Determine scope with validation
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

    # Configure provider
    result = provider_mgr.use_provider(
        module_id, cast(ScopeType, scope), config, source=None
    )

    # Display result
    console.print(f"\n[green]✓ Configured {provider_id}[/green]")
    console.print(f"  Scope: {scope}")
    console.print(f"  File: {result.file}")
    if "default_model" in config:
        console.print(f"  Model: {config['default_model']}")
    elif "default_deployment" in config:
        console.print(f"  Deployment: {config['default_deployment']}")


@provider.command("current")
def provider_current():
    """Show currently active provider."""
    config = create_config_manager()
    provider_mgr = ProviderManager(config)

    info = provider_mgr.get_current_provider()

    if not info:
        console.print("[yellow]No provider configured[/yellow]")
        console.print("\nConfigure a provider with:")
        console.print("  [cyan]amplifier init[/cyan]")
        console.print("  or")
        console.print("  [cyan]amplifier provider use <provider>[/cyan]")
        return

    console.print(
        f"\n[bold]Active provider:[/bold] {info.module_id.replace('provider-', '')}"
    )
    console.print(f"  Source: {info.source}")

    if "default_model" in info.config:
        console.print(f"  Model: {info.config['default_model']}")
    elif "default_deployment" in info.config:
        console.print(f"  Deployment: {info.config['default_deployment']}")


@provider.command("list")
def provider_list():
    """List available providers."""
    # Ensure providers are installed (post-update fix)
    _ensure_providers_ready()

    config = create_config_manager()
    provider_mgr = ProviderManager(config)

    providers = provider_mgr.list_providers()

    table = Table(title="Available Providers")
    table.add_column("ID", style="green")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for module_id, name, desc in providers:
        # Remove provider- prefix for display
        display_id = module_id.replace("provider-", "")
        table.add_row(display_id, name, desc)

    console.print(table)


@provider.command("reset")
@click.option(
    "--local", "scope_flag", flag_value="local", help="Reset local configuration"
)
@click.option(
    "--project", "scope_flag", flag_value="project", help="Reset project configuration"
)
@click.option(
    "--global", "scope_flag", flag_value="global", help="Reset global configuration"
)
def provider_reset(scope_flag: str | None):
    """Remove provider override.

    Resets to default provider.
    """
    config_manager = create_config_manager()

    # Determine scope with validation
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

    provider_mgr = ProviderManager(config_manager)
    result = provider_mgr.reset_provider(cast(ScopeType, scope))

    if result.removed:
        console.print(f"[green]✓ Removed provider override at {scope} scope[/green]")
        console.print("  Now using provider from profile")
    else:
        console.print(f"[yellow]No provider override at {scope} scope[/yellow]")


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

    from ..provider_loader import get_provider_models

    config_manager = create_config_manager()

    # Determine which provider to query
    if provider_id is None:
        manager = ProviderManager(config_manager)
        current = manager.get_current_provider()
        if current is None:
            console.print(
                "[yellow]No active provider. Run 'amplifier provider use' first "
                "or specify a provider ID.[/]"
            )
            ctx.exit(1)
        provider_id = current.module_id

    # Normalize provider ID (handle both "anthropic" and "provider-anthropic")
    module_id = (
        provider_id
        if provider_id.startswith("provider-")
        else f"provider-{provider_id}"
    )
    display_name = module_id.replace("provider-", "")

    # Get stored provider config (for credentials/endpoints)
    manager = ProviderManager(config_manager)
    stored_config = manager.get_provider_config(module_id)

    # Fetch models
    try:
        model_list = get_provider_models(
            module_id, config_manager, collected_config=stored_config
        )
    except Exception as e:
        console.print(f"[red]Failed to load provider '{display_name}': {e}[/]")
        # Provide helpful next steps based on whether provider is configured
        if stored_config:
            console.print(
                f"\nRe-configure with: [cyan]amplifier provider use {display_name}[/]"
            )
        else:
            console.print(
                f"\nConfigure first with: [cyan]amplifier provider use {display_name}[/]"
            )
            console.print("Or run: [cyan]amplifier init[/]")
        ctx.exit(1)

    # Handle empty list
    if not model_list:
        console.print(f"[yellow]No models available for provider '{display_name}'.[/]")
        console.print("This provider may require manual model specification.")
        return

    # Build and display table
    table = Table(title=f"Models for {display_name}")
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


def prompt_scope() -> Literal["local", "project", "global"]:
    """Interactive scope selection.

    Returns:
        Scope string (local/project/global)
    """
    console.print("\nConfigure for:")
    console.print("  [1] Just you (local)")
    console.print("  [2] Whole team (project)")
    console.print("  [3] All your projects (global)")

    choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    mapping: dict[str, Literal["local", "project", "global"]] = {
        "1": "local",
        "2": "project",
        "3": "global",
    }
    return mapping[choice]
