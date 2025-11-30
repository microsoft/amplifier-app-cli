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
from ..effective_config import get_effective_config_summary
from ..lib.app_settings import AppSettings
from ..paths import create_agent_loader
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..runtime.config import resolve_app_config

InteractiveChat = Callable[[dict, list, bool, str | None, str], Coroutine[Any, Any, None]]
InteractiveResume = Callable[[dict, list, bool, str, list[dict], str], Coroutine[Any, Any, None]]
ExecuteSingle = Callable[[str, dict, list, bool, str | None, str, str], Coroutine[Any, Any, None]]
ExecuteSingleWithSession = Callable[[str, dict, list, bool, str, list[dict], str, str], Coroutine[Any, Any, None]]
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
            console.print("   Run [cyan]amplifier module update[/cyan] to update")
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
    interactive_chat_with_session: InteractiveResume,
    execute_single: ExecuteSingle,
    execute_single_with_session: ExecuteSingleWithSession,
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
    @click.option("--max-tokens", type=int, help="Maximum output tokens")
    @click.option("--mode", type=click.Choice(["chat", "single"]), default="single", help="Execution mode")
    @click.option("--resume", help="Resume specific session with new prompt")
    @click.option("--verbose", "-v", is_flag=True, help="Verbose output")
    @click.option(
        "--output-format",
        type=click.Choice(["text", "json", "json-trace"]),
        default="text",
        help="Output format: text (markdown), json (response only), json-trace (full execution detail)",
    )
    def run(
        prompt: str | None,
        profile: str | None,
        provider: str,
        model: str | None,
        max_tokens: int | None,
        mode: str,
        resume: str | None,
        verbose: bool,
        output_format: str,
    ):
        """Execute a prompt or start an interactive session."""
        from ..session_store import SessionStore

        # Handle --resume flag
        if resume:
            store = SessionStore()
            if not store.exists(resume):
                console.print(f"[red]Error:[/red] Session '{resume}' not found")
                sys.exit(1)

            try:
                transcript, metadata = store.load(resume)
                console.print(f"[green]✓[/green] Resuming session: {resume}")
                console.print(f"  Messages: {len(transcript)}")

                saved_profile = metadata.get("profile", "unknown")
                if not profile and saved_profile and saved_profile != "unknown":
                    profile = saved_profile
                    console.print(f"  Using saved profile: {profile}")

            except Exception as exc:
                console.print(f"[red]Error loading session:[/red] {exc}")
                sys.exit(1)

            # Determine mode based on prompt presence
            if prompt is None and sys.stdin.isatty():
                # No prompt, no pipe → interactive mode
                mode = "chat"
            else:
                # Has prompt or piped input → single-shot mode
                if prompt is None:
                    prompt = sys.stdin.read()
                    if not prompt or not prompt.strip():
                        console.print("[red]Error:[/red] Prompt required when resuming in single mode")
                        sys.exit(1)
                mode = "single"
        else:
            transcript = None

        cli_overrides = {}

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

        # If a specific provider was requested, filter providers to that entry
        if provider:
            provider_module = provider if provider.startswith("provider-") else f"provider-{provider}"
            providers_list = config_data.get("providers", [])

            matching = [
                entry for entry in providers_list if isinstance(entry, dict) and entry.get("module") == provider_module
            ]

            if not matching:
                console.print(f"[red]Error:[/red] Provider '{provider}' not available in active profile")
                sys.exit(1)

            selected_provider = {**matching[0]}
            selected_config = dict(selected_provider.get("config") or {})

            if model:
                selected_config["default_model"] = model
            if max_tokens:
                selected_config["max_tokens"] = max_tokens

            selected_provider["config"] = selected_config
            config_data["providers"] = [selected_provider]

            # Hint orchestrator if it supports default provider configuration
            session_cfg = config_data.setdefault("session", {})
            orchestrator_cfg = session_cfg.get("orchestrator")
            if isinstance(orchestrator_cfg, dict):
                orchestrator_config = dict(orchestrator_cfg.get("config") or {})
                orchestrator_config["default_provider"] = provider_module
                orchestrator_cfg["config"] = orchestrator_config
            elif isinstance(orchestrator_cfg, str):
                # Convert shorthand into dict form with default provider hint
                # Preserve orchestrator_source when converting to dict format
                orchestrator_dict: dict[str, Any] = {
                    "module": orchestrator_cfg,
                    "config": {"default_provider": provider_module},
                }
                if "orchestrator_source" in session_cfg:
                    orchestrator_dict["source"] = session_cfg["orchestrator_source"]
                session_cfg["orchestrator"] = orchestrator_dict

            orchestrator_meta = config_data.setdefault("orchestrator", {})
            if isinstance(orchestrator_meta, dict):
                meta_config = dict(orchestrator_meta.get("config") or {})
                meta_config["default_provider"] = provider_module
                orchestrator_meta["config"] = meta_config
        elif model or max_tokens:
            providers_list = config_data.get("providers", [])
            if not providers_list:
                console.print("[yellow]Warning:[/yellow] No providers configured; ignoring CLI overrides")
            else:
                updated_providers: list[dict[str, Any]] = []
                override_applied = False

                for entry in providers_list:
                    if not override_applied and isinstance(entry, dict) and entry.get("module"):
                        new_entry = {**entry}
                        merged_config = dict(new_entry.get("config") or {})
                        if model:
                            merged_config["default_model"] = model
                        if max_tokens:
                            merged_config["max_tokens"] = max_tokens
                        new_entry["config"] = merged_config
                        updated_providers.append(new_entry)
                        override_applied = True
                    else:
                        updated_providers.append(entry)

                config_data["providers"] = updated_providers

        # Run background update check
        asyncio.run(_check_updates_background())

        if mode == "chat":
            # Interactive mode
            if resume:
                # Resume existing session (transcript loaded earlier)
                if transcript is None:
                    console.print("[red]Error:[/red] Failed to load session transcript")
                    sys.exit(1)
                asyncio.run(
                    interactive_chat_with_session(
                        config_data, search_paths, verbose, resume, transcript, active_profile_name
                    )
                )
            else:
                # New session - banner displayed by interactive_chat
                session_id = str(uuid.uuid4())
                asyncio.run(interactive_chat(config_data, search_paths, verbose, session_id, active_profile_name))
        else:
            # Single-shot mode
            if prompt is None:
                # Allow piping prompt content via stdin
                if not sys.stdin.isatty():
                    prompt = sys.stdin.read()
                    if prompt is not None and not prompt.strip():
                        prompt = None
                if prompt is None:
                    console.print("[red]Error:[/red] Prompt required in single mode")
                    sys.exit(1)

            # Always persist single-shot sessions
            if resume:
                # Resume existing session with context
                if transcript is None:
                    console.print("[red]Error:[/red] Failed to load session transcript")
                    sys.exit(1)
                asyncio.run(
                    execute_single_with_session(
                        prompt,
                        config_data,
                        search_paths,
                        verbose,
                        resume,
                        transcript,
                        active_profile_name,
                        output_format,
                    )
                )
            else:
                # Create new session
                session_id = str(uuid.uuid4())
                if output_format == "text":
                    config_summary = get_effective_config_summary(config_data, active_profile_name)
                    console.print(f"\n[dim]Session ID: {session_id}[/dim]")
                    console.print(f"[dim]{config_summary.format_banner_line()}[/dim]")
                asyncio.run(
                    execute_single(
                        prompt, config_data, search_paths, verbose, session_id, active_profile_name, output_format
                    )
                )

    return run


__all__ = ["register_run_command"]
