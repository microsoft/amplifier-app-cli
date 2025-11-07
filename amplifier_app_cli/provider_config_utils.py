"""Shared provider configuration collection functions.

Eliminates duplication between init.py and provider.py commands.
Provides environment variable detection for smoother user experience.
"""

import logging
import os

from rich.console import Console
from rich.prompt import Prompt

from .key_manager import KeyManager

console = Console()
logger = logging.getLogger(__name__)


def configure_anthropic(key_manager: KeyManager) -> dict:
    """Collect Anthropic configuration with env var detection.

    Args:
        key_manager: Key manager instance

    Returns:
        Provider configuration dict
    """
    console.print()

    # Check for existing env var
    existing_key_in_env = os.environ.get("ANTHROPIC_API_KEY")

    # API key
    if not key_manager.has_key("ANTHROPIC_API_KEY"):
        console.print("API key: Get one at https://console.anthropic.com/settings/keys")
        if existing_key_in_env:
            console.print("  [dim](Found in environment - will use if you don't configure)[/dim]")
        api_key = Prompt.ask("API key", password=True)
        key_manager.save_key("ANTHROPIC_API_KEY", api_key)
        console.print("[green]✓ Saved[/green]")
    else:
        console.print("[green]✓ Using existing API key[/green]")

    # Model
    console.print()
    console.print("Model?")
    console.print("  [1] claude-sonnet-4-5 (recommended)")
    console.print("  [2] claude-opus-4-1 (most capable)")
    console.print("  [3] claude-haiku-4-5 (fastest, cheapest)")
    console.print("  [4] custom")

    model_choice = Prompt.ask("Choice", choices=["1", "2", "3", "4"], default="1")
    model_map = {"1": "claude-sonnet-4-5", "2": "claude-opus-4-1", "3": "claude-haiku-4-5", "4": None}

    if model_choice == "4":
        model = Prompt.ask("Model name")
    else:
        model = model_map[model_choice]

    console.print(f"[green]✓ Using {model}[/green]")

    return {"default_model": model, "api_key": "${ANTHROPIC_API_KEY}"}


def configure_openai(key_manager: KeyManager) -> dict:
    """Collect OpenAI configuration with env var detection.

    Args:
        key_manager: Key manager instance

    Returns:
        Provider configuration dict
    """
    console.print()

    # Check for existing env var
    existing_key_in_env = os.environ.get("OPENAI_API_KEY")

    # API key
    if not key_manager.has_key("OPENAI_API_KEY"):
        console.print("API key: Get one at https://platform.openai.com/api-keys")
        if existing_key_in_env:
            console.print("  [dim](Found in environment - will use if you don't configure)[/dim]")
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


def configure_azure_openai(
    key_manager: KeyManager,
    endpoint: str | None = None,
    deployment: str | None = None,
    use_azure_cli: bool | None = None,
) -> dict:
    """Collect Azure OpenAI configuration with env var detection.

    Args:
        key_manager: Key manager instance
        endpoint: Optional endpoint (if provided via CLI flag, skips prompt)
        deployment: Optional deployment (if provided via CLI flag, skips prompt)
        use_azure_cli: Optional auth method (if provided via CLI flag, skips prompt)

    Returns:
        Provider configuration dict
    """
    console.print()

    # Endpoint - use provided, env var, or prompt
    if endpoint:
        # Provided via CLI flag - use directly
        key_manager.save_key("AZURE_OPENAI_ENDPOINT", endpoint)
        console.print(f"Azure endpoint: {endpoint}")
        console.print("[green]✓ Saved[/green]")
    else:
        # Prompt with env var as default if available
        console.print("Azure endpoint:")
        existing_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if existing_endpoint:
            console.print(f"  [dim](Found in environment: {existing_endpoint})[/dim]")
            endpoint = Prompt.ask("Endpoint URL", default=existing_endpoint)
        else:
            endpoint = Prompt.ask("Endpoint URL", default="https://my-resource.openai.azure.com/")
        key_manager.save_key("AZURE_OPENAI_ENDPOINT", endpoint)
        console.print("[green]✓ Saved[/green]")

    # Auth method - use provided, detect env vars, or prompt
    if use_azure_cli is not None:
        # Provided via CLI flag - use directly
        if use_azure_cli:
            console.print("[green]✓ Will use DefaultAzureCredential[/green]")
            console.print("  (Works with 'az login' locally or managed identity in Azure)")
            key_manager.save_key("AZURE_USE_DEFAULT_CREDENTIAL", "true")
            auth_choice = "2"
        else:
            # Flag was False, meaning use API key
            auth_choice = "1"
    else:
        # Prompt with smart default based on env vars
        console.print()
        console.print("Authentication?")
        console.print("  [1] API key")
        console.print("  [2] Azure CLI (az login)")

        # Determine default based on what's already configured
        existing_use_default_cred = os.environ.get("AZURE_USE_DEFAULT_CREDENTIAL", "").lower() in ("true", "1", "yes")
        existing_api_key = os.environ.get("AZURE_OPENAI_API_KEY")

        if existing_use_default_cred:
            console.print("  [dim](Detected AZURE_USE_DEFAULT_CREDENTIAL=true in environment)[/dim]")
            default_auth = "2"
        elif existing_api_key:
            console.print("  [dim](Detected AZURE_OPENAI_API_KEY in environment)[/dim]")
            default_auth = "1"
        else:
            default_auth = "2"

        auth_choice = Prompt.ask("Choice", choices=["1", "2"], default=default_auth)

    # Deployment - use provided, env var, or prompt
    if deployment:
        # Provided via CLI flag - use directly
        console.print(f"Deployment: {deployment}")
    else:
        # Prompt with env var as default if available
        console.print()
        console.print("Deployment name:")
        console.print("  Note: Use your Azure deployment name, not model name")
        existing_deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        if existing_deployment:
            console.print(f"  [dim](Found in environment: {existing_deployment})[/dim]")
            deployment = Prompt.ask("Deployment", default=existing_deployment)
        else:
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
    """Collect Ollama configuration with env var detection.

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

    # Base URL - use env var if set
    existing_host = os.environ.get("OLLAMA_HOST")
    if existing_host:
        console.print(f"  [dim](Using OLLAMA_HOST: {existing_host})[/dim]")
        base_url = existing_host
    else:
        base_url = "http://localhost:11434"

    console.print()
    console.print("Make sure Ollama is running:")
    console.print("  ollama serve")
    console.print(f"  ollama pull {model}")
    console.print(f"[green]✓ Using {model}[/green]")

    return {"default_model": model, "base_url": base_url}
