"""Amplifier CLI - Command-line interface for the Amplifier platform."""

import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import click

from amplifier_app_cli.utils.help_formatter import AmplifierGroup

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle
from amplifier_core import AmplifierSession
from amplifier_core import ModuleValidationError  # pyright: ignore[reportAttributeAccessIssue]
from amplifier_foundation import sanitize_message
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.panel import Panel

from .commands.agents import agents as agents_group
from .commands.allowed_dirs import allowed_dirs as allowed_dirs_group
from .commands.denied_dirs import denied_dirs as denied_dirs_group
from .commands.bundle import bundle as bundle_group
from .commands.collection import collection as collection_group
from .commands.init import check_first_run
from .commands.init import init_cmd
from .commands.init import prompt_first_run_init
from .commands.module import module as module_group
from .commands.profile import profile as profile_group
from .commands.provider import provider as provider_group
from .commands.reset import reset as reset_cmd
from .commands.run import register_run_command
from .commands.session import register_session_commands
from .commands.source import source as source_group
from .session_runner import create_initialized_session
from .session_runner import SessionConfig
from .commands.tool import tool as tool_group
from .commands.update import update as update_cmd
from .commands.version import version as version_cmd
from .console import Markdown
from .console import console
from .effective_config import get_effective_config_summary
from .key_manager import KeyManager
from .lib.legacy import parse_markdown_body
from .paths import create_profile_loader
from .session_store import SessionStore
from .ui.error_display import display_validation_error
from .utils.version import get_version

logger = logging.getLogger(__name__)


# Load API keys from ~/.amplifier/keys.env on startup
# This allows keys saved by 'amplifier init' or 'amplifier provider use' to be available
_key_manager = KeyManager()

# Cancel flag for ESC-based cancellation (legacy, kept for compatibility)
_cancel_requested = False


# Placeholder for the run command; assigned after registration below
_run_command: Callable | None = None


def _detect_shell() -> str | None:
    """Detect current shell from $SHELL environment variable.

    Returns:
        Shell name ('bash', 'zsh', or 'fish') or None if detection fails
    """
    shell_path = os.environ.get("SHELL", "")
    if not shell_path:
        return None

    shell_name = Path(shell_path).name.lower()

    # Check for known shells
    if "bash" in shell_name:
        return "bash"
    if "zsh" in shell_name:
        return "zsh"
    if "fish" in shell_name:
        return "fish"

    return None


def _get_shell_config_file(shell: str) -> Path:
    """Get the standard config file path for a shell.

    Args:
        shell: Shell name ('bash', 'zsh', or 'fish')

    Returns:
        Path to shell config file
    """
    home = Path.home()

    if shell == "bash":
        # Prefer .bashrc on Linux, .bash_profile on macOS
        bashrc = home / ".bashrc"
        bash_profile = home / ".bash_profile"
        if bashrc.exists():
            return bashrc
        return bash_profile

    if shell == "zsh":
        return home / ".zshrc"

    if shell == "fish":
        # For fish, we create a completion file directly
        return home / ".config" / "fish" / "completions" / "amplifier.fish"

    return home / f".{shell}rc"  # Fallback


def _completion_already_installed(config_file: Path, shell: str) -> bool:
    """Check if completion is already installed in config file.

    Args:
        config_file: Path to shell config file
        shell: Shell name

    Returns:
        True if completion marker found in file
    """
    if not config_file.exists():
        return False

    try:
        content = config_file.read_text(encoding="utf-8")
        completion_marker = f"_AMPLIFIER_COMPLETE={shell}_source"
        return completion_marker in content
    except OSError:
        return False


def _can_safely_modify(config_file: Path) -> bool:
    """Check if it's safe to modify the config file.

    Args:
        config_file: Path to shell config file

    Returns:
        True if safe to append to file
    """
    # If file exists, must be writable
    if config_file.exists():
        return os.access(config_file, os.W_OK)

    # If file doesn't exist, parent directory must be writable
    parent = config_file.parent
    if not parent.exists():
        # Need to create parent directories - check if we can
        try:
            parent.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    return os.access(parent, os.W_OK)


