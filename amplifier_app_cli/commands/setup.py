"""Interactive setup command for Amplifier."""

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.prompt import Prompt

from ..key_manager import KeyManager
from ..paths import create_config_manager

console = Console()


PROVIDER_INFO = {
    "anthropic": {
        "name": "Anthropic Claude",
        "key_var": "ANTHROPIC_API_KEY",
        "key_url": "https://console.anthropic.com/settings/keys",
        "model": "claude-sonnet-4-5",
        "module": "provider-anthropic",
        "notes": "Recommended - most tested provider",
    },
    "openai": {
        "name": "OpenAI",
        "key_var": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
        "model": "gpt-5.1-mini",
        "module": "provider-openai",
        "notes": "Good alternative, gpt-5.1-mini is cost-effective",
    },
    "azure": {
        "name": "Azure OpenAI",
        "key_var": "AZURE_OPENAI_API_KEY",
        "key_url": "https://portal.azure.com",
        "model": "gpt-5.1-chat",
        "module": "provider-azure-openai",
        "notes": "Enterprise users with Azure subscriptions",
        "extra_vars": ["AZURE_OPENAI_ENDPOINT"],
    },
    "ollama": {
        "name": "Ollama (Local)",
        "key_var": None,
        "key_url": "https://ollama.ai",
        "model": "llama3.2:3b",
        "module": "provider-ollama",
        "notes": "Free, runs locally, no API key needed",
    },
}


