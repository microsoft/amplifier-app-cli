"""Amplifier CLI - Command-line interface for the Amplifier platform."""

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import click

from amplifier_app_cli.utils.help_formatter import AmplifierGroup

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle
from amplifier_core import AmplifierSession
from prompt_toolkit import PromptSession

from .commands.agents import agents as agents_group
from .commands.allowed_dirs import allowed_dirs as allowed_dirs_group
from .commands.denied_dirs import denied_dirs as denied_dirs_group
from .commands.bundle import bundle as bundle_group
from .commands.completion import can_safely_modify as _can_safely_modify
from .commands.completion import (
    completion_already_installed as _completion_already_installed,
)
from .commands.completion import detect_shell as _detect_shell
from .commands.completion import shell_config_file as _get_shell_config_file
from .commands.completion import (
    install_completion_to_config as _install_completion_to_config,
)
from .commands.completion import show_manual_instructions as _show_manual_instructions
from .commands.init import check_first_run
from .commands.init import init_cmd
from .commands.init import prompt_first_run_init
from .commands.module import module as module_group
from .commands.notify import notify as notify_group
from .commands.provider import provider as provider_group
from .commands.routing import routing_group
from .commands.reset import reset as reset_cmd
from .commands.run import register_run_command
from .commands.session import register_session_commands
from .commands.source import source as source_group
from .session_runner import create_initialized_session
from .runtime.cleanup_events import CLEANUP_FINALLY_BEGIN  # noqa: F401
from .runtime.cleanup_events import CLEANUP_FINALLY_END  # noqa: F401
from .runtime.cleanup_events import CLEANUP_RENDER_BEGIN  # noqa: F401
from .runtime.cleanup_events import CLEANUP_RENDER_END  # noqa: F401
from .runtime.cleanup_events import CLEANUP_STORE_BEGIN  # noqa: F401
from .runtime.cleanup_events import CLEANUP_STORE_END  # noqa: F401
from .commands.tool import tool as tool_group
from .commands.update import update as update_cmd
from .commands.version import version as version_cmd
from .console import Markdown
from .console import console
from .effective_config import get_effective_config_summary
from .key_manager import KeyManager
from .session_store import SessionStore
from .runtime.terminal_encoding import ensure_utf8_output as _ensure_utf8_output
from .ui.command_config_flags import parse_config_flags as _parse_config_flags  # noqa: F401
from .ui.command_processor import CommandProcessor
from .ui.repl import supports_layered_ui
from .ui.interaction_controller import apply_ui_mode_transition
from .ui.interaction_controller import next_shift_tab_state
from .ui.interaction_state import TrustState
from .ui.git_yield import capture_git_diff
from .ui.mode_profiles import ModeProfileRegistry
from .ui.mode_profiles import ModeRuntimeBinding
from .ui.turn_outcomes import is_shell_tool_name as _is_shell_tool_name  # noqa: F401
from .ui.error_display import display_llm_error
from .ui.error_display import display_validation_error
from .ui.log_filter import LLMErrorLogFilter
from .utils.error_format import escape_markup
from .utils.version import get_core_version
from .utils.version import get_version

logger = logging.getLogger(__name__)

# Suppress duplicate LLM error lines from console output.
# The CLI renders LLM errors as Rich panels — the logger.error() calls
# from the provider ("[PROVIDER] Anthropic API error: ...") and session
# ("Execution failed: ...") would duplicate the same info as raw text.
# This filter must be attached to the console HANDLER (not the root logger)
# so it intercepts records propagated from child loggers like provider modules.
# Log file handlers managed by hooks use their own loggers and are unaffected.
_llm_error_filter = LLMErrorLogFilter()


def _attach_llm_error_filter() -> None:
    """Attach the app-owned LLM filter after logging is configured."""
    from .runtime.log_filter_setup import attach_llm_error_filter

    attach_llm_error_filter(_llm_error_filter)


# Load API keys from ~/.amplifier/keys.env on startup
# This allows keys saved by 'amplifier init' or 'amplifier provider use' to be available
KeyManager()


