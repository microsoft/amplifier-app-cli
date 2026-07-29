"""Non-recursive interactive session switching."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle


@dataclass(frozen=True, slots=True)
class InteractiveLoopRequest:
    config: dict[str, Any]
    search_paths: list[Path]
    verbose: bool
    session_id: str | None = None
    bundle_name: str = "unknown"
    prepared_bundle: PreparedBundle | None = None
    initial_prompt: str | None = None
    initial_transcript: list[dict[str, Any]] | None = None
    initial_display_transcript: list[dict[str, Any]] | None = None
    initial_show_thinking: bool = False


@dataclass(frozen=True, slots=True)
class InteractiveLoopDependencies:
    console: Console
    escape_markup: Callable[[object], str]
    run_session: Callable[..., Awaitable[str | None]]


async def run_interactive_loop(
    request: InteractiveLoopRequest,
    dependencies: InteractiveLoopDependencies,
) -> None:
    """Run sessions until exit, switching resume targets in-process."""
    config = request.config
    search_paths = request.search_paths
    session_id = request.session_id
    bundle_name = request.bundle_name
    prepared_bundle = request.prepared_bundle
    prompt = request.initial_prompt
    transcript = request.initial_transcript
    display_transcript = (
        transcript
        if request.initial_display_transcript is None
        else request.initial_display_transcript
    )
    show_thinking = request.initial_show_thinking

    while True:
        requested_session = await dependencies.run_session(
            config=config,
            search_paths=search_paths,
            verbose=request.verbose,
            session_id=session_id,
            bundle_name=bundle_name,
            prepared_bundle=prepared_bundle,
            initial_prompt=prompt,
            initial_transcript=transcript,
            initial_display_transcript=display_transcript,
            initial_show_thinking=show_thinking,
        )
        if not requested_session:
            return

        from amplifier_app_cli.commands.session import display_session_history
        from amplifier_app_cli.commands.session import prepare_resume_context
        from amplifier_app_cli.commands.session import select_history_messages

        try:
            (
                session_id,
                transcript,
                metadata,
                config,
                search_paths,
                prepared_bundle,
                _saved_bundle,
                bundle_name,
            ) = prepare_resume_context(
                requested_session,
                lambda: search_paths,
                dependencies.console,
            )
        except Exception as error:
            dependencies.console.print(
                "[red]Unable to resume session:[/red] "
                f"{dependencies.escape_markup(error)}"
            )
            return

        dependencies.console.print(
            f"\n[dim]Switching to session {requested_session[:12]}[/dim]"
        )
        display_session_history(transcript, metadata, max_messages=10)
        display_transcript = select_history_messages(transcript, max_messages=10)
        show_thinking = False
        prompt = None


__all__ = [
    "InteractiveLoopDependencies",
    "InteractiveLoopRequest",
    "run_interactive_loop",
]
