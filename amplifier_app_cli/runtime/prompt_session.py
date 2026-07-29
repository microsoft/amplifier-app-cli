"""Prompt-toolkit session construction for the legacy interactive surface."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession

from amplifier_app_cli.project_utils import get_project_slug
from amplifier_app_cli.ui.command_processor import CommandProcessor
from amplifier_app_cli.ui.repl import create_prompt_session


def create_interactive_prompt_session(
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
    """Create the project-scoped editable prompt session."""
    history_path = (
        Path.home() / ".amplifier" / "projects" / get_project_slug() / "repl_history"
    )
    return create_prompt_session(
        history_path=history_path,
        commands=commands or CommandProcessor.COMMANDS,
        get_active_mode=get_active_mode,
        get_is_running=get_is_running,
        get_queued_count=get_queued_count,
        on_interrupt=on_interrupt,
        mode_shortcuts=(
            mode_shortcuts
            if mode_shortcuts is not None
            else {name: name for name in CommandProcessor.BUILTIN_MODE_NAMES}
        ),
        skill_shortcuts=skill_shortcuts if skill_shortcuts is not None else {},
        mcp_prompts=mcp_prompts,
        mode_names=mode_names,
        skill_names=skill_names,
        model_names=model_names,
        bundle_name=bundle_name,
        session_id=session_id,
    )


__all__ = ["create_interactive_prompt_session"]
