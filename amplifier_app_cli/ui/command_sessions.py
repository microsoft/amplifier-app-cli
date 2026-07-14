"""Transcript, status, session, and help commands for the interactive CLI."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from amplifier_foundation import sanitize_message

from amplifier_app_cli.ui.interaction_runtime_state import interaction_state_for

from .command_registry import CommandRegistry, CommandSource


class CommandSessionMixin:
    """Implement session administration commands for CommandProcessor."""

    session: Any
    bundle_name: str
    command_registry: CommandRegistry

    if TYPE_CHECKING:

        def _refresh_command_registry(self) -> CommandRegistry: ...

    async def _save_transcript(self, filename: str) -> str:
        """Save current transcript with sanitization for non-JSON-serializable objects.

        Saves to the session directory: ~/.amplifier/projects/<project-slug>/sessions/<session-id>/
        """
        # Default filename if not provided
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"transcript_{timestamp}.json"

        # Get messages from context
        context = self.session.coordinator.get("context")
        if context and hasattr(context, "get_messages"):
            messages = await context.get_messages()

            # Sanitize messages to handle ThinkingBlock and other non-serializable objects
            from ..session_store import SessionStore

            store = SessionStore()
            sanitized_messages = [sanitize_message(msg) for msg in messages]

            # Save to session directory (proper location)
            session_id = self.session.coordinator.session_id
            session_dir = store.base_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            path = session_dir / filename

            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "messages": sanitized_messages,
                        "config": self.session.config,
                    },
                    f,
                    indent=2,
                )

            return str(path)

        return "No transcript available"

    async def _get_status(self) -> str:
        """Get session status information."""
        lines = ["**Session status**", ""]
        session_id = self.session.coordinator.session_id
        lines.append(f"- Session ID: `{session_id}`")

        # Show session name if available
        try:
            from ..session_store import SessionStore

            store = SessionStore()
            if store.exists(session_id):
                metadata = store.get_metadata(session_id)
                if metadata.get("name"):
                    lines.append(f"- Name: {metadata['name']}")
                if metadata.get("description"):
                    # Truncate long descriptions
                    desc = metadata["description"]
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    lines.append(f"- Description: {desc}")
        except Exception:
            pass  # Silently skip if we can't load metadata

        lines.append(f"- Config: `{self.bundle_name}`")

        # Active mode status
        active_mode = interaction_state_for(self.session.coordinator).bundle_mode
        lines.append(f"- Mode: `{active_mode or 'none'}`")

        # Context size
        context = self.session.coordinator.get("context")
        if context and hasattr(context, "get_messages"):
            messages = await context.get_messages()
            lines.append(f"- Messages: {len(messages)}")

        # Active providers
        providers = self.session.coordinator.get("providers")
        if providers:
            provider_names = list(providers.keys())
            lines.append(f"- Providers: {', '.join(provider_names)}")

        # Available tools
        tools = self.session.coordinator.get("tools")
        if tools:
            lines.append(f"- Tools: {len(tools)}")

        return "\n".join(lines)

    async def _clear_context(self):
        """Clear the conversation context."""
        context = self.session.coordinator.get("context")
        if context and hasattr(context, "clear"):
            await context.clear()

    async def _rename_session(self, new_name: str) -> str:
        """Rename the current session."""
        new_name = new_name.strip()
        if not new_name:
            return "Usage: `/rename <new name>`"

        session_id = self.session.coordinator.session_id

        try:
            from datetime import datetime, UTC
            from ..session_store import SessionStore

            store = SessionStore()
            if not store.exists(session_id):
                return f"Session {session_id[:8]}... not found in storage"

            # Update the name in metadata
            store.update_metadata(
                session_id,
                {
                    "name": new_name[:50],  # Limit name length
                    "name_generated_at": datetime.now(UTC).isoformat(),
                },
            )

            return f"✓ Session renamed to: {new_name[:50]}"

        except Exception as e:
            return f"Failed to rename session: {e}"

    async def _fork_session(self, args: str) -> str:
        """Fork the current session at a specific turn.

        Usage:
            /fork          - Show conversation turns
            /fork 3        - Fork at turn 3
            /fork 3 myname - Fork at turn 3 with custom name
        """
        from ..session_store import SessionStore

        # Check if session fork utilities are available
        try:
            from amplifier_foundation.session import (
                fork_session,
                count_turns,
                get_turn_summary,
            )
        except ImportError:
            return "Error: Session fork utilities not available. Install amplifier-foundation with session support."

        store = SessionStore()
        session_id = self.session.coordinator.session_id
        session_dir = store.base_dir / session_id

        if not session_dir.exists():
            return f"Error: Session directory not found: {session_dir}"

        # Get current messages to count turns
        context = self.session.coordinator.get("context")
        if not context or not hasattr(context, "get_messages"):
            return "Error: No context available"

        messages = await context.get_messages()
        max_turns = count_turns(messages)

        if max_turns == 0:
            return "Error: No turns to fork from (no user messages)"

        # Parse arguments
        parts = args.strip().split()
        turn = None
        custom_name = None

        if len(parts) >= 1 and parts[0]:
            try:
                turn = int(parts[0])
            except ValueError:
                # Maybe it's a name without turn? Show help
                return "Usage: `/fork <turn> [name]`\n\nRun `/fork` first to see your conversation turns."

        if len(parts) >= 2:
            custom_name = parts[1]

        # If no turn specified, show turn previews (most recent first)
        if turn is None:
            lines = ["", "Your conversation turns (most recent first):", ""]

            # Show turns in reverse order (most recent first)
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
                    marker = " ← you are here" if t == max_turns else ""
                    lines.append(f"  [{t}] {user_preview}{tool_info}{marker}")
                except Exception:
                    lines.append(f"  [{t}] (unable to preview)")

            if max_turns > 10:
                lines.append(f"  ... {max_turns - 10} earlier turns")

            lines.append("")
            lines.append("To fork, run: `/fork <turn>`")
            lines.append("Example: /fork 3        - fork at turn 3")
            lines.append("         /fork 3 my-fix - fork at turn 3 with name 'my-fix'")
            return "\n".join(lines)

        # Validate turn
        if turn < 1 or turn > max_turns:
            return f"Error: Turn {turn} out of range (1-{max_turns})"

        # Perform the fork
        try:
            result = fork_session(
                session_dir,
                turn=turn,
                new_session_id=custom_name,
                include_events=True,
            )

            lines = [
                f"✓ Forked session created: {result.session_id}",
                f"  Messages: {result.message_count}",
                f"  Forked at turn: {result.forked_from_turn} of {max_turns}",
            ]
            if result.events_count > 0:
                lines.append(f"  Events copied: {result.events_count}")
            lines.append("")
            lines.append(
                f"Resume with: amplifier session resume {result.session_id[:8]}"
            )

            return "\n".join(lines)

        except Exception as e:
            return f"Error forking session: {e}"

    def _format_help(self) -> str:
        """Format help text with commands and dynamic modes section."""
        self._refresh_command_registry()
        lines = ["Available Commands:"]
        for spec in self.command_registry.specs:
            if spec.source is not CommandSource.BUILTIN or not spec.advertised:
                continue
            for name in spec.names:
                lines.append(f"  {name:<12} - {spec.description}")

        modes = tuple(
            spec
            for spec in self.command_registry.specs
            if spec.source is CommandSource.MODE and spec.advertised
        )
        if modes:
            lines.extend(("", "Mode Shortcuts:"))
            for spec in modes:
                lines.append(f"  {spec.name:<12} - {spec.description}")

        skills = tuple(
            spec
            for spec in self.command_registry.specs
            if spec.source
            in {CommandSource.SKILL, CommandSource.BUNDLE, CommandSource.USER}
            and spec.advertised
        )
        if skills:
            lines.append("")
            lines.append("Skill Commands:")
            for spec in sorted(skills, key=lambda item: item.name):
                lines.append(f"  {spec.name:<12} - {spec.description}")

        mcp_commands = tuple(
            spec
            for spec in self.command_registry.specs
            if spec.source is CommandSource.MCP and spec.advertised
        )
        if mcp_commands:
            lines.extend(("", "MCP Prompt Commands:"))
            for spec in sorted(mcp_commands, key=lambda item: item.name):
                lines.append(f"  {spec.name:<12} - {spec.description}")

        return "\n".join(lines)

    @property
    def _display_bundle_name(self) -> str:
        """Return the bundle name with any 'bundle:' prefix removed."""
        return self.bundle_name.removeprefix("bundle:")


__all__ = ["CommandSessionMixin"]
