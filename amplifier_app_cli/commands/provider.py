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

console = Console()


@click.group()
def provider():
    """Manage AI providers."""
    pass


@provider.command("use")
@click.argument("provider_id")
@click.option("--model", help="Model name (Anthropic/OpenAI/Ollama)")
@click.option("--deployment", help="Deployment name (Azure OpenAI)")
@click.option("--endpoint", help="Azure endpoint URL")
@click.option("--use-azure-cli", is_flag=True, help="Use Azure CLI auth (Azure OpenAI)")
@click.option("--local", "scope_flag", flag_value="local", help="Configure locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Configure for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Configure globally (all projects)")
def provider_use(
    provider_id: str,
    model: str | None,
    deployment: str | None,
    endpoint: str | None,
    use_azure_cli: bool,
    scope_flag: str | None,
):
    """Configure provider.

    Examples:
      amplifier provider use anthropic --model claude-opus-4-1 --local
      amplifier provider use openai --model gpt-5.1 --project
      amplifier provider use azure-openai --endpoint https://... --deployment gpt-5.1-codex --use-azure-cli
      amplifier provider use ollama --model llama3
    """
    # Build module ID (handle both "anthropic" and "provider-anthropic")
    module_id = provider_id if provider_id.startswith("provider-") else f"provider-{provider_id}"

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
    )

    if config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

    # Determine scope with validation
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="local",
        )
        if was_fallback:
            console.print(
                "[yellow]Note:[/yellow] Running from home directory, using global scope (~/.amplifier/settings.yaml)"
            )
    except ScopeNotAvailableError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        return

    # Configure provider
    result = provider_mgr.use_provider(module_id, cast(ScopeType, scope), config, source=None)

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

    console.print(f"\n[bold]Active provider:[/bold] {info.module_id.replace('provider-', '')}")
    console.print(f"  Source: {info.source}")

    if "default_model" in info.config:
        console.print(f"  Model: {info.config['default_model']}")
    elif "default_deployment" in info.config:
        console.print(f"  Deployment: {info.config['default_deployment']}")


@provider.command("list")
def provider_list():
    """List available providers."""
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
@click.option("--local", "scope_flag", flag_value="local", help="Reset local configuration")
@click.option("--project", "scope_flag", flag_value="project", help="Reset project configuration")
@click.option("--global", "scope_flag", flag_value="global", help="Reset global configuration")
def provider_reset(scope_flag: str | None):
    """Remove provider override.

    Resets to whatever the profile specifies.
    """
    config_manager = create_config_manager()

    # Determine scope with validation
    try:
        scope, was_fallback = get_effective_scope(
            cast(ScopeType, scope_flag) if scope_flag else None,
            config_manager,
            default_scope="local",
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
    mapping: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
    return mapping[choice]
