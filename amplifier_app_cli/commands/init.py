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


def _is_provider_module_installed(provider_id: str) -> bool:
    """Check if a provider module is actually installed and importable.

    This catches the case where provider settings exist but the module
    was uninstalled (e.g., after `amplifier update` which wipes the venv).

    Args:
        provider_id: Provider module ID (e.g., "provider-anthropic")

    Returns:
        True if the module can be imported, False otherwise
    """
    import importlib
    import importlib.metadata

    # Normalize to full module ID
    module_id = (
        provider_id
        if provider_id.startswith("provider-")
        else f"provider-{provider_id}"
    )

    # Try entry point first (most reliable for properly installed modules)
    try:
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        for ep in eps:
            if ep.name == module_id:
                # Entry point exists - module is installed
                return True
    except Exception:
        pass

    # Fall back to direct import check
    try:
        # Convert provider ID to Python module name
        provider_name = module_id.replace("provider-", "")
        module_name = f"amplifier_module_provider_{provider_name.replace('-', '_')}"
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def check_first_run() -> bool:
    """Check if this appears to be first run (no provider configured).

    Returns True if user should run `amplifier init` before starting a session.

    Detection is based on whether a provider is configured in settings - NOT on
    API key presence, since not all providers require API keys (e.g., Ollama, vLLM,
    Azure OpenAI with CLI auth).

    If a provider is configured but its module is missing (post-update scenario where
    `amplifier update` wiped the venv), this function will automatically reinstall
    all known provider modules without user interaction. We install ALL providers
    (not just the configured one) because bundles may include multiple providers.
    """
    config = create_config_manager()
    provider_mgr = ProviderManager(config)
    current_provider = provider_mgr.get_current_provider()

    # No provider configured = true first run, need interactive init
    if current_provider is None:
        # Check if any provider's credentials are in environment
        from ..provider_env_detect import detect_provider_from_env

        detected_provider = detect_provider_from_env()
        if detected_provider is not None:
            # Provider credentials found in env - auto-configure it
            logger.info(f"Auto-configuring provider from env vars: {detected_provider}")
            # Auto-configure with minimal config (credentials from env)
            provider_mgr.use_provider(detected_provider, scope="global", config={})
            return False
        return True

    # Provider is configured - check if its module is actually installed
    if not _is_provider_module_installed(current_provider.module_id):
        # Post-update scenario: settings exist but provider modules were wiped
        # Auto-fix by reinstalling ALL known providers (bundles may need multiple)
        logger.info(
            f"Provider {current_provider.module_id} is configured but not installed. "
            "Auto-installing providers (this can happen after `amplifier update`)..."
        )
        console.print("[dim]Installing provider modules...[/dim]")

        installed = install_known_providers(config, console, verbose=True)
        if installed:
            # Successfully reinstalled - no need for full init
            console.print()
            return False
        else:
            # Auto-fix failed - fall back to full init
            logger.warning("Failed to auto-install providers, will prompt for init")
            return True

    # Provider configured and module installed - no init needed
    return False


