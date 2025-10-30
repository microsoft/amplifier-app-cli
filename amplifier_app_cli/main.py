"""Amplifier CLI - Command-line interface for the Amplifier platform."""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import cast

import click
from amplifier_core import AmplifierSession
from amplifier_profiles import compile_profile_to_mount_plan
from amplifier_profiles.utils import parse_markdown_body
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .commands.collection import collection as collection_group
from .commands.init import check_first_run
from .commands.init import init_cmd
from .commands.init import prompt_first_run_init
from .commands.logs import logs_cmd
from .commands.provider import provider as provider_group
from .commands.setup import setup_cmd
from .data.profiles import get_system_default_profile
from .key_manager import KeyManager
from .paths import create_agent_loader
from .paths import create_config_manager
from .paths import create_module_resolver
from .paths import create_profile_loader
from .session_store import SessionStore

logger = logging.getLogger(__name__)

console = Console()

# Load API keys from ~/.amplifier/keys.env on startup
# This allows keys saved by 'amplifier setup' to be available
_key_manager = KeyManager()

# Abort flag for ESC-based cancellation
_abort_requested = False


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

        Agents are pre-loaded into session.config["agents"] during session initialization
        by _load_agents_into_session(), so this just formats and displays them.
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


def resolve_app_config(
    cli_config: dict[str, Any] | None = None,
    config_file: str | None = None,
    profile_override: str | None = None,
) -> dict[str, Any]:
    """
    Resolve application configuration with proper precedence.

    Precedence (later overrides earlier):
    1. Default mount plan
    2. Active profile (with inheritance and overlays)
    3. User config (~/.amplifier/config.toml)
    4. Project config (.amplifier/config.toml)
    5. --config file (if provided)
    6. CLI overrides (if provided)
    7. Environment variable expansion

    Args:
        cli_config: Configuration overrides from CLI options
        config_file: Path to explicit config file
        profile_override: Profile name to use (overrides active profile)

    Returns:
        Resolved configuration dictionary
    """
    # 1. Start with minimal default mount plan
    config = {
        "session": {
            "orchestrator": "loop-basic",
            "context": "context-simple",
        },
        "providers": [],
        "tools": [],
        "agents": [],
        "hooks": [],
    }

    # 2. Apply active profile (if set)
    config_manager = create_config_manager()
    loader = create_profile_loader()
    agent_loader = create_agent_loader()  # Create agent loader with CLI search paths

    # Use profile override if provided, otherwise check for active profile
    active_profile_name = profile_override or config_manager.get_active_profile()

    if active_profile_name:
        try:
            # Load profile (library handles inheritance automatically)
            profile = loader.load_profile(active_profile_name)

            # Compile to mount plan (library handles merging)
            # Inject agent_loader so compiler can load agents from collections
            profile_config = compile_profile_to_mount_plan(profile, agent_loader=agent_loader)  # type: ignore[call-arg]

            # Merge into base config
            config = deep_merge(config, profile_config)

        except Exception as e:
            console.print(f"[yellow]Warning: Could not load profile '{active_profile_name}': {e}[/yellow]")

    # 3. Apply settings.yaml overrides (user → project → local)

    merged_settings = config_manager.get_merged_settings()

    # Provider override REPLACES profile providers (policy override)
    if "config" in merged_settings and "providers" in merged_settings["config"]:
        config["providers"] = merged_settings["config"]["providers"]

    # Module overrides merge with profile modules (additive)
    if "modules" in merged_settings:
        settings_overlay: dict[str, Any] = {}
        if "tools" in merged_settings["modules"]:
            settings_overlay["tools"] = merged_settings["modules"]["tools"]
        if "hooks" in merged_settings["modules"]:
            settings_overlay["hooks"] = merged_settings["modules"]["hooks"]
        if "agents" in merged_settings["modules"]:
            settings_overlay["agents"] = merged_settings["modules"]["agents"]

        # Deep merge module additions (uses smart module list merging)
        if settings_overlay:
            config = deep_merge(config, settings_overlay)

    # 4. Apply CLI overrides (already in session format)
    if cli_config:
        config = deep_merge(config, cli_config)

    # 5. Expand environment variables
    config = expand_env_vars(config)

    return config


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two config dicts (overlay takes precedence).

    Special handling for module lists (providers, tools, hooks, agents):
    - Merges lists by module ID instead of replacing
    - Overlay modules override base modules with same ID
    - New modules from overlay are appended
    """
    result = base.copy()

    # Module list keys that need special merging
    module_list_keys = {"providers", "tools", "hooks", "agents"}

    for key, value in overlay.items():
        if key in module_list_keys and key in result:
            # Special handling for module lists
            if isinstance(result[key], list) and isinstance(value, list):
                result[key] = _merge_module_lists(result[key], value)
            else:
                # If either isn't a list, use overlay value
                result[key] = value
        elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def _merge_module_lists(
    base_modules: list[dict[str, Any]], overlay_modules: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge two module lists by module ID.

    Modules with the same 'module' ID are replaced by the overlay version.
    New modules from overlay are appended.
    """
    # Create lookup dictionaries by module ID
    base_by_id = {m.get("module"): m for m in base_modules if isinstance(m, dict) and "module" in m}
    overlay_by_id = {m.get("module"): m for m in overlay_modules if isinstance(m, dict) and "module" in m}

    # Start with base modules, updating with overlay modules
    merged_by_id = base_by_id.copy()
    merged_by_id.update(overlay_by_id)

    # Preserve order: base modules first (potentially updated), then new overlay modules
    result = []
    seen_ids = set()

    # Add base modules (potentially overridden by overlay)
    for module in base_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module["module"]
            if module_id not in seen_ids:
                result.append(merged_by_id[module_id])
                seen_ids.add(module_id)

    # Add any new modules from overlay that weren't in base
    for module in overlay_modules:
        if isinstance(module, dict) and "module" in module:
            module_id = module["module"]
            if module_id not in seen_ids:
                result.append(module)
                seen_ids.add(module_id)

    return result


