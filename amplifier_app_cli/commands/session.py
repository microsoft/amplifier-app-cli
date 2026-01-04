"""Session management commands."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import click
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from ..console import console
from ..lib.app_settings import AppSettings
from ..paths import create_agent_loader
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..project_utils import get_project_slug
from ..runtime.config import resolve_config
from ..session_store import SessionStore, extract_session_mode
from ..types import (
    ExecuteSingleProtocol,
    InteractiveChatProtocol,
    SearchPathProviderProtocol,
)


def _prepare_resume_context(
    session_id: str,
    profile_override: str | None,
    get_module_search_paths: Callable[[], list[str]],
    console: "Console",
) -> tuple[str, list, dict, dict, list, "PreparedBundle | None", str | None, str | None, str]:
    """Prepare context for resuming a session.

    Handles the common logic for loading and configuring a session resume:
    - Load session transcript and metadata
    - Detect bundle vs profile mode from saved session
    - Resolve configuration
    - Prepare bundle if needed

    Args:
        session_id: The session ID to resume (must be valid/already resolved)
        profile_override: Optional profile to use instead of saved session config
        get_module_search_paths: Callable to get module search paths
        console: Rich console for output (passed to resolve_config)

    Returns:
        Tuple of:
            - session_id: str (confirmed session ID)
            - transcript: list (conversation messages)
            - metadata: dict (session metadata)
            - config_data: dict (resolved config)
            - search_paths: list (module search paths)
            - prepared_bundle: PreparedBundle | None
            - bundle_name: str | None (if bundle mode was detected)
            - saved_profile: str | None (if profile mode was detected and used)
            - active_profile: str (display name like "bundle:foundation" or "dev")
    """
    store = SessionStore()
    transcript, metadata = store.load(session_id)

    # Detect if this was a bundle-based or profile-based session
    bundle_name = None
    effective_profile = profile_override
    saved_profile_used = None  # Only set if actually using saved profile

    if not profile_override:
        saved_bundle, saved_profile = extract_session_mode(metadata)
        if saved_bundle:
            bundle_name = saved_bundle
        elif saved_profile:
            effective_profile = saved_profile
            saved_profile_used = saved_profile

    config_manager = create_config_manager()
    profile_loader = create_profile_loader()
    agent_loader = create_agent_loader()
    app_settings = AppSettings(config_manager)

    # Check first run / auto-install providers BEFORE config resolution
    from .init import check_first_run

    check_first_run()

    # Get project slug for session-scoped settings
    project_slug = get_project_slug()

    # Resolve configuration using unified function (single source of truth)
    config_data, prepared_bundle = resolve_config(
        bundle_name=bundle_name,
        profile_override=effective_profile,
        config_manager=config_manager,
        profile_loader=profile_loader,
        agent_loader=agent_loader,
        app_settings=app_settings,
        console=console,
        session_id=session_id,
        project_slug=project_slug,
    )

    search_paths = get_module_search_paths()

    # Determine active_profile for SessionConfig
    # - If user specified --profile, use that
    # - If resuming a bundle session, construct "bundle:<name>"
    # - If resuming a profile session, use the saved profile
    # - Fallback to "unknown"
    if profile_override:
        active_profile = profile_override
    elif bundle_name:
        active_profile = f"bundle:{bundle_name}"
    elif saved_profile_used:
        active_profile = saved_profile_used
    else:
        active_profile = "unknown"

    return (
        session_id,
        transcript,
        metadata,
        config_data,
        search_paths,
        prepared_bundle,
        bundle_name,
        saved_profile_used,
        active_profile,
    )


def _display_session_history(
    transcript: list[dict],
    metadata: dict,
    *,
    show_thinking: bool = False,
    max_messages: int = 10,
) -> None:
    """Display conversation history for resumed session.

    Uses shared message renderer for consistency with live chat.

    Args:
        transcript: List of message dictionaries from SessionStore
        metadata: Session metadata (session_id, created, profile, etc.)
        show_thinking: Whether to show thinking blocks
        max_messages: Max messages to show (0 = all, default 10)
    """
    from ..ui import render_message

    # Build banner with session info
    session_id = metadata.get("session_id", "unknown")
    created = metadata.get("created", "unknown")
    profile = metadata.get("profile", "unknown")
    model = metadata.get("model", "unknown")

    # Calculate time since creation
    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        elapsed = now - created_dt
        hours = int(elapsed.total_seconds() // 3600)
        minutes = int((elapsed.total_seconds() % 3600) // 60)
        time_ago = f"{hours}h {minutes}m ago" if hours > 0 else f"{minutes}m ago"
    except Exception:
        time_ago = "unknown"

    # Show banner at top with session info
    model_display = model.split("/")[-1] if "/" in model else model
    banner_text = (
        f"[bold cyan]Amplifier Interactive Session (Resumed)[/bold cyan]\n"
        f"Session: {session_id[:8]}... | Started: {time_ago}\n"
        f"Profile: {profile} | Model: {model_display}\n"
        f"Commands: /help | Multi-line: Ctrl-J | Exit: Ctrl-D"
    )

    console.print()
    console.print(Panel.fit(banner_text, border_style="cyan"))
    console.print()

    # Filter to user/assistant messages only
    display_messages = [m for m in transcript if m.get("role") in ("user", "assistant")]

    # Handle message limiting
    skipped_count = 0
    if max_messages > 0 and len(display_messages) > max_messages:
        skipped_count = len(display_messages) - max_messages
        display_messages = display_messages[-max_messages:]
        console.print(f"[dim]... {skipped_count} earlier messages. Use --full-history to see all[/dim]")
        console.print()

    # Render conversation history
    for message in display_messages:
        render_message(message, console, show_thinking=show_thinking)

    console.print()  # Spacing before prompt


async def _replay_session_history(
    transcript: list[dict], metadata: dict, *, speed: float = 2.0, show_thinking: bool = False
) -> None:
    """Replay conversation history with simulated timing.

    Uses shared message renderer for consistency with live chat.

    Args:
        transcript: List of message dictionaries with timestamps
        metadata: Session metadata
        speed: Speed multiplier (2.0 = twice as fast)
        show_thinking: Whether to show thinking blocks
    """
    from ..ui import render_message

    # Build banner with session info and replay status
    session_id = metadata.get("session_id", "unknown")
    created = metadata.get("created", "unknown")
    profile = metadata.get("profile", "unknown")
    model = metadata.get("model", "unknown")

    # Calculate time since creation
    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        elapsed = now - created_dt
        hours = int(elapsed.total_seconds() // 3600)
        minutes = int((elapsed.total_seconds() % 3600) // 60)
        time_ago = f"{hours}h {minutes}m ago" if hours > 0 else f"{minutes}m ago"
    except Exception:
        time_ago = "unknown"

    # Show banner at top with replay info
    model_display = model.split("/")[-1] if "/" in model else model
    banner_text = (
        f"[bold cyan]Amplifier Interactive Session (Replaying at {speed}x)[/bold cyan]\n"
        f"Session: {session_id[:8]}... | Started: {time_ago}\n"
        f"Profile: {profile} | Model: {model_display}\n"
        f"[dim]Ctrl-C to skip replay[/dim] | Commands: /help | Multi-line: Ctrl-J | Exit: Ctrl-D"
    )

    console.print()
    console.print(Panel.fit(banner_text, border_style="cyan"))
    console.print()

    prev_timestamp = None
    interrupted = False
    interrupt_index = 0

    for idx, message in enumerate(transcript):
        try:
            role = message.get("role")

            # Skip system/developer messages
            if role not in ("user", "assistant"):
                continue

            # Calculate delay (uses timestamps if available, else content-based)
            curr_timestamp = message.get("timestamp")
            content = message.get("content", "")
            content_str = content if isinstance(content, str) else str(content)

            delay = _calculate_replay_delay(prev_timestamp, curr_timestamp, speed, content_str)
            await asyncio.sleep(delay)

            # Render using shared renderer
            render_message(message, console, show_thinking=show_thinking)

            prev_timestamp = curr_timestamp

        except KeyboardInterrupt:
            # User interrupted - show remaining messages instantly
            console.print("\n[yellow]⚡ Skipped to end[/yellow]\n")
            interrupted = True
            interrupt_index = idx
            break

    # Show remaining messages if interrupted
    if interrupted:
        for remaining_message in transcript[interrupt_index + 1 :]:
            if remaining_message.get("role") in ("user", "assistant"):
                render_message(remaining_message, console, show_thinking=show_thinking)


def _calculate_replay_delay(
    prev_timestamp: str | None, curr_timestamp: str | None, speed: float, message_content: str = ""
) -> float:
    """Calculate delay between messages for replay.

    Args:
        prev_timestamp: ISO8601 timestamp of previous message (None if not available)
        curr_timestamp: ISO8601 timestamp of current message (None if not available)
        speed: Speed multiplier (2.0 = twice as fast)
        message_content: Message content for length-based timing fallback

    Returns:
        Delay in seconds (adjusted for speed and clamped to reasonable range)
    """
    # If we have timestamps, use them
    if prev_timestamp and curr_timestamp:
        try:
            prev_dt = datetime.fromisoformat(prev_timestamp.replace("Z", "+00:00"))
            curr_dt = datetime.fromisoformat(curr_timestamp.replace("Z", "+00:00"))

            actual_delay = (curr_dt - prev_dt).total_seconds()
            replay_delay = actual_delay / speed

            # Clamp to reasonable range
            min_delay = 0.5  # Don't go faster than 500ms between messages
            max_delay = 10.0  # Don't wait more than 10s even if original was longer

            return max(min_delay, min(replay_delay, max_delay))
        except Exception:
            pass  # Fall through to content-based timing

    # Fallback: Content-length based timing (simulates reading/typing time)
    # Base delay: 1.5 seconds
    # Add 0.5 seconds per 100 characters (scaled by speed)
    base_delay = 1.5
    char_delay = (len(message_content) / 100) * 0.5
    total_delay = (base_delay + char_delay) / speed

    # Clamp to reasonable range
    return max(0.5, min(total_delay, 10.0))


def register_session_commands(
    cli: click.Group,
    *,
    interactive_chat: InteractiveChatProtocol,
    execute_single: ExecuteSingleProtocol,
    get_module_search_paths: SearchPathProviderProtocol,
):
    """Register session commands on the root CLI group."""

    @cli.command(name="continue")
    @click.argument("prompt", required=False)
    @click.option("--profile", "-P", help="Profile to use for resumed session")
    @click.option("--no-history", is_flag=True, help="Skip displaying conversation history")
    @click.option("--full-history", is_flag=True, help="Show all messages (default: last 10)")
    @click.option("--replay", is_flag=True, help="Replay conversation with timing simulation")
    @click.option("--replay-speed", "-s", type=float, default=2.0, help="Replay speed multiplier (default: 2.0)")
    @click.option("--show-thinking", is_flag=True, help="Show thinking blocks in history")
    def continue_session(
        prompt: str | None,
        profile: str | None,
        no_history: bool,
        full_history: bool,
        replay: bool,
        replay_speed: float,
        show_thinking: bool,
    ):
        """Resume the most recent session.

        With no prompt: Resume in interactive mode.
        With prompt: Execute prompt in single-shot mode with session context.
        """
        store = SessionStore()

        # Get most recent session
        session_ids = store.list_sessions()
        if not session_ids:
            console.print("[yellow]No sessions found to resume.[/yellow]")
            console.print("\nStart a new session with: [cyan]amplifier[/cyan]")
            sys.exit(1)

        # Resume most recent
        session_id = session_ids[0]

        try:
            # Use shared helper to prepare resume context
            (
                session_id,
                transcript,
                metadata,
                config_data,
                search_paths,
                prepared_bundle,
                bundle_name,
                saved_profile,
                active_profile,
            ) = _prepare_resume_context(session_id, profile, get_module_search_paths, console)

            # Display resume status
            console.print(f"[green]✓[/green] Resuming most recent session: {session_id}")
            console.print(f"  Messages: {len(transcript)}")
            if bundle_name:
                console.print(f"  Using saved bundle: {bundle_name}")
            elif saved_profile:
                console.print(f"  Using saved profile: {saved_profile}")

            # Display history or replay (when resuming without prompt)
            if prompt is None and not no_history:
                if replay:
                    asyncio.run(
                        _replay_session_history(transcript, metadata, speed=replay_speed, show_thinking=show_thinking)
                    )
                else:
                    _display_session_history(
                        transcript,
                        metadata,
                        show_thinking=show_thinking,
                        max_messages=0 if full_history else 10,
                    )

            # Determine mode based on prompt presence
            if prompt is None and sys.stdin.isatty():
                # No prompt, no pipe → interactive mode
                asyncio.run(
                    interactive_chat(
                        config_data,
                        search_paths,
                        False,
                        session_id=session_id,
                        profile_name=active_profile,
                        prepared_bundle=prepared_bundle,
                        initial_transcript=transcript,
                    )
                )
            else:
                # Has prompt or piped input → single-shot mode with context
                if prompt is None:
                    prompt = sys.stdin.read()
                    if not prompt or not prompt.strip():
                        console.print("[red]Error:[/red] Prompt required when using piped input")
                        sys.exit(1)

                # Execute single prompt with session context
                asyncio.run(
                    execute_single(
                        prompt,
                        config_data,
                        search_paths,
                        False,
                        session_id=session_id,
                        profile_name=active_profile,
                        prepared_bundle=prepared_bundle,
                        initial_transcript=transcript,
                    )
                )

        except Exception as exc:
            console.print(f"[red]Error resuming session:[/red] {exc}")
            sys.exit(1)

    @cli.group(invoke_without_command=True)
    @click.pass_context
    def session(ctx: click.Context):
        """Manage Amplifier sessions."""
        if ctx.invoked_subcommand is None:
            click.echo("\n" + ctx.get_help())
            ctx.exit()

    @session.command(name="list")
    @click.option("--limit", "-n", default=20, help="Number of sessions to show")
    @click.option("--all-projects", is_flag=True, help="Show sessions from all projects")
    @click.option("--project", type=click.Path(), help="Show sessions for specific project path")
    def sessions_list(limit: int, all_projects: bool, project: str | None):
        """List recent sessions for the current project or across all projects."""
        if all_projects:
            projects_dir = Path.home() / ".amplifier" / "projects"
            if not projects_dir.exists():
                console.print("[yellow]No sessions found.[/yellow]")
                return

            all_sessions = []
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                sessions_dir = project_dir / "sessions"
                if not sessions_dir.exists():
                    continue

                store = SessionStore(base_dir=sessions_dir)
                for session_id in store.list_sessions():
                    session_path = sessions_dir / session_id
                    try:
                        mtime = session_path.stat().st_mtime
                        all_sessions.append((project_dir.name, session_id, session_path, mtime))
                    except Exception:
                        continue

            all_sessions.sort(key=lambda x: x[3], reverse=True)
            all_sessions = all_sessions[:limit]

            if not all_sessions:
                console.print("[yellow]No sessions found.[/yellow]")
                return

            table = Table(title="All Sessions (All Projects)", show_header=True, header_style="bold cyan")
            table.add_column("Project", style="magenta")
            table.add_column("Session ID", style="green")
            table.add_column("Last Modified", style="yellow")
            table.add_column("Messages")

            for project_slug, session_id, session_path, mtime in all_sessions:
                modified = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
                transcript_file = session_path / "transcript.jsonl"
                message_count = "?"
                if transcript_file.exists():
                    try:
                        with open(transcript_file, encoding="utf-8") as f:
                            message_count = str(sum(1 for _ in f))
                    except Exception:
                        pass

                display_slug = project_slug if len(project_slug) <= 30 else project_slug[:27] + "..."
                table.add_row(display_slug, session_id, modified, message_count)

            console.print(table)
            return

        if project:
            project_path = Path(project).resolve()
            project_slug = str(project_path).replace("/", "-").replace("\\", "-").replace(":", "")
            if not project_slug.startswith("-"):
                project_slug = "-" + project_slug

            sessions_dir = Path.home() / ".amplifier" / "projects" / project_slug / "sessions"
            if not sessions_dir.exists():
                console.print(f"[yellow]No sessions found for project: {project}[/yellow]")
                return

            store = SessionStore(base_dir=sessions_dir)
            _display_project_sessions(store, limit, f"Sessions for {project}")
            return

        store = SessionStore()
        project_slug = get_project_slug()
        _display_project_sessions(store, limit, f"Sessions for Current Project ({project_slug})")

    @session.command(name="show")
    @click.argument("session_id")
    @click.option("--detailed", "-d", is_flag=True, help="Show detailed transcript metadata")
    def sessions_show(session_id: str, detailed: bool):
        """Show session metadata and (optionally) transcript."""
        store = SessionStore()

        try:
            session_id = store.find_session(session_id)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] No session found matching '{session_id}'")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        try:
            transcript, metadata = store.load(session_id)
        except Exception as exc:
            console.print(f"[red]Error loading session:[/red] {exc}")
            sys.exit(1)

        panel_content = [
            f"[bold]Session ID:[/bold] {session_id}",
            f"[bold]Created:[/bold] {metadata.get('created', 'unknown')}",
            f"[bold]Profile:[/bold] {metadata.get('profile', 'unknown')}",
            f"[bold]Model:[/bold] {metadata.get('model', 'unknown')}",
            f"[bold]Messages:[/bold] {metadata.get('turn_count', len(transcript))}",
        ]
        console.print(Panel("\n".join(panel_content), title="Session Info", border_style="cyan"))

        if detailed:
            console.print("\n[bold]Transcript:[/bold]")
            for item in transcript:
                console.print(json.dumps(item, indent=2))

    @session.command(name="delete")
    @click.argument("session_id")
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation")
    def sessions_delete(session_id: str, force: bool):
        """Delete a stored session."""
        store = SessionStore()

        try:
            session_id = store.find_session(session_id)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] No session found matching '{session_id}'")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        if not force:
            confirm = console.input(f"Delete session '{session_id}'? [y/N]: ")
            if confirm.lower() != "y":
                console.print("[yellow]Cancelled[/yellow]")
                return

        try:
            import shutil

            session_path = store.base_dir / session_id
            shutil.rmtree(session_path)
            console.print(f"[green]✓[/green] Deleted session: {session_id}")
        except Exception as exc:
            console.print(f"[red]Error deleting session:[/red] {exc}")
            sys.exit(1)

    @session.command(name="resume")
    @click.argument("session_id")
    @click.option("--profile", "-P", help="Profile to use for resumed session")
    @click.option("--no-history", is_flag=True, help="Skip displaying conversation history")
    @click.option("--full-history", is_flag=True, help="Show all messages (default: last 10)")
    @click.option("--replay", is_flag=True, help="Replay conversation with timing simulation")
    @click.option("--replay-speed", "-s", type=float, default=2.0, help="Replay speed multiplier (default: 2.0)")
    @click.option("--show-thinking", is_flag=True, help="Show thinking blocks in history")
    def sessions_resume(
        session_id: str,
        profile: str | None,
        no_history: bool,
        full_history: bool,
        replay: bool,
        replay_speed: float,
        show_thinking: bool,
    ):
        """Resume a stored interactive session."""
        store = SessionStore()

        try:
            session_id = store.find_session(session_id)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] No session found matching '{session_id}'")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        try:
            # Use shared helper to prepare resume context
            (
                session_id,
                transcript,
                metadata,
                config_data,
                search_paths,
                prepared_bundle,
                bundle_name,
                saved_profile,
                active_profile,
            ) = _prepare_resume_context(session_id, profile, get_module_search_paths, console)

            # Display resume status
            console.print(f"[green]✓[/green] Resuming session: {session_id}")
            console.print(f"  Messages: {len(transcript)}")
            if bundle_name:
                console.print(f"  Using saved bundle: {bundle_name}")
            elif saved_profile:
                console.print(f"  Using saved profile: {saved_profile}")

            # Display history or replay before entering interactive mode
            if not no_history:
                if replay:
                    asyncio.run(
                        _replay_session_history(transcript, metadata, speed=replay_speed, show_thinking=show_thinking)
                    )
                else:
                    _display_session_history(
                        transcript,
                        metadata,
                        show_thinking=show_thinking,
                        max_messages=0 if full_history else 10,
                    )

            asyncio.run(
                interactive_chat(
                    config_data,
                    search_paths,
                    False,
                    session_id=session_id,
                    profile_name=active_profile,
                    prepared_bundle=prepared_bundle,
                    initial_transcript=transcript,
                )
            )
        except Exception as exc:
            console.print(f"[red]Error resuming session:[/red] {exc}")
            sys.exit(1)

    @session.command(name="cleanup")
    @click.option("--days", "-d", default=30, help="Delete sessions older than N days")
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation")
    def sessions_cleanup(days: int, force: bool):
        """Delete sessions older than N days."""
        store = SessionStore()

        if not force:
            confirm = console.input(f"Delete sessions older than {days} days? [y/N]: ")
            if confirm.lower() != "y":
                console.print("[yellow]Cancelled[/yellow]")
                return

        cutoff = datetime.now(UTC) - timedelta(days=days)
        removed = store.cleanup_old_sessions(days=days)

        console.print(f"[green]✓[/green] Removed {removed} sessions older than {cutoff:%Y-%m-%d}")

    # Register interactive resume on root CLI (not session subgroup)
    @cli.command(name="resume")
    @click.argument("session_id", required=False, default=None)
    @click.option("--limit", "-n", default=10, type=int, help="Number of sessions per page")
    @click.pass_context
    def interactive_resume(ctx: click.Context, session_id: str | None, limit: int):
        """Interactively select and resume a session.

        If SESSION_ID is provided (can be partial), resumes that session directly.
        Otherwise, shows recent sessions with numbered selection.

        Use [n] for next page, [p] for previous page, [q] to quit.
        """
        if session_id:
            # Direct resume with partial ID
            store = SessionStore()
            try:
                full_id = store.find_session(session_id)
            except FileNotFoundError:
                console.print(f"[red]Error:[/red] No session found matching '{session_id}'")
                sys.exit(1)
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
                sys.exit(1)

            # Delegate to sessions_resume
            ctx.invoke(
                sessions_resume,
                session_id=full_id,
                profile=None,
                no_history=False,
                full_history=False,
                replay=False,
                replay_speed=2.0,
                show_thinking=False,
            )
            return

        _interactive_resume_impl(ctx, limit, sessions_resume)


def _format_time_ago(dt: datetime) -> str:
    """Format a datetime as a human-readable time ago string.

    Args:
        dt: A timezone-aware datetime object

    Returns:
        Human-readable string like "2m ago", "3h ago", "1d ago"
    """
    now = datetime.now(UTC)
    elapsed = now - dt

    seconds = int(elapsed.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _get_session_display_info(store: SessionStore, session_id: str) -> dict:
    """Get display information for a session.

    Args:
        store: SessionStore instance
        session_id: Session ID to get info for

    Returns:
        Dict with keys: session_id, profile, turn_count, time_ago, mtime
    """
    session_path = store.base_dir / session_id
    info = {
        "session_id": session_id,
        "profile": "unknown",
        "turn_count": "?",
        "time_ago": "unknown",
        "mtime": 0,
    }

    # Get modification time
    try:
        mtime = session_path.stat().st_mtime
        info["mtime"] = mtime
        dt = datetime.fromtimestamp(mtime, tz=UTC)
        info["time_ago"] = _format_time_ago(dt)
    except Exception:
        pass

    # Get message count from transcript
    transcript_file = session_path / "transcript.jsonl"
    if transcript_file.exists():
        try:
            with open(transcript_file, encoding="utf-8") as f:
                info["turn_count"] = str(sum(1 for _ in f))
        except Exception:
            pass

    # Get profile from metadata
    metadata_file = session_path / "metadata.json"
    if metadata_file.exists():
        try:
            with open(metadata_file, encoding="utf-8") as f:
                metadata = json.load(f)
                info["profile"] = metadata.get("profile", "unknown")
        except Exception:
            pass

    return info


def _interactive_resume_impl(
    ctx: click.Context,
    limit: int,
    sessions_resume_cmd: click.Command,
) -> None:
    """Implementation of interactive resume with paging.

    Args:
        ctx: Click context for invoking commands
        limit: Number of sessions per page
        sessions_resume_cmd: The sessions_resume command to invoke
    """
    store = SessionStore()
    all_session_ids = store.list_sessions()

    # Filter to top-level sessions only (no parent_id)
    # Sub-sessions are created by agent delegation and shouldn't appear in resume list
    session_ids = []
    for sid in all_session_ids:
        try:
            _, metadata = store.load(sid)
            # Sub-sessions have parent_id - exclude them
            if not metadata.get("parent_id"):
                session_ids.append(sid)
        except Exception:
            # Include sessions we can't load metadata for (let user see them)
            session_ids.append(sid)

    # Replace all_session_ids with filtered list
    all_session_ids = session_ids

    if not all_session_ids:
        console.print("[yellow]No sessions found to resume.[/yellow]")
        console.print("\nStart a new session with: [cyan]amplifier[/cyan]")
        return

    # If only one session, auto-select it
    if len(all_session_ids) == 1:
        console.print(f"[dim]Only one session found, resuming...[/dim]")
        ctx.invoke(
            sessions_resume_cmd,
            session_id=all_session_ids[0],
            profile=None,
            no_history=False,
            full_history=False,
            replay=False,
            replay_speed=2.0,
            show_thinking=False,
        )
        return

    # Paging state
    page_offset = 0
    total_sessions = len(all_session_ids)

    while True:
        # Clear and display header
        console.print()
        console.print("[bold cyan]Recent Sessions[/bold cyan]")
        console.print()

        # Get sessions for current page
        page_sessions = all_session_ids[page_offset : page_offset + limit]

        # Display numbered list
        for idx, session_id in enumerate(page_sessions, 1):
            info = _get_session_display_info(store, session_id)

            # Format session ID (first 8 chars + ...)
            short_id = session_id[:8] + "..." if len(session_id) > 8 else session_id

            # Format profile (truncate if too long)
            profile = info["profile"]
            if len(profile) > 20:
                profile = profile[:17] + "..."

            console.print(
                f"  [cyan][{idx}][/cyan] {short_id} | "
                f"[magenta]{profile}[/magenta] | "
                f"{info['turn_count']} turns | "
                f"[dim]{info['time_ago']}[/dim]"
            )

        console.print()

        # Show navigation options
        nav_options = []
        if page_offset + limit < total_sessions:
            nav_options.append("[n] Next page")
        if page_offset > 0:
            nav_options.append("[p] Previous page")
        nav_options.append("[q] Quit")

        console.print(f"  [dim]{' | '.join(nav_options)}[/dim]")
        console.print()

        # Build valid choices
        valid_numbers = [str(i) for i in range(1, len(page_sessions) + 1)]
        valid_nav = []
        if page_offset + limit < total_sessions:
            valid_nav.append("n")
        if page_offset > 0:
            valid_nav.append("p")
        valid_nav.append("q")

        all_choices = valid_numbers + valid_nav

        # Prompt for selection
        try:
            choice = Prompt.ask(
                "Select session",
                choices=all_choices,
                default="1",
                show_choices=False,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled[/yellow]")
            return

        # Handle navigation
        if choice == "n":
            page_offset += limit
            continue
        elif choice == "p":
            page_offset = max(0, page_offset - limit)
            continue
        elif choice == "q":
            console.print("[yellow]Cancelled[/yellow]")
            return

        # Handle number selection
        try:
            selection_idx = int(choice) - 1
            selected_session_id = page_sessions[selection_idx]

            # Invoke the existing sessions_resume command
            ctx.invoke(
                sessions_resume_cmd,
                session_id=selected_session_id,
                profile=None,
                no_history=False,
                full_history=False,
                replay=False,
                replay_speed=2.0,
                show_thinking=False,
            )
            return
        except (ValueError, IndexError):
            console.print("[red]Invalid selection[/red]")
            continue


def _display_project_sessions(store: SessionStore, limit: int, title: str) -> None:
    session_ids = store.list_sessions()[:limit]

    if not session_ids:
        console.print("[yellow]No sessions found.[/yellow]")
        return

    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Session ID", style="green")
    table.add_column("Last Modified", style="yellow")
    table.add_column("Messages")

    for session_id in session_ids:
        session_path = store.base_dir / session_id
        try:
            mtime = session_path.stat().st_mtime
            modified = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            modified = "unknown"

        transcript_file = session_path / "transcript.jsonl"
        message_count = "?"
        if transcript_file.exists():
            try:
                with open(transcript_file) as f:
                    message_count = str(sum(1 for _ in f))
            except Exception:
                pass

        table.add_row(session_id, modified, message_count)

    console.print(table)


__all__ = ["register_session_commands"]
