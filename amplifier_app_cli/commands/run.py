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
    from ..utils.update_check import check_updates_background

    try:
        # Check all sources (unified)
        report = await check_updates_background()

        if not report:
            return  # Skipped (cached)

        # Show cached git updates
        if report.cached_git_sources:
            console.print()
            console.print("[yellow]⚠ Updates available:[/yellow]")

            for status in report.cached_git_sources[:3]:  # Show max 3
                console.print(f"   • {status.name}@{status.ref}")
                console.print(f"     {status.cached_sha} → {status.remote_sha} ({status.age_days}d old)")

            if len(report.cached_git_sources) > 3:
                console.print(f"   ... and {len(report.cached_git_sources) - 3} more")

            console.print()
            console.print("   Run [cyan]amplifier module refresh[/cyan] to update")
            console.print()

        # Show local source info (remote ahead)
        local_with_remote_ahead = [
            s for s in report.local_file_sources if s.has_remote and s.remote_sha and s.remote_sha != s.local_sha
        ]

        if local_with_remote_ahead:
            console.print()
            console.print("[cyan]ℹ Local sources behind remote:[/cyan]")

            for status in local_with_remote_ahead[:3]:
                console.print(f"   • {status.name}: {status.local_sha} → {status.remote_sha}")
                if status.commits_behind > 0:
                    console.print(f"     {status.commits_behind} commits behind")

            console.print()
            console.print("   [dim]Use git pull in local directories to update[/dim]")
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
