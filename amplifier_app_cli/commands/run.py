"""Primary run command for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any

import click

if TYPE_CHECKING:
    pass

from rich.panel import Panel

from amplifier_foundation.exceptions import BundleError, BundleValidationError

from ..console import console
from ..session_store import extract_session_mode
from ..effective_config import get_effective_config_summary
from ..lib.settings import AppSettings
from ..paths import create_config_manager
from ..runtime.config import resolve_config
from ..types import (
    ExecuteSingleProtocol,
    InteractiveChatProtocol,
    SearchPathProviderProtocol,
)

logger = logging.getLogger(__name__)


def register_run_command(
    cli: click.Group,
    *,
    interactive_chat: InteractiveChatProtocol,
    execute_single: ExecuteSingleProtocol,
    get_module_search_paths: SearchPathProviderProtocol,
    check_first_run: Callable[[], bool],
    prompt_first_run_init: Callable[[Any], bool],
):
    """Register the run command on the root CLI group."""

    @cli.command()
    @click.argument("prompt", required=False)
    @click.option("--bundle", "-B", help="Bundle to use for this session")
    @click.option("--provider", "-p", default=None, help="LLM provider to use")
    @click.option("--model", "-m", help="Model to use (provider-specific)")
    @click.option("--max-tokens", type=int, help="Maximum output tokens")
    @click.option(
        "--mode",
        type=click.Choice(["chat", "single"]),
        default="single",
        help="Execution mode",
    )
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
            try:
                resume = store.find_session(resume)
            except FileNotFoundError:
                console.print(f"[red]Error:[/red] No session found matching '{resume}'")
                sys.exit(1)
            except ValueError as e:
                from ..utils.error_format import format_error_message

                console.print(f"[red]Error:[/red] {format_error_message(e)}")
                sys.exit(1)

            try:
                transcript, metadata = store.load(resume)
                console.print(f"[green]✓[/green] Resuming session: {resume}")
                console.print(f"  Messages: {len(transcript)}")

                # Detect bundle from saved session
                if not bundle:
                    saved_bundle, _legacy = extract_session_mode(metadata)
                    if saved_bundle:
                        bundle = saved_bundle
                        console.print(f"  Using saved bundle: {bundle}")

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
                        console.print(
                            "[red]Error:[/red] Prompt required when resuming in single mode"
                        )
                        sys.exit(1)
                mode = "single"
        else:
            transcript = None
            metadata = None

        config_manager = create_config_manager()

        # Check for active bundle from settings (via 'amplifier bundle use')
        # CLI --bundle flag takes precedence over settings
        if not bundle:
            bundle_settings = config_manager.get_merged_settings().get("bundle", {})
            if isinstance(bundle_settings, dict):
                bundle = bundle_settings.get("active")

        # Default to foundation bundle when no explicit bundle is configured
        if not bundle:
            bundle = "foundation"

        # Check if first run init is needed
        # This runs unconditionally - --provider just selects from configured providers,
        # it doesn't bypass the need for configuration
        if check_first_run():
            if sys.stdin.isatty():
                prompt_first_run_init(console)
            else:
                # Non-interactive context (CI, Docker, shadow env)
                # Auto-init from environment variables
                from .init import auto_init_from_env

                auto_init_from_env(console)

        # Agent loading is now handled via foundation's bundle.load_agent_metadata()
        app_settings = AppSettings()

        # Track configuration source for display (always bundle mode now)
        config_source_name = f"bundle:{bundle}"

        # Resolve configuration using unified function (single source of truth)
        try:
            config_data, prepared_bundle = resolve_config(
                bundle_name=bundle,
                app_settings=app_settings,
                console=console,
            )
        except FileNotFoundError as exc:
            # Bundle not found - display error gracefully without traceback
            console.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)
        except BundleValidationError as exc:
            # Bundle validation failed (e.g., malformed YAML, missing required fields)
            console.print()
            console.print(
                Panel(
                    str(exc),
                    title="[bold white on red] Bundle Validation Error [/bold white on red]",
                    border_style="red",
                    padding=(1, 2),
                )
            )
            sys.exit(1)
        except BundleError as exc:
            # General bundle error (loading, resolution, etc.)
            console.print()
            console.print(
                Panel(
                    str(exc),
                    title="[bold white on red] Bundle Error [/bold white on red]",
                    border_style="red",
                    padding=(1, 2),
                )
            )
            sys.exit(1)

        search_paths = get_module_search_paths()

        # Handle provider/model CLI overrides
        if model and not provider:
            # Require --provider when using --model for clarity
            console.print(
                "[red]Error:[/red] --model requires --provider\n"
                "Specify which provider to use: --provider anthropic --model claude-opus-4-6\n"
                "Run 'amplifier provider use --help' for configuration options"
            )
            sys.exit(1)

        if provider:
            provider_module = (
                provider if provider.startswith("provider-") else f"provider-{provider}"
            )
            providers_list = config_data.get("providers", [])

            # Find the target provider
            target_idx = None
            for i, entry in enumerate(providers_list):
                if isinstance(entry, dict) and entry.get("module") == provider_module:
                    target_idx = i
                    break

            if target_idx is None:
                console.print(
                    f"[red]Error:[/red] Provider '{provider}' not configured\n"
                    f"Available providers: {', '.join(p.get('module', '?').replace('provider-', '') for p in providers_list if isinstance(p, dict))}\n"
                    f"Run 'amplifier provider use --help' for configuration options"
                )
                sys.exit(1)

            # Clone ALL providers (keep multi-provider setup intact)
            updated_providers: list[dict[str, Any]] = []
            for i, entry in enumerate(providers_list):
                entry_copy = {**entry}
                entry_copy["config"] = dict(entry.get("config") or {})

                if i == target_idx:
                    # Promote this provider to priority 0 (highest)
                    entry_copy["config"]["priority"] = 0

                    if model:
                        entry_copy["config"]["default_model"] = model
                    if max_tokens:
                        entry_copy["config"]["max_tokens"] = max_tokens

                updated_providers.append(entry_copy)

            config_data["providers"] = updated_providers

            # CRITICAL: Update the prepared bundle's mount plan with modified providers
            # The bundle was already prepared with original config, we need to update it
            if prepared_bundle and hasattr(prepared_bundle, "mount_plan"):
                prepared_bundle.mount_plan["providers"] = updated_providers

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
        elif max_tokens:
            # Allow --max-tokens without --provider (applies to priority provider)
            providers_list = config_data.get("providers", [])
            if not providers_list:
                console.print(
                    "[yellow]Warning:[/yellow] No providers configured; ignoring --max-tokens"
                )
            else:
                # Find provider with lowest priority number (highest precedence)
                min_priority = float("inf")
                target_idx = 0
                for i, entry in enumerate(providers_list):
                    if isinstance(entry, dict):
                        entry_config = entry.get("config", {})
                        priority = (
                            entry_config.get("priority", 100)
                            if isinstance(entry_config, dict)
                            else 100
                        )
                        if priority < min_priority:
                            min_priority = priority
                            target_idx = i

                updated_providers: list[dict[str, Any]] = []
                for i, entry in enumerate(providers_list):
                    entry_copy = {**entry}
                    if i == target_idx:
                        entry_copy["config"] = dict(entry.get("config") or {})
                        entry_copy["config"]["max_tokens"] = max_tokens
                    updated_providers.append(entry_copy)

                config_data["providers"] = updated_providers

                # CRITICAL: Update the prepared bundle's mount plan with modified providers
                if prepared_bundle and hasattr(prepared_bundle, "mount_plan"):
                    prepared_bundle.mount_plan["providers"] = updated_providers

        # Run update check (uses unified startup_checker with settings.yaml)
        from ..utils.startup_checker import check_and_notify

        asyncio.run(check_and_notify())

        if mode == "chat":
            # Interactive mode - supports optional initial_prompt for auto-execution
            # Check for piped input if no prompt provided
            initial_prompt = prompt
            if initial_prompt is None and not sys.stdin.isatty():
                initial_prompt = sys.stdin.read()
                if initial_prompt is not None and not initial_prompt.strip():
                    initial_prompt = None

            if resume:
                # Resume existing session (transcript loaded earlier)
                if transcript is None:
                    console.print("[red]Error:[/red] Failed to load session transcript")
                    sys.exit(1)
                # Display conversation history before resuming (reuse session.py's display)
                from .session import _display_session_history

                _display_session_history(transcript, metadata or {})
                asyncio.run(
                    interactive_chat(
                        config_data,
                        search_paths,
                        verbose,
                        session_id=resume,
                        bundle_name=config_source_name,
                        prepared_bundle=prepared_bundle,
                        initial_prompt=initial_prompt,
                        initial_transcript=transcript,
                    )
                )
            else:
                # New session - banner displayed by interactive_chat
                session_id = str(uuid.uuid4())
                asyncio.run(
                    interactive_chat(
                        config_data,
                        search_paths,
                        verbose,
                        session_id=session_id,
                        bundle_name=config_source_name,
                        prepared_bundle=prepared_bundle,
                        initial_prompt=initial_prompt,
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
                    execute_single(
                        prompt,
                        config_data,
                        search_paths,
                        verbose,
                        session_id=resume,
                        bundle_name=config_source_name,
                        output_format=output_format,
                        prepared_bundle=prepared_bundle,
                        initial_transcript=transcript,
                    )
                )
            else:
                # Create new session
                session_id = str(uuid.uuid4())
                if output_format == "text":
                    config_summary = get_effective_config_summary(
                        config_data, config_source_name
                    )
                    console.print(f"\n[dim]Session ID: {session_id}[/dim]")
                    console.print(f"[dim]{config_summary.format_banner_line()}[/dim]")
                asyncio.run(
                    execute_single(
                        prompt,
                        config_data,
                        search_paths,
                        verbose,
                        session_id=session_id,
                        bundle_name=config_source_name,
                        output_format=output_format,
                        prepared_bundle=prepared_bundle,
                    )
                )

    return run


__all__ = ["register_run_command"]
