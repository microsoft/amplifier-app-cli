"""Primary run command for the Amplifier CLI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import click
from amplifier_foundation.exceptions import BundleError, BundleValidationError
from amplifier_foundation.modules import ModuleActivationError
from rich.panel import Panel

from ..console import console
from ..effective_config import get_effective_config_summary
from ..lib.settings import AppSettings
from ..paths import create_config_manager
from ..runtime.config import resolve_config
from ..session_store import extract_session_mode
from ..types import (
    ExecuteSingleProtocol,
    InteractiveChatProtocol,
    SearchPathProviderProtocol,
)
from ..utils.error_format import escape_markup

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _scoped_sigint_handler(handler: Any) -> Iterator[bool]:
    """Install a SIGINT handler for the duration of the block, where possible.

    ``signal.signal()`` is only callable from the main thread of the main
    interpreter; anywhere else CPython raises
    ``ValueError: signal only works in main thread of the main interpreter``.

    Before GAP-023 and GAP-027, the two interruptible phases in this module
    (the startup update check and bundle preparation) installed no handler at
    all, so both were safe to call from any thread. Adding an unguarded
    ``signal.signal()`` call narrowed that contract: it makes those phases
    raise for any caller that does not own the main thread.

    No caller that actually reaches these phases off the main thread has been
    identified. Two candidate embedders were checked and neither reaches them:
    ``amplifierd`` does not depend on this package at all, and
    ``amplifier-app-actions`` imports only ``console`` and ``session_runner``,
    not this module. So this guard is preventive, not a fix for an observed
    field failure -- it restores the "safe to call from any thread" contract
    that GAP-023/GAP-027 silently removed, at no cost.

    Declining to install leaves the caller without the Ctrl+C acknowledgment
    it never had before those fixes, and changes nothing else. That is a
    deliberate restoration of shipped behavior for a context where signal
    handling is structurally unavailable, not an error being swallowed -- so
    it is reported at debug level and the block runs either way.

    Yields True if the handler was installed, False if it was declined.
    """
    if threading.current_thread() is not threading.main_thread():
        logger.debug(
            "SIGINT handler not installed: not on the main thread (%s). "
            "Interrupt acknowledgment is unavailable in this context.",
            threading.current_thread().name,
        )
        yield False
        return

    try:
        original = signal.signal(signal.SIGINT, handler)
    except ValueError as exc:
        # Reachable on the main thread of a *subinterpreter*, where
        # threading.main_thread() reports that subinterpreter's own main
        # thread but signal.signal() still refuses.
        logger.debug("SIGINT handler not installed: %s", exc)
        yield False
        return

    try:
        yield True
    finally:
        signal.signal(signal.SIGINT, original)


def _run_startup_update_check() -> None:
    """Run the startup update check, but let Ctrl+C skip it immediately and
    fall through to the user's actual command instead of the whole
    invocation dying (GAP-023).

    Before this fix, the pre-REPL "Checking for updates..." phase (this
    function's caller, ``asyncio.run(check_and_notify())``) ran with no
    SIGINT handling of its own -- ``_execute_with_interrupt``'s handler
    (main.py, installed inside the per-turn wrapper) and the headless goal
    path's ``_goal_sigint_handler`` (main.py) both install a handler
    *around the operation they guard*; this phase runs earlier than either,
    so it had no equivalent. A Ctrl+C here fell through to Click's default
    top-level (EOFError, KeyboardInterrupt) handler, which prints
    "Aborted!" and kills the ENTIRE ``amplifier run`` invocation -- not
    just the optional, best-effort update check. Measured on native
    Windows: the interrupt was not merely delayed, it destroyed the user's
    whole command over an update check they never asked to wait on.

    Fix: run the check on its own event loop with its own SIGINT handler
    (same pattern as the two handlers above), so a Ctrl+C here cancels only
    this task -- printing a clear message -- and the caller proceeds to run
    the user's actual prompt/command normally.
    """
    from ..utils.startup_checker import check_and_notify

    interrupted = False

    def _update_check_sigint_handler(signum, frame):
        nonlocal interrupted
        interrupted = True
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop = asyncio.new_event_loop()
    with _scoped_sigint_handler(_update_check_sigint_handler):
        try:
            asyncio.set_event_loop(loop)
            task = loop.create_task(check_and_notify())
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    if interrupted:
        console.print("[dim]Update check skipped (Ctrl+C) -- continuing...[/dim]")


def _resolve_config_interruptibly(
    *,
    bundle_name: str | None,
    app_settings: AppSettings,
    console: Any,
) -> tuple[dict[str, Any], Any]:
    """Wrap resolve_config() with a scoped SIGINT handler and a clean
    cancellation message (GAP-027).

    resolve_config() -- bundle discovery, git clone/fetch, compose,
    activate -- runs EARLIER than every other SIGINT-aware phase in this
    file: earlier than _run_startup_update_check() (GAP-023's own scoped
    handler, above) and earlier than main.py's per-turn
    _execute_with_interrupt() handler. Before this fix it had none of its
    own, so a Ctrl+C here fell all the way through to Python's raw
    default SIGINT handler with:

      1. No acknowledgment at all -- every OTHER interruptible phase in
         the app prints something ("Update check skipped...", "Stopping
         after current operation completes...", etc.) the instant Ctrl+C
         is pressed. This phase printed nothing, for however long the
         unwind took.

      2. A non-deterministic landing spot. resolve_config() calls through
         many layers of third-party library code (git subprocess calls,
         pydantic schema validation, importlib.metadata lookups, etc.)
         with no exception handling of its own around the interrupt.
         Confirmed on native Windows: the IDENTICAL repro (a bundle
         source pointed at a black-holed IP so the git clone blocks
         deterministically, Ctrl+C sent ~6s after spawn), run twice back
         to back, produced two different failure modes from the same
         keystroke: once the process ran on completely unaffected for
         60+ seconds (the interrupt seemingly lost), and once it died in
         ~2s but with a BARE, unhandled Python traceback dumped straight
         to the user's terminal -- ending in a lone "KeyboardInterrupt"
         with zero context, landing inside pydantic's
         complete_model_class -> create_schema_validator ->
         importlib.metadata.entry_points() chain, nothing to do with git
         at all. Neither outcome is acceptable, and which one a user gets
         is pure timing luck.

    This is a genuinely different location and cause from GAP-014
    (git.py's unbounded wait), GAP-023 (the update-check phase, which
    runs AFTER this one), GAP-025 (git.py's BaseException cleanup), and
    GAP-026 (subprocess_runner.py's delegation cancellation) -- none of
    those touch this call site, and none of them install any
    acknowledgment or containment for an interrupt landing here.

    Fix: same established pattern as _run_startup_update_check() -- a
    scoped SIGINT handler installed only for the duration of this call,
    printing the same "Cancelling..." convention used everywhere else in
    the app the moment Ctrl+C is pressed (fixing the missing-feedback
    symptom), while still delivering the real interrupt via
    signal.default_int_handler so the existing unwind-and-cleanup
    machinery (GAP-014/025's process-tree kill, etc.) runs exactly as it
    did before this fix. The KeyboardInterrupt is then caught at THIS
    single, deliberate point -- regardless of which arbitrary library
    frame it actually surfaces in -- and converted into one clean,
    actionable message and a normal process exit, instead of a raw
    traceback landing wherever the timing happened to put it.
    """
    interrupted = False

    def _bundle_prep_sigint_handler(signum: int, frame: Any) -> None:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            console.print(
                "\n[yellow]Cancelling bundle preparation... "
                "(this may take a moment to unwind cleanly)[/yellow]"
            )
        # Still deliver the real interrupt so every existing unwind path
        # (git.py's process-tree kill, etc.) behaves exactly as before --
        # this handler only ADDS an acknowledgment and a clean landing
        # spot, it does not change what happens once the exception is
        # actually raised.
        signal.default_int_handler(signum, frame)

    with _scoped_sigint_handler(_bundle_prep_sigint_handler):
        try:
            return resolve_config(
                bundle_name=bundle_name,
                app_settings=app_settings,
                console=console,
            )
        except KeyboardInterrupt:
            console.print("[red]Bundle preparation cancelled.[/red]")
            sys.exit(130)


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
    @click.option(
        "--matrix",
        default=None,
        help="Routing matrix to use for this session (e.g. anthropic, balanced)",
    )
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
        matrix: str | None,
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

                console.print(
                    f"[red]Error:[/red] {escape_markup(format_error_message(e))}"
                )
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
                console.print(f"[red]Error loading session:[/red] {escape_markup(exc)}")
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

        # Default to anchors bundle when no explicit bundle is configured
        if not bundle:
            bundle = "anchors"

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

        # Resolve configuration using unified function (single source of truth).
        # GAP-027: wrapped for scoped SIGINT handling + a clean cancellation
        # message instead of a raw traceback landing wherever an interrupt
        # happens to surface (see _resolve_config_interruptibly docstring).
        try:
            config_data, prepared_bundle = _resolve_config_interruptibly(
                bundle_name=bundle,
                app_settings=app_settings,
                console=console,
            )
        except FileNotFoundError as exc:
            # Bundle not found - display error gracefully without traceback
            console.print(f"[red]Error:[/red] {escape_markup(exc)}")
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
        except ModuleActivationError as exc:
            # One or more modules failed to download/activate. App-layer policy
            # is to abort rather than start a session that is quietly missing
            # capabilities. Render the failure and how to proceed.
            console.print()
            console.print(
                Panel(
                    f"{exc}\n\n"
                    "The session was not started because it would have been missing\n"
                    "the capabilities above.\n\n"
                    "To start anyway with the modules that did load, re-run with:\n"
                    "  AMPLIFIER_ALLOW_PARTIAL_BUNDLE=1",
                    title="[bold white on red] Module Activation Failed [/bold white on red]",
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

        # Per-session routing matrix override (--matrix).
        # Bridge the flag into the mounted hooks-routing config's default_matrix,
        # mirroring how --bundle selects a per-session bundle. This intentionally
        # does not persist anything to settings; it only affects this session.
        if matrix and prepared_bundle and hasattr(prepared_bundle, "mount_plan"):
            routing_entry = None
            for hook in prepared_bundle.mount_plan.get("hooks") or []:
                if isinstance(hook, dict) and hook.get("module") == "hooks-routing":
                    routing_entry = hook
                    break
            if routing_entry is None:
                console.print(
                    "[yellow]Warning:[/yellow] --matrix ignored: this bundle does "
                    "not mount the 'hooks-routing' module"
                )
            else:
                routing_config = routing_entry.get("config")
                if not isinstance(routing_config, dict):
                    routing_config = {}
                    routing_entry["config"] = routing_config
                routing_config["default_matrix"] = matrix

        # Handle provider/model CLI overrides
        if model and not provider:
            # Require --provider when using --model for clarity
            console.print(
                "[red]Error:[/red] --model requires --provider\n"
                "Specify which provider to use: --provider anthropic --model claude-opus-4-6\n"
                "Run 'amplifier provider list' to see your configured providers"
            )
            sys.exit(1)

        if provider:
            provider_module = (
                provider if provider.startswith("provider-") else f"provider-{provider}"
            )
            providers_list = config_data.get("providers", [])

            # Find the target provider — two-pass search:
            # Pass 1: exact match on instance id/mount name.
            # _map_id_to_instance_id copies id → instance_id without stripping id,
            # so both fields co-exist on resolved entries; either leg can match.
            target_idx = None
            for i, entry in enumerate(providers_list):
                if isinstance(entry, dict) and (
                    entry.get("id") == provider or entry.get("instance_id") == provider
                ):
                    target_idx = i
                    break

            if target_idx is None:
                # Pass 2: fallback — module-type match (original behavior).
                # Preserves single-instance usage: -p anthropic → provider-anthropic.
                for i, entry in enumerate(providers_list):
                    if (
                        isinstance(entry, dict)
                        and entry.get("module") == provider_module
                    ):
                        target_idx = i
                        break

            if target_idx is None:
                console.print(
                    f"[red]Error:[/red] Provider '{provider}' not configured\n"
                    f"Available providers: {', '.join(p.get('id') or p.get('instance_id') or p.get('module', '?').replace('provider-', '') for p in providers_list if isinstance(p, dict))}\n"
                    "Add one with 'amplifier provider add', or see all options with 'amplifier provider --help'"
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
        _run_startup_update_check()

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
