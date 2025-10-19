"""Interactive initialization command for Amplifier."""

import logging

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.prompt import Prompt

from ..key_manager import KeyManager
from ..provider_manager import ProviderManager
from ..settings import SettingsManager

console = Console()
logger = logging.getLogger(__name__)


def check_first_run() -> bool:
    """Check if this appears to be first run (no API keys configured)."""
    key_manager = KeyManager()

    # Check if any common provider keys exist
    return not any(
        [
            key_manager.has_key("ANTHROPIC_API_KEY"),
            key_manager.has_key("OPENAI_API_KEY"),
            key_manager.has_key("AZURE_OPENAI_API_KEY"),
        ]
    )


def prompt_first_run_init(console_arg: Console) -> bool:
    """Prompt user to run init on first run. Returns True if init was run."""
    console_arg.print()
    console_arg.print("[yellow]⚠️  No API key found![/yellow]")
    console_arg.print()
    console_arg.print("Amplifier needs an AI provider to work. Let's set that up quickly.")
    console_arg.print()

    if Confirm.ask("Run interactive setup now?", default=True):
        # Import here to avoid circular dependency
        import click

        ctx = click.get_current_context()
        ctx.invoke(init_cmd)
        return True
    console_arg.print()
    console_arg.print("[yellow]Setup skipped.[/yellow] To configure later, run:")
    console_arg.print("  [cyan]amplifier init[/cyan]")
    console_arg.print()
    console_arg.print("Or set an API key manually:")
    console_arg.print('  [cyan]export ANTHROPIC_API_KEY="your-key"[/cyan]')
    console_arg.print()
    return False


@click.command("init")
def init_cmd():
    """Interactive first-time setup wizard.

    Auto-runs on first invocation if no configuration exists.
    Configures provider credentials, model, and active profile.
    """
    console.print()
    console.print(Panel.fit("[bold cyan]Welcome to Amplifier![/bold cyan]", border_style="cyan"))
    console.print()

    key_manager = KeyManager()
    settings = SettingsManager()
    provider_mgr = ProviderManager(settings)

    # Step 1: Provider selection
    console.print("[bold]Step 1: Provider[/bold]")
    console.print("  [1] Anthropic Claude (recommended)")
    console.print("  [2] OpenAI")
    console.print("  [3] Azure OpenAI")
    console.print("  [4] Ollama (local, free)")
    console.print()

    provider_choice = Prompt.ask("Which provider?", choices=["1", "2", "3", "4"], default="1")

    provider_map = {"1": "anthropic", "2": "openai", "3": "azure-openai", "4": "ollama"}
    provider_id = provider_map[provider_choice]

    # Step 2: Provider-specific configuration
    if provider_id == "anthropic":
        config = configure_anthropic(key_manager)
    elif provider_id == "openai":
        config = configure_openai(key_manager)
    elif provider_id == "azure-openai":
        config = configure_azure_openai(key_manager)
    else:  # ollama
        config = configure_ollama()

    # Step 3: Profile selection
    console.print()
    console.print("[bold]Step 2: Profile[/bold]")
    console.print("  [1] dev (recommended - full development tools)")
    console.print("  [2] base (essential tools only)")
    console.print("  [3] full (everything enabled)")
    console.print()

    profile_choice = Prompt.ask("Which profile?", choices=["1", "2", "3"], default="1")
    profile_map = {"1": "dev", "2": "base", "3": "full"}
    profile_id = profile_map[profile_choice]

    # Get canonical source for provider
    from ..module_resolution.registry import get_canonical_module_source

    try:
        provider_source = get_canonical_module_source(f"provider-{provider_id}")
    except ValueError as e:
        console.print(f"[yellow]Warning: Could not get canonical source for provider: {e}[/yellow]")
        provider_source = None

    # Save configuration
    settings.set_active_profile(profile_id)
    provider_mgr.use_provider(f"provider-{provider_id}", scope="local", config=config, source=provider_source)

    console.print()
    console.print(
        Panel.fit(
            '[bold green]✓ Ready![/bold green]\n\nTry it now:\n  [cyan]amplifier "Hello, Amplifier!"[/cyan]',
            border_style="green",
        )
    )
    console.print()


