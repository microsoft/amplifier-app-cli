"""First-run detection, auto-initialization, and init dashboard for Amplifier."""

import logging

import click
from rich.console import Console
from rich.prompt import Confirm
from rich.prompt import Prompt
from rich.table import Table

from ..key_manager import KeyManager
from ..lib.settings import AppSettings
from ..paths import create_config_manager
from ..provider_config_utils import configure_provider
from ..provider_manager import ProviderManager
from ..provider_env_detect import detect_provider_from_env
from ..provider_sources import install_known_providers
from .routing import _discover_matrix_files
from .routing import _get_configured_provider_types
from .routing import _load_all_matrices
from .routing import _resolve_role

console = Console()
logger = logging.getLogger(__name__)


def _get_settings() -> AppSettings:
    """Get AppSettings instance. Extracted for testability."""
    return AppSettings()


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
    """Prompt user to run init on first run. Returns True if provider was added.

    When no providers are configured, guides the user to `amplifier init`
    or auto-triggers the provider management flow.

    Note: Post-update scenarios (settings exist but module missing) are auto-fixed
    in check_first_run() and won't reach this function.
    """
    console_arg.print()
    console_arg.print("[yellow]⚠️  No provider configured![/yellow]")
    console_arg.print()
    console_arg.print("Amplifier needs an AI provider. Let's set one up:")
    console_arg.print(
        "[dim]Tip: Run [bold]amplifier init[/bold] to configure providers and routing[/dim]"
    )
    console_arg.print()

    if Confirm.ask("Run setup now?", default=True):
        from .provider import provider_manage_loop

        settings = _get_settings()
        provider_manage_loop(settings)
        # Check if a provider was actually added
        providers = settings.get_provider_overrides()
        return len(providers) > 0
    console_arg.print()
    console_arg.print("[yellow]Setup skipped.[/yellow] To configure later, run:")
    console_arg.print("  [cyan]amplifier init[/cyan]")
    console_arg.print()
    console_arg.print("Or add a provider directly:")
    console_arg.print("  [cyan]amplifier provider add[/cyan]")
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


# ============================================================
# Task 3: init dashboard — combined setup
# ============================================================


def _display_name(module_id: str) -> str:
    """Get display name from module ID (strip provider- prefix)."""
    return module_id.replace("provider-", "")


def init_dashboard_loop(settings: AppSettings) -> None:
    """Combined setup dashboard — composes provider and routing management."""
    while True:
        console.print(
            "\n  [bold]══════════════════════════════════════════════════════[/bold]"
        )
        console.print("  [bold]Amplifier Setup[/bold]")
        console.print(
            "  [bold]══════════════════════════════════════════════════════[/bold]\n"
        )

        # 1. Display provider summary table (condensed)
        providers = settings.get_provider_overrides()
        if not providers:
            console.print("  [yellow]No providers configured.[/yellow]\n")
        else:
            table = Table(title="Providers")
            table.add_column("Name/ID", style="cyan")
            table.add_column("Default Model")
            table.add_column("Priority", justify="right")

            # Find min priority for star marker
            priorities = []
            for p in providers:
                config = p.get("config", {})
                pri = config.get("priority", 100) if isinstance(config, dict) else 100
                priorities.append(pri)
            min_priority = min(priorities) if priorities else 0

            for p in providers:
                module = p.get("module", "unknown")
                display = p.get("id") or _display_name(module)
                config = p.get("config", {})
                model = (
                    config.get("default_model", "-")
                    if isinstance(config, dict)
                    else "-"
                )
                pri = config.get("priority", 100) if isinstance(config, dict) else 100

                is_primary = pri == min_priority
                name_col = f"★ {display}" if is_primary else f"  {display}"

                table.add_row(name_col, model, str(pri))

            console.print(table)

        # 2. Display routing summary (condensed resolution)
        routing_config = settings.get_routing_config()
        active_matrix = routing_config.get("matrix", "balanced")
        console.print(f"  Routing: [bold]{active_matrix}[/bold]")

        # Show condensed resolution if matrices are available
        matrix_files = _discover_matrix_files()
        matrices = _load_all_matrices(matrix_files)
        if active_matrix in matrices:
            matrix_data = matrices[active_matrix]
            provider_types = _get_configured_provider_types(settings)
            roles = matrix_data.get("roles", {})

            if roles:
                table = Table(title=f"Routing: {active_matrix}")
                table.add_column("Role", style="cyan")
                table.add_column("Model", style="green")
                table.add_column("Provider")

                for role_name, role_config in roles.items():
                    model, provider_type = _resolve_role(role_config, provider_types)
                    if model and provider_type:
                        table.add_row(role_name, model, provider_type)
                    else:
                        table.add_row(
                            role_name,
                            "[yellow]⚠ (no provider)[/yellow]",
                            "[dim]-[/dim]",
                        )

                console.print(table)

        # 3. Actions
        console.print("\n  Actions:")
        console.print("    [p] Manage providers")
        console.print("    [r] Manage routing")
        console.print("    [d] Done")
        console.print()

        try:
            choice = Prompt.ask("  Choice", default="d").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "d":
            break
        elif choice == "p":
            from .provider import provider_manage_loop

            provider_manage_loop(settings)
            # Returns here, re-renders dashboard
        elif choice == "r":
            from .routing import routing_manage_loop

            routing_manage_loop(settings)
            # Returns here, re-renders dashboard


@click.command("init")
def init_cmd() -> None:
    """Interactive setup — manage providers and routing."""
    settings = _get_settings()

    # First-run: if no providers, go straight to provider manage
    providers = settings.get_provider_overrides()
    if not providers:
        console.print(
            "\n  [yellow]No providers configured. Let's set one up:[/yellow]\n"
        )
        from .provider import provider_manage_loop

        provider_manage_loop(settings)

    init_dashboard_loop(settings)
