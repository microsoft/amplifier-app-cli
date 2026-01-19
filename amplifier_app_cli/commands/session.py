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
from ..lib.settings import AppSettings
from ..project_utils import get_project_slug
from ..runtime.config import resolve_config
from ..session_store import SessionStore, extract_session_mode
from ..types import (
    ExecuteSingleProtocol,
    InteractiveChatProtocol,
    SearchPathProviderProtocol,
)

# Import session fork utilities from foundation
try:
    from amplifier_foundation.session import (
        fork_session,
        get_fork_preview,
        get_session_lineage,
        get_turn_summary,
        count_turns,
        ForkResult,
    )

    HAS_SESSION_FORK = True
except ImportError:
    HAS_SESSION_FORK = False


def _record_bundle_override(
    metadata: dict, new_bundle: str, original_config: str
) -> None:
    """Record a bundle override in session metadata for diagnostics.

    Tracks when users force a different bundle on resume, enabling session analyst
    to understand potential instability from mixed bundle usage.

    Args:
        metadata: Session metadata dict (modified in place)
        new_bundle: The bundle being forced
        original_config: The original bundle the session was created with
    """
    # Initialize bundle_overrides list if not present
    if "bundle_overrides" not in metadata:
        metadata["bundle_overrides"] = []

    # Record this override with timestamp
    metadata["bundle_overrides"].append(
        {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "original": original_config,
            "forced": new_bundle,
        }
    )


