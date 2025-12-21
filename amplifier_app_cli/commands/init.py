"""Interactive initialization command for Amplifier."""

import logging

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.prompt import Prompt

from ..key_manager import KeyManager
from ..paths import create_config_manager
from ..provider_config_utils import configure_provider
from ..provider_manager import ProviderManager
from ..provider_sources import install_known_providers

console = Console()
logger = logging.getLogger(__name__)


def check_first_run() -> bool:
    """Check if this appears to be first run (no provider configured).

    Returns True if user should run `amplifier init` before starting a session.
    Checks both:
    1. API keys (environment or keyring) - necessary for authentication
    2. Provider configured in settings - necessary for bundle system to know which provider to use

    Both conditions must be satisfied for a working setup. Having an API key alone
    is not sufficient because the bundle system needs to know which provider to use.
    """
    key_manager = KeyManager()

    # Check if any API key is present
    # Note: For Azure, we check ENDPOINT instead of API_KEY because Azure supports
    # multiple auth methods (API key, Azure CLI via DefaultAzureCredential, Managed Identity)
    # and ENDPOINT is always saved regardless of auth method.
    has_api_key = any(
        [
            key_manager.has_key("ANTHROPIC_API_KEY"),
            key_manager.has_key("OPENAI_API_KEY"),
            key_manager.has_key("AZURE_OPENAI_ENDPOINT"),  # Detects both API key and Azure CLI auth
        ]
    )

    # Also check if a provider is configured in settings
    # This ensures that even with an API key in env, we still prompt init if no provider configured
    config = create_config_manager()
    provider_mgr = ProviderManager(config)
    has_configured_provider = provider_mgr.get_current_provider() is not None

    # First run if either condition is missing
    return not (has_api_key and has_configured_provider)


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
    config = create_config_manager()
    provider_mgr = ProviderManager(config)

    # Step 0: Install known providers (downloads if not cached)
    console.print("[bold]Installing providers...[/bold]")
    install_known_providers(config_manager=config, console=console, verbose=True)
    console.print()

    # Step 1: Provider selection - discover installed providers dynamically
    console.print("[bold]Step 1: Provider[/bold]")

    # Get discovered providers
    providers = provider_mgr.list_providers()

    if not providers:
        console.print("[red]Error: No providers available. Installation may have failed.[/red]")
        return

    # Build dynamic menu from discovered providers
    provider_map: dict[str, str] = {}
    reverse_map: dict[str, str] = {}  # module_id -> menu number
    for idx, (module_id, name, _desc) in enumerate(providers, 1):
        provider_map[str(idx)] = module_id
        reverse_map[module_id] = str(idx)
        console.print(f"  [{idx}] {name}")

    console.print()

    choices = list(provider_map.keys())

    # Determine default based on currently configured provider
    default = "1"  # Fallback to first provider
    current_provider = provider_mgr.get_current_provider()
    if current_provider and current_provider.module_id in reverse_map:
        default = reverse_map[current_provider.module_id]

    provider_choice = Prompt.ask("Which provider?", choices=choices, default=default)
    module_id = provider_map[provider_choice]

    # Get existing config for this provider (if re-configuring)
    # This allows previous values to be used as defaults
    # Read from global (USER) scope since init is for global first-time setup
    existing_config = provider_mgr.get_provider_config(module_id, scope="global")

    # Step 2: Provider-specific configuration using unified dispatcher
    provider_config = configure_provider(module_id, key_manager, existing_config=existing_config)
    if provider_config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

    # Save provider configuration to user's global settings (~/.amplifier/settings.yaml)
    # This is first-time setup, so it should be available across all projects
    # Note: We don't set a profile - the bundle system handles defaults
    provider_mgr.use_provider(module_id, scope="global", config=provider_config, source=None)

    console.print()
    console.print(
        Panel.fit(
            '[bold green]✓ Ready![/bold green]\n\nTry it now:\n  [cyan]amplifier run "Hello, Amplifier!"[/cyan]',
            border_style="green",
        )
    )
    console.print()