def expand_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively expand environment variables in config values.

    Replaces ${VAR_NAME} with the value of the environment variable.
    If the variable is not set, it expands to an empty string.

    Args:
        config: Configuration dictionary

    Returns:
        Configuration with expanded environment variables
    """

    def expand_value(value: Any) -> Any:
        if isinstance(value, str):
            # Replace ${VAR} with os.environ.get("VAR", "")
            return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
        if isinstance(value, dict):
            return {k: expand_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand_value(v) for v in value]
        return value

    return expand_value(config)


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
        # Explicitly pass all parameters with defaults (Click doesn't auto-fill when using ctx.invoke)
        ctx.invoke(
            run,
            prompt=None,
            config=None,
            profile=None,
            provider=None,
            model=None,
            mode="chat",
            session_id=None,
            verbose=False,
        )


@cli.command()
@click.argument("prompt", required=False)
@click.option("--config", "-c", type=click.Path(exists=True), help="Configuration file path")
@click.option("--profile", "-P", help="Profile to use for this session")
@click.option("--provider", "-p", default=None, help="LLM provider to use")
@click.option("--model", "-m", help="Model to use (provider-specific)")
@click.option("--mode", type=click.Choice(["chat", "single"]), default="single", help="Execution mode")
@click.option("--session-id", help="Session ID for persistence (generates UUID if not provided)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def run(
    prompt: str | None,
    config: str | None,
    profile: str | None,
    provider: str,
    model: str | None,
    mode: str,
    session_id: str | None,
    verbose: bool,
):
    """Execute a prompt or start an interactive session."""

    # Build CLI config overrides (minimal, only what was explicitly specified)
    cli_overrides = {}
    if provider:
        # Note: this will need transformation in resolve_app_config
        cli_overrides.setdefault("provider", {})["name"] = provider
    if model:
        cli_overrides.setdefault("provider", {})["model"] = model

    # Determine active profile name (including fallback)
    config_manager = create_config_manager()
    active_profile_name = profile or config_manager.get_active_profile() or get_system_default_profile()

    # Check for first run (no API keys) and offer init
    if check_first_run() and not profile and not config and prompt_first_run_init(console):
        # Init was run, reload active profile
        active_profile_name = config_manager.get_active_profile() or get_system_default_profile()

    # Resolve full configuration with proper precedence
    config_data = resolve_app_config(cli_config=cli_overrides, config_file=config, profile_override=active_profile_name)

    # Get module search paths
    search_paths = get_module_search_paths()

    if mode == "chat":
        # Generate session ID if not provided
        if not session_id:
            session_id = str(uuid.uuid4())
            console.print(f"\n[dim]Session ID: {session_id}[/dim]")
        asyncio.run(interactive_chat(config_data, search_paths, verbose, session_id, active_profile_name))
    else:
        if not prompt:
            console.print("[red]Error:[/red] Prompt required in single mode")
            sys.exit(1)
        # Pass session_id and profile_name to execute_single
        asyncio.run(execute_single(prompt, config_data, search_paths, verbose, session_id, active_profile_name))


@cli.group(invoke_without_command=True)
@click.pass_context
def profile(ctx):
    """Manage Amplifier profiles."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@profile.command(name="list")
def profile_list():
    """List all available profiles."""
    loader = create_profile_loader()
    config_manager = create_config_manager()
    profiles = loader.list_profiles()
    active_profile = config_manager.get_active_profile()
    project_default = config_manager.get_project_default()

    if not profiles:
        console.print("[yellow]No profiles found.[/yellow]")
        return

    # Create table like module and agent lists
    table = Table(title="Available Profiles", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Status")

    for profile_name in profiles:
        source = loader.get_profile_source(profile_name)
        source_label = source or "unknown"

        # Build status indicators
        status_parts = []
        if profile_name == active_profile:
            status_parts.append("[bold green]active[/bold green]")
        if profile_name == project_default:
            status_parts.append("[cyan]default[/cyan]")

        status = ", ".join(status_parts) if status_parts else ""

        table.add_row(profile_name, source_label, status)

    console.print(table)


@profile.command(name="current")
def profile_current():
    """Show the currently active profile and its source."""
    config_manager = create_config_manager()

    # Determine active profile and source (inline - ruthless simplicity)
    # Check local first (highest priority)
    local = config_manager._read_yaml(config_manager.paths.local)
    if local and "profile" in local and "active" in local["profile"]:
        profile_name = local["profile"]["active"]
        source = "local"
    else:
        # Check project default
        project_default = config_manager.get_project_default()
        if project_default:
            profile_name = project_default
            source = "default"
        else:
            # Check user settings
            user = config_manager._read_yaml(config_manager.paths.user)
            if user and "profile" in user and "active" in user["profile"]:
                profile_name = user["profile"]["active"]
                source = "user"
            else:
                profile_name = None
                source = None

    if profile_name:
        if source == "local":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from local settings)[/dim]")
            console.print("Source: [cyan].amplifier/settings.local.yaml[/cyan]")
        elif source == "default":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from project default)[/dim]")
            console.print("Source: [cyan].amplifier/settings.yaml[/cyan]")
        elif source == "user":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from user settings)[/dim]")
            console.print("Source: [cyan]~/.amplifier/settings.yaml[/cyan]")
    else:
        console.print("[yellow]No active profile set[/yellow]")
        console.print(f"Using system default: [bold]{get_system_default_profile()}[/bold]")
        console.print("\n[bold]To set a profile:[/bold]")
        console.print("  Local:   [cyan]amplifier profile use <name>[/cyan]")
        console.print("  Project: [cyan]amplifier profile use <name> --project[/cyan]")
        console.print("  Global:  [cyan]amplifier profile use <name> --global[/cyan]")


