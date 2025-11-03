"""Primary run command for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any

import click

from ..console import console
from ..data.profiles import get_system_default_profile
from ..lib.app_settings import AppSettings
from ..paths import create_agent_loader
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..runtime.config import resolve_app_config

InteractiveChat = Callable[[dict, list, bool, str | None, str], Coroutine[Any, Any, None]]
ExecuteSingle = Callable[[str, dict, list, bool, str | None, str], Coroutine[Any, Any, None]]
SearchPathProvider = Callable[[], list]


async def _check_updates_background():
    """Check for updates in background (non-blocking).

    Runs automatically on startup. Shows notifications if updates available.
    Failures are silent (logged only, don't disrupt user).
    """
    from ..utils.update_check import check_amplifier_updates_background
    from ..utils.update_check import check_module_updates_background

    try:
        # Check Amplifier libraries
        amplifier_result = await check_amplifier_updates_background()

        if amplifier_result and amplifier_result.has_updates:
            console.print()
            console.print("[green]✨ Amplifier updates available![/green]")

            for update in amplifier_result.updates_available[:3]:  # Show max 3
                console.print(f"   • {update.library}: {update.installed_sha} → {update.remote_sha}")

            if len(amplifier_result.updates_available) > 3:
                console.print(f"   ... and {len(amplifier_result.updates_available) - 3} more")

            console.print()
            console.print("   Run [cyan]amplifier update[/cyan] to upgrade")
            console.print()

        # Check modules
        module_updates = await check_module_updates_background()

        if module_updates and len(module_updates) > 0:
            console.print()
            console.print("[yellow]⚠ Module updates available:[/yellow]")

            for mod in module_updates[:3]:  # Show max 3
                console.print(f"   • {mod['module_id']}@{mod['ref']} ({mod['age_days']}d old)")

            if len(module_updates) > 3:
                console.print(f"   ... and {len(module_updates) - 3} more")

            console.print()
            console.print("   Run [cyan]amplifier module refresh[/cyan] to update")
            console.print()

    except Exception as e:
        # Silent failure - don't disrupt user
        import logging

        logging.getLogger(__name__).debug(f"Background update check failed: {e}")


def register_run_command(
    cli: click.Group,
    *,
    interactive_chat: InteractiveChat,
    execute_single: ExecuteSingle,
    get_module_search_paths: SearchPathProvider,
    check_first_run: Callable[[], bool],
    prompt_first_run_init: Callable[[Any], bool],
):
    """Register the run command on the root CLI group."""

    @cli.command()
    @click.argument("prompt", required=False)
    @click.option("--profile", "-P", help="Profile to use for this session")
    @click.option("--provider", "-p", default=None, help="LLM provider to use")
    @click.option("--model", "-m", help="Model to use (provider-specific)")
    @click.option("--mode", type=click.Choice(["chat", "single"]), default="single", help="Execution mode")
    @click.option("--session-id", help="Session ID for persistence (generates UUID if not provided)")
    @click.option("--verbose", "-v", is_flag=True, help="Verbose output")
    def run(
        prompt: str | None,
        profile: str | None,
        provider: str,
        model: str | None,
        mode: str,
        session_id: str | None,
        verbose: bool,
    ):
        """Execute a prompt or start an interactive session."""

        cli_overrides = {}
        if provider:
            cli_overrides.setdefault("provider", {})["name"] = provider
        if model:
            cli_overrides.setdefault("provider", {})["model"] = model

        config_manager = create_config_manager()
        active_profile_name = profile or config_manager.get_active_profile() or get_system_default_profile()

        if check_first_run() and not profile and prompt_first_run_init(console):
            active_profile_name = config_manager.get_active_profile() or get_system_default_profile()

        profile_loader = create_profile_loader()
        agent_loader = create_agent_loader()
        app_settings = AppSettings(config_manager)

        config_data = resolve_app_config(
            config_manager=config_manager,
            profile_loader=profile_loader,
            agent_loader=agent_loader,
            app_settings=app_settings,
            cli_config=cli_overrides,
            profile_override=active_profile_name,
            console=console,
        )

        search_paths = get_module_search_paths()

        # Run background update check
        asyncio.run(_check_updates_background())

        if mode == "chat":
            if not session_id:
                session_id = str(uuid.uuid4())
                console.print(f"\n[dim]Session ID: {session_id}[/dim]")
            asyncio.run(interactive_chat(config_data, search_paths, verbose, session_id, active_profile_name))
        else:
            if prompt is None:
                # Allow piping prompt content via stdin when no positional argument provided.
                if not sys.stdin.isatty():
                    prompt = sys.stdin.read()
                    if prompt is not None and not prompt.strip():
                        prompt = None
                if prompt is None:
                    console.print("[red]Error:[/red] Prompt required in single mode")
                    sys.exit(1)

            asyncio.run(execute_single(prompt, config_data, search_paths, verbose, session_id, active_profile_name))

    return run


__all__ = ["register_run_command"]