# Placeholder for the run command; assigned after registration below
_run_command: Callable | None = None


def get_module_search_paths() -> list[Path]:
    """
    Determine module search paths for ModuleLoader.

    Returns:
        List of paths to search for modules
    """
    paths = []

    # Check project-local modules first
    project_modules = Path(".amplifier/modules")
    if project_modules.exists():
        paths.append(project_modules)

    # Then user modules
    user_modules = Path.home() / ".amplifier" / "modules"
    if user_modules.exists():
        paths.append(user_modules)

    return paths


@click.group(cls=AmplifierGroup, invoke_without_command=True)
@click.version_option(
    version=f"{get_version()} (core {get_core_version()})",
    prog_name="amplifier",
)
@click.option(
    "--install-completion",
    is_flag=False,
    flag_value="auto",
    default=None,
    help="Install shell completion for the specified shell (bash, zsh, or fish)",
)
@click.pass_context
def cli(ctx, install_completion):
    """Amplifier - AI-powered modular development platform."""
    # Handle --install-completion flag
    if install_completion:
        # Auto-detect shell (always, no argument needed)
        shell = _detect_shell()

        if not shell:
            console.print("[yellow]⚠️ Could not detect shell from $SHELL[/yellow]\n")
            console.print("Supported shells: bash, zsh, fish\n")
            console.print("Add completion manually for your shell:\n")
            console.print(
                '  [cyan]Bash:  eval "$(_AMPLIFIER_COMPLETE=bash_source amplifier)"[/cyan]'
            )
            console.print(
                '  [cyan]Zsh:   eval "$(_AMPLIFIER_COMPLETE=zsh_source amplifier)"[/cyan]'
            )
            console.print(
                "  [cyan]Fish:  _AMPLIFIER_COMPLETE=fish_source amplifier > ~/.config/fish/completions/amplifier.fish[/cyan]"
            )
            ctx.exit(1)

        # At this point, shell is guaranteed to be str (not None)
        assert shell is not None  # Help type checker
        console.print(f"[dim]Detected shell: {shell}[/dim]")

        # Get config file location
        config_file = _get_shell_config_file(shell)

        # Check if already installed (idempotent!)
        if _completion_already_installed(config_file, shell):
            console.print(
                f"[green]✓ Completion already configured in {config_file}[/green]\n"
            )
            console.print("[dim]To use in this terminal:[/dim]")
            if shell == "fish":
                console.print(f"  [cyan]source {config_file}[/cyan]")
            else:
                console.print(f"  [cyan]source {config_file}[/cyan]")
            console.print("\n[dim]Already active in new terminals.[/dim]")
            ctx.exit(0)

        # Check if safe to auto-install
        if _can_safely_modify(config_file):
            # Auto-install!
            success = _install_completion_to_config(config_file, shell)

            if success:
                console.print(f"[green]✓ Added completion to {config_file}[/green]\n")
                console.print("[dim]To activate:[/dim]")
                console.print(f"  [cyan]source {config_file}[/cyan]")
                console.print("\n[dim]Or start a new terminal.[/dim]")
                ctx.exit(0)

        # Fallback to manual instructions
        console.print("[yellow]⚠️ Could not auto-install[/yellow]")
        _show_manual_instructions(shell, config_file)
        ctx.exit(1)

    # If no command specified, launch chat mode
    # Note: Update check happens inside run command (not here, to avoid slowing other commands)
    # For initial prompt support, use: amplifier run --mode chat "prompt"
    if ctx.invoked_subcommand is None:
        if _run_command is None:
            raise RuntimeError("Run command not registered")
        ctx.invoke(
            _run_command,
            prompt=None,
            bundle=None,  # Will check settings for active bundle
            provider=None,
            model=None,
            mode="chat",
            resume=None,
            verbose=False,
        )