def build_effective_config_with_sources(chain):
    """Build effective configuration with source tracking.

    Args:
        chain: List of Profile objects (foundation → base → dev order)

    Returns:
        Tuple of (effective_config_dict, sources_dict)
        - effective_config: The merged configuration
        - sources: Dict tracking source profile for each value
    """
    effective_config = {
        "session": {},
        "providers": {},  # Dict keyed by module name
        "tools": {},  # Dict keyed by module name
        "hooks": {},  # Dict keyed by module name
        "agents": {},  # Dict keyed by module name
    }

    sources = {
        "session": {},
        "providers": {},
        "tools": {},
        "hooks": {},
        "agents": {},
    }

    # Process each profile in the chain
    for profile in chain:
        profile_name = profile.profile.name

        # Merge session fields
        if profile.session:
            for field in [
                "orchestrator",
                "context",
                "max_tokens",
                "compact_threshold",
                "auto_compact",
            ]:
                value = getattr(profile.session, field, None)
                if value is not None:
                    # Track previous source if this is an override
                    if field in effective_config["session"] and field in sources["session"]:
                        old_source = sources["session"][field]
                        # Handle nested tuples - just keep the original source
                        if isinstance(old_source, tuple):
                            old_source = old_source[0]
                        sources["session"][field] = (profile_name, old_source)
                    else:
                        sources["session"][field] = profile_name
                    effective_config["session"][field] = value

        # Merge providers
        if profile.providers:
            for provider in profile.providers:
                module_name = provider.module
                if module_name in effective_config["providers"]:
                    old_source = sources["providers"][module_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["providers"][module_name] = (profile_name, old_source)
                else:
                    sources["providers"][module_name] = profile_name
                effective_config["providers"][module_name] = provider

        # Merge tools
        if profile.tools:
            for tool in profile.tools:
                module_name = tool.module
                if module_name in effective_config["tools"]:
                    old_source = sources["tools"][module_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["tools"][module_name] = (profile_name, old_source)
                else:
                    sources["tools"][module_name] = profile_name
                effective_config["tools"][module_name] = tool

        # Merge hooks
        if profile.hooks:
            for hook in profile.hooks:
                module_name = hook.module
                if module_name in effective_config["hooks"]:
                    old_source = sources["hooks"][module_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["hooks"][module_name] = (profile_name, old_source)
                else:
                    sources["hooks"][module_name] = profile_name
                effective_config["hooks"][module_name] = hook

        # Note: agents is now AgentsConfig (with dirs, include, inline fields)
        # Actual agent loading happens in compiler, not here
        # Store agents config for display
        if profile.agents:
            if "agents_config" not in effective_config:
                effective_config["agents_config"] = {}
            effective_config["agents_config"] = profile.agents.model_dump()
            sources["agents_config"] = profile_name

    return effective_config, sources


def render_effective_config(chain, detailed):
    """Render the effective configuration with source annotations.

    Args:
        chain: List of Profile objects (foundation → base → dev order)
        detailed: Whether to show detailed configuration fields
    """
    # Build the effective configuration
    config, sources = build_effective_config_with_sources(chain)

    # Show inheritance chain
    console.print("\n[bold]Inheritance:[/bold]", end=" ")
    chain_names = " → ".join([p.profile.name for p in chain])
    console.print(f"{chain_names}")

    console.print("\n[bold]Effective Configuration:[/bold]\n")

    # Helper to format source annotation
    def format_source(source):
        from rich.markup import escape

        if isinstance(source, list | tuple) and len(source) == 2:
            # This is an override - escape for Rich markup
            current, previous = source
            current_escaped = escape(str(current))
            previous_escaped = escape(str(previous))
            return f" [yellow]\\[from {current_escaped}, overrides {previous_escaped}][/yellow]"
        if source:
            # New value - escape for Rich markup
            source_escaped = escape(str(source))
            return f" [cyan]\\[from {source_escaped}][/cyan]"
        return ""

    # Render Session section
    if config["session"]:
        console.print("[bold]Session:[/bold]")
        for field, value in config["session"].items():
            source = sources["session"].get(field, "")
            source_str = format_source(source)
            console.print(f"  {field}: {value}{source_str}")
        console.print()

    # Render Providers section
    if config["providers"]:
        console.print("[bold]Providers:[/bold]")
        for module_name, provider in config["providers"].items():
            source = sources["providers"].get(module_name, "")
            source_str = format_source(source)
            console.print(f"  {module_name}{source_str}")

            if detailed and provider.config:
                for k, v in provider.config.items():
                    console.print(f"    {k}: {v}")
        console.print()

    # Render Tools section
    if config["tools"]:
        console.print("[bold]Tools:[/bold]")
        for module_name, tool in config["tools"].items():
            source = sources["tools"].get(module_name, "")
            source_str = format_source(source)
            console.print(f"  {module_name}{source_str}")

            if detailed and tool.config:
                for k, v in tool.config.items():
                    console.print(f"    {k}: {v}")
        console.print()

    # Render Hooks section
    if config["hooks"]:
        console.print("[bold]Hooks:[/bold]")
        for module_name, hook in config["hooks"].items():
            source = sources["hooks"].get(module_name, "")
            source_str = format_source(source)
            console.print(f"  {module_name}{source_str}")

            if detailed and hook.config:
                for k, v in hook.config.items():
                    console.print(f"    {k}: {v}")
        console.print()

    # Render Agents section (config for agent discovery/loading)
    if config.get("agents_config"):
        console.print("[bold]Agents:[/bold]")
        agents_cfg = config["agents_config"]
        source = sources.get("agents_config", "")
        source_str = format_source(source)

        if agents_cfg.get("dirs"):
            console.print(f"  dirs: {agents_cfg['dirs']}{source_str}")
        if agents_cfg.get("include"):
            console.print(f"  include: {agents_cfg['include']}{source_str}")
        if agents_cfg.get("inline"):
            inline_count = len(agents_cfg["inline"])
            console.print(f"  inline: {inline_count} agent(s){source_str}")
            if detailed:
                for agent_name in agents_cfg["inline"]:
                    console.print(f"    - {agent_name}")


@profile.command(name="show")
@click.argument("name")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed configuration values")
def profile_show(name: str, detailed: bool):
    """Show details of a specific profile with inheritance chain."""
    loader = create_profile_loader()

    try:
        profile_obj = loader.load_profile(name)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Show profile metadata
    console.print(f"[bold]Profile:[/bold] {profile_obj.profile.name}")
    console.print(f"[bold]Version:[/bold] {profile_obj.profile.version}")
    console.print(f"[bold]Description:[/bold] {profile_obj.profile.description}")

    # Profile already has resolved inheritance - create chain with just this profile
    chain = [profile_obj]

    # Show effective configuration
    render_effective_config(chain, detailed)


@profile.command(name="use")
@click.argument("name")
@click.option("--local", "scope_flag", flag_value="local", help="Set locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Set for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Set globally (all projects)")
def profile_use(name: str, scope_flag: str | None):
    """Set active profile.

    Without scope: Sets locally (.amplifier/settings.local.yaml)
    With --project: Sets project default (.amplifier/settings.yaml)
    With --global: Sets user default (~/.amplifier/settings.yaml)

    Examples:
      amplifier profile use dev
      amplifier profile use production --project
      amplifier profile use base --global
    """
    loader = create_profile_loader()
    config_manager = create_config_manager()

    # Verify profile exists
    try:
        loader.load_profile(name)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Determine scope (default to local)
    scope = scope_flag or "local"

    if scope == "local":
        config_manager.set_active_profile(name)
        console.print(f"[green]✓ Using '{name}' profile locally[/green]")
        console.print("  File: .amplifier/settings.local.yaml")
    elif scope == "project":
        config_manager.set_project_default(name)
        console.print(f"[green]✓ Set '{name}' as project default[/green]")
        console.print("  File: .amplifier/settings.yaml")
        console.print("  [yellow]Remember to commit .amplifier/settings.yaml[/yellow]")
    elif scope == "global":
        # Set in user settings
        from amplifier_config import Scope

        config_manager.update_settings({"profile": {"active": name}}, scope=Scope.USER)
        console.print(f"[green]✓ Set '{name}' globally[/green]")
        console.print("  File: ~/.amplifier/settings.yaml")


@profile.command(name="apply")
@click.argument("name")
def profile_apply(name: str):
    """Set the active profile (alias for 'profile use').

    DEPRECATED: Use 'amplifier profile use <name>' instead.
    """
    loader = create_profile_loader()
    config_manager = create_config_manager()

    # Verify profile exists
    try:
        loader.load_profile(name)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Profile '{name}' not found")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Set active profile
    config_manager.set_active_profile(name)
    console.print(f"[green]✓[/green] Activated profile: {name}")
    console.print("[dim]Note: 'profile apply' is deprecated, use 'profile use' instead[/dim]")


@profile.command(name="reset")
def profile_reset():
    """Clear the local profile choice (falls back to project default if set)."""
    from amplifier_config import Scope

    config_manager = create_config_manager()
    config_manager.clear_active_profile(scope=Scope.LOCAL)

    # Check if there's a project default to fall back to
    project_default = config_manager.get_project_default()
    if project_default:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using project default: [bold]{project_default}[/bold]")
    else:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using system default: [bold]{get_system_default_profile()}[/bold]")


@profile.command(name="default")
@click.option("--set", "set_default", metavar="NAME", help="Set project default profile")
@click.option("--clear", is_flag=True, help="Clear project default profile")
def profile_default(set_default: str | None, clear: bool):
    """
    Manage project default profile.

    Without options, shows the current project default.
    The project default is used when no local profile is set.

    Note: The project default (.amplifier/settings.yaml profile.default) is intended
    to be checked into version control.
    """
    config_manager = create_config_manager()

    if clear:
        config_manager.clear_project_default()
        console.print("[green]✓[/green] Cleared project default profile")
        return

    if set_default:
        # Verify profile exists
        loader = create_profile_loader()
        try:
            loader.load_profile(set_default)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Profile '{set_default}' not found")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        # Set project default
        config_manager.set_project_default(set_default)
        console.print(f"[green]✓[/green] Set project default: {set_default}")
        console.print("\n[yellow]Note:[/yellow] Remember to commit .amplifier/settings.yaml")
        return

    # Show current project default
    project_default = config_manager.get_project_default()
    if project_default:
        console.print(f"[bold green]Project default:[/bold green] {project_default}")
        console.print("Source: [cyan].amplifier/settings.yaml[/cyan]")
    else:
        console.print("[yellow]No project default set[/yellow]")
        console.print(f"System default: [bold]{get_system_default_profile()}[/bold]")
        console.print("\nSet a project default with:")
        console.print("  [cyan]amplifier profile default --set <name>[/cyan]")


def _load_agents_into_session(session: AmplifierSession) -> None:
    """Load agents from agents_config dirs into session.config["agents"] dict.

    The task tool expects agents to be available as a dict in session.config["agents"],
    but agents_config only specifies where to load them from (dirs field).
    This function loads agents from those directories and populates the dict.

    Search order (later overrides earlier):
    1. Bundled agents (amplifier_app_cli/data/agents) - always loaded as fallback
    2. User-specified dirs from agents_config - override bundled

    Args:
        session: AmplifierSession to populate with agents
    """
    from pathlib import Path

    from amplifier_app_cli.agent_config import load_agent_configs_from_directory

    # Start with bundled agents as fallback
    # __file__ is in amplifier_app_cli/ so parent / "data" / "agents" points to bundled agents
    bundled_agents_dir = Path(__file__).parent / "data" / "agents"
    all_agents = {}
    if bundled_agents_dir.exists():
        all_agents = load_agent_configs_from_directory(bundled_agents_dir)
        logger.debug(f"Loaded {len(all_agents)} bundled agents from {bundled_agents_dir}")

    # Get agents_config from session
    agents_config = session.config.get("agents_config", {})

    # Load from user-specified directories (if any) - these override bundled
    if agents_config and agents_config.get("dirs"):
        for agent_dir_str in agents_config.get("dirs", []):
            # Handle relative paths by resolving from current directory
            agent_dir = Path(agent_dir_str).expanduser()
            if not agent_dir.is_absolute():
                agent_dir = Path.cwd() / agent_dir

            if agent_dir.exists():
                agents = load_agent_configs_from_directory(agent_dir)
                all_agents.update(agents)  # Override bundled with user agents
                logger.debug(f"Loaded {len(agents)} agents from {agent_dir}")

    # Apply include filter if specified
    include_filter = agents_config.get("include") if agents_config else None
    if include_filter:
        all_agents = {name: config for name, config in all_agents.items() if name in include_filter}

    # Store loaded agents in session.config["agents"] for task tool access
    session.config["agents"] = all_agents

    logger.info(f"Loaded {len(all_agents)} agents into session config")


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

    # Load agents from agents_config into session.config["agents"] for task tool
    _load_agents_into_session(session)

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
    from .agent_config import load_agent_configs_from_directory

    async def spawn_with_agent_wrapper(agent_name: str, instruction: str, sub_session_id: str):
        """Wrapper for session spawning using coordinator infrastructure."""
        from .session_spawner import spawn_sub_session

        # Get agents from session config
        agents = session.config.get("agents", {})

        # Also try loading from directory if configured
        agent_dir = Path(__file__).parent / "agents" / "core"
        if agent_dir.exists():
            file_agents = load_agent_configs_from_directory(agent_dir)
            agents = {**file_agents, **agents}  # Profile agents override file agents

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


def _get_profile_modules(profile_name: str) -> list[dict[str, Any]]:
    """Extract modules from a profile.

    Args:
        profile_name: Profile name to load

    Returns:
        List of module dicts with: id, type, source, description
    """

    try:
        loader = create_profile_loader()
        profile = loader.load_profile(profile_name)

        modules = []

        # Extract providers
        for provider in profile.providers:
            modules.append(
                {
                    "id": provider.module,
                    "type": "provider",
                    "source": provider.source or "unknown",
                    "description": "",
                }
            )

        # Extract tools
        for tool in profile.tools:
            modules.append({"id": tool.module, "type": "tool", "source": tool.source or "unknown", "description": ""})

        # Extract hooks
        for hook in profile.hooks:
            modules.append({"id": hook.module, "type": "hook", "source": hook.source or "unknown", "description": ""})

        # Extract session modules
        modules.append(
            {
                "id": profile.session.orchestrator.module,
                "type": "orchestrator",
                "source": profile.session.orchestrator.source or "unknown",
                "description": "",
            }
        )

        modules.append(
            {
                "id": profile.session.context.module,
                "type": "context",
                "source": profile.session.context.source or "unknown",
                "description": "",
            }
        )

        return modules

    except Exception as e:
        logger.warning(f"Failed to load profile modules from '{profile_name}': {e}")
        return []


@cli.group(invoke_without_command=True)
@click.pass_context
def module(ctx):
    """Manage Amplifier modules."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@module.command("list")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["all", "orchestrator", "provider", "tool", "agent", "context", "hook"]),
    default="all",
    help="Module type to list",
)
def list_modules(type: str):
    """List installed and profile modules."""
    import asyncio

    from amplifier_core.loader import ModuleLoader

    from .data.profiles import get_system_default_profile

    # Get installed modules
    loader = ModuleLoader()
    modules_info = asyncio.run(loader.discover())
    resolver = create_module_resolver()

    # Display installed modules
    if modules_info:
        table = Table(title="Installed Modules (via entry points)", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Source", style="magenta")
        table.add_column("Origin", style="cyan")
        table.add_column("Description")

        for module_info in modules_info:
            if type != "all" and type != module_info.type:
                continue

            # Try to resolve source and origin
            try:
                source_obj, origin = resolver.resolve_with_layer(module_info.id)
                source_str = str(source_obj)
                # Truncate long sources
                if len(source_str) > 40:
                    source_str = source_str[:37] + "..."
            except Exception:
                source_str = "unknown"
                origin = "unknown"

            table.add_row(module_info.id, module_info.type, source_str, origin, module_info.description)

        console.print(table)
    else:
        console.print("[dim]No installed modules found[/dim]")

    # Get active profile with proper fallback chain
    # Priority: explicit active → project default → system default
    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile() or get_system_default_profile()

    # Determine profile source for display (inline - ruthless simplicity)
    local = config_manager._read_yaml(config_manager.paths.local)
    if local and "profile" in local and "active" in local["profile"]:
        source_label = "active"
    elif config_manager.get_project_default():
        source_label = "project default"
    else:
        source_label = "system default"

    profile_modules = _get_profile_modules(active_profile)

    if profile_modules:
        # Filter by type
        filtered = [m for m in profile_modules if type == "all" or m["type"] == type]

        if filtered:
            console.print()  # Blank line between sections
            table = Table(
                title=f"Profile Modules (from profile '{active_profile}' ({source_label}))",
                show_header=True,
                header_style="bold green",
            )
            table.add_column("Name", style="green")
            table.add_column("Type", style="yellow")
            table.add_column("Source", style="magenta")

            for mod in filtered:
                source_str = str(mod["source"])
                # Truncate long git URLs for display
                if len(source_str) > 60:
                    source_str = source_str[:57] + "..."

                table.add_row(mod["id"], mod["type"], source_str)

            console.print(table)


@module.command("show")
@click.argument("module_name")
def module_show(module_name: str):
    """Show detailed information about a module."""
    import asyncio

    from amplifier_core.loader import ModuleLoader

    from .data.profiles import get_system_default_profile

    # Get active profile with proper fallback chain
    config_manager = create_config_manager()
    active_profile = config_manager.get_active_profile() or get_system_default_profile()

    # Check profile modules first
    profile_modules = _get_profile_modules(active_profile)
    found_in_profile = None
    for mod in profile_modules:
        if mod["id"] == module_name:
            found_in_profile = mod
            break

    if found_in_profile:
        # Display profile module info
        source_str = str(found_in_profile["source"])

        # Determine profile source for display (inline - ruthless simplicity)
        config_manager = create_config_manager()
        local = config_manager._read_yaml(config_manager.paths.local)
        if local and "profile" in local and "active" in local["profile"]:
            origin_label = f"Profile '{active_profile}' (active)"
        elif config_manager.get_project_default():
            origin_label = f"Profile '{active_profile}' (project default)"
        else:
            origin_label = f"Profile '{active_profile}' (system default)"

        panel_content = f"""[bold]Name:[/bold] {found_in_profile["id"]}
[bold]Type:[/bold] {found_in_profile["type"]}
[bold]Source:[/bold] {source_str}
[bold]Origin:[/bold] {origin_label}
[bold]Status:[/bold] Configured (loaded at runtime)"""

        console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="green"))
        return

    # Fall back to installed modules
    loader = ModuleLoader()
    modules_info = asyncio.run(loader.discover())

    # Find the requested module
    found_module = None
    for module_info in modules_info:
        if module_info.id == module_name:
            found_module = module_info
            break

    if not found_module:
        console.print(f"[red]Module '{module_name}' not found[/red]")
        console.print(f"[dim]Checked profile '{active_profile}' and installed packages[/dim]")
        sys.exit(1)

    # Display installed module info
    panel_content = f"""[bold]Name:[/bold] {found_module.id}
[bold]Type:[/bold] {found_module.type}
[bold]Description:[/bold] {found_module.description}
[bold]Mount Point:[/bold] {found_module.mount_point}
[bold]Version:[/bold] {found_module.version}
[bold]Origin:[/bold] Installed (entry point)"""

    console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="cyan"))


@module.command("add")
@click.argument("module_id")
@click.option("--local", "scope_flag", flag_value="local", help="Add locally (just you)")
@click.option("--project", "scope_flag", flag_value="project", help="Add for project (team)")
@click.option("--global", "scope_flag", flag_value="global", help="Add globally (all projects)")
def module_add(module_id: str, scope_flag: str | None):
    """Add module to configuration.

    Examples:
      amplifier module add tool-jupyter --local
      amplifier module add hook-custom --project
    """
    from typing import Literal

    from .module_manager import ModuleManager

    # Determine module type from ID
    if module_id.startswith("tool-"):
        module_type = "tool"
    elif module_id.startswith("hooks-"):
        module_type = "hook"
    elif module_id.startswith("agent-"):
        module_type = "agent"
    else:
        console.print("[red]Error:[/red] Module ID must start with tool-, hooks-, or agent-")
        console.print("\nExamples:")
        console.print("  amplifier module add tool-jupyter")
        console.print("  amplifier module add hooks-logging")
        console.print("  amplifier module add agent-custom")
        return

    # Prompt for scope if not provided
    if not scope_flag:
        console.print("\nAdd for:")
        console.print("  [1] Just you (local)")
        console.print("  [2] Whole team (project)")
        console.print("  [3] All your projects (global)")
        choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")
        scope_map: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
        scope = scope_map[choice]
    else:
        scope = cast(Literal["local", "project", "global"], scope_flag)

    # Add module
    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)

    result = module_mgr.add_module(module_id, module_type, scope)  # type: ignore[arg-type]

    console.print(f"[green]✓ Added {module_id}[/green]")
    console.print(f"  Scope: {scope}")
    console.print(f"  File: {result.file}")


@module.command("remove")
@click.argument("module_id")
@click.option("--local", "scope_flag", flag_value="local", help="Remove from local")
@click.option("--project", "scope_flag", flag_value="project", help="Remove from project")
@click.option("--global", "scope_flag", flag_value="global", help="Remove from global")
def module_remove(module_id: str, scope_flag: str | None):
    """Remove module from configuration.

    Examples:
      amplifier module remove tool-jupyter --local
    """
    from typing import Literal

    from .module_manager import ModuleManager

    # Prompt for scope if not provided
    if not scope_flag:
        console.print("\nRemove from:")
        console.print("  [1] Just you (local)")
        console.print("  [2] Whole team (project)")
        console.print("  [3] All your projects (global)")
        choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="1")
        scope_map: dict[str, Literal["local", "project", "global"]] = {"1": "local", "2": "project", "3": "global"}
        scope = scope_map[choice]
    else:
        scope = cast(Literal["local", "project", "global"], scope_flag)

    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)

    module_mgr.remove_module(module_id, scope)  # type: ignore[arg-type]

    console.print(f"[green]✓ Removed {module_id} from {scope}[/green]")


@module.command("current")
def module_current():
    """Show currently configured modules from settings."""
    from .module_manager import ModuleManager

    config_manager = create_config_manager()
    module_mgr = ModuleManager(config_manager)

    modules = module_mgr.get_current_modules()

    if not modules:
        console.print("[yellow]No modules configured in settings[/yellow]")
        console.print("\nAdd modules with:")
        console.print("  [cyan]amplifier module add <module-id>[/cyan]")
        return

    table = Table(title="Currently Configured Modules (from settings)")
    table.add_column("Module", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Source", style="cyan")

    for mod in modules:
        table.add_row(mod.module_id, mod.module_type, mod.source)

    console.print(table)
    console.print("\n[dim]Note: This shows modules added via settings.[/dim]")
    console.print("[dim]For all installed modules, use: amplifier module list[/dim]")


# Register standalone commands
cli.add_command(collection_group)
cli.add_command(logs_cmd)
cli.add_command(init_cmd)
cli.add_command(provider_group)
cli.add_command(setup_cmd)  # Keep for backward compat, deprecated

# Note: Agent commands removed (YAGNI - not implemented, agents managed via profiles)
# Agent configuration happens in profiles, agent loading via amplifier-profiles library


@cli.group(invoke_without_command=True)
@click.pass_context
def session(ctx):
    """Manage Amplifier sessions."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@session.command(name="list")
@click.option("--limit", "-n", default=20, help="Number of sessions to show")
@click.option("--all-projects", is_flag=True, help="Show sessions from all projects")
@click.option("--project", type=click.Path(), help="Show sessions for specific project path")
def sessions_list(limit: int, all_projects: bool, project: str | None):
    """List recent sessions for current project (or all projects with --all-projects)."""
    from .project_utils import get_project_slug

    if all_projects:
        # List sessions from all projects
        projects_dir = Path.home() / ".amplifier" / "projects"
        if not projects_dir.exists():
            console.print("[yellow]No sessions found.[/yellow]")
            return

        # Collect all sessions across all projects
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

        # Sort by mtime (newest first) and take limit
        all_sessions.sort(key=lambda x: x[3], reverse=True)
        all_sessions = all_sessions[:limit]

        if not all_sessions:
            console.print("[yellow]No sessions found.[/yellow]")
            return

        # Create display table
        table = Table(title="All Sessions (All Projects)", show_header=True, header_style="bold cyan")
        table.add_column("Project", style="magenta")
        table.add_column("Session ID", style="green")
        table.add_column("Last Modified", style="yellow")
        table.add_column("Messages")

        for project_slug, session_id, session_path, mtime in all_sessions:
            modified = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

            # Try to get message count
            transcript_file = session_path / "transcript.jsonl"
            message_count = "?"
            if transcript_file.exists():
                try:
                    with open(transcript_file) as f:
                        message_count = str(sum(1 for _ in f))
                except Exception:
                    pass

            # Truncate project slug for display
            display_slug = project_slug if len(project_slug) <= 30 else project_slug[:27] + "..."
            table.add_row(display_slug, session_id, modified, message_count)

        console.print(table)

    elif project:
        # List sessions for specific project
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

    else:
        # List sessions for current project only (default)
        store = SessionStore()
        project_slug = get_project_slug()
        _display_project_sessions(store, limit, f"Sessions for Current Project ({project_slug})")


def _display_project_sessions(store: SessionStore, limit: int, title: str):
    """Helper to display sessions for a single project."""
    session_ids = store.list_sessions()[:limit]

    if not session_ids:
        console.print("[yellow]No sessions found.[/yellow]")
        return

    # Create display table
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Session ID", style="green")
    table.add_column("Last Modified", style="yellow")
    table.add_column("Messages")
    table.add_column("Profile", style="cyan")

    for session_id in session_ids:
        try:
            # Get session info
            session_path = store.base_dir / session_id
            mtime = session_path.stat().st_mtime
            modified = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

            # Try to get message count
            transcript_file = session_path / "transcript.jsonl"
            message_count = "?"
            if transcript_file.exists():
                with open(transcript_file) as f:
                    message_count = str(sum(1 for _ in f))

            # Try to get profile from metadata
            metadata_file = session_path / "metadata.json"
            profile_name = "?"
            if metadata_file.exists():
                try:
                    import json

                    with open(metadata_file) as f:
                        metadata = json.load(f)
                        profile_name = metadata.get("profile", "?")
                except Exception:
                    pass

            table.add_row(session_id, modified, message_count, profile_name)
        except Exception:
            # Skip sessions we can't read
            continue

    console.print(table)


@session.command(name="show")
@click.argument("session_id")
@click.option("--detailed", "-d", is_flag=True, help="Show full transcript")
def sessions_show(session_id: str, detailed: bool):
    """Show session details."""
    store = SessionStore()

    if not store.exists(session_id):
        console.print(f"[red]Error:[/red] Session '{session_id}' not found")
        sys.exit(1)

    try:
        transcript, metadata = store.load(session_id)

        # Show metadata
        console.print(f"[bold]Session:[/bold] {session_id}")
        # Handle both 'created' and 'created_at' for backward compatibility
        created = metadata.get("created") or metadata.get("created_at", "Unknown")
        console.print(f"[bold]Created:[/bold] {created}")
        console.print(f"[bold]Messages:[/bold] {len(transcript)}")

        if metadata.get("profile"):
            console.print(f"[bold]Profile:[/bold] {metadata['profile']}")
        if metadata.get("model"):
            console.print(f"[bold]Model:[/bold] {metadata['model']}")

        if detailed and transcript:
            console.print("\n[bold]Transcript:[/bold]")
            for msg in transcript:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")

                if role == "user":
                    console.print("\n[bold green]User:[/bold green]")
                    console.print(content[:500] + ("..." if len(content) > 500 else ""))
                elif role == "assistant":
                    console.print("\n[bold blue]Assistant:[/bold blue]")
                    console.print(content[:500] + ("..." if len(content) > 500 else ""))

    except Exception as e:
        console.print(f"[red]Error loading session:[/red] {e}")
        sys.exit(1)


@session.command(name="delete")
@click.argument("session_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def sessions_delete(session_id: str, force: bool):
    """Delete a session."""
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
    except Exception as e:
        console.print(f"[red]Error deleting session:[/red] {e}")
        sys.exit(1)


@session.command(name="resume")
@click.argument("session_id")
@click.option("--config", "-c", type=click.Path(exists=True), help="Configuration file path")
@click.option("--profile", "-P", help="Profile to use for resumed session")
def sessions_resume(session_id: str, config: str | None, profile: str | None):
    """Resume a previous session."""
    store = SessionStore()

    if not store.exists(session_id):
        console.print(f"[red]Error:[/red] Session '{session_id}' not found")
        sys.exit(1)

    try:
        transcript, metadata = store.load(session_id)

        console.print(f"[green]✓[/green] Resuming session: {session_id}")
        console.print(f"  Messages: {len(transcript)}")

        # Resolve configuration (use profile from session if not overridden)
        saved_profile = metadata.get("profile", "unknown")
        if not profile and saved_profile and saved_profile != "unknown":
            profile = saved_profile
            console.print(f"  Using saved profile: {profile}")

        config_data = resolve_app_config(config_file=config, profile_override=profile)

        # Get module search paths
        search_paths = get_module_search_paths()

        # Determine profile name for session tracking
        active_profile = profile if profile else saved_profile

        # Run interactive chat with restored context
        asyncio.run(
            interactive_chat_with_session(config_data, search_paths, False, session_id, transcript, active_profile)
        )

    except Exception as e:
        console.print(f"[red]Error resuming session:[/red] {e}")
        sys.exit(1)


@session.command(name="cleanup")
@click.option("--days", "-d", default=30, help="Delete sessions older than N days")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def sessions_cleanup(days: int, force: bool):
    """Clean up old sessions."""
    store = SessionStore()

    if not force:
        confirm = console.input(f"Delete sessions older than {days} days? [y/N]: ")
        if confirm.lower() != "y":
            console.print("[yellow]Cancelled[/yellow]")
            return

    try:
        removed = store.cleanup_old_sessions(days=days)
        console.print(f"[green]✓[/green] Removed {removed} old session(s)")
    except Exception as e:
        console.print(f"[red]Error during cleanup:[/red] {e}")
        sys.exit(1)


@cli.group(invoke_without_command=True)
@click.pass_context
def source(ctx):
    """Manage module source overrides."""
    if ctx.invoked_subcommand is None:
        click.echo("\n" + ctx.get_help())
        ctx.exit()


@source.command("add")
@click.argument("module_id")
@click.argument("source_uri")
@click.option("--global", "-g", "is_global", is_flag=True, help="Add to user settings (~/.amplifier/)")
def source_add(module_id: str, source_uri: str, is_global: bool):
    """Add module source override.

    Examples:
      amplifier source add tool-bash ~/dev/amplifier-module-tool-bash
      amplifier source add provider-openai git+https://github.com/me/fork@main --global
    """

    from amplifier_config import Scope

    config_manager = create_config_manager()
    scope_enum = Scope.USER if is_global else Scope.PROJECT
    config_manager.add_source_override(module_id, source_uri, scope=scope_enum)

    scope_label = "user" if is_global else "project"
    console.print(f"[green]✓[/green] Added {scope_label} override for '{module_id}'")
    console.print(f"  Source: {source_uri}")
    console.print(f"  File: {config_manager.scope_to_path(scope_enum)}")


@source.command("remove")
@click.argument("module_id")
@click.option("--global", "-g", "is_global", is_flag=True, help="Remove from user settings")
def source_remove(module_id: str, is_global: bool):
    """Remove module source override."""
    from amplifier_config import Scope

    config_manager = create_config_manager()
    scope_enum = Scope.USER if is_global else Scope.PROJECT
    removed = config_manager.remove_source_override(module_id, scope=scope_enum)

    if removed:
        scope_label = "user" if is_global else "project"
        console.print(f"[green]✓[/green] Removed {scope_label} override for '{module_id}'")
    else:
        console.print(f"[yellow]No override found for '{module_id}'[/yellow]")


@source.command("list")
def source_list():
    """List all module source overrides."""

    config_manager = create_config_manager()
    sources = config_manager.get_module_sources()

    if not sources:
        console.print("[yellow]No source overrides configured[/yellow]")
        console.print("\nAdd overrides with:")
        console.print("  [cyan]amplifier source add <module> <uri>[/cyan]")
        return

    # Create table
    table = Table(title="Module Source Overrides", show_header=True, header_style="bold cyan")
    table.add_column("Module", style="green")
    table.add_column("Source", style="magenta")

    for module_id, source_uri in sorted(sources.items()):
        # Truncate long URIs
        display_uri = source_uri if len(source_uri) <= 60 else source_uri[:57] + "..."
        table.add_row(module_id, display_uri)

    console.print(table)


@source.command("show")
@click.argument("module_id")
def source_show(module_id: str):
    """Show resolution path for a module.

    Displays all 6 resolution layers and which one resolved the module.
    """

    resolver = create_module_resolver()

    console.print(f"[bold]Module:[/bold] {module_id}\n")
    console.print("[bold]Resolution Path:[/bold]")

    # Show all 6 layers
    env_key = f"AMPLIFIER_MODULE_{module_id.upper().replace('-', '_')}"
    env_val = os.getenv(env_key)
    console.print(
        f"  1. Environment ({env_key}): " + (f"[green]✓ {env_val}[/green]" if env_val else "[dim]not set[/dim]")
    )

    # Check workspace
    workspace = Path(".amplifier/modules") / module_id
    console.print(
        "  2. Workspace (.amplifier/modules/): "
        + ("[green]✓ found[/green]" if workspace.exists() else "[dim]not found[/dim]")
    )

    # Check project settings

    config_manager = create_config_manager()
    merged_sources = config_manager.get_module_sources()
    project_source = merged_sources.get(module_id) if module_id in merged_sources else None

    console.print(
        "  3. Project (.amplifier/settings.yaml): "
        + (f"[green]✓ {project_source}[/green]" if project_source else "[dim]not found[/dim]")
    )

    console.print("  4. User (~/.amplifier/settings.yaml): [dim](merged with project)[/dim]")
    console.print("  5. Profile: [dim](depends on active profile)[/dim]")
    console.print("  6. Package: [dim](installed packages)[/dim]")

    # Try to resolve
    try:
        source, layer = resolver.resolve_with_layer(module_id)
        console.print(f"\n[bold green]✓ Resolved via:[/bold green] {layer}")
        console.print(f"[bold green]Source:[/bold green] {source}")
    except Exception as e:
        console.print(f"\n[bold red]✗ Failed:[/bold red] {e}")


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

    # Load agents from agents_config into session.config["agents"] for task tool
    _load_agents_into_session(session)

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


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