def _install_completion_to_config(config_file: Path, shell: str) -> bool:
    """Append completion line to shell config file.

    Args:
        config_file: Path to shell config file
        shell: Shell name

    Returns:
        True if successful
    """
    try:
        # Ensure parent directory exists
        config_file.parent.mkdir(parents=True, exist_ok=True)

        # For fish, write the actual completion script
        if shell == "fish":
            # Fish uses a different approach - we need to invoke Click's completion
            import subprocess

            result = subprocess.run(
                ["amplifier"],
                env={**os.environ, "_AMPLIFIER_COMPLETE": "fish_source"},
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                config_file.write_text(result.stdout, encoding="utf-8")
                return True
            return False

        # For bash/zsh, append eval line
        with open(config_file, "a", encoding="utf-8") as f:
            f.write("\n# Amplifier shell completion\n")
            f.write(f'eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"\n')

        return True

    except OSError:
        return False


def _show_manual_instructions(shell: str, config_file: Path):
    """Show manual installation instructions as fallback.

    Args:
        shell: Shell name
        config_file: Suggested config file path
    """
    console.print(f"\n[yellow]Add this line to {config_file}:[/yellow]")

    if shell == "fish":
        console.print(f"  [cyan]_AMPLIFIER_COMPLETE=fish_source amplifier > {config_file}[/cyan]")
    else:
        console.print(f'  [cyan]eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"[/cyan]')

    console.print("\n[dim]Then reload your shell or start a new terminal.[/dim]")


class CommandProcessor:
    """Process slash commands and special directives."""

    COMMANDS = {
        "/think": {"action": "enable_plan_mode", "description": "Enable read-only planning mode"},
        "/do": {
            "action": "disable_plan_mode",
            "description": "Exit plan mode and allow modifications",
        },
        "/save": {"action": "save_transcript", "description": "Save conversation transcript"},
        "/status": {"action": "show_status", "description": "Show session status"},
        "/clear": {"action": "clear_context", "description": "Clear conversation context"},
        "/help": {"action": "show_help", "description": "Show available commands"},
        "/config": {"action": "show_config", "description": "Show current configuration"},
        "/tools": {"action": "list_tools", "description": "List available tools"},
        "/agents": {"action": "list_agents", "description": "List available agents"},
        "/allowed-dirs": {"action": "manage_allowed_dirs", "description": "Manage allowed write directories"},
        "/denied-dirs": {"action": "manage_denied_dirs", "description": "Manage denied write directories"},
    }

    def __init__(self, session: AmplifierSession, profile_name: str = "unknown"):
        self.session = session
        self.profile_name = profile_name
        self.plan_mode = False
        self.plan_mode_unregister = None  # Store unregister function

    def process_input(self, user_input: str) -> tuple[str, dict[str, Any]]:
        """
        Process user input and extract commands.

        Returns:
            (action, data) tuple
        """
        # Check for commands
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command in self.COMMANDS:
                cmd_info = self.COMMANDS[command]
                return cmd_info["action"], {"args": args, "command": command}
            return "unknown_command", {"command": command}

        # Regular prompt
        return "prompt", {"text": user_input, "plan_mode": self.plan_mode}

    async def handle_command(self, action: str, data: dict[str, Any]) -> str:
        """Handle a command action."""

        if action == "enable_plan_mode":
            self.plan_mode = True
            self._configure_plan_mode(True)
            return "✓ Plan Mode enabled - all modifications disabled"

        if action == "disable_plan_mode":
            self.plan_mode = False
            self._configure_plan_mode(False)
            return "✓ Plan Mode disabled - modifications enabled"

        if action == "save_transcript":
            path = await self._save_transcript(data.get("args", ""))
            return f"✓ Transcript saved to {path}"

        if action == "show_status":
            status = await self._get_status()
            return status

        if action == "clear_context":
            await self._clear_context()
            return "✓ Context cleared"

        if action == "show_help":
            return self._format_help()

        if action == "show_config":
            return await self._get_config_display()

        if action == "list_tools":
            return await self._list_tools()

        if action == "list_agents":
            return await self._list_agents()

        if action == "manage_allowed_dirs":
            return await self._manage_allowed_dirs(data.get("args", ""))

        if action == "manage_denied_dirs":
            return await self._manage_denied_dirs(data.get("args", ""))

        if action == "unknown_command":
            return f"Unknown command: {data['command']}. Use /help for available commands."

        return f"Unhandled action: {action}"

    def _configure_plan_mode(self, enabled: bool):
        """Configure session for plan mode."""
        # Import HookResult here to avoid circular import
        from amplifier_core.models import HookResult

        # Access hooks via the coordinator
        hooks = self.session.coordinator.get("hooks")
        if hooks:
            if enabled:
                # Register plan mode hook that denies write operations
                async def plan_mode_hook(_event: str, data: dict[str, Any]) -> HookResult:
                    tool_name = data.get("tool")
                    if tool_name in ["write", "edit", "bash", "task"]:
                        return HookResult(
                            action="deny",
                            reason="Write operations disabled in Plan Mode",
                        )
                    return HookResult(action="continue")

                # Register the hook with the hooks registry and store unregister function
                if hasattr(hooks, "register"):
                    self.plan_mode_unregister = hooks.register("tool:pre", plan_mode_hook, priority=0, name="plan_mode")
            else:
                # Unregister plan mode hook if we have the unregister function
                if self.plan_mode_unregister:
                    self.plan_mode_unregister()
                    self.plan_mode_unregister = None

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
            from .session_store import SessionStore

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
        lines = ["Session Status:"]
        lines.append(f"  Session ID: {self.session.coordinator.session_id}")
        lines.append(f"  Profile: {self.profile_name}")

        # Plan mode status
        lines.append(f"  Plan Mode: {'ON' if self.plan_mode else 'OFF'}")

        # Context size
        context = self.session.coordinator.get("context")
        if context and hasattr(context, "get_messages"):
            messages = await context.get_messages()
            lines.append(f"  Messages: {len(messages)}")

        # Active providers
        providers = self.session.coordinator.get("providers")
        if providers:
            provider_names = list(providers.keys())
            lines.append(f"  Providers: {', '.join(provider_names)}")

        # Available tools
        tools = self.session.coordinator.get("tools")
        if tools:
            lines.append(f"  Tools: {len(tools)}")

        return "\n".join(lines)

    async def _clear_context(self):
        """Clear the conversation context."""
        context = self.session.coordinator.get("context")
        if context and hasattr(context, "clear"):
            await context.clear()

    def _format_help(self) -> str:
        """Format help text."""
        lines = ["Available Commands:"]
        for cmd, info in self.COMMANDS.items():
            lines.append(f"  {cmd:<12} - {info['description']}")
        return "\n".join(lines)

    async def _get_config_display(self) -> str:
        """Display current configuration using profile show format or bundle display."""
        from .console import console

        # Check if we're in bundle mode (profile_name is "bundle:<name>")
        if self.profile_name.startswith("bundle:"):
            bundle_name = self.profile_name.removeprefix("bundle:")
            await self._render_bundle_config(bundle_name, console)
        else:
            # Profile mode: use inheritance chain display
            from .commands.profile import render_effective_config
            from .paths import create_config_manager
            from .paths import create_profile_loader

            loader = create_profile_loader()
            config_manager = create_config_manager()

            # Load inheritance chain for source tracking
            chain_names = loader.get_inheritance_chain(self.profile_name)
            chain_dicts = loader.load_inheritance_chain_dicts(self.profile_name)
            source_overrides = config_manager.get_module_sources()

            # render_effective_config prints directly to console with rich formatting
            render_effective_config(chain_dicts, chain_names, source_overrides, detailed=True)

        # Also show loaded agents (available at runtime)
        # Note: agents can be a dict (resolved agents) or list/other format (profile config)
        loaded_agents = self.session.config.get("agents", {})
        if isinstance(loaded_agents, dict) and loaded_agents:
            # Filter out profile config keys (dirs, include, inline) - only show resolved agent names
            agent_names = [k for k in loaded_agents if k not in ("dirs", "include", "inline")]
            if agent_names:
                console.print()  # Blank line after Agents: section
                console.print("[bold]Loaded Agents:[/bold]")
                for name in sorted(agent_names):
                    console.print(f"  {name}")

        return ""  # Output already printed

    async def _render_bundle_config(self, bundle_name: str, console: Any) -> None:
        """Render bundle configuration display."""
        config = self.session.config

        console.print(f"\n[bold]Bundle Configuration:[/bold] {bundle_name}\n")

        # Session section
        session_config = config.get("session", {})
        if session_config:
            console.print("[bold]Session:[/bold]")
            for field in ["orchestrator", "context"]:
                if field in session_config:
                    value = session_config[field]
                    if isinstance(value, dict) and "module" in value:
                        console.print(f"  {field}:")
                        console.print(f"    module: {value.get('module', 'unknown')}")
                        if value.get("source"):
                            source = value["source"]
                            if len(source) > 60:
                                source = source[:57] + "..."
                            console.print(f"    source: {source}")
                    else:
                        console.print(f"  {field}: {value}")

        # Providers section
        providers = config.get("providers", [])
        if providers:
            console.print("\n[bold]Providers:[/bold]")
            for provider in providers:
                if isinstance(provider, dict):
                    module = provider.get("module", "unknown")
                    console.print(f"  - {module}")
                    if provider.get("source"):
                        source = provider["source"]
                        if len(source) > 60:
                            source = source[:57] + "..."
                        console.print(f"    source: {source}")
                    if provider.get("config"):
                        console.print("    config:")
                        for key, val in provider["config"].items():
                            console.print(f"      {key}: {val}")

        # Tools section
        tools = config.get("tools", [])
        if tools:
            console.print("\n[bold]Tools:[/bold]")
            for tool in tools:
                if isinstance(tool, dict):
                    module = tool.get("module", "unknown")
                    console.print(f"  - {module}")
                elif isinstance(tool, str):
                    console.print(f"  - {tool}")

        # Hooks section
        hooks = config.get("hooks", [])
        if hooks:
            console.print("\n[bold]Hooks:[/bold]")
            for hook in hooks:
                if isinstance(hook, dict):
                    module = hook.get("module", "unknown")
                    console.print(f"  - {module}")
                elif isinstance(hook, str):
                    console.print(f"  - {hook}")

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
        # Note: agents can be a dict (resolved agents) or list/other format (profile config)
        all_agents = self.session.config.get("agents", {})

        if not isinstance(all_agents, dict):
            return "No agents available (agents not loaded as dict)"

        # Filter out profile config keys - only show resolved agent entries
        agent_items = {
            k: v for k, v in all_agents.items() if k not in ("dirs", "include", "inline") and isinstance(v, dict)
        }

        if not agent_items:
            return "No agents available (check profile's agents configuration)"

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
        from .lib.settings import AppSettings
        from .project_utils import get_project_slug

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
            lines.append("  /allowed-dirs add <path>      - Add directory (session scope)")
            lines.append("  /allowed-dirs remove <path>   - Remove directory (session scope)")
            return "\n".join(lines)

        elif subcommand == "add":
            if not path_arg:
                return "Usage: /allowed-dirs add <path>"

            resolved = Path(path_arg).expanduser().resolve()
            settings.add_allowed_write_path(str(resolved), "session")
            return f"✓ Added {resolved} (session scope)"

        elif subcommand == "remove":
            if not path_arg:
                return "Usage: /allowed-dirs remove <path>"

            removed = settings.remove_allowed_write_path(path_arg, "session")
            if removed:
                return f"✓ Removed {path_arg} (session scope)"
            else:
                return f"Path not found in session scope: {path_arg}\nNote: /allowed-dirs remove only removes from session scope."

        else:
            return """Usage:
  /allowed-dirs list            - List allowed directories
  /allowed-dirs add <path>      - Add directory (session scope)
  /allowed-dirs remove <path>   - Remove directory (session scope)"""

    async def _manage_denied_dirs(self, args: str) -> str:
        """Manage denied write directories (session-scoped).

        Usage:
            /denied-dirs list
            /denied-dirs add <path>
            /denied-dirs remove <path>
        """
        from .lib.settings import AppSettings
        from .project_utils import get_project_slug

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
            lines.append("  /denied-dirs add <path>      - Add directory (session scope)")
            lines.append("  /denied-dirs remove <path>   - Remove directory (session scope)")
            return "\n".join(lines)

        elif subcommand == "add":
            if not path_arg:
                return "Usage: /denied-dirs add <path>"

            resolved = Path(path_arg).expanduser().resolve()
            settings.add_denied_write_path(str(resolved), "session")
            return f"✓ Denied {resolved} (session scope)"

        elif subcommand == "remove":
            if not path_arg:
                return "Usage: /denied-dirs remove <path>"

            removed = settings.remove_denied_write_path(path_arg, "session")
            if removed:
                return f"✓ Removed {path_arg} from denied paths (session scope)"
            else:
                return f"Path not found in session scope: {path_arg}\nNote: /denied-dirs remove only removes from session scope."

        else:
            return """Usage:
  /denied-dirs list            - List denied directories
  /denied-dirs add <path>      - Add directory (session scope)
  /denied-dirs remove <path>   - Remove directory (session scope)"""


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
@click.version_option(version=get_version(), prog_name="amplifier")
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
            console.print('  [cyan]Bash:  eval "$(_AMPLIFIER_COMPLETE=bash_source amplifier)"[/cyan]')
            console.print('  [cyan]Zsh:   eval "$(_AMPLIFIER_COMPLETE=zsh_source amplifier)"[/cyan]')
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
            console.print(f"[green]✓ Completion already configured in {config_file}[/green]\n")
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

    # If no command specified, launch chat mode with current profile
    # Note: Update check happens inside run command (not here, to avoid slowing other commands)
    # For initial prompt support, use: amplifier run --mode chat "prompt"
    if ctx.invoked_subcommand is None:
        if _run_command is None:
            raise RuntimeError("Run command not registered")
        ctx.invoke(
            _run_command,
            prompt=None,
            profile=None,
            bundle=None,  # Will check settings for active bundle
            provider=None,
            model=None,
            mode="chat",
            resume=None,
            verbose=False,
        )


async def _process_runtime_mentions(session: AmplifierSession, prompt: str) -> None:
    """Process @mentions in user input at runtime.

    Args:
        session: Active session to add context messages to
        prompt: User's input that may contain @mentions
    """
    import logging

    from .lib.mention_loading import MentionLoader
    from .utils.mentions import has_mentions

    logger = logging.getLogger(__name__)

    if not has_mentions(prompt):
        return

    logger.info("Processing @mentions in user input")

    # Load @mentioned files (resolve relative to current working directory)
    from pathlib import Path

    # Use the same mention_resolver registered for tools (ensures consistency)
    mention_resolver = session.coordinator.get_capability("mention_resolver")
    loader = MentionLoader(resolver=mention_resolver)
    deduplicator = session.coordinator.get_capability("mention_deduplicator")
    context_messages = loader.load_mentions(prompt, relative_to=Path.cwd(), deduplicator=deduplicator)

    if not context_messages:
        logger.debug("No files found for runtime @mentions (or all already loaded)")
        return

    logger.info(f"Loaded {len(context_messages)} unique context files from runtime @mentions")

    # Add context messages to session as developer messages (before user message)
    context = session.coordinator.get("context")
    for i, msg in enumerate(context_messages):
        msg_dict = msg.model_dump()
        logger.debug(f"Adding runtime context {i + 1}/{len(context_messages)}: {len(msg.content)} chars")
        await context.add_message(msg_dict)


def _create_prompt_session() -> PromptSession:
    """Create configured PromptSession for REPL.

    Provides:
    - Persistent history at ~/.amplifier/projects/<project-slug>/repl_history
    - Green prompt styling matching Rich console
    - History search with Ctrl-R
    - Multi-line input with Ctrl-J
    - Graceful fallback to in-memory history on errors

    Returns:
        Configured PromptSession instance

    Philosophy:
    - Ruthless simplicity: Use library's defaults, minimal config
    - Graceful degradation: Fallback to in-memory if file history fails
    - User experience: History is project-scoped (aligned with sessions)
    - Reliable keys: Ctrl-J works in all terminals
    """
    from amplifier_app_cli.project_utils import get_project_slug

    project_slug = get_project_slug()
    history_path = Path.home() / ".amplifier" / "projects" / project_slug / "repl_history"

    # Ensure project directory exists
    history_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to use file history, fallback to in-memory
    try:
        history = FileHistory(str(history_path))
    except OSError as e:
        # Fallback if history file is corrupted or inaccessible
        history = InMemoryHistory()
        logger.warning(f"Could not load history from {history_path}: {e}. Using in-memory history for this session.")

    # Create key bindings for multi-line support
    kb = KeyBindings()

    @kb.add("c-j")  # Ctrl-J inserts newline (terminal-reliable)
    def insert_newline(event):
        """Insert newline character for multi-line input."""
        event.current_buffer.insert_text("\n")

    @kb.add("enter")  # Enter submits (even in multiline mode)
    def accept_input(event):
        """Submit input on Enter."""
        event.current_buffer.validate_and_handle()

    return PromptSession(
        message=HTML("\n<ansigreen><b>></b></ansigreen> "),
        history=history,
        key_bindings=kb,
        multiline=True,  # Enable multi-line display
        prompt_continuation="  ",  # Two spaces for alignment (cleaner than "... ")
        enable_history_search=True,  # Enables Ctrl-R
    )


async def interactive_chat(
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    profile_name: str = "unknown",
    prepared_bundle: "PreparedBundle | None" = None,
    initial_prompt: str | None = None,
    initial_transcript: list[dict] | None = None,
):
    """Run an interactive chat session.

    This is the unified entry point for interactive REPL sessions. It handles:
    - New sessions (initial_transcript=None)
    - Resumed sessions (initial_transcript provided)
    - Bundle mode and profile mode (via prepared_bundle)
    - Initial prompt auto-execution
    - Ctrl+C cancellation handling

    Args:
        config: Resolved mount plan configuration
        search_paths: Module search paths
        verbose: Enable verbose output
        session_id: Optional session ID (generated if not provided)
        profile_name: Profile or bundle name (e.g., "dev" or "bundle:foundation")
        prepared_bundle: PreparedBundle from foundation's prepare workflow (bundle mode only)
        initial_prompt: Optional prompt to auto-execute before entering interactive loop
        initial_transcript: If provided, restore this transcript (resume mode)
    """
    global _cancel_requested

    # === SESSION CREATION (unified via create_initialized_session) ===
    session_config = SessionConfig(
        config=config,
        search_paths=search_paths,
        verbose=verbose,
        session_id=session_id,
        profile_name=profile_name,
        initial_transcript=initial_transcript,
        prepared_bundle=prepared_bundle,
    )

    # Create fully initialized session (handles all setup including resume)
    initialized = await create_initialized_session(session_config, console)
    session = initialized.session
    actual_session_id = initialized.session_id

    # Create command processor
    command_processor = CommandProcessor(session, profile_name)

    # Create session store for saving
    store = SessionStore()

    # Register incremental save hook for crash recovery between tool calls
    from .incremental_save import register_incremental_save

    register_incremental_save(session, store, actual_session_id, profile_name, config)

    # Show banner only for NEW sessions (resume shows banner via history display in commands/session.py)
    if not session_config.is_resume:
        config_summary = get_effective_config_summary(config, profile_name)
        console.print(
            Panel.fit(
                f"[bold cyan]Amplifier Interactive Session[/bold cyan]\n"
                f"[dim]Session ID: [/dim][dim bright_yellow]{actual_session_id}[/dim bright_yellow]\n"
                f"[dim]{config_summary.format_banner_line()}[/dim]\n"
                f"Commands: /help | Multi-line: Ctrl-J | Exit: Ctrl-D",
                border_style="cyan",
            )
        )

    # Create prompt session for history and advanced editing
    prompt_session = _create_prompt_session()

    # Helper to extract model name from config
    def _extract_model_name() -> str:
        if isinstance(config.get("providers"), list) and config["providers"]:
            first_provider = config["providers"][0]
            if isinstance(first_provider, dict) and "config" in first_provider:
                provider_config = first_provider["config"]
                return provider_config.get("model") or provider_config.get("default_model", "unknown")
        return "unknown"

    # Helper to save session after each turn
    async def _save_session():
        context = session.coordinator.get("context")
        if context and hasattr(context, "get_messages"):
            messages = await context.get_messages()
            metadata = {
                "session_id": actual_session_id,
                "created": datetime.now(UTC).isoformat(),
                "profile": profile_name,
                "model": _extract_model_name(),
                "turn_count": len([m for m in messages if m.get("role") == "user"]),
            }
            store.save(actual_session_id, messages, metadata)

    # Helper to execute a prompt with Ctrl+C handling
    async def _execute_with_interrupt(prompt_text: str) -> bool:
        """Execute prompt with interrupt handling. Returns True if completed, False if cancelled."""
        # Reset cancellation state for new execution
        session.coordinator.cancellation.reset()

        def sigint_handler(signum, frame):
            """Handle Ctrl+C with graceful/immediate cancellation."""
            cancellation = session.coordinator.cancellation

            if cancellation.is_cancelled:
                # Second Ctrl+C - request immediate cancellation
                # Use call_soon_threadsafe since we're in a signal handler
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(session.coordinator.request_cancel(immediate=True))
                )
                console.print("\n[bold red]Cancelling immediately...[/bold red]")
            else:
                # First Ctrl+C - request graceful cancellation
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(session.coordinator.request_cancel(immediate=False))
                )
                # Show what's running
                running_tools = cancellation.running_tool_names
                if running_tools:
                    tools_str = ", ".join(running_tools)
                    console.print(f"\n[yellow]Cancelling after [bold]{tools_str}[/bold] completes... (Ctrl+C again to force)[/yellow]")
                else:
                    console.print("\n[yellow]Cancelling after current operation completes... (Ctrl+C again to force)[/yellow]")

        original_handler = signal.signal(signal.SIGINT, sigint_handler)

        try:
            execute_task = asyncio.create_task(session.execute(prompt_text))

            # Poll task while checking for cancellation
            while not execute_task.done():
                # Check for immediate cancellation - cancel the task
                if session.coordinator.cancellation.is_immediate:
                    execute_task.cancel()
                    break
                await asyncio.sleep(0.05)

            try:
                response = await execute_task
                from .ui import render_message

                render_message({"role": "assistant", "content": response}, console)

                # Emit prompt:complete event
                hooks = session.coordinator.get("hooks")
                if hooks:
                    from amplifier_core.events import PROMPT_COMPLETE

                    await hooks.emit(PROMPT_COMPLETE, {"prompt": prompt_text, "response": response})

                # Save session after execution (even if cancelled - preserves state)
                await _save_session()

                # Return based on cancellation status
                if session.coordinator.cancellation.is_cancelled:
                    console.print("\n[yellow]Cancelled[/yellow]")
                    return False
                return True

            except asyncio.CancelledError:
                # Immediate cancellation - task was force-cancelled
                console.print("\n[yellow]Cancelled[/yellow]")
                # Still save session to preserve any partial progress
                await _save_session()
                return False

        finally:
            signal.signal(signal.SIGINT, original_handler)
            # Don't reset cancellation here - session.py handles status

    # Execute initial prompt if provided
    if initial_prompt:
        console.print(f"\n[bold cyan]>[/bold cyan] {initial_prompt[:100]}{'...' if len(initial_prompt) > 100 else ''}")
        console.print("\n[dim]Processing... (Ctrl+C to cancel)[/dim]")

        # Process runtime @mentions in initial prompt
        await _process_runtime_mentions(session, initial_prompt)
        await _execute_with_interrupt(initial_prompt)

    # === REPL LOOP ===
    try:
        while True:
            try:
                # Get user input with history, editing, and paste support
                with patch_stdout():
                    user_input = await prompt_session.prompt_async()

                if user_input.lower() in ["exit", "quit"]:
                    break

                if user_input.strip():
                    # Process input for commands
                    action, data = command_processor.process_input(user_input)

                    if action == "prompt":
                        console.print("\n[dim]Processing... (Ctrl+C to cancel)[/dim]")

                        # Process runtime @mentions in user input
                        await _process_runtime_mentions(session, data["text"])
                        await _execute_with_interrupt(data["text"])

                    else:
                        # Handle command
                        result = await command_processor.handle_command(action, data)
                        console.print(f"[cyan]{result}[/cyan]")

            except EOFError:
                # Ctrl-D - graceful exit
                console.print("\n[dim]Exiting...[/dim]")
                break

            except ModuleValidationError as e:
                display_validation_error(console, e, verbose=verbose)

            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                if verbose:
                    console.print_exception()

    finally:
        await initialized.cleanup()
        console.print("\n[yellow]Session ended[/yellow]\n")