def prompt_first_run_init(console_arg: Console) -> bool:
    """Prompt user to run init on first run. Returns True if init was run.

    Note: Post-update scenarios (settings exist but module missing) are auto-fixed
    in check_first_run() and won't reach this function.
    """
    console_arg.print()
    console_arg.print("[yellow]⚠️  No provider configured![/yellow]")
    console_arg.print()
    console_arg.print("Amplifier needs an AI provider. Let's set that up quickly.")
    console_arg.print(
        "[dim]Tip: Set ANTHROPIC_API_KEY, OPENAI_API_KEY, etc. to skip this.[/dim]"
    )
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
@click.option(
    "--yes",
    "-y",
    "non_interactive",
    is_flag=True,
    help="Non-interactive mode: use env vars and defaults, skip prompts",
)
def init_cmd(non_interactive: bool = False):
    """Interactive first-time setup wizard.

    Auto-runs on first invocation if no configuration exists.
    Configures provider credentials and model.

    Use --yes/-y for non-interactive mode (CI/CD, shadow containers).
    In non-interactive mode, providers are configured from environment
    variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) without prompts.
    """
    import sys

    # Check for TTY if interactive mode requested
    if not non_interactive and not sys.stdin.isatty():
        console.print(
            "[red]Error:[/red] Interactive mode requires a TTY. "
            "Use --yes flag for non-interactive setup."
        )
        console.print("\nExample:")
        console.print("  amplifier init --yes")
        return
    # Non-interactive mode: use env detection and defaults
    if non_interactive:
        from ..provider_env_detect import detect_provider_from_env

        key_manager = KeyManager()
        config = create_config_manager()
        provider_mgr = ProviderManager(config)

        # Install providers quietly
        install_known_providers(config_manager=config, console=None, verbose=False)

        # Detect provider from environment
        # detect_provider_from_env() returns the full module_id (e.g., "provider-anthropic")
        module_id = detect_provider_from_env()
        if module_id is None:
            console.print(
                "[red]Error:[/red] No provider credentials found in environment."
            )
            console.print("\nSet one of these environment variables:")
            console.print("  ANTHROPIC_API_KEY")
            console.print("  OPENAI_API_KEY")
            console.print("  AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT")
            return

        # Extract display name (e.g., "anthropic" from "provider-anthropic")
        display_name = module_id.removeprefix("provider-")

        # Configure provider non-interactively
        provider_config = configure_provider(
            module_id, key_manager, non_interactive=True
        )
        if provider_config is None:
            console.print("[red]Configuration failed.[/red]")
            return

        # Save provider configuration
        provider_mgr.use_provider(
            module_id, scope="global", config=provider_config, source=None
        )

        console.print(f"[green]✓ Configured {display_name} from environment[/green]")
        return

    # Interactive mode
    console.print()
    console.print(
        Panel.fit("[bold cyan]Welcome to Amplifier![/bold cyan]", border_style="cyan")
    )
    console.print()

    key_manager = KeyManager()
    config = create_config_manager()
    provider_mgr = ProviderManager(config)

    # Step 0: Install known providers (downloads if not cached)
    console.print("[bold]Installing providers...[/bold]")
    install_known_providers(config_manager=config, console=console, verbose=True)
    console.print()

    # Refresh Python's view of installed packages after uv pip install
    # The subprocess install adds .pth files and metadata that the current
    # Python process doesn't see until we explicitly refresh
    import importlib
    import importlib.metadata
    import site

    # Invalidate import caches
    importlib.invalidate_caches()

    # Re-process site-packages to pick up new .pth files from editable installs
    # This updates sys.path with any new package locations
    for site_dir in site.getsitepackages():
        site.addsitedir(site_dir)

    # Also clear importlib.metadata's internal cache by forcing fresh distribution discovery
    # In Python 3.12+, this is the only way to see newly installed packages
    if hasattr(importlib.metadata, "distributions"):
        # Force fresh iteration of distributions to clear any caching
        list(importlib.metadata.distributions())

    # Step 1: Provider selection - discover installed providers dynamically
    console.print("[bold]Step 1: Provider[/bold]")

    # Get discovered providers
    providers = provider_mgr.list_providers()

    if not providers:
        console.print(
            "[red]Error: No providers available. Installation may have failed.[/red]"
        )
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
    provider_config = configure_provider(
        module_id, key_manager, existing_config=existing_config
    )
    if provider_config is None:
        console.print("[red]Configuration cancelled.[/red]")
        return

    # Save provider configuration to user's global settings (~/.amplifier/settings.yaml)
    # This is first-time setup, so it should be available across all projects
    
    provider_mgr.use_provider(
        module_id, scope="global", config=provider_config, source=None
    )

    console.print()
    console.print(
        Panel.fit(
            "[bold green]✓ Ready![/bold green]\n\nStart an interactive session:\n  [cyan]amplifier[/cyan]\n\nThen ask:\n  [dim]Tell me about the Amplifier ecosystem[/dim]",
            border_style="green",
        )
    )
    console.print()