@click.command("setup")
@click.option("--provider", type=click.Choice(["anthropic", "openai", "azure", "ollama"]), help="Provider to configure")
@click.option("--api-key", help="API key (skips interactive prompt)")
def setup_cmd(provider: str | None, api_key: str | None):
    """Interactive setup wizard for Amplifier.

    Guides you through configuring API keys and creating a custom profile.
    Creates a profile that extends your current setup but uses your chosen provider.
    """
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]Welcome to Amplifier Setup![/bold cyan]\nLet's get you configured with an AI provider.",
            border_style="cyan",
        )
    )
    console.print()

    key_manager = KeyManager()

    # Step 1: Choose provider (if not specified)
    if not provider:
        console.print("[bold]Which AI provider would you like to use?[/bold]\n")
        console.print("  [cyan]1.[/cyan] Anthropic Claude [dim](recommended - most tested)[/dim]")
        console.print("  [cyan]2.[/cyan] OpenAI")
        console.print("  [cyan]3.[/cyan] Azure OpenAI")
        console.print("  [cyan]4.[/cyan] Ollama [dim](local, free, no API key)[/dim]")
        console.print("  [cyan]5.[/cyan] Skip [dim](configure manually later)[/dim]")
        console.print()

        choice = Prompt.ask("Choose provider", choices=["1", "2", "3", "4", "5"], default="1")

        if choice == "5":
            console.print("\n[yellow]Setup skipped.[/yellow] You can run [cyan]amplifier setup[/cyan] anytime.")
            return

        provider_map = {"1": "anthropic", "2": "openai", "3": "azure", "4": "ollama"}
        provider = provider_map[choice]

    provider_info = PROVIDER_INFO[provider]
    console.print(f"\n[bold green]✓[/bold green] Using {provider_info['name']}")

    # Step 2: Get API key (if provider needs one)
    use_managed_identity = False
    if provider_info["key_var"]:
        # Check if key already exists
        skip_key_entry = False
        if key_manager.has_key(provider_info["key_var"]):
            console.print(f"\n[green]ℹ[/green] API key already configured for {provider_info['name']}")
            if not Confirm.ask("Replace existing key?", default=False):
                console.print("[yellow]✓[/yellow] Keeping existing key")
                skip_key_entry = True

        # For Azure, offer DefaultAzureCredential option (works with az login)
        if (
            provider == "azure"
            and not skip_key_entry
            and Confirm.ask("Use Azure CLI authentication (az login)?", default=False)
        ):
            use_managed_identity = True
            skip_key_entry = True
            console.print("[green]ℹ[/green] Will use DefaultAzureCredential")
            console.print("[dim]This works with 'az login' for local dev or managed identity in Azure[/dim]")
            # Set environment variable for provider to use
            import os

            os.environ["AZURE_USE_DEFAULT_CREDENTIAL"] = "true"
            key_manager.save_key("AZURE_USE_DEFAULT_CREDENTIAL", "true")

        # Get API key if needed
        if not skip_key_entry:
            if api_key is None:  # Not provided via CLI
                console.print(f"\n[bold]You'll need an API key for {provider_info['name']}[/bold]")
                console.print(f"Get one here: [link]{provider_info['key_url']}[/link]\n")

                api_key = Prompt.ask(f"Paste your {provider_info['name']} API key", password=True)

            # Save the key
            if api_key:
                key_manager.save_key(provider_info["key_var"], api_key)
                console.print("[green]✓[/green] API key saved securely to [cyan]~/.amplifier/keys.env[/cyan]")

    # Azure requires endpoint configuration (always prompt)
    if provider == "azure":
        import os

        console.print("\n[bold]Azure endpoint configuration[/bold]")
        current_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if current_endpoint:
            console.print(f"[dim]Current: {current_endpoint}[/dim]")

        endpoint = Prompt.ask(
            "Azure OpenAI resource endpoint", default=current_endpoint or "https://your-resource.openai.azure.com/"
        )
        key_manager.save_key("AZURE_OPENAI_ENDPOINT", endpoint)
        console.print(f"[green]✓[/green] Endpoint saved: [dim]{endpoint}[/dim]")
    elif provider != "anthropic" and provider != "openai":
        # Ollama - no key needed
        console.print("\n[green]ℹ[/green] Ollama runs locally - no API key needed")
        console.print("Make sure Ollama is installed and running:")
        console.print("  1. Install: [link]https://ollama.ai[/link]")
        console.print("  2. Pull model: [cyan]ollama pull llama3.2:3b[/cyan]")
        console.print("  3. Verify: [cyan]ollama list[/cyan]\n")

    # Step 3: Get model/deployment name
    console.print("\n[bold]Configuring model...[/bold]")

    if provider == "azure":
        model_or_deployment = Prompt.ask(
            "Azure deployment name (chat models only: gpt-5.1, gpt-5.1-mini, gpt-5.1-codex, etc.)",
            default=provider_info["model"],
        )
    elif provider == "ollama":
        model_or_deployment = Prompt.ask(
            "Ollama model name (e.g., llama3.2:3b, codellama, mistral)", default=provider_info["model"]
        )
    else:
        model_or_deployment = Prompt.ask(f"{provider_info['name']} model", default=provider_info["model"])

    # Step 4: Create custom profile
    console.print("\n[bold]Creating custom profile...[/bold]")

    profile_name = f"my-{provider}"
    profile_path = Path.home() / ".amplifier" / "profiles" / f"{profile_name}.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)

    # Get canonical module sources from registry
    # Sources will come from profile (no canonical registry - YAGNI cleanup)
    try:
        orchestrator_source = None
        context_source = None
        provider_source = None
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("\nCannot create profile - required modules not found in bundled profiles")
        return

    # Build provider config
    provider_config = f"""---
profile:
  name: {profile_name}
  version: 1.0.0
  description: Custom profile with {provider_info["name"]}

session:
  orchestrator:
    module: loop-basic
    source: {orchestrator_source}
  context:
    module: context-simple
    source: {context_source}
    config:
      max_tokens: 100000
      compact_threshold: 0.8
      auto_compact: true

providers:
  - module: {provider_info["module"]}
    source: {provider_source}
    config:
"""

    if provider == "azure":
        if use_managed_identity:
            provider_config += f"""      default_deployment: {model_or_deployment}
      azure_endpoint: ${{AZURE_OPENAI_ENDPOINT}}
      api_version: "2024-10-01-preview"
      use_default_credential: true
"""
        else:
            provider_config += f"""      default_deployment: {model_or_deployment}
      api_key: ${{{provider_info["key_var"]}}}
      azure_endpoint: ${{AZURE_OPENAI_ENDPOINT}}
      api_version: "2024-10-01-preview"
"""
    elif provider == "ollama":
        provider_config += f"""      model: {model_or_deployment}
      base_url: http://localhost:11434
"""
    else:
        provider_config += f"""      model: {model_or_deployment}
      api_key: ${{{provider_info["key_var"]}}}
"""

    provider_config += f"""---

# {profile_name.title()} Profile

Minimal working configuration with {provider_info["name"]} provider.

**Created by**: amplifier setup
**Provider**: {provider_info["name"]}
**Model/Deployment**: {model_or_deployment}

This is a minimal, standalone profile with:
- Basic orchestrator (loop-basic) for universal compatibility
- Simple context manager with 100K tokens
- ONLY {provider_info["name"]} provider (no inheritance conflicts)

To add tools, hooks, or agents, extend from this profile or copy/modify it.
"""

    # Write profile
    with open(profile_path, "w") as f:
        f.write(provider_config)

    console.print(f"[green]✓[/green] Created profile: [cyan]{profile_name}[/cyan]")
    console.print(f"  Location: [dim]{profile_path}[/dim]")
    console.print("  Type: [dim]Minimal standalone[/dim]")
    console.print(f"  Provider: [dim]{provider_info['name']}[/dim]")
    console.print(f"  Model: [dim]{model_or_deployment}[/dim]")

    # Step 5: Set as active
    console.print()
    if Confirm.ask(f"Set '{profile_name}' as your default profile?", default=True):
        config = create_config_manager()
        config.set_active_profile(profile_name)
        console.print(f"[green]✓[/green] Activated profile: [cyan]{profile_name}[/cyan]")

    # Step 5: Success!
    console.print()
    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            "Try it now:\n"
            '  [cyan]amplifier run "Hello, Amplifier!"[/cyan]\n'
            "  [cyan]amplifier[/cyan]  [dim]# Start chat mode[/dim]",
            border_style="green",
        )
    )
    console.print()