def _prepare_resume_context(
    session_id: str,
    get_module_search_paths: Callable[[], list[str]],
    console: "Console",
    *,
    bundle_override: str | None = None,
) -> tuple[str, list, dict, dict, list, "PreparedBundle | None", str | None, str]:
    """Prepare context for resuming a session.

    Handles the common logic for loading and configuring a session resume:
    - Load session transcript and metadata
    - Extract bundle from saved session
    - Resolve configuration
    - Prepare bundle if needed

    Args:
        session_id: The session ID to resume (must be valid/already resolved)
        get_module_search_paths: Callable to get module search paths
        console: Rich console for output (passed to resolve_config)
        bundle_override: Optional bundle to force (overrides saved session bundle).

    Returns:
        Tuple of:
            - session_id: str (confirmed session ID)
            - transcript: list (conversation messages)
            - metadata: dict (session metadata)
            - config_data: dict (resolved config)
            - search_paths: list (module search paths)
            - prepared_bundle: PreparedBundle | None
            - bundle_name: str | None (if bundle was detected)
            - active_bundle: str (display name like "bundle:foundation")
    """
    store = SessionStore()
    transcript, metadata = store.load(session_id)

    # Extract bundle from saved session metadata
    saved_bundle, _ = extract_session_mode(metadata)

    bundle_name = None

    # Force bundle override takes precedence
    if bundle_override:
        bundle_name = bundle_override
        original_config = saved_bundle or "unknown"
        # Only warn if forcing a different bundle than original
        if bundle_override != saved_bundle:
            console.print(
                f"[yellow]⚠ Forcing bundle override:[/yellow] {bundle_override}\n"
                f"[dim]  (session was created with: {original_config})[/dim]"
            )
        _record_bundle_override(metadata, bundle_override, original_config)
    elif saved_bundle:
        bundle_name = saved_bundle
    # If no saved bundle, bundle_name stays None and resolve_config will use default

    app_settings = AppSettings()

    # Check first run / auto-install providers BEFORE config resolution
    from .init import check_first_run

    check_first_run()

    # Get project slug for session-scoped settings
    project_slug = get_project_slug()

    # Resolve configuration using unified function (single source of truth)
    config_data, prepared_bundle = resolve_config(
        bundle_name=bundle_name,
        agent_loader=None,
        app_settings=app_settings,
        console=console,
        session_id=session_id,
        project_slug=project_slug,
    )

    search_paths = get_module_search_paths()

    # Determine active_bundle for display
    active_bundle = f"bundle:{bundle_name}" if bundle_name else "unknown"

    return (
        session_id,
        transcript,
        metadata,
        config_data,
        search_paths,
        prepared_bundle,
        bundle_name,
        active_bundle,
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
        metadata: Session metadata (session_id, created, bundle, etc.)
        show_thinking: Whether to show thinking blocks
        max_messages: Max messages to show (0 = all, default 10)
    """
    from ..ui import render_message

    # Build banner with session info
    session_id = metadata.get("session_id", "unknown")
    created = metadata.get("created", "unknown")
    bundle = metadata.get("bundle", "unknown")
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
        f"Bundle: {bundle} | Model: {model_display}\n"
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
        console.print(
            f"[dim]... {skipped_count} earlier messages. Use --full-history to see all[/dim]"
        )
        console.print()

    # Render conversation history
    for message in display_messages:
        render_message(message, console, show_thinking=show_thinking)

    console.print()  # Spacing before prompt


async def _replay_session_history(
    transcript: list[dict],
    metadata: dict,
    *,
    speed: float = 2.0,
    show_thinking: bool = False,
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
    bundle = metadata.get("bundle", "unknown")
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
        f"Bundle: {bundle} | Model: {model_display}\n"
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

            delay = _calculate_replay_delay(
                prev_timestamp, curr_timestamp, speed, content_str
            )
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
    prev_timestamp: str | None,
    curr_timestamp: str | None,
    speed: float,
    message_content: str = "",
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
    @click.option(
        "--force-bundle",
        "-B",
        help="[Experimental] Force a different bundle for this session. "
        "May cause instability if the bundle differs significantly from the original.",
    )
    @click.option(
        "--no-history", is_flag=True, help="Skip displaying conversation history"
    )
    @click.option(
        "--full-history", is_flag=True, help="Show all messages (default: last 10)"
    )
    @click.option(
        "--replay", is_flag=True, help="Replay conversation with timing simulation"
    )
    @click.option(
        "--replay-speed",
        "-s",
        type=float,
        default=2.0,
        help="Replay speed multiplier (default: 2.0)",
    )
    @click.option(
        "--show-thinking", is_flag=True, help="Show thinking blocks in history"
    )
    def continue_session(
        prompt: str | None,
        force_bundle: str | None,
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
                active_bundle,
            ) = _prepare_resume_context(
                session_id,
                get_module_search_paths,
                console,
                bundle_override=force_bundle,
            )

            # Display resume status
            console.print(
                f"[green]✓[/green] Resuming most recent session: {session_id}"
            )
            console.print(f"  Messages: {len(transcript)}")
            if bundle_name and not force_bundle:
                console.print(f"  Using saved bundle: {bundle_name}")

            # Display history or replay (when resuming without prompt)
            if prompt is None and not no_history:
                if replay:
                    asyncio.run(
                        _replay_session_history(
                            transcript,
                            metadata,
                            speed=replay_speed,
                            show_thinking=show_thinking,
                        )
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
                        bundle_name=active_bundle,
                        prepared_bundle=prepared_bundle,
                        initial_transcript=transcript,
                    )
                )
            else:
                # Has prompt or piped input → single-shot mode with context
                if prompt is None:
                    prompt = sys.stdin.read()
                    if not prompt or not prompt.strip():
                        console.print(
                            "[red]Error:[/red] Prompt required when using piped input"
                        )
                        sys.exit(1)

                # Execute single prompt with session context
                asyncio.run(
                    execute_single(
                        prompt,
                        config_data,
                        search_paths,
                        False,
                        session_id=session_id,
                        bundle_name=active_bundle,
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
    @click.option(
        "--all-projects", is_flag=True, help="Show sessions from all projects"
    )
    @click.option(
        "--project", type=click.Path(), help="Show sessions for specific project path"
    )
    @click.option(
        "--tree", "-t", "tree_session", help="Show lineage tree for a session"
    )
    def sessions_list(
        limit: int, all_projects: bool, project: str | None, tree_session: str | None
    ):
        """List recent sessions for the current project or across all projects.

        Use --tree SESSION_ID to show the lineage tree for a specific session.
        """
        # Handle --tree option first
        if tree_session:
            if not HAS_SESSION_FORK:
                console.print("[red]Error:[/red] Session fork utilities not available.")
                console.print("Install amplifier-foundation with session support.")
                sys.exit(1)

            store = SessionStore()
            try:
                session_id = store.find_session(tree_session)
            except FileNotFoundError:
                console.print(
                    f"[red]Error:[/red] No session found matching '{tree_session}'"
                )
                sys.exit(1)
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
                sys.exit(1)

            session_dir = store.base_dir / session_id
            lineage = get_session_lineage(session_dir, store.base_dir)

            console.print()
            console.print("[bold cyan]Session Lineage Tree[/bold cyan]")
            console.print()

            # Show ancestors first (root to parent)
            ancestors = lineage.get("ancestors", [])
            for i, ancestor_id in enumerate(reversed(ancestors)):
                indent = "  " * i
                console.print(f"{indent}[dim]{ancestor_id[:8]}...[/dim]")
                console.print(f"{indent}│")

            # Show current session
            current_indent = "  " * len(ancestors)
            session_info = _get_session_display_info(store, session_id)
            forked_info = ""
            if lineage.get("forked_from_turn"):
                forked_info = (
                    f" [dim](forked at turn {lineage['forked_from_turn']})[/dim]"
                )
            console.print(
                f"{current_indent}[bold green]{session_id[:8]}...[/bold green]{forked_info}"
            )

            # Show children (forks)
            children = lineage.get("children", [])
            if children:
                for i, child in enumerate(children):
                    is_last = i == len(children) - 1
                    prefix = "└─" if is_last else "├─"
                    child_id = child.get("session_id", "unknown")
                    fork_turn = child.get("forked_from_turn", "?")
                    console.print(
                        f"{current_indent}{prefix} [cyan]{child_id[:8]}...[/cyan] "
                        f"[dim](forked at turn {fork_turn})[/dim]"
                    )
            else:
                console.print(f"{current_indent}[dim](no forks)[/dim]")

            console.print()
            console.print(
                f"[dim]Depth: {lineage.get('depth', 0)} | "
                f"Children: {len(children)}[/dim]"
            )
            return
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
                        all_sessions.append(
                            (project_dir.name, session_id, session_path, mtime)
                        )
                    except Exception:
                        continue

            all_sessions.sort(key=lambda x: x[3], reverse=True)
            all_sessions = all_sessions[:limit]

            if not all_sessions:
                console.print("[yellow]No sessions found.[/yellow]")
                return

            table = Table(
                title="All Sessions (All Projects)",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Name", style="cyan", max_width=30)
            table.add_column("Project", style="magenta", max_width=20)
            table.add_column("Session ID", style="green")
            table.add_column("Modified", style="yellow")
            table.add_column("Msgs", justify="right")

            for project_slug, session_id, session_path, mtime in all_sessions:
                modified = datetime.fromtimestamp(mtime, tz=UTC).strftime(
                    "%Y-%m-%d %H:%M"
                )
                transcript_file = session_path / "transcript.jsonl"
                message_count = "?"
                if transcript_file.exists():
                    try:
                        with open(transcript_file, encoding="utf-8") as f:
                            message_count = str(sum(1 for _ in f))
                    except Exception:
                        pass

                # Get session name from metadata
                session_name = ""
                try:
                    metadata_file = session_path / "metadata.json"
                    if metadata_file.exists():
                        import json

                        metadata = json.loads(metadata_file.read_text())
                        session_name = metadata.get("name", "")
                        if len(session_name) > 30:
                            session_name = session_name[:27] + "..."
                except Exception:
                    pass

                display_slug = (
                    project_slug
                    if len(project_slug) <= 20
                    else project_slug[:17] + "..."
                )
                short_id = session_id[:8] + "..."
                table.add_row(
                    session_name or "[dim]unnamed[/dim]",
                    display_slug,
                    short_id,
                    modified,
                    message_count,
                )

            console.print(table)
            return

        if project:
            project_path = Path(project).resolve()
            project_slug = (
                str(project_path).replace("/", "-").replace("\\", "-").replace(":", "")
            )
            if not project_slug.startswith("-"):
                project_slug = "-" + project_slug

            sessions_dir = (
                Path.home() / ".amplifier" / "projects" / project_slug / "sessions"
            )
            if not sessions_dir.exists():
                console.print(
                    f"[yellow]No sessions found for project: {project}[/yellow]"
                )
                return

            store = SessionStore(base_dir=sessions_dir)
            _display_project_sessions(store, limit, f"Sessions for {project}")
            return

        store = SessionStore()
        project_slug = get_project_slug()
        _display_project_sessions(
            store, limit, f"Sessions for Current Project ({project_slug})"
        )

    @session.command(name="show")
    @click.argument("session_id")
    @click.option(
        "--detailed", "-d", is_flag=True, help="Show detailed transcript metadata"
    )
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
            f"[bold]Bundle:[/bold] {metadata.get('bundle', 'unknown')}",
            f"[bold]Model:[/bold] {metadata.get('model', 'unknown')}",
            f"[bold]Messages:[/bold] {metadata.get('turn_count', len(transcript))}",
        ]
        console.print(
            Panel("\n".join(panel_content), title="Session Info", border_style="cyan")
        )

        if detailed:
            console.print("\n[bold]Transcript:[/bold]")
            for item in transcript:
                console.print(json.dumps(item, indent=2))

    @session.command(name="fork")
    @click.argument("session_id")
    @click.option(
        "--at-turn",
        "-t",
        "turn",
        type=int,
        help="Turn number to fork at (default: latest)",
    )
    @click.option("--name", "-n", "new_name", help="Custom name/ID for forked session")
    @click.option(
        "--resume",
        "-r",
        "resume_after",
        is_flag=True,
        help="Resume forked session immediately",
    )
    @click.option("--no-events", is_flag=True, help="Skip copying events.jsonl")
    def sessions_fork(
        session_id: str,
        turn: int | None,
        new_name: str | None,
        resume_after: bool,
        no_events: bool,
    ):
        """Fork a session from a specific turn.

        Creates a new session with conversation history up to the specified turn.
        The forked session is independently resumable and tracks lineage to parent.

        Examples:

            amplifier session fork abc123 --at-turn 3

            amplifier session fork abc123 --at-turn 3 --name "jwt-approach"

            amplifier session fork abc123 --at-turn 3 --resume
        """
        if not HAS_SESSION_FORK:
            console.print("[red]Error:[/red] Session fork utilities not available.")
            console.print("Install amplifier-foundation with session support.")
            sys.exit(1)

        store = SessionStore()

        # Find the session
        try:
            session_id = store.find_session(session_id)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] No session found matching '{session_id}'")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        session_dir = store.base_dir / session_id

        # If no turn specified, show interactive selection or use latest
        if turn is None:
            # Load transcript to count turns
            transcript_path = session_dir / "transcript.jsonl"
            if not transcript_path.exists():
                console.print(f"[red]Error:[/red] No transcript found for session")
                sys.exit(1)

            messages = []
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            max_turns = count_turns(messages)
            if max_turns == 0:
                console.print(
                    "[red]Error:[/red] Session has no user messages to fork from"
                )
                sys.exit(1)

            # Show turn previews for selection (most recent first)
            console.print()
            console.print(
                f"[bold cyan]Session {session_id[:8]}... ({max_turns} turns)[/bold cyan]"
            )
            console.print()
            console.print("[dim]Most recent first:[/dim]")
            console.print()

            turns_to_show = min(max_turns, 10)
            for t in range(max_turns, max(0, max_turns - turns_to_show), -1):
                try:
                    summary = get_turn_summary(messages, t)
                    user_preview = summary["user_content"][:55]
                    if len(summary["user_content"]) > 55:
                        user_preview += "..."
                    tool_info = (
                        f" [{summary['tool_count']} tools]"
                        if summary["tool_count"]
                        else ""
                    )
                    marker = " [green]← current[/green]" if t == max_turns else ""
                    console.print(
                        f"  [cyan][{t}][/cyan] {user_preview}{tool_info}{marker}"
                    )
                except Exception:
                    console.print(
                        f"  [cyan][{t}][/cyan] [dim](unable to preview)[/dim]"
                    )

            if max_turns > 10:
                console.print(f"  [dim]... {max_turns - 10} earlier turns[/dim]")

            console.print()

            # Prompt for turn selection
            try:
                turn_input = Prompt.ask(
                    "Fork at turn",
                    default=str(max_turns),
                )
                turn = int(turn_input)
            except (ValueError, KeyboardInterrupt):
                console.print("[yellow]Cancelled[/yellow]")
                return

        # Show preview before forking
        try:
            preview = get_fork_preview(session_dir, turn)
            console.print()
            console.print(f"[bold]Fork Preview:[/bold]")
            console.print(f"  Parent: {preview['parent_id'][:8]}...")
            console.print(f"  Fork at turn: {turn} of {preview['max_turns']}")
            console.print(f"  Messages to copy: {preview['message_count']}")
            if preview["has_orphaned_tools"]:
                console.print(
                    f"  [yellow]Note: {preview['orphaned_tool_count']} tool call(s) will be completed with synthetic results[/yellow]"
                )
            console.print()
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        # Perform the fork
        try:
            result = fork_session(
                session_dir,
                turn=turn,
                new_session_id=new_name,
                include_events=not no_events,
            )

            console.print(
                f"[green]✓[/green] Forked session created: {result.session_id}"
            )
            console.print(f"  Messages: {result.message_count}")
            console.print(f"  Parent: {result.parent_id[:8]}...")
            console.print(f"  Forked at turn: {result.forked_from_turn}")
            if result.events_count > 0:
                console.print(f"  Events copied: {result.events_count}")
            console.print()
            console.print(
                f"Resume with: [cyan]amplifier session resume {result.session_id[:8]}[/cyan]"
            )

            # Resume if requested
            if resume_after:
                console.print()
                console.print("[dim]Resuming forked session...[/dim]")
                # Invoke the resume command
                ctx = click.get_current_context()
                ctx.invoke(
                    sessions_resume,
                    session_id=result.session_id,
                    force_bundle=None,
                    no_history=False,
                    full_history=False,
                    replay=False,
                    replay_speed=2.0,
                    show_thinking=False,
                )

        except Exception as e:
            console.print(f"[red]Error forking session:[/red] {e}")
            sys.exit(1)

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
    @click.option(
        "--force-bundle",
        "-B",
        help="[Experimental] Force a different bundle for this session. "
        "May cause instability if the bundle differs significantly from the original.",
    )
    @click.option(
        "--no-history", is_flag=True, help="Skip displaying conversation history"
    )
    @click.option(
        "--full-history", is_flag=True, help="Show all messages (default: last 10)"
    )
    @click.option(
        "--replay", is_flag=True, help="Replay conversation with timing simulation"
    )
    @click.option(
        "--replay-speed",
        "-s",
        type=float,
        default=2.0,
        help="Replay speed multiplier (default: 2.0)",
    )
    @click.option(
        "--show-thinking", is_flag=True, help="Show thinking blocks in history"
    )
    def sessions_resume(
        session_id: str,
        force_bundle: str | None,
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
                active_bundle,
            ) = _prepare_resume_context(
                session_id,
                get_module_search_paths,
                console,
                bundle_override=force_bundle,
            )

            # Display resume status
            console.print(f"[green]✓[/green] Resuming session: {session_id}")
            console.print(f"  Messages: {len(transcript)}")
            if bundle_name and not force_bundle:
                console.print(f"  Using saved bundle: {bundle_name}")

            # Display history or replay before entering interactive mode
            if not no_history:
                if replay:
                    asyncio.run(
                        _replay_session_history(
                            transcript,
                            metadata,
                            speed=replay_speed,
                            show_thinking=show_thinking,
                        )
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
                    bundle_name=active_bundle,
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

        console.print(
            f"[green]✓[/green] Removed {removed} sessions older than {cutoff:%Y-%m-%d}"
        )

    # Register interactive resume on root CLI (not session subgroup)
    @cli.command(name="resume")
    @click.argument("session_id", required=False, default=None)
    @click.option(
        "--limit", "-n", default=10, type=int, help="Number of sessions per page"
    )
    @click.option(
        "--force-bundle",
        "-B",
        help="[Experimental] Force a different bundle for this session. "
        "May cause instability if the bundle differs significantly from the original.",
    )
    @click.pass_context
    def interactive_resume(
        ctx: click.Context, session_id: str | None, limit: int, force_bundle: str | None
    ):
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
                console.print(
                    f"[red]Error:[/red] No session found matching '{session_id}'"
                )
                sys.exit(1)
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
                sys.exit(1)

            # Delegate to sessions_resume
            ctx.invoke(
                sessions_resume,
                session_id=full_id,
                force_bundle=force_bundle,
                no_history=False,
                full_history=False,
                replay=False,
                replay_speed=2.0,
                show_thinking=False,
            )
            return

        _interactive_resume_impl(ctx, limit, sessions_resume, force_bundle=force_bundle)


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
        Dict with keys: session_id, name, bundle, turn_count, time_ago, mtime
    """
    session_path = store.base_dir / session_id
    info = {
        "session_id": session_id,
        "name": "",
        "bundle": "unknown",
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

    # Get bundle and name from metadata
    metadata_file = session_path / "metadata.json"
    if metadata_file.exists():
        try:
            with open(metadata_file, encoding="utf-8") as f:
                metadata = json.load(f)
                info["bundle"] = metadata.get("bundle", "unknown")
                info["name"] = metadata.get("name", "")
        except Exception:
            pass

    return info


def _interactive_resume_impl(
    ctx: click.Context,
    limit: int,
    sessions_resume_cmd: click.Command,
    *,
    force_bundle: str | None = None,
) -> None:
    """Implementation of interactive resume with paging.

    Args:
        ctx: Click context for invoking commands
        limit: Number of sessions per page
        sessions_resume_cmd: The sessions_resume command to invoke
        force_bundle: Optional bundle to force for the resumed session
    """
    store = SessionStore()
    # list_sessions() defaults to top_level_only=True, filtering out spawned sub-sessions
    all_session_ids = store.list_sessions()

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
            force_bundle=force_bundle,
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

        # Display numbered list (continuous numbering across pages)
        first_num = page_offset + 1
        last_num = page_offset + len(page_sessions)
        for idx, session_id in enumerate(page_sessions, first_num):
            info = _get_session_display_info(store, session_id)

            # Format session ID (first 8 chars + ...)
            short_id = session_id[:8] + "..." if len(session_id) > 8 else session_id

            # Format session name (truncate if too long)
            name = info.get("name", "")
            if name:
                if len(name) > 30:
                    name = name[:27] + "..."
                name_display = f"[bold]{name}[/bold] "
            else:
                name_display = ""

            # Format bundle (truncate if too long)
            bundle = info["bundle"]
            if len(bundle) > 15:
                bundle = bundle[:12] + "..."

            console.print(
                f"  [cyan][{idx}][/cyan] {name_display}{short_id} | "
                f"[magenta]{bundle}[/magenta] | "
                f"{info['turn_count']} turns | "
                f"[dim]{info['time_ago']}[/dim]",
                highlight=False,
            )

        console.print()

        # Show navigation options (typed commands, not TUI hotkeys)
        has_next = page_offset + limit < total_sessions
        has_prev = page_offset > 0
        nav_hints = []
        if has_next:
            remaining = total_sessions - (page_offset + limit)
            nav_hints.append(f"'n' next ({remaining})")
        if has_prev:
            nav_hints.append(f"'p' prev ({page_offset})")
        nav_hints.append("'q' quit")

        console.print(f"  [dim]{' | '.join(nav_hints)}[/dim]")
        console.print()

        # Prompt for selection (accept any input, validate manually)
        try:
            choice = Prompt.ask("Select session", default=str(first_num))
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled[/yellow]")
            return

        choice = choice.strip().lower()

        # Handle navigation commands (accept variants)
        if choice in ("n", "next"):
            if has_next:
                page_offset += limit
                continue
            else:
                console.print("[yellow]No more sessions[/yellow]")
                continue
        elif choice in ("p", "prev", "previous"):
            if has_prev:
                page_offset -= limit
                continue
            else:
                console.print("[yellow]Already at first page[/yellow]")
                continue
        elif choice in ("q", "quit", "exit"):
            console.print("[yellow]Cancelled[/yellow]")
            return

        # Handle number selection (continuous numbering)
        try:
            selection_num = int(choice)
            if first_num <= selection_num <= last_num:
                # Convert global number to index into page_sessions
                page_idx = selection_num - first_num
                selected_session_id = page_sessions[page_idx]

                # Invoke the existing sessions_resume command
                ctx.invoke(
                    sessions_resume_cmd,
                    session_id=selected_session_id,
                    force_bundle=force_bundle,
                    no_history=False,
                    full_history=False,
                    replay=False,
                    replay_speed=2.0,
                    show_thinking=False,
                )
                return
            else:
                console.print(f"[yellow]Please enter {first_num}-{last_num}[/yellow]")
                continue
        except ValueError:
            console.print(
                "[yellow]Invalid input. Enter a number, 'n' for next, 'p' for prev, or 'q' to quit.[/yellow]"
            )
            continue


def _display_project_sessions(store: SessionStore, limit: int, title: str) -> None:
    session_ids = store.list_sessions()[:limit]

    if not session_ids:
        console.print("[yellow]No sessions found.[/yellow]")
        return

    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Name", style="cyan", max_width=35)
    table.add_column("Session ID", style="green")
    table.add_column("Last Modified", style="yellow")
    table.add_column("Msgs", justify="right")

    for session_id in session_ids:
        session_path = store.base_dir / session_id
        try:
            mtime = session_path.stat().st_mtime
            modified = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M")
        except Exception:
            modified = "unknown"

        # Get session name from metadata
        session_name = ""
        try:
            metadata = store.get_metadata(session_id)
            session_name = metadata.get("name", "")
            if len(session_name) > 35:
                session_name = session_name[:32] + "..."
        except Exception:
            pass

        transcript_file = session_path / "transcript.jsonl"
        message_count = "?"
        if transcript_file.exists():
            try:
                with open(transcript_file) as f:
                    message_count = str(sum(1 for _ in f))
            except Exception:
                pass

        # Show short session ID
        short_id = session_id[:8] + "..."
        table.add_row(
            session_name or "[dim]unnamed[/dim]", short_id, modified, message_count
        )

    console.print(table)


__all__ = ["register_session_commands"]
