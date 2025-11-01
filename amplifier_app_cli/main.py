"""Amplifier CLI - Command-line interface for the Amplifier platform."""

import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import click
from amplifier_core import AmplifierSession
from amplifier_profiles.utils import parse_markdown_body
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.panel import Panel

from .commands.collection import collection as collection_group
from .commands.init import check_first_run
from .commands.init import init_cmd
from .commands.init import prompt_first_run_init
from .commands.logs import logs_cmd
from .commands.module import module as module_group
from .commands.profile import profile as profile_group
from .commands.provider import provider as provider_group
from .commands.run import register_run_command
from .commands.session import register_session_commands
from .commands.setup import setup_cmd
from .commands.source import source as source_group
from .data.profiles import get_system_default_profile
from .key_manager import KeyManager
from .lib.app_settings import AppSettings
from .console import console
from .paths import create_agent_loader
from .paths import create_config_manager
from .paths import create_module_resolver
from .paths import create_profile_loader
from .runtime.config import resolve_app_config
from .session_store import SessionStore

logger = logging.getLogger(__name__)

# Load API keys from ~/.amplifier/keys.env on startup
# This allows keys saved by 'amplifier setup' to be available
_key_manager = KeyManager()

# Abort flag for ESC-based cancellation
_abort_requested = False

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
        content = config_file.read_text()
        completion_marker = f"_AMPLIFIER_COMPLETE={shell}_source"
        return completion_marker in content
    except Exception:
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
        except Exception:
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
                config_file.write_text(result.stdout)
                return True
            return False

        # For bash/zsh, append eval line
        with open(config_file, "a") as f:
            f.write("\n# Amplifier shell completion\n")
            f.write(f'eval "$(_AMPLIFIER_COMPLETE={shell}_source amplifier)"\n')

        return True

    except Exception:
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
        "/stop": {"action": "halt_execution", "description": "Stop current execution"},
        "/save": {"action": "save_transcript", "description": "Save conversation transcript"},
        "/status": {"action": "show_status", "description": "Show session status"},
        "/clear": {"action": "clear_context", "description": "Clear conversation context"},
        "/help": {"action": "show_help", "description": "Show available commands"},
        "/config": {"action": "show_config", "description": "Show current configuration"},
        "/tools": {"action": "list_tools", "description": "List available tools"},
        "/agents": {"action": "list_agents", "description": "List available agents"},
    }

    def __init__(self, session: AmplifierSession):
        self.session = session
        self.plan_mode = False
        self.halted = False
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

        if action == "halt_execution":
            self.halted = True
            # Signal orchestrator to stop if it supports halting
            orchestrator = self.session.coordinator.get("orchestrator")
            if orchestrator and hasattr(orchestrator, "halt"):
                await orchestrator.halt()
            return "✓ Execution halted"

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
        """Save current transcript with sanitization for non-JSON-serializable objects."""
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
            sanitized_messages = [store._sanitize_message(msg) for msg in messages]

            # Save to file
            path = Path(".amplifier/transcripts") / filename
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w") as f:
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
        """Display current configuration."""
        config_str = json.dumps(self.session.config, indent=2)
        return f"Current Configuration:\n{config_str}"

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
        all_agents = self.session.config.get("agents", {})

        if not all_agents:
            return "No agents available (check profile's agents configuration)"

        # Format output
        lines = ["Available Agents:"]
        for name, config in sorted(all_agents.items()):
            meta = config.get("meta", {})
            description = meta.get("description", "No description")
            # Handle multi-line descriptions - take first line only
            first_line = description.split("\n")[0]
            # Truncate if too long
            if len(first_line) > 50:
                first_line = first_line[:47] + "..."
            lines.append(f"  {name:<25} - {first_line}")

        return "\n".join(lines)




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


