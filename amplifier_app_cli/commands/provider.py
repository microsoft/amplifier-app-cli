"""Provider management commands."""

from typing import Any
from typing import Literal
from typing import cast

import click
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ..key_manager import KeyManager
from ..provider_manager import ProviderManager
from ..provider_manager import ScopeType
from ..settings import SettingsManager

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
      amplifier provider use anthropic --model claude-opus-4 --local
      amplifier provider use openai --model gpt-4o --project
      amplifier provider use azure-openai --endpoint https://... --deployment gpt-5-codex --use-azure-cli
      amplifier provider use ollama --model llama3
    """
    # Build module ID
    module_id = f"provider-{provider_id}"

    # Validate provider exists
    settings = SettingsManager()
    provider_mgr = ProviderManager(settings)

    valid_providers = {p[0]: p[1] for p in provider_mgr.list_providers()}
    if module_id not in valid_providers:
        console.print(f"[red]Error:[/red] Unknown provider '{provider_id}'")
        console.print("\nAvailable providers:")
        for pid, name, _ in provider_mgr.list_providers():
            console.print(f"  • {pid.replace('provider-', '')} ({name})")
        return

    # Collect provider-specific configuration
    key_manager = KeyManager()

    config: dict[str, Any]

    if provider_id in ["anthropic", "openai", "ollama"]:
        # These providers need model
        if not model:
            model = prompt_model_for_provider(provider_id)

        config = {"model": model}

        # Add API key reference
        if provider_id == "anthropic":
            # Check for existing key
            if not key_manager.has_key("ANTHROPIC_API_KEY"):
                console.print("\n[yellow]No API key found for Anthropic.[/yellow]")
                console.print("Get one at: https://console.anthropic.com/settings/keys")
                api_key = Prompt.ask("API key", password=True)
                key_manager.save_key("ANTHROPIC_API_KEY", api_key)
                console.print("[green]✓ Saved[/green]")
            config["api_key"] = "${ANTHROPIC_API_KEY}"

        elif provider_id == "openai":
            if not key_manager.has_key("OPENAI_API_KEY"):
                console.print("\n[yellow]No API key found for OpenAI.[/yellow]")
                console.print("Get one at: https://platform.openai.com/api-keys")
                api_key = Prompt.ask("API key", password=True)
                key_manager.save_key("OPENAI_API_KEY", api_key)
                console.print("[green]✓ Saved[/green]")
            config["api_key"] = "${OPENAI_API_KEY}"

        elif provider_id == "ollama":
            config["base_url"] = "http://localhost:11434"
            console.print("\n[dim]Make sure Ollama is running:[/dim]")
            console.print("  ollama serve")
            console.print(f"  ollama pull {model}")

    elif provider_id == "azure-openai":
        # Azure OpenAI needs endpoint, deployment, and auth
        if not endpoint:
            endpoint = Prompt.ask("Azure endpoint", default="https://my-resource.openai.azure.com/")
            key_manager.save_key("AZURE_OPENAI_ENDPOINT", endpoint)

        if not deployment:
            console.print("\n[dim]Note: Use your Azure deployment name, not model name[/dim]")
            deployment = Prompt.ask("Deployment name", default="gpt-5-codex")

        config = {
            "azure_endpoint": "${AZURE_OPENAI_ENDPOINT}",
            "default_deployment": deployment,
            "api_version": "2024-10-01-preview",
        }

        # Determine auth method (interactive if not specified via flag)
        if use_azure_cli:
            # Explicitly requested Azure CLI auth via flag
            console.print("\n[green]✓ Will use DefaultAzureCredential[/green]")
            console.print("  (Works with 'az login' locally or managed identity in Azure)")
            key_manager.save_key("AZURE_USE_DEFAULT_CREDENTIAL", "true")
            config["use_default_credential"] = "true"  # type: ignore[assignment]
        else:
            # Interactive auth method selection
            console.print("\nAuthentication?")
            console.print("  [1] API key")
            console.print("  [2] Azure CLI (az login)")

            auth_choice = Prompt.ask("Choice", choices=["1", "2"], default="2")

            if auth_choice == "1":
                # API key auth
                if not key_manager.has_key("AZURE_OPENAI_API_KEY"):
                    api_key = Prompt.ask("Azure OpenAI API key", password=True)
                    key_manager.save_key("AZURE_OPENAI_API_KEY", api_key)
                    console.print("[green]✓ Saved[/green]")
                else:
                    console.print("[green]✓ Using existing API key[/green]")
                config["api_key"] = "${AZURE_OPENAI_API_KEY}"
            else:
                # Azure CLI auth
                console.print("[green]✓ Will use DefaultAzureCredential[/green]")
                console.print("  (Works with 'az login' locally or managed identity in Azure)")
                key_manager.save_key("AZURE_USE_DEFAULT_CREDENTIAL", "true")
                config["use_default_credential"] = "true"  # type: ignore[assignment]

    else:
        console.print(f"[red]Error:[/red] Unsupported provider: {provider_id}")
        return

    # Determine scope
    scope = scope_flag or prompt_scope()

    # Configure provider
    result = provider_mgr.use_provider(module_id, cast(ScopeType, scope), config)

    # Display result
    console.print(f"\n[green]✓ Configured {provider_id}[/green]")
    console.print(f"  Scope: {scope}")
    console.print(f"  File: {result.file}")
    if "model" in config:
        console.print(f"  Model: {config['model']}")
    elif "default_deployment" in config:
        console.print(f"  Deployment: {config['default_deployment']}")


@provider.command("current")
def provider_current():
    """Show currently active provider."""
    settings = SettingsManager()
    provider_mgr = ProviderManager(settings)

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

    if "model" in info.config:
        console.print(f"  Model: {info.config['model']}")
    elif "default_deployment" in info.config:
        console.print(f"  Deployment: {info.config['default_deployment']}")


@provider.command("list")
def provider_list():
    """List available providers."""
    settings = SettingsManager()
    provider_mgr = ProviderManager(settings)

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
    scope = scope_flag or prompt_scope()

    settings = SettingsManager()
    provider_mgr = ProviderManager(settings)

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


def prompt_model_for_provider(provider_id: str) -> str:
    """Prompt for model based on provider.

    Args:
        provider_id: Provider identifier

    Returns:
        Selected model name
    """
    console.print("\nModel?")

    if provider_id == "anthropic":
        models = {"1": "claude-sonnet-4-5", "2": "claude-opus-4", "3": "claude-haiku-4-5", "4": None}
        console.print("  [1] claude-sonnet-4-5 (recommended)")
        console.print("  [2] claude-opus-4 (most capable)")
        console.print("  [3] claude-haiku-4-5 (fastest, cheapest)")
        console.print("  [4] custom")
        choices = ["1", "2", "3", "4"]

    elif provider_id == "openai":
        models = {"1": "gpt-5-mini", "2": "gpt-5-codex", "3": "gpt-5", "4": None}
        console.print("  [1] gpt-5-mini (recommended)")
        console.print("  [2] gpt-5-codex (code-focused)")
        console.print("  [3] gpt-5 (most capable)")
        console.print("  [4] custom")
        choices = ["1", "2", "3", "4"]

    elif provider_id == "ollama":
        models = {"1": "llama3", "2": "codellama", "3": "mistral", "4": None}
        console.print("  [1] llama3 (recommended)")
        console.print("  [2] codellama (code-focused)")
        console.print("  [3] mistral")
        console.print("  [4] custom")
        choices = ["1", "2", "3", "4"]

    else:
        return Prompt.ask("Model name")

    choice = Prompt.ask("Choice", choices=choices, default="1")

    if models[choice] is None:
        return Prompt.ask("Model name")
    return models[choice]
