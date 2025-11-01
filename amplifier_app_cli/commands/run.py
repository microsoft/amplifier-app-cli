"""Primary run command for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Callable

import click

from ..console import console
from ..data.profiles import get_system_default_profile
from ..lib.app_settings import AppSettings
from ..paths import create_agent_loader
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..runtime.config import resolve_app_config

InteractiveChat = Callable[[dict, list, bool, str | None, str], None]
ExecuteSingle = Callable[[str, dict, list, bool, str | None, str], None]
SearchPathProvider = Callable[[], list]


def register_run_command(
    cli: click.Group,
    *,
    interactive_chat: InteractiveChat,
    execute_single: ExecuteSingle,
    get_module_search_paths: SearchPathProvider,
    check_first_run: Callable[[], bool],
    prompt_first_run_init: Callable[[any], bool],
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

        if mode == "chat":
            if not session_id:
                session_id = str(uuid.uuid4())
                console.print(f"\n[dim]Session ID: {session_id}[/dim]")
            asyncio.run(interactive_chat(config_data, search_paths, verbose, session_id, active_profile_name))
        else:
            if not prompt:
                console.print("[red]Error:[/red] Prompt required in single mode")
                sys.exit(1)
            asyncio.run(execute_single(prompt, config_data, search_paths, verbose, session_id, active_profile_name))

    return run


__all__ = ["register_run_command"]
