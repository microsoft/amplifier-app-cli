"""First-run detection and auto-initialization for Amplifier.

The `amplifier init` command has been removed. First-run detection now
triggers the `amplifier provider add` flow instead.
"""

import logging

from rich.console import Console
from rich.prompt import Confirm

from ..key_manager import KeyManager
from ..paths import create_config_manager
from ..provider_config_utils import configure_provider
from ..provider_manager import ProviderManager
from ..provider_env_detect import detect_provider_from_env
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

    Returns True if the user needs to add a provider before starting a session.

    Detection is based on whether a provider is configured in settings - NOT on
    API key presence, since not all providers require API keys (e.g., Ollama, vLLM,
    Azure OpenAI with CLI auth).

    IMPORTANT: If no provider is configured, the user must run
    `amplifier provider add`. We do NOT silently pick a default provider based
    on environment variables - the user must explicitly configure their provider.
    This ensures:
    1. User explicitly chooses their provider
    2. No surprise defaults that may not match bundle requirements
    3. Clear error path when nothing is configured

    If a provider is configured but its module is missing (post-update scenario where
    `amplifier update` wiped the venv), this function will automatically reinstall
    all known provider modules without user interaction. We install ALL providers
    (not just the configured one) because bundles may include multiple providers.
    """
    config = create_config_manager()
    provider_mgr = ProviderManager(config)
    current_provider = provider_mgr.get_current_provider()

    logger.debug(
        f"check_first_run: current_provider={current_provider.module_id if current_provider else None}"
    )

    # No provider configured = MUST add one
    # Do NOT silently pick defaults from env vars - user must explicitly configure
    if current_provider is None:
        logger.info(
            "No provider configured in settings. "
            "User must explicitly configure a provider via 'amplifier provider add'."
        )
        return True

    # Provider is configured - check if its module is actually installed
    module_installed = _is_provider_module_installed(current_provider.module_id)
    logger.debug(
        f"check_first_run: provider={current_provider.module_id}, "
        f"module_installed={module_installed}"
    )

    if not module_installed:
        # Post-update scenario: settings exist but provider modules were wiped
        # Auto-fix by reinstalling ALL known providers (bundles may need multiple)
        logger.info(
            f"Provider {current_provider.module_id} is configured but module not installed. "
            "Auto-installing providers (this can happen after 'amplifier update')..."
        )
        console.print("[dim]Installing provider modules...[/dim]")

        installed = install_known_providers(config, console, verbose=True)
        if installed:
            # Successfully reinstalled - no need for full init
            logger.debug("check_first_run: auto-install succeeded, no init needed")
            console.print()
            return False
        else:
            # Auto-fix failed - fall back to provider add prompt
            logger.warning(
                "Failed to auto-install providers after detecting missing modules. "
                "Will prompt user to add a provider."
            )
            return True

    # Provider configured and module installed - no init needed
    logger.debug(
        f"check_first_run: provider {current_provider.module_id} configured and installed, "
        "no init needed"
    )
    return False


def prompt_first_run_init(console_arg: Console) -> bool:
    """Prompt user to add a provider on first run. Returns True if provider was added.

    When no providers are configured, auto-triggers the provider add flow
    with a friendly message.

    Note: Post-update scenarios (settings exist but module missing) are auto-fixed
    in check_first_run() and won't reach this function.
    """
    console_arg.print()
    console_arg.print("[yellow]⚠️  No provider configured![/yellow]")
    console_arg.print()
    console_arg.print("Amplifier needs an AI provider. Let's set one up:")
    console_arg.print(
        "[dim]Tip: Run [bold]amplifier provider add[/bold] to configure a provider[/dim]"
    )
    console_arg.print()

    if Confirm.ask("Run provider add now?", default=True):
        # Import here to avoid circular dependency
        import click

        from .provider import provider_add

        ctx = click.get_current_context()
        ctx.invoke(provider_add)
        return True
    console_arg.print()
    console_arg.print("[yellow]Setup skipped.[/yellow] To configure later, run:")
    console_arg.print("  [cyan]amplifier provider add[/cyan]")
    console_arg.print()
    console_arg.print("Or set an API key manually:")
    console_arg.print('  [cyan]export ANTHROPIC_API_KEY="your-key"[/cyan]')
    console_arg.print()
    return False


def auto_init_from_env(console_arg: Console | None = None) -> bool:
    """Auto-configure from environment variables in non-interactive contexts.

    Equivalent to 'amplifier provider add --yes' but called programmatically.
    Used when check_first_run() returns True and stdin is not a TTY
    (Docker containers, CI pipelines, shadow environments).

    Returns True if a provider was configured, False otherwise.
    This is best-effort — failures are logged but never raised.
    """
    try:
        logger.info(
            "Non-interactive environment detected, "
            "attempting auto-init from environment variables"
        )

        config = create_config_manager()

        # Install providers quietly
        install_known_providers(config_manager=config, console=None, verbose=False)

        # Detect provider from environment
        module_id = detect_provider_from_env()
        if module_id is None:
            msg = (
                "No provider credentials found in environment. "
                "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, etc. "
                "or run 'amplifier provider add' interactively."
            )
            logger.warning(msg)
            if console_arg:
                console_arg.print(f"[yellow]{msg}[/yellow]")
            return False

        # Configure provider non-interactively
        key_manager = KeyManager()
        provider_mgr = ProviderManager(config)

        provider_config = configure_provider(
            module_id, key_manager, non_interactive=True
        )
        if provider_config is None:
            logger.warning("Auto-init: provider configuration failed")
            if console_arg:
                console_arg.print(
                    "[yellow]Auto-init failed. Run 'amplifier provider add' manually.[/yellow]"
                )
            return False

        # Save provider configuration
        provider_mgr.use_provider(
            module_id, scope="global", config=provider_config, source=None
        )

        display_name = module_id.removeprefix("provider-")
        logger.info(f"Auto-configured {display_name} from environment")
        if console_arg:
            console_arg.print(
                f"[green]\u2713 Auto-configured {display_name} from environment[/green]"
            )
        return True

    except Exception as e:
        logger.warning(f"Auto-init failed: {e}")
        if console_arg:
            console_arg.print(
                f"[yellow]Auto-init failed: {e}. "
                f"Run 'amplifier provider add' manually.[/yellow]"
            )
        return False
