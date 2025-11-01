"""Session management commands."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from collections.abc import Coroutine
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import click
from rich.panel import Panel
from rich.table import Table

from ..console import console
from ..lib.app_settings import AppSettings
from ..paths import create_agent_loader
from ..paths import create_config_manager
from ..paths import create_profile_loader
from ..project_utils import get_project_slug
from ..runtime.config import resolve_app_config
from ..session_store import SessionStore

InteractiveResume = Callable[[dict, list[Path], bool, str, list[dict], str], Coroutine[Any, Any, None]]
SearchPathProvider = Callable[[], list[Path]]


def register_session_commands(
    cli: click.Group,
    *,
    interactive_chat_with_session: InteractiveResume,
    get_module_search_paths: SearchPathProvider,
):
    """Register session commands on the root CLI group."""

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
                        with open(transcript_file) as f:
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

        if not store.exists(session_id):
            console.print(f"[red]Error:[/red] Session '{session_id}' not found")
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

        if not store.exists(session_id):
            console.print(f"[red]Error:[/red] Session '{session_id}' not found")
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
    def sessions_resume(session_id: str, profile: str | None):
        """Resume a stored interactive session."""
        store = SessionStore()

        if not store.exists(session_id):
            console.print(f"[red]Error:[/red] Session '{session_id}' not found")
            sys.exit(1)

        try:
            transcript, metadata = store.load(session_id)

            console.print(f"[green]✓[/green] Resuming session: {session_id}")
            console.print(f"  Messages: {len(transcript)}")

            saved_profile = metadata.get("profile", "unknown")
            if not profile and saved_profile and saved_profile != "unknown":
                profile = saved_profile
                console.print(f"  Using saved profile: {profile}")

            config_manager = create_config_manager()
            profile_loader = create_profile_loader()
            agent_loader = create_agent_loader()
            app_settings = AppSettings(config_manager)

            config_data = resolve_app_config(
                config_manager=config_manager,
                profile_loader=profile_loader,
                agent_loader=agent_loader,
                app_settings=app_settings,
                profile_override=profile,
                console=console,
            )

            search_paths = get_module_search_paths()
            active_profile = profile if profile else saved_profile

            asyncio.run(
                interactive_chat_with_session(config_data, search_paths, False, session_id, transcript, active_profile)
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