def check_first_run() -> bool:
    """Check if this appears to be first run (no provider configured)."""
    key_manager = KeyManager()

    # Check if any provider is configured
    # Note: For Azure, we check ENDPOINT instead of API_KEY because Azure supports
    # multiple auth methods (API key, Azure CLI via DefaultAzureCredential, Managed Identity)
    # and ENDPOINT is always saved regardless of auth method.
    return not any(
        [
            key_manager.has_key("ANTHROPIC_API_KEY"),
            key_manager.has_key("OPENAI_API_KEY"),
            key_manager.has_key("AZURE_OPENAI_ENDPOINT"),  # Detects both API key and Azure CLI auth
        ]
    )


def prompt_first_run_setup(console: Console) -> bool:
    """Prompt user to run setup on first run. Returns True if setup was run."""
    console.print()
    console.print("[yellow]⚠️  No API key found![/yellow]")
    console.print()
    console.print("Amplifier needs an AI provider to work. Let's set that up quickly.")
    console.print()

    if Confirm.ask("Run interactive setup now?", default=True):
        # Import here to avoid circular dependency
        import click

        ctx = click.get_current_context()
        ctx.invoke(setup_cmd)
        return True
    console.print()
    console.print("[yellow]Setup skipped.[/yellow] To configure later, run:")
    console.print("  [cyan]amplifier setup[/cyan]")
    console.print()
    console.print("Or set an API key manually:")
    console.print('  [cyan]export ANTHROPIC_API_KEY="your-key"[/cyan]')
    console.print()
    return False
