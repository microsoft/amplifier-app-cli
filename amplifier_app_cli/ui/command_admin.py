"""Tool, agent, scope, and skill commands for the interactive CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amplifier_app_cli.console import console


class CommandAdminMixin:
    """Implement runtime inventory and policy commands for CommandProcessor."""

    session: Any

    async def _list_tools(self) -> str:
        """List available tools."""
        tools = self.session.coordinator.get("tools")
        if not tools:
            return "No tools available"

        lines = ["Available Tools:"]
        for name, tool in tools.items():
            desc = getattr(tool, "description", "No description")
            # Handle multi-line descriptions - take first line only
            first_line = desc.split("\n")[0]
            # Truncate if too long
            if len(first_line) > 60:
                first_line = first_line[:57] + "..."
            lines.append(f"  {name:<20} - {first_line}")

        return "\n".join(lines)

    async def _list_agents(self) -> str:
        """List available agents from current configuration.

        Agents are loaded into session.config["agents"] via mount plan (compiler).
        """
        # Get pre-loaded agents from session config
        # Note: agents can be a dict (resolved agents) or list/other format
        all_agents = self.session.config.get("agents", {})

        if not isinstance(all_agents, dict):
            return "No agents available (agents not loaded as dict)"

        # Filter out config keys - only show resolved agent entries
        agent_items = {
            k: v
            for k, v in all_agents.items()
            if k not in ("dirs", "include", "inline") and isinstance(v, dict)
        }

        if not agent_items:
            return "No agents available (check bundle's agents configuration)"

        # Display each agent with full frontmatter (excluding instruction)
        console.print(f"\n[bold]Available Agents[/bold] ({len(agent_items)} loaded)\n")

        for name, config in sorted(agent_items.items()):
            # Agent name as header
            console.print(f"[bold cyan]{name}[/bold cyan]")

            # Full description
            description = config.get("description", "No description")
            console.print(f"  [dim]Description:[/dim] {description}")

            # Providers
            providers = config.get("providers", [])
            if providers:
                provider_names = [p.get("module", "unknown") for p in providers]
                console.print(f"  [dim]Providers:[/dim] {', '.join(provider_names)}")

            # Tools
            tools = config.get("tools", [])
            if tools:
                tool_names = [t.get("module", "unknown") for t in tools]
                console.print(f"  [dim]Tools:[/dim] {', '.join(tool_names)}")

            # Hooks
            hooks = config.get("hooks", [])
            if hooks:
                hook_names = [h.get("module", "unknown") for h in hooks]
                console.print(f"  [dim]Hooks:[/dim] {', '.join(hook_names)}")

            # Session overrides
            session = config.get("session", {})
            if session:
                session_items = [f"{k}={v}" for k, v in session.items()]
                console.print(f"  [dim]Session:[/dim] {', '.join(session_items)}")

            console.print()  # Blank line between agents

        return ""  # Output already printed

    async def _manage_allowed_dirs(self, args: str) -> str:
        """Manage allowed write directories (session-scoped).

        Usage:
            /allowed-dirs list
            /allowed-dirs add <path>
            /allowed-dirs remove <path>
        """
        from ..lib.settings import AppSettings
        from ..project_utils import get_project_slug

        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower() if parts else "list"
        path_arg = parts[1] if len(parts) > 1 else ""

        # Get session-scoped settings
        session_id = self.session.coordinator.session_id
        project_slug = get_project_slug()
        settings = AppSettings().with_session(session_id, project_slug)

        if subcommand == "list":
            paths = settings.get_allowed_write_paths()
            if not paths:
                lines = ["No allowed directories configured."]
            else:
                lines = ["Allowed Write Directories:"]
                for p, scope in paths:
                    lines.append(f"  {p} ({scope})")

            # Add help text
            lines.append("")
            lines.append("Usage:")
            lines.append("  /allowed-dirs list            - List allowed directories")
            lines.append("  `/allowed-dirs add <path>` - Add directory (session scope)")
            lines.append(
                "  `/allowed-dirs remove <path>` - Remove directory (session scope)"
            )
            return "\n".join(lines)

        elif subcommand == "add":
            if not path_arg:
                return "Usage: `/allowed-dirs add <path>`"

            resolved = Path(path_arg).expanduser().resolve()
            settings.add_allowed_write_path(str(resolved), "session")
            return f"✓ Added {resolved} (session scope)"

        elif subcommand == "remove":
            if not path_arg:
                return "Usage: `/allowed-dirs remove <path>`"

            removed = settings.remove_allowed_write_path(path_arg, "session")
            if removed:
                return f"✓ Removed {path_arg} (session scope)"
            else:
                return f"Path not found in session scope: {path_arg}\nNote: /allowed-dirs remove only removes from session scope."

        else:
            return """Usage:
  `/allowed-dirs list` - List allowed directories
  `/allowed-dirs add <path>` - Add directory (session scope)
  `/allowed-dirs remove <path>` - Remove directory (session scope)"""

    async def _manage_denied_dirs(self, args: str) -> str:
        """Manage denied write directories (session-scoped).

        Usage:
            /denied-dirs list
            /denied-dirs add <path>
            /denied-dirs remove <path>
        """
        from ..lib.settings import AppSettings
        from ..project_utils import get_project_slug

        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower() if parts else "list"
        path_arg = parts[1] if len(parts) > 1 else ""

        # Get session-scoped settings
        session_id = self.session.coordinator.session_id
        project_slug = get_project_slug()
        settings = AppSettings().with_session(session_id, project_slug)

        if subcommand == "list":
            paths = settings.get_denied_write_paths()
            if not paths:
                lines = ["No denied directories configured."]
            else:
                lines = ["Denied Write Directories:"]
                for p, scope in paths:
                    lines.append(f"  {p} ({scope})")

            # Add help text
            lines.append("")
            lines.append("Usage:")
            lines.append("  /denied-dirs list            - List denied directories")
            lines.append("  `/denied-dirs add <path>` - Add directory (session scope)")
            lines.append(
                "  `/denied-dirs remove <path>` - Remove directory (session scope)"
            )
            return "\n".join(lines)

        elif subcommand == "add":
            if not path_arg:
                return "Usage: `/denied-dirs add <path>`"

            resolved = Path(path_arg).expanduser().resolve()
            settings.add_denied_write_path(str(resolved), "session")
            return f"✓ Denied {resolved} (session scope)"

        elif subcommand == "remove":
            if not path_arg:
                return "Usage: `/denied-dirs remove <path>`"

            removed = settings.remove_denied_write_path(path_arg, "session")
            if removed:
                return f"✓ Removed {path_arg} from denied paths (session scope)"
            else:
                return f"Path not found in session scope: {path_arg}\nNote: /denied-dirs remove only removes from session scope."

        else:
            return """Usage:
  `/denied-dirs list` - List denied directories
  `/denied-dirs add <path>` - Add directory (session scope)
  `/denied-dirs remove <path>` - Remove directory (session scope)"""

    async def _list_skills(self) -> str:
        """List available skills with descriptions and shortcuts."""
        discovery = self.session.coordinator.get_capability("skills_discovery")

        if not discovery:
            return (
                "Skills system not available. Include a bundle with skills to enable."
            )

        skills = discovery.list_skills()
        if not skills:
            return "No skills found. Create skills in .amplifier/skills/ or include a bundle with skills."

        lines = ["Available Skills:"]
        for item in skills:
            name, description = item[0], item[1] if len(item) > 1 else ""
            if description:
                lines.append(f"  {name:<20} {description}")
            else:
                lines.append(f"  {name}")

        # Add shortcuts section
        shortcuts = discovery.get_shortcuts()
        if shortcuts:
            lines.append("")
            lines.append("Shortcuts:")
            for shortcut_name in shortcuts:
                lines.append(f"  /{shortcut_name}")

        lines.append("")
        lines.append("Use `/skill <name>` to load a skill.")
        return "\n".join(lines)

    async def _load_skill(self, skill_name: str, arguments: str) -> tuple[bool, str]:
        """Load a skill and return a structured result for execution.

        Args:
            skill_name: Name of the skill to load
            arguments: Optional context arguments from the user

        Returns:
            Tuple of (is_prompt, text) where is_prompt=True means text is a
            synthetic prompt for session.execute(), and is_prompt=False means
            text is an error/usage message to display to the user.
        """
        if not skill_name:
            return False, "Usage: `/skill <name> [context]`"

        discovery = self.session.coordinator.get_capability("skills_discovery")

        if not discovery:
            return (
                False,
                "Skills system not available. Include a bundle with skills to enable.",
            )

        skill = discovery.find(skill_name)
        if not skill:
            # Get available skills for error message
            skills = discovery.list_skills()
            available = ", ".join(s[0] for s in skills) if skills else "none"
            return False, f"Unknown skill: {skill_name}. Available: {available}"

        # Fork skills cannot see the parent conversation, so arguments must be
        # passed through the load_skill tool's explicit arguments parameter.
        if arguments:
            return (
                True,
                f'Use the load_skill tool to load the skill "{skill_name}", '
                f"passing the user's input as the `arguments` parameter "
                f'(load_skill(skill_name="{skill_name}", arguments=...)) so the skill '
                f"receives it. The user's input is: {arguments}",
            )
        else:
            return True, f'Use the load_skill tool to load the skill "{skill_name}".'


__all__ = ["CommandAdminMixin"]