@click.group(invoke_without_command=True)
@click.version_option()
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
    if ctx.invoked_subcommand is None:
        if _run_command is None:
            raise RuntimeError("Run command not registered")
        ctx.invoke(
            _run_command,
            prompt=None,
            profile=None,
            provider=None,
            model=None,
            mode="chat",
            session_id=None,
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

    loader = MentionLoader()
    context_messages = loader.load_mentions(prompt, relative_to=Path.cwd())

    if not context_messages:
        logger.debug("No files found for runtime @mentions")
        return

    logger.info(f"Loaded {len(context_messages)} context files from runtime @mentions")

    # Add context messages to session (before user message)
    context = session.coordinator.get("context")
    for i, msg in enumerate(context_messages):
        msg_dict = msg.model_dump()
        logger.debug(f"Adding runtime context {i + 1}/{len(context_messages)}: {len(msg.content)} chars")
        await context.add_message(msg_dict)


async def _process_profile_mentions(session: AmplifierSession, profile_name: str) -> None:
    """Process @mentions in profile markdown body.

    Args:
        session: Active session to add context messages to
        profile_name: Name of active profile
    """
    import logging

    from amplifier_core.message_models import Message

    from .lib.mention_loading import MentionLoader
    from .utils.mentions import has_mentions

    logger = logging.getLogger(__name__)

    # Load profile and extract markdown body
    profile_loader = create_profile_loader()
    try:
        logger.info(f"Processing @mentions for profile: {profile_name}")

        profile_file = profile_loader.find_profile_file(profile_name)
        if not profile_file:
            logger.debug(f"Profile file not found for: {profile_name}")
            return

        logger.debug(f"Found profile file: {profile_file}")

        markdown_body = parse_markdown_body(profile_file.read_text())
        if not markdown_body:
            logger.debug(f"No markdown body in profile: {profile_name}")
            return

        logger.debug(f"Profile markdown body length: {len(markdown_body)} chars")

        if not has_mentions(markdown_body):
            logger.debug("No @mentions found in profile markdown")
            return

        logger.info("Profile contains @mentions, loading context files...")

        # Load @mentioned files
        loader = MentionLoader()
        context_messages = loader.load_mentions(markdown_body, relative_to=profile_file.parent)

        logger.info(f"Loaded {len(context_messages)} context messages from profile @mentions")

        # Add context messages to session
        context = session.coordinator.get("context")
        logger.debug(f"Got context object: {type(context)}")

        for i, msg in enumerate(context_messages):
            msg_dict = msg.model_dump()
            logger.debug(
                f"Adding context message {i + 1}/{len(context_messages)}: role={msg.role}, content_length={len(msg.content)}"
            )
            await context.add_message(msg_dict)

        # Add system instruction with @mentions preserved as references
        system_msg = Message(role="system", content=markdown_body)
        logger.debug(f"Adding system instruction with @mentions preserved (length={len(markdown_body)})")
        await context.add_message(system_msg.model_dump())

        # Verify messages were added
        all_messages = await context.get_messages()
        logger.debug(f"Total messages in context after processing: {len(all_messages)}")

    except (FileNotFoundError, ValueError) as e:
        # Profile not found or invalid - skip mention processing
        logger.warning(f"Failed to process profile @mentions: {e}")
        pass


def _create_prompt_session() -> PromptSession:
    """Create configured PromptSession for REPL.

    Provides:
    - Persistent history at ~/.amplifier/repl_history
    - Green prompt styling matching Rich console
    - History search with Ctrl-R
    - Multi-line input with Ctrl-J
    - Graceful fallback to in-memory history on errors

    Returns:
        Configured PromptSession instance

    Philosophy:
    - Ruthless simplicity: Use library's defaults, minimal config
    - Graceful degradation: Fallback to in-memory if file history fails
    - User experience: History location follows XDG pattern (~/.amplifier/)
    - Reliable keys: Ctrl-J works in all terminals
    """
    history_path = Path.home() / ".amplifier" / "repl_history"

    # Ensure .amplifier directory exists
    history_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to use file history, fallback to in-memory
    try:
        history = FileHistory(str(history_path))
    except Exception as e:
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
    config: dict, search_paths: list[Path], verbose: bool, session_id: str | None = None, profile_name: str = "default"
):
    """Run an interactive chat session."""
    # Generate session ID if not provided
    if not session_id:
        session_id = str(uuid.uuid4())

    # Create session with resolved config and session_id
    # Session creates its own loader with coordinator (so it can see mounted resolver)
    session = AmplifierSession(config, session_id=session_id)

    # Mount module source resolver (app-layer policy)

    resolver = create_module_resolver()
    await session.coordinator.mount("module-source-resolver", resolver)

    # Show loading indicator during initialization (modules loading, etc.)
    with console.status("[dim]Loading...[/dim]", spinner="dots"):
        await session.initialize()

    # Process profile @mentions if profile has markdown body
    await _process_profile_mentions(session, profile_name)

    # Register CLI approval provider if approval hook is active (app-layer policy)
    from .approval_provider import CLIApprovalProvider

    register_provider = session.coordinator.get_capability("approval.register_provider")
    if register_provider:
        approval_provider = CLIApprovalProvider(console)
        register_provider(approval_provider)
        logger.info("Registered CLIApprovalProvider for interactive approvals")

    # Register session spawning capability for agent delegation (app-layer policy)
    async def spawn_with_agent_wrapper(agent_name: str, instruction: str, sub_session_id: str):
        """Wrapper for session spawning using coordinator infrastructure."""
        from .session_spawner import spawn_sub_session

        # Get agents from session config (loaded via mount plan)
        agents = session.config.get("agents", {})

        return await spawn_sub_session(agent_name, instruction, session, agents, sub_session_id)

    session.coordinator.register_capability("session.spawn_with_agent", spawn_with_agent_wrapper)

    # Create command processor
    command_processor = CommandProcessor(session)

    # Create session store for saving
    store = SessionStore()

    console.print(
        Panel.fit(
            "[bold cyan]Amplifier Interactive Session[/bold cyan]\nCommands: /help | Multi-line: Ctrl-J | Exit: Ctrl-D",
            border_style="cyan",
        )
    )

    # Create prompt session for history and advanced editing
    prompt_session = _create_prompt_session()

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
                        # Normal prompt execution
                        console.print("\n[dim]Processing... (Ctrl-C to abort)[/dim]")

                        # Process runtime @mentions in user input
                        await _process_runtime_mentions(session, data["text"])

                        # Install signal handler to catch Ctrl-C without raising KeyboardInterrupt
                        global _abort_requested
                        _abort_requested = False

                        def sigint_handler(signum, frame):
                            """Handle Ctrl-C by setting abort flag instead of raising exception."""
                            global _abort_requested
                            _abort_requested = True

                        original_handler = signal.signal(signal.SIGINT, sigint_handler)

                        try:
                            # Run execute as cancellable task
                            execute_task = asyncio.create_task(session.execute(data["text"]))

                            # Poll task while checking for abort flag
                            while not execute_task.done():
                                if _abort_requested:
                                    execute_task.cancel()
                                    break
                                await asyncio.sleep(0.05)  # Check every 50ms

                            # Handle result or cancellation
                            try:
                                response = await execute_task
                                console.print("\n" + response)

                                # Save session after each interaction
                                context = session.coordinator.get("context")
                                if context and hasattr(context, "get_messages"):
                                    messages = await context.get_messages()
                                    # Extract model from providers config
                                    model_name = "unknown"
                                    if isinstance(config.get("providers"), list) and config["providers"]:
                                        first_provider = config["providers"][0]
                                        if isinstance(first_provider, dict) and "config" in first_provider:
                                            # Check both "model" and "default_model" keys
                                            provider_config = first_provider["config"]
                                            model_name = provider_config.get("model") or provider_config.get(
                                                "default_model", "unknown"
                                            )

                                    metadata = {
                                        "created": datetime.now(UTC).isoformat(),
                                        "profile": profile_name,
                                        "model": model_name,
                                        "turn_count": len([m for m in messages if m.get("role") == "user"]),
                                    }
                                    store.save(session_id, messages, metadata)
                            except asyncio.CancelledError:
                                # Ctrl-C pressed during processing
                                console.print("\n[yellow]Aborted (Ctrl-C)[/yellow]")
                                if command_processor.halted:
                                    command_processor.halted = False
                        finally:
                            # Always restore original signal handler
                            signal.signal(signal.SIGINT, original_handler)
                            _abort_requested = False
                    else:
                        # Handle command
                        result = await command_processor.handle_command(action, data)
                        console.print(f"[cyan]{result}[/cyan]")

            except EOFError:
                # Ctrl-D - graceful exit
                console.print("\n[dim]Exiting...[/dim]")
                break

            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                if verbose:
                    console.print_exception()
    finally:
        await session.cleanup()
        console.print("\n[yellow]Session ended[/yellow]\n")