async def _process_runtime_mentions(session: AmplifierSession, prompt: str) -> str:
    """Process @mentions in user input at runtime.

    Returns the prompt with <context_file> XML blocks prepended for any resolved
    @mentions, or the original prompt unchanged if no mentions resolve.

    Args:
        session: Active session for capability lookup
        prompt: User's input that may contain @mentions

    Returns:
        Expanded prompt string (original unchanged when no mentions resolve).
    """
    from pathlib import Path

    from amplifier_foundation.mentions import expand_mentions_in_instruction

    mention_resolver = session.coordinator.get_capability("mention_resolver")
    if mention_resolver is None:
        return prompt

    logger.info("Processing @mentions in user input")
    deduplicator = session.coordinator.get_capability("mention_deduplicator")
    return await expand_mentions_in_instruction(
        prompt,
        resolver=mention_resolver,
        deduplicator=deduplicator,
        relative_to=Path.cwd(),
    )


process_runtime_mentions = _process_runtime_mentions


def _create_prompt_session(
    get_active_mode: Callable | None = None,
    *,
    commands: dict[str, dict[str, Any]] | None = None,
    get_is_running: Callable | None = None,
    get_queued_count: Callable | None = None,
    on_interrupt: Callable[[], bool] | None = None,
    mode_shortcuts: dict[str, Any] | None = None,
    skill_shortcuts: dict[str, Any] | None = None,
    mcp_prompts: tuple[tuple[str, str, str], ...] = (),
    mode_names: list[str] | None = None,
    skill_names: list[str] | None = None,
    model_names: Callable[[], tuple[str, ...]] | None = None,
    bundle_name: str = "unknown",
    session_id: str | None = None,
) -> PromptSession:
    """Compatibility wrapper for project-scoped prompt session construction."""
    from .runtime.prompt_session import create_interactive_prompt_session

    return create_interactive_prompt_session(
        get_active_mode,
        commands=commands,
        get_is_running=get_is_running,
        get_queued_count=get_queued_count,
        on_interrupt=on_interrupt,
        mode_shortcuts=mode_shortcuts,
        skill_shortcuts=skill_shortcuts,
        mcp_prompts=mcp_prompts,
        mode_names=mode_names,
        skill_names=skill_names,
        model_names=model_names,
        bundle_name=bundle_name,
        session_id=session_id,
    )


async def _apply_ui_mode_transition(
    session_state: dict[str, Any],
    previous_mode: str | None,
    mode_profiles: ModeProfileRegistry,
    mode_binding: ModeRuntimeBinding,
    active_mode_state: dict[str, str | None],
    trust_state: TrustState | None = None,
) -> str:
    """Compatibility wrapper for the typed interaction controller."""
    return await apply_ui_mode_transition(
        session_state,
        previous_mode,
        mode_profiles,
        mode_binding,
        active_mode_state,
        trust_state,
    )


def _next_shift_tab_state(
    active_mode: str | None,
    mode_profiles: ModeProfileRegistry,
) -> tuple[str, str]:
    """Compatibility wrapper for the typed interaction controller."""
    return next_shift_tab_state(active_mode, mode_profiles)


async def interactive_chat(
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    bundle_name: str = "unknown",
    prepared_bundle: "PreparedBundle | None" = None,
    initial_prompt: str | None = None,
    initial_transcript: list[dict] | None = None,
    initial_display_transcript: list[dict] | None = None,
    initial_show_thinking: bool = False,
) -> None:
    """Run interactive sessions, switching resume targets in-process."""
    from .runtime.interactive_resume_loop import InteractiveLoopDependencies
    from .runtime.interactive_resume_loop import InteractiveLoopRequest
    from .runtime.interactive_resume_loop import run_interactive_loop

    await run_interactive_loop(
        InteractiveLoopRequest(
            config=config,
            search_paths=search_paths,
            verbose=verbose,
            session_id=session_id,
            bundle_name=bundle_name,
            prepared_bundle=prepared_bundle,
            initial_prompt=initial_prompt,
            initial_transcript=initial_transcript,
            initial_display_transcript=initial_display_transcript,
            initial_show_thinking=initial_show_thinking,
        ),
        InteractiveLoopDependencies(
            console=console,
            escape_markup=escape_markup,
            run_session=_interactive_chat_session,
        ),
    )


