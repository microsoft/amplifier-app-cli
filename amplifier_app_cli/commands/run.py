"""Primary run command for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import Callable
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import click

from ..console import console
from ..data.profiles import get_system_default_profile
from ..effective_config import get_effective_config_summary
from ..lib.app_settings import AppSettings
from ..paths import create_agent_loader
from ..paths import create_bundle_resolver
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..runtime.config import resolve_app_config

logger = logging.getLogger(__name__)

InteractiveChat = Callable[[dict, list, bool, str | None, str, Path | None], Coroutine[Any, Any, None]]
InteractiveResume = Callable[[dict, list, bool, str, list[dict], str, Path | None], Coroutine[Any, Any, None]]
ExecuteSingle = Callable[[str, dict, list, bool, str | None, str, str, Path | None], Coroutine[Any, Any, None]]
ExecuteSingleWithSession = Callable[
    [str, dict, list, bool, str, list[dict], str, str, Path | None], Coroutine[Any, Any, None]
]
SearchPathProvider = Callable[[], list]


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
    @click.option("--bundle", "-B", help="Bundle to use for this session (alternative to profile)")
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
        bundle: str | None,
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

        # Check for active bundle from settings (opt-in via 'amplifier bundle use')
        # CLI --bundle flag takes precedence over settings
        if not bundle:
            bundle_settings = config_manager.get_merged_settings().get("bundle", {})
            if isinstance(bundle_settings, dict):
                bundle = bundle_settings.get("active")

        active_profile_name = profile or config_manager.get_active_profile() or get_system_default_profile()

        if check_first_run() and not profile and prompt_first_run_init(console):
            active_profile_name = config_manager.get_active_profile() or get_system_default_profile()

        profile_loader = create_profile_loader()

        # Create bundle resolver if bundle is specified (either from CLI or settings),
        # and get bundle base_path early so we can pass it to agent_loader for @mention resolution
        bundle_resolver = create_bundle_resolver() if bundle else None
        bundle_base_path = None
        if bundle and bundle_resolver:
            try:
                bundle_obj = asyncio.run(bundle_resolver.load(bundle))
                bundle_base_path = bundle_obj.base_path
            except Exception as e:
                # Log warning; full error will be handled later in resolve_app_config
                logger.warning("Early bundle load failed for '%s': %s", bundle, e)

        # Create agent loader with appropriate mode:
        # - Bundle mode: only load bundle agents (not profile/collection agents)
        # - Profile mode: only load profile/collection agents (not bundle agents)
        agent_loader = create_agent_loader(
            use_bundle=bool(bundle), bundle_name=bundle, bundle_base_path=bundle_base_path
        )
        app_settings = AppSettings(config_manager)

        # Track configuration source for display
        # When bundle is specified, use bundle name; otherwise use profile name
        config_source_name = f"bundle:{bundle}" if bundle else active_profile_name

        config_data = resolve_app_config(
            config_manager=config_manager,
            profile_loader=profile_loader,
            agent_loader=agent_loader,
            app_settings=app_settings,
            cli_config=cli_overrides,
            profile_override=active_profile_name if not bundle else None,
            bundle_name=bundle,
            bundle_resolver=bundle_resolver,
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

        # Run update check (uses unified startup_checker with settings.yaml)
        from ..utils.startup_checker import check_and_notify

        asyncio.run(check_and_notify())

        if mode == "chat":
            # Interactive mode
            if resume:
                # Resume existing session (transcript loaded earlier)
                if transcript is None:
                    console.print("[red]Error:[/red] Failed to load session transcript")
                    sys.exit(1)
                asyncio.run(
                    interactive_chat_with_session(
                        config_data, search_paths, verbose, resume, transcript, config_source_name, bundle_base_path
                    )
                )
            else:
                # New session - banner displayed by interactive_chat
                session_id = str(uuid.uuid4())
                asyncio.run(
                    interactive_chat(
                        config_data, search_paths, verbose, session_id, config_source_name, bundle_base_path
                    )
                )
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
                        config_source_name,
                        output_format,
                        bundle_base_path,
                    )
                )
            else:
                # Create new session
                session_id = str(uuid.uuid4())
                if output_format == "text":
                    config_summary = get_effective_config_summary(config_data, config_source_name)
                    console.print(f"\n[dim]Session ID: {session_id}[/dim]")
                    console.print(f"[dim]{config_summary.format_banner_line()}[/dim]")
                asyncio.run(
                    execute_single(
                        prompt,
                        config_data,
                        search_paths,
                        verbose,
                        session_id,
                        config_source_name,
                        output_format,
                        bundle_base_path,
                    )
                )

    return run


__all__ = ["register_run_command"]