async def execute_single(
    prompt: str,
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    profile_name: str = "unknown",
):
    """Execute a single prompt and exit."""
    # Immediate feedback that something is happening
    console.print("[dim]Initializing session...[/dim]", end="")
    console.print("\r", end="")  # Clear the line after initialization

    # Create session with resolved config and session_id
    # Session creates its own loader with coordinator
    session = AmplifierSession(config, session_id=session_id)

    try:
        # Mount module source resolver (app-layer policy)

        resolver = create_module_resolver()
        await session.coordinator.mount("module-source-resolver", resolver)
        await session.initialize()

        # Process profile @mentions if profile has markdown body
        await _process_profile_mentions(session, profile_name)

        # Register CLI approval provider if approval hook is active (app-layer policy)
        from .approval_provider import CLIApprovalProvider

        register_provider = session.coordinator.get_capability("approval.register_provider")
        if register_provider:
            approval_provider = CLIApprovalProvider(console)
            register_provider(approval_provider)

        # Process runtime @mentions in user input
        await _process_runtime_mentions(session, prompt)

        if verbose:
            console.print(f"[dim]Executing: {prompt}[/dim]")

        response = await session.execute(prompt)
        if verbose:
            console.print(f"[dim]Response type: {type(response)}, length: {len(response) if response else 0}[/dim]")
        console.print(response)
        console.print()  # Add blank line after output to prevent running into shell prompt

        # Always save session (for debugging/archival)
        context = session.coordinator.get("context")
        messages = getattr(context, "messages", [])
        if messages:
            # Get actual session_id from session (may be auto-generated)
            actual_session_id = session.session_id

            # Get model name from provider
            providers = session.coordinator.get("providers") or {}
            model_name = "unknown"
            for prov_name, prov in providers.items():
                if hasattr(prov, "model"):
                    model_name = f"{prov_name}/{prov.model}"
                    break
                if hasattr(prov, "default_model"):
                    model_name = f"{prov_name}/{prov.default_model}"
                    break

            store = SessionStore()
            metadata = {
                "created_at": datetime.now(UTC).isoformat(),
                "profile": profile_name,
                "model": model_name,
                "turn_count": len([m for m in messages if m.get("role") == "user"]),
            }
            store.save(actual_session_id, messages, metadata)
            if verbose:
                console.print(f"[dim]Session {actual_session_id[:8]}... saved[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)
    finally:
        await session.cleanup()


# Register standalone commands
cli.add_command(collection_group)
cli.add_command(logs_cmd)
cli.add_command(init_cmd)
cli.add_command(profile_group)
cli.add_command(module_group)
cli.add_command(provider_group)
cli.add_command(source_group)
cli.add_command(setup_cmd)  # Keep for backward compat, deprecated

# Note: Agent commands removed (YAGNI - not implemented, agents managed via profiles)
# Agent configuration happens in profiles, agent loading via amplifier-profiles library


async def interactive_chat_with_session(
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str,
    initial_transcript: list[dict],
    profile_name: str = "unknown",
):
    """Run an interactive chat session with restored context."""
    # Create session with resolved config and session_id
    # Session creates its own loader with coordinator (so it can see mounted resolver)
    session = AmplifierSession(config, session_id=session_id)

    # Mount module source resolver (app-layer policy)

    resolver = create_module_resolver()
    await session.coordinator.mount("module-source-resolver", resolver)

    await session.initialize()

    # Register CLI approval provider if approval hook is active (app-layer policy)
    from .approval_provider import CLIApprovalProvider

    register_provider = session.coordinator.get_capability("approval.register_provider")
    if register_provider:
        approval_provider = CLIApprovalProvider(console)
        register_provider(approval_provider)

    # Restore context from transcript if available
    context = session.coordinator.get("context")
    if context and hasattr(context, "set_messages") and initial_transcript:
        await context.set_messages(initial_transcript)
        console.print(f"[dim]Restored {len(initial_transcript)} messages[/dim]")

    # Create command processor
    command_processor = CommandProcessor(session)

    console.print(
        Panel.fit(
            "[bold cyan]Amplifier Interactive Session (Resumed)[/bold cyan]\n"
            "Commands: /help | Multi-line: Ctrl-J | Exit: Ctrl-D",
            border_style="cyan",
        )
    )

    # Create session store for saving
    store = SessionStore()

    # Create prompt session for history and advanced editing
    prompt_session = _create_prompt_session()

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
                        # Normal prompt execution
                        console.print("\n[dim]Processing... (Ctrl-C to abort)[/dim]")

                        # Process runtime @mentions in user input
                        await _process_runtime_mentions(session, data["text"])

                        # Install signal handler to catch Ctrl-C without raising KeyboardInterrupt
                        global _abort_requested
                        _abort_requested = False

                        def sigint_handler(signum, frame):
                            """Handle Ctrl-C by setting abort flag instead of raising exception."""
                            global _abort_requested
                            _abort_requested = True

                        original_handler = signal.signal(signal.SIGINT, sigint_handler)

                        try:
                            # Run execute as cancellable task
                            execute_task = asyncio.create_task(session.execute(data["text"]))

                            # Poll task while checking for abort flag
                            while not execute_task.done():
                                if _abort_requested:
                                    execute_task.cancel()
                                    break
                                await asyncio.sleep(0.05)  # Check every 50ms

                            # Handle result or cancellation
                            try:
                                response = await execute_task
                                console.print("\n" + response)

                                # Save session after each interaction
                                if context and hasattr(context, "get_messages"):
                                    messages = await context.get_messages()
                                    # Extract model from providers config
                                    model_name = "unknown"
                                    if isinstance(config.get("providers"), list) and config["providers"]:
                                        first_provider = config["providers"][0]
                                        if isinstance(first_provider, dict) and "config" in first_provider:
                                            # Check both "model" and "default_model" keys
                                            provider_config = first_provider["config"]
                                            model_name = provider_config.get("model") or provider_config.get(
                                                "default_model", "unknown"
                                            )

                                    metadata = {
                                        "created": datetime.now(UTC).isoformat(),
                                        "profile": profile_name,
                                        "model": model_name,
                                        "turn_count": len([m for m in messages if m.get("role") == "user"]),
                                    }
                                    store.save(session_id, messages, metadata)
                            except asyncio.CancelledError:
                                # Ctrl-C pressed during processing
                                console.print("\n[yellow]Aborted (Ctrl-C)[/yellow]")
                                if command_processor.halted:
                                    command_processor.halted = False
                        finally:
                            # Always restore original signal handler
                            signal.signal(signal.SIGINT, original_handler)
                            _abort_requested = False
                    else:
                        # Handle command
                        result = await command_processor.handle_command(action, data)
                        console.print(f"[cyan]{result}[/cyan]")

            except EOFError:
                # Ctrl-D - graceful exit
                console.print("\n[dim]Exiting...[/dim]")
                break

            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                if verbose:
                    console.print_exception()
    finally:
        await session.cleanup()
        console.print("\n[yellow]Session ended[/yellow]\n")


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
    interactive_chat_with_session=interactive_chat_with_session,
    get_module_search_paths=get_module_search_paths,
)


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