async def _interactive_chat_session(
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    bundle_name: str = "unknown",
    prepared_bundle: "PreparedBundle | None" = None,
    initial_prompt: str | None = None,
    initial_transcript: list[dict] | None = None,
    initial_display_transcript: list[dict] | None = None,
    initial_show_thinking: bool = False,
) -> str | None:
    """Compatibility entrypoint for the focused interactive session host."""
    from .runtime.interactive_host import InteractiveHostDependencies
    from .runtime.interactive_host import InteractiveHostRequest
    from .runtime.interactive_host import run_interactive_host

    request = InteractiveHostRequest(
        config=config,
        search_paths=search_paths,
        verbose=verbose,
        session_id=session_id,
        bundle_name=bundle_name,
        prepared_bundle=prepared_bundle,
        initial_prompt=initial_prompt,
        initial_transcript=initial_transcript,
        initial_display_transcript=initial_display_transcript,
        initial_show_thinking=initial_show_thinking,
    )
    dependencies = InteractiveHostDependencies(
        console=console,
        input_stream=sys.stdin,
        create_initialized_session=create_initialized_session,
        session_store_factory=SessionStore,
        command_processor_factory=CommandProcessor,
        supports_layered_ui=supports_layered_ui,
        effective_config_summary=get_effective_config_summary,
        get_version=get_version,
        get_core_version=get_core_version,
        create_prompt_session=_create_prompt_session,
        process_runtime_mentions=_process_runtime_mentions,
        capture_diff=capture_git_diff,
        display_validation_error=display_validation_error,
        escape_markup=escape_markup,
    )
    return await run_interactive_host(request, dependencies)


async def execute_single(
    prompt: str,
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    bundle_name: str = "unknown",
    output_format: str = "text",
    prepared_bundle: "PreparedBundle | None" = None,
    initial_transcript: list[dict] | None = None,
) -> None:
    """Execute one prompt through the focused single-shot runtime."""
    from .runtime.single_execution import SingleExecutionDependencies
    from .runtime.single_execution import SingleExecutionRequest
    from .runtime.single_execution import run_single_execution
    from .trace_collector import TraceCollector

    request = SingleExecutionRequest(
        prompt=prompt,
        config=config,
        search_paths=search_paths,
        verbose=verbose,
        session_id=session_id,
        bundle_name=bundle_name,
        output_format=output_format,
        prepared_bundle=prepared_bundle,
        initial_transcript=initial_transcript,
    )
    dependencies = SingleExecutionDependencies(
        console=console,
        create_initialized_session=create_initialized_session,
        process_runtime_mentions=_process_runtime_mentions,
        session_store_factory=SessionStore,
        markdown_factory=Markdown,
        display_validation_error=display_validation_error,
        display_llm_error=display_llm_error,
        escape_markup=escape_markup,
        trace_collector_factory=TraceCollector,
    )
    await run_single_execution(request, dependencies)


# Register standalone commands
cli.add_command(agents_group)
cli.add_command(allowed_dirs_group)
cli.add_command(denied_dirs_group)
cli.add_command(bundle_group)
cli.add_command(init_cmd)
cli.add_command(module_group)
cli.add_command(notify_group)
cli.add_command(provider_group)
cli.add_command(routing_group)
cli.add_command(source_group)
cli.add_command(tool_group)
cli.add_command(update_cmd)
cli.add_command(version_cmd)
cli.add_command(reset_cmd)


# Note: The *_with_session variants were removed in favor of unified functions
# that accept optional initial_transcript parameter for resume functionality.
# See execute_single() and interactive_chat() above.

_run_command = register_run_command(
    cli,
    interactive_chat=interactive_chat,
    execute_single=execute_single,
    get_module_search_paths=get_module_search_paths,
    check_first_run=check_first_run,
    prompt_first_run_init=prompt_first_run_init,
)

register_session_commands(
    cli,
    interactive_chat=interactive_chat,
    execute_single=execute_single,
    get_module_search_paths=get_module_search_paths,
)


def main():
    """Main entry point."""
    _ensure_utf8_output()
    _attach_llm_error_filter()
    cli()


if __name__ == "__main__":
    main()