async def execute_single(
    prompt: str,
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    profile_name: str = "unknown",
    output_format: str = "text",
    prepared_bundle: "PreparedBundle | None" = None,
    initial_transcript: list[dict] | None = None,
):
    """Execute a single prompt and exit.
    
    This is the unified entry point for single-shot execution. It handles:
    - New sessions (initial_transcript=None)
    - Resumed sessions (initial_transcript provided)
    - Bundle mode and profile mode (via prepared_bundle)
    - All output formats (text, json, json-trace)
    
    Args:
        prompt: The user prompt to execute
        config: Effective configuration dict
        search_paths: Paths for module resolution
        verbose: Enable verbose output
        session_id: Optional session ID (generated if None)
        profile_name: Profile/bundle name for metadata
        output_format: Output format (text, json, json-trace)
        prepared_bundle: PreparedBundle for bundle mode
        initial_transcript: If provided, restore this transcript (resume mode)
    """
    # === OUTPUT REDIRECTION (must happen before any console output) ===
    # In JSON mode, redirect all output to stderr so only JSON goes to stdout
    if output_format in ["json", "json-trace"]:
        original_stdout = sys.stdout
        original_console_file = console.file
        sys.stdout = sys.stderr
        console.file = sys.stderr
    else:
        original_stdout = None
        original_console_file = None

    # For JSON output, store response data to output after cleanup
    json_output_data: dict[str, Any] | None = None

    # For json-trace, create trace collector
    trace_collector = None
    if output_format == "json-trace":
        from .trace_collector import TraceCollector
        trace_collector = TraceCollector()

    # === SESSION CREATION (unified via create_initialized_session) ===
    session_config = SessionConfig(
        config=config,
        search_paths=search_paths,
        verbose=verbose,
        session_id=session_id,
        profile_name=profile_name,
        initial_transcript=initial_transcript,
        prepared_bundle=prepared_bundle,
        output_format=output_format,
    )
    
    # Create fully initialized session (handles all setup including resume)
    initialized = await create_initialized_session(session_config, console)
    session = initialized.session

    try:
        # Register trace collector hooks if in json-trace mode
        if trace_collector:
            hooks = session.coordinator.get("hooks")
            if hooks:
                hooks.register("tool:pre", trace_collector.on_tool_pre, priority=1000, name="trace_collector_pre")
                hooks.register("tool:post", trace_collector.on_tool_post, priority=1000, name="trace_collector_post")

        # Process runtime @mentions in user input
        await _process_runtime_mentions(session, prompt)

        if verbose:
            console.print(f"[dim]Executing: {prompt}[/dim]")

        response = await session.execute(prompt)

        # Get metadata for output
        actual_session_id = session.session_id
        providers = session.coordinator.get("providers") or {}
        model_name = "unknown"
        for prov_name, prov in providers.items():
            if hasattr(prov, "model"):
                model_name = f"{prov_name}/{prov.model}"
                break
            if hasattr(prov, "default_model"):
                model_name = f"{prov_name}/{prov.default_model}"
                break

        # Emit prompt:complete (canonical kernel event) BEFORE formatting output
        # This ensures hook output goes to stderr in JSON mode
        hooks = session.coordinator.get("hooks")
        if hooks:
            from amplifier_core.events import PROMPT_COMPLETE
            await hooks.emit(PROMPT_COMPLETE, {"prompt": prompt, "response": response})

        # Output response based on format
        if output_format in ["json", "json-trace"]:
            # Store data for JSON output in finally block (after all hooks fired)
            json_output_data = {
                "status": "success",
                "response": response,
                "session_id": actual_session_id,
                "profile": profile_name,
                "model": model_name,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            # Add trace data if collecting
            if trace_collector:
                json_output_data["execution_trace"] = trace_collector.get_trace()
                json_output_data["metadata"] = trace_collector.get_metadata()
        else:
            # Text output for humans
            if verbose:
                console.print(f"[dim]Response type: {type(response)}, length: {len(response) if response else 0}[/dim]")
            console.print(Markdown(response))
            console.print()  # Add blank line after output to prevent running into shell prompt

        # Always save session (for debugging/archival)
        context = session.coordinator.get("context")
        messages = await context.get_messages() if context else []
        if messages:
            store = SessionStore()
            metadata = {
                "session_id": actual_session_id,
                "created": datetime.now(UTC).isoformat(),
                "profile": profile_name,
                "model": model_name,
                "turn_count": len([m for m in messages if m.get("role") == "user"]),
            }
            store.save(actual_session_id, messages, metadata)
            if verbose and output_format == "text":
                console.print(f"[dim]Session {actual_session_id[:8]}... saved[/dim]")

    except ModuleValidationError as e:
        if output_format in ["json", "json-trace"]:
            # Restore stdout before writing error JSON
            if original_stdout is not None:
                sys.stdout = original_stdout
            error_output = {
                "status": "error",
                "error": str(e),
                "error_type": "ModuleValidationError",
                "session_id": session.session_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            print(json.dumps(error_output, indent=2))
        else:
            # Clean display for module validation errors
            display_validation_error(console, e, verbose=verbose)
        sys.exit(1)

    except Exception as e:
        if output_format in ["json", "json-trace"]:
            # Restore stdout before writing error JSON
            if original_stdout is not None:
                sys.stdout = original_stdout
            # JSON error output
            error_output = {
                "status": "error",
                "error": str(e),
                "session_id": session.session_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            print(json.dumps(error_output, indent=2))
        else:
            # Try clean display for module validation errors (including wrapped ones)
            if not display_validation_error(console, e, verbose=verbose):
                # Fall back to generic error output
                console.print(f"[red]Error:[/red] {e}")
                if verbose:
                    console.print_exception()
        sys.exit(1)

    finally:
        await initialized.cleanup()
        # Allow async tasks to complete before output
        if output_format in ["json", "json-trace"]:
            await asyncio.sleep(0.1)  # Brief pause for any deferred hook output
        # Flush stderr to ensure all hook output is written
        sys.stderr.flush()
        # Restore stdout and print JSON
        if json_output_data is not None and original_stdout is not None:
            sys.stdout = original_stdout
            print(json.dumps(json_output_data, indent=2))
            sys.stdout.flush()
        elif original_stdout is not None:
            sys.stdout = original_stdout
        if original_console_file is not None:
            console.file = original_console_file


# Register standalone commands
cli.add_command(agents_group)
cli.add_command(allowed_dirs_group)
cli.add_command(denied_dirs_group)
cli.add_command(bundle_group)
cli.add_command(collection_group)
cli.add_command(init_cmd)
cli.add_command(profile_group)
cli.add_command(module_group)
cli.add_command(provider_group)
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
    cli()


if __name__ == "__main__":
    main()