def configure_anthropic(key_manager: KeyManager) -> dict:
    """Collect Anthropic configuration.

    Args:
        key_manager: Key manager instance

    Returns:
        Provider configuration dict
    """
    console.print()

    # API key
    if not key_manager.has_key("ANTHROPIC_API_KEY"):
        console.print("API key: Get one at https://console.anthropic.com/settings/keys")
        api_key = Prompt.ask("API key", password=True)
        key_manager.save_key("ANTHROPIC_API_KEY", api_key)
        console.print("[green]✓ Saved[/green]")
    else:
        console.print("[green]✓ Using existing API key[/green]")

    # Model
    console.print()
    console.print("Model?")
    console.print("  [1] claude-sonnet-4-5 (recommended)")
    console.print("  [2] claude-opus-4 (most capable)")
    console.print("  [3] claude-haiku-4-5 (fastest, cheapest)")
    console.print("  [4] custom")

    model_choice = Prompt.ask("Choice", choices=["1", "2", "3", "4"], default="1")
    model_map = {"1": "claude-sonnet-4-5", "2": "claude-opus-4", "3": "claude-haiku-4-5", "4": None}

    if model_choice == "4":
        model = Prompt.ask("Model name")
    else:
        model = model_map[model_choice]

    console.print(f"[green]✓ Using {model}[/green]")

    return {"default_model": model, "api_key": "${ANTHROPIC_API_KEY}"}


def configure_openai(key_manager: KeyManager) -> dict:
    """Collect OpenAI configuration.

    Args:
        key_manager: Key manager instance

    Returns:
        Provider configuration dict
    """
    console.print()

    # API key
    if not key_manager.has_key("OPENAI_API_KEY"):
        console.print("API key: Get one at https://platform.openai.com/api-keys")
        api_key = Prompt.ask("API key", password=True)
        key_manager.save_key("OPENAI_API_KEY", api_key)
        console.print("[green]✓ Saved[/green]")
    else:
        console.print("[green]✓ Using existing API key[/green]")

    # Model
    console.print()
    console.print("Model?")
    console.print("  [1] gpt-5-mini (recommended)")
    console.print("  [2] gpt-5-codex (code-focused)")
    console.print("  [3] gpt-5 (most capable)")
    console.print("  [4] custom")

    model_choice = Prompt.ask("Choice", choices=["1", "2", "3", "4"], default="1")
    model_map = {"1": "gpt-5-mini", "2": "gpt-5-codex", "3": "gpt-5", "4": None}

    if model_choice == "4":
        model = Prompt.ask("Model name")
    else:
        model = model_map[model_choice]

    console.print(f"[green]✓ Using {model}[/green]")

    return {"default_model": model, "api_key": "${OPENAI_API_KEY}"}


def configure_azure_openai(key_manager: KeyManager) -> dict:
    """Collect Azure OpenAI configuration.

    Args:
        key_manager: Key manager instance

    Returns:
        Provider configuration dict
    """
    console.print()

    # Endpoint
    console.print("Azure endpoint:")
    endpoint = Prompt.ask("Endpoint URL", default="https://my-resource.openai.azure.com/")
    key_manager.save_key("AZURE_OPENAI_ENDPOINT", endpoint)
    console.print("[green]✓ Saved[/green]")

    # Auth method
    console.print()
    console.print("Authentication?")
    console.print("  [1] API key")
    console.print("  [2] Azure CLI (az login)")

    auth_choice = Prompt.ask("Choice", choices=["1", "2"], default="2")

    # Deployment
    console.print()
    console.print("Deployment name:")
    console.print("  Note: Use your Azure deployment name, not model name")
    deployment = Prompt.ask("Deployment", default="gpt-5-codex")

    # Build complete config
    config: dict = {
        "azure_endpoint": "${AZURE_OPENAI_ENDPOINT}",
        "default_deployment": deployment,
        "api_version": "2024-10-01-preview",
    }

    if auth_choice == "1":
        api_key = Prompt.ask("Azure OpenAI API key", password=True)
        key_manager.save_key("AZURE_OPENAI_API_KEY", api_key)
        config["api_key"] = "${AZURE_OPENAI_API_KEY}"
        console.print("[green]✓ Saved[/green]")
    else:
        console.print("[green]✓ Will use DefaultAzureCredential[/green]")
        console.print("  (Works with 'az login' locally or managed identity in Azure)")
        key_manager.save_key("AZURE_USE_DEFAULT_CREDENTIAL", "true")
        config["use_default_credential"] = True

    console.print("[green]✓ Configured[/green]")

    return config


def configure_ollama() -> dict:
    """Collect Ollama configuration.

    Returns:
        Provider configuration dict
    """
    console.print()
    console.print("Model?")
    console.print("  [1] llama3 (recommended)")
    console.print("  [2] codellama (code-focused)")
    console.print("  [3] mistral")
    console.print("  [4] custom")

    model_choice = Prompt.ask("Choice", choices=["1", "2", "3", "4"], default="1")
    model_map = {"1": "llama3", "2": "codellama", "3": "mistral", "4": None}

    if model_choice == "4":
        model = Prompt.ask("Model name")
    else:
        model = model_map[model_choice]

    console.print()
    console.print("Make sure Ollama is running:")
    console.print("  ollama serve")
    console.print(f"  ollama pull {model}")
    console.print(f"[green]✓ Using {model}[/green]")

    return {"default_model": model, "base_url": "http://localhost:11434"}
