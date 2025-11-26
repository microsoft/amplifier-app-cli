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
    for idx, (module_id, name, _desc) in enumerate(providers, 1):
        provider_map[str(idx)] = module_id
        console.print(f"  [{idx}] {name}")

    console.print()

    choices = list(provider_map.keys())
    default = "1"  # First provider is default

    provider_choice = Prompt.ask("Which provider?", choices=choices, default=default)
    module_id = provider_map[provider_choice]

    # Step 2: Provider-specific configuration using unified dispatcher
    provider_config = configure_provider(module_id, key_manager)
    if provider_config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

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

    # Save configuration
    config.set_active_profile(profile_id)
    provider_mgr.use_provider(module_id, scope="local", config=provider_config, source=None)

    console.print()
    console.print(
        Panel.fit(
            '[bold green]✓ Ready![/bold green]\n\nTry it now:\n  [cyan]amplifier run "Hello, Amplifier!"[/cyan]',
            border_style="green",
        )
    )
    console.print()
