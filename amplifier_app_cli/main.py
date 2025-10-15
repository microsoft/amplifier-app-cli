"""Amplifier CLI - Command-line interface for the Amplifier platform."""

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import toml
from amplifier_core import AmplifierSession
from amplifier_core import ModuleLoader
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .commands.logs import logs_cmd
from .logging_setup import init_json_logging
from .profiles import ProfileLoader
from .profiles import ProfileManager
from .session_store import SessionStore

logger = logging.getLogger(__name__)

# Initialize JSON logging early
init_json_logging()

console = Console()


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
        """Save current transcript."""
        # Default filename if not provided
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"transcript_{timestamp}.json"

        # Get messages from context
        context = self.session.coordinator.get("context")
        if context and hasattr(context, "get_messages"):
            messages = await context.get_messages()

            # Save to file
            path = Path(".amplifier/transcripts") / filename
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w") as f:
                json.dump(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "messages": messages,
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
            lines.append(f"  {name:<20} - {desc}")

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
    import tomli

    from amplifier_app_cli.profiles import ProfileLoader
    from amplifier_app_cli.profiles import ProfileManager
    from amplifier_app_cli.profiles import compile_profile_to_mount_plan

    # Helper to safely load TOML
    def load_toml_safe(path: Path) -> dict[str, Any]:
        try:
            with open(path, "rb") as f:
                return tomli.load(f)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not load {path}: {e}[/yellow]")
            return {}

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
    manager = ProfileManager()
    loader = ProfileLoader()

    # Use profile override if provided, otherwise check for active profile
    active_profile_name = profile_override or manager.get_active_profile()

    if active_profile_name:
        try:
            # Load base profile
            base_profile = loader.load_profile(active_profile_name)

            # Resolve inheritance chain
            inheritance_chain = loader.resolve_inheritance(base_profile)

            # Start with the base (bottom of chain)
            if inheritance_chain:
                profile_config = compile_profile_to_mount_plan(inheritance_chain[0], [])

                # Merge each parent in the chain
                for parent_profile in inheritance_chain[1:]:
                    profile_config = deep_merge(profile_config, compile_profile_to_mount_plan(parent_profile, []))

                # Load and apply overlays for the final profile
                overlays = loader.load_overlays(active_profile_name)
                if overlays:
                    for overlay in overlays:
                        overlay_config = compile_profile_to_mount_plan(overlay, [])
                        profile_config = deep_merge(profile_config, overlay_config)

                config = deep_merge(config, profile_config)

        except Exception as e:
            console.print(f"[yellow]Warning: Could not load profile '{active_profile_name}': {e}[/yellow]")

    # 3. Merge user config (~/.amplifier/config.toml)
    user_path = Path.home() / ".amplifier" / "config.toml"
    if user_path.exists():
        user_config = load_toml_safe(user_path)
        if user_config:
            # Transform and merge
            user_config = transform_toml_to_session_config(user_config)
            config = deep_merge(config, user_config)

    # 4. Merge project config (.amplifier/config.toml)
    project_path = Path(".amplifier") / "config.toml"
    if project_path.exists():
        project_config = load_toml_safe(project_path)
        if project_config:
            # Transform and merge
            project_config = transform_toml_to_session_config(project_config)
            config = deep_merge(config, project_config)

    # 5. Merge --config file if provided
    if config_file:
        explicit_path = Path(config_file)
        if explicit_path.exists():
            explicit_config = load_toml_safe(explicit_path)
            if explicit_config:
                # Transform and merge
                explicit_config = transform_toml_to_session_config(explicit_config)

                # OVERRIDE SEMANTICS: If explicit config specifies providers,
                # clear any accumulated providers so they get replaced, not merged
                if "providers" in explicit_config:
                    config.pop("providers", None)

                config = deep_merge(config, explicit_config)
        else:
            console.print(f"[red]Error: Config file not found: {config_file}[/red]")
            sys.exit(1)

    # 6. Apply CLI overrides (already in session format)
    if cli_config:
        config = deep_merge(config, cli_config)

    # 7. Expand environment variables
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


@click.group()
@click.version_option()
def cli():
    """Amplifier - AI-powered modular development platform."""
    pass


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

    # Resolve full configuration with proper precedence
    config_data = resolve_app_config(cli_config=cli_overrides, config_file=config, profile_override=profile)

    # Get module search paths
    search_paths = get_module_search_paths()

    # Determine active profile name for session metadata
    manager = ProfileManager()
    active_profile_name = profile or manager.get_active_profile() or "default"

    if mode == "chat":
        # Generate session ID if not provided
        if not session_id:
            session_id = str(uuid.uuid4())
            console.print(f"[dim]Session ID: {session_id}[/dim]")
        asyncio.run(interactive_chat(config_data, search_paths, verbose, session_id, active_profile_name))
    else:
        if not prompt:
            console.print("[red]Error:[/red] Prompt required in single mode")
            sys.exit(1)
        # Pass session_id and profile_name to execute_single
        asyncio.run(execute_single(prompt, config_data, search_paths, verbose, session_id, active_profile_name))


@cli.group()
def profile():
    """Manage Amplifier profiles."""
    pass


@profile.command(name="list")
def profile_list():
    """List all available profiles."""
    loader = ProfileLoader()
    manager = ProfileManager()
    profiles = loader.list_profiles()
    active_profile = manager.get_active_profile()

    if not profiles:
        console.print("[yellow]No profiles found.[/yellow]")
        return

    console.print("[bold]Available Profiles:[/bold]\n")
    for profile_name in profiles:
        source = loader.get_profile_source(profile_name)
        if source is None:
            source_label = ""
        else:
            source_label = {
                "official": "[blue](official)[/blue]",
                "team": "[green](team)[/green]",
                "user": "[cyan](user)[/cyan]",
            }.get(source, "")

        # Mark active profile
        if profile_name == active_profile:
            console.print(f"  ★ [bold green]{profile_name}[/bold green] {source_label} [dim](active)[/dim]")
        else:
            console.print(f"  • {profile_name} {source_label}")


@profile.command(name="current")
def profile_current():
    """Show the currently active profile and its source."""
    manager = ProfileManager()
    profile_name, source = manager.get_profile_source()

    if profile_name:
        if source == "local":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from local choice)[/dim]")
            console.print("Source: [cyan].amplifier/profile[/cyan]")
        elif source == "default":
            console.print(f"[bold green]Active profile:[/bold green] {profile_name} [dim](from project default)[/dim]")
            console.print("Source: [cyan].amplifier/default-profile[/cyan]")
    else:
        console.print("[yellow]No active profile set[/yellow]")
        console.print("Using hardcoded defaults")
        console.print("\n[bold]To set a profile:[/bold]")
        console.print("  Local:   [cyan]amplifier profile apply <name>[/cyan]")
        console.print("  Project: [cyan]amplifier profile default --set <name>[/cyan]")


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

        # Merge agents (dict of config overlays)
        if profile.agents:
            for agent_name, agent_config in profile.agents.items():
                if agent_name in effective_config["agents"]:
                    old_source = sources["agents"][agent_name]
                    if isinstance(old_source, tuple):
                        old_source = old_source[0]
                    sources["agents"][agent_name] = (profile_name, old_source)
                else:
                    sources["agents"][agent_name] = profile_name
                effective_config["agents"][agent_name] = agent_config

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

    # Render Agents section (config overlays for sub-sessions)
    if config["agents"]:
        console.print("[bold]Agents:[/bold]")
        for agent_name, agent_config in config["agents"].items():
            source = sources["agents"].get(agent_name, "")
            source_str = format_source(source)
            # Show description if available
            description = agent_config.get("description", "")
            desc_str = f" - {description}" if description else ""
            console.print(f"  {agent_name}{desc_str}{source_str}")

            if detailed:
                # Show system instruction if present
                if "system" in agent_config and "instruction" in agent_config["system"]:
                    instruction = agent_config["system"]["instruction"]
                    # Truncate long instructions
                    if len(instruction) > 80:
                        instruction = instruction[:77] + "..."
                    console.print(f"    instruction: {instruction}")
                # Show provider/tool counts
                if "providers" in agent_config:
                    console.print(f"    providers: {len(agent_config['providers'])}")
                if "tools" in agent_config:
                    console.print(f"    tools: {len(agent_config['tools'])}")


@profile.command(name="show")
@click.argument("name")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed configuration values")
def profile_show(name: str, detailed: bool):
    """Show details of a specific profile with inheritance chain."""
    loader = ProfileLoader()

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

    # Get inheritance chain
    chain = loader.resolve_inheritance(profile_obj)

    # Show effective configuration
    render_effective_config(chain, detailed)


@profile.command(name="apply")
@click.argument("name")
def profile_apply(name: str):
    """Set the active profile for the current project."""
    loader = ProfileLoader()
    manager = ProfileManager()

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
    manager.set_active_profile(name)
    console.print(f"[green]✓[/green] Activated profile: {name}")


@profile.command(name="reset")
def profile_reset():
    """Clear the local profile choice (falls back to project default if set)."""
    manager = ProfileManager()
    manager.clear_active_profile()

    # Check if there's a project default to fall back to
    project_default = manager.get_project_default()
    if project_default:
        console.print("[green]✓[/green] Cleared local profile")
        console.print(f"Now using project default: [bold]{project_default}[/bold]")
    else:
        console.print("[green]✓[/green] Cleared local profile")
        console.print("Now using hardcoded defaults")


@profile.command(name="default")
@click.option("--set", "set_default", metavar="NAME", help="Set project default profile")
@click.option("--clear", is_flag=True, help="Clear project default profile")
def profile_default(set_default: str | None, clear: bool):
    """
    Manage project default profile.

    Without options, shows the current project default.
    The project default is used when no local profile is set.

    Note: The project default file (.amplifier/default-profile) is intended
    to be checked into version control.
    """
    manager = ProfileManager()

    if clear:
        manager.clear_project_default()
        console.print("[green]✓[/green] Cleared project default profile")
        return

    if set_default:
        # Verify profile exists
        loader = ProfileLoader()
        try:
            loader.load_profile(set_default)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Profile '{set_default}' not found")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        # Set project default
        manager.set_project_default(set_default)
        console.print(f"[green]✓[/green] Set project default: {set_default}")
        console.print("\n[yellow]Note:[/yellow] Remember to commit .amplifier/default-profile")
        return

    # Show current project default
    project_default = manager.get_project_default()
    if project_default:
        console.print(f"[bold green]Project default:[/bold green] {project_default}")
        console.print("Source: [cyan].amplifier/default-profile[/cyan]")
    else:
        console.print("[yellow]No project default set[/yellow]")
        console.print("\nSet a project default with:")
        console.print("  [cyan]amplifier profile default --set <name>[/cyan]")


def transform_toml_to_session_config(toml_config: dict[str, Any]) -> dict[str, Any]:
    """
    Transform TOML config format to AmplifierSession expected format.

    This transforms user-friendly TOML into the internal session config structure.
    Does NOT provide defaults - that's done by resolve_app_config().

    TOML format:
        [provider]
        name = "anthropic"
        model = "claude-sonnet-4-5"

        [modules]
        orchestrator = "loop-basic"
        context = "context-simple"
        tools = ["filesystem", "bash"]

    AmplifierSession format:
        {
            "session": {
                "orchestrator": "loop-basic",
                "context": "context-simple"
            },
            "providers": [
                {
                    "module": "provider-anthropic",
                    "config": {"model": "claude-sonnet-4-5"}
                }
            ],
            "tools": [
                {"module": "tool-filesystem"},
                {"module": "tool-bash"}
            ]
        }
    """
    # Start with empty structure
    session_config: dict[str, Any] = {}

    # Transform orchestrator and context from modules section
    if "modules" in toml_config:
        if "orchestrator" in toml_config["modules"] or "context" in toml_config["modules"]:
            session_config["session"] = {}
            if "orchestrator" in toml_config["modules"]:
                session_config["session"]["orchestrator"] = toml_config["modules"]["orchestrator"]
            if "context" in toml_config["modules"]:
                session_config["session"]["context"] = toml_config["modules"]["context"]

        # Transform tools list
        if "tools" in toml_config["modules"]:
            tools = toml_config["modules"]["tools"]
            if isinstance(tools, list):
                tool_configs = []
                for tool in tools:
                    tool_module = {"module": f"tool-{tool}"}
                    # Check for tool-specific config in [tools.X] sections
                    if "tools" in toml_config and tool in toml_config["tools"]:
                        tool_module["config"] = toml_config["tools"][tool]
                    tool_configs.append(tool_module)
                session_config["tools"] = tool_configs

        # Transform hooks from modules section
        if "hooks" in toml_config["modules"]:
            hooks = toml_config["modules"]["hooks"]
            if isinstance(hooks, list):
                session_config["hooks"] = hooks

    # Transform provider configuration
    if "provider" in toml_config:
        provider = toml_config["provider"]
        provider_name = provider.get("name", "mock")

        # Build provider config
        provider_config: dict[str, Any] = {"module": f"provider-{provider_name}"}

        # Add provider-specific config
        config_dict: dict[str, Any] = {}
        if "model" in provider:
            config_dict["model"] = provider["model"]

        # Handle nested provider.config section - merge it into the top level
        if "config" in provider and isinstance(provider["config"], dict):
            config_dict.update(provider["config"])

        # Add any other provider settings (excluding name, model, and config)
        extra_config = {k: v for k, v in provider.items() if k not in ["name", "model", "config"]}
        if extra_config:
            config_dict.update(extra_config)

        if config_dict:
            provider_config["config"] = config_dict

        session_config["providers"] = [provider_config]

    # Transform hooks from top level (preferred location)
    if "hooks" in toml_config:
        hooks = toml_config["hooks"]
        if isinstance(hooks, list):
            session_config["hooks"] = hooks

    # Copy session settings if present
    if "session" in toml_config and any(
        key in toml_config["session"] for key in ["max_tokens", "compact_threshold", "auto_compact"]
    ):
        # Context config specifically
        if "context" not in session_config:
            session_config["context"] = {}
        if "config" not in session_config["context"]:
            session_config["context"]["config"] = {}

        if "max_tokens" in toml_config["session"]:
            session_config["context"]["config"]["max_tokens"] = toml_config["session"]["max_tokens"]
        if "compact_threshold" in toml_config["session"]:
            session_config["context"]["config"]["compact_threshold"] = toml_config["session"]["compact_threshold"]
        if "auto_compact" in toml_config["session"]:
            session_config["context"]["config"]["auto_compact"] = toml_config["session"]["auto_compact"]

    # Transform agents if present
    if "agents" in toml_config:
        agents = toml_config["agents"]
        if isinstance(agents, list):
            session_config["agents"] = [{"module": f"agent-{agent}"} for agent in agents]

    # Transform hooks if present
    if "hooks" in toml_config:
        hooks = toml_config["hooks"]
        # Handle direct array format: hooks = [{module = "hooks-logging", config = {...}}]
        if isinstance(hooks, list):
            session_config["hooks"] = hooks
        # Handle nested format: hooks = {enabled = ["backup", "logging"]}
        elif "enabled" in hooks and isinstance(hooks["enabled"], list):
            session_config["hooks"] = [{"module": f"hooks-{hook}"} for hook in hooks["enabled"]]

    return session_config


async def interactive_chat(
    config: dict, search_paths: list[Path], verbose: bool, session_id: str | None = None, profile_name: str = "default"
):
    """Run an interactive chat session."""
    # Generate session ID if not provided
    if not session_id:
        session_id = str(uuid.uuid4())

    # Create loader with search paths
    loader = ModuleLoader(search_paths=search_paths if search_paths else None)

    # Create session with resolved config, loader, and session_id
    session = AmplifierSession(config, loader=loader, session_id=session_id)
    await session.initialize()

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
            "[bold cyan]Amplifier Interactive Session[/bold cyan]\n"
            "Type '/help' for commands, 'exit' or press Ctrl+C to quit",
            border_style="cyan",
        )
    )

    try:
        while True:
            try:
                prompt = console.input("\n[bold green]>[/bold green] ")
                if prompt.lower() in ["exit", "quit"]:
                    break

                if prompt.strip():
                    # Process input for commands
                    action, data = command_processor.process_input(prompt)

                    if action == "prompt":
                        # Normal prompt execution
                        console.print("[dim]Processing...[/dim]")
                        response = await session.execute(data["text"])
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
                    else:
                        # Handle command
                        result = await command_processor.handle_command(action, data)
                        console.print(f"[cyan]{result}[/cyan]")

            except KeyboardInterrupt:
                # Allow /stop command or Ctrl+C to interrupt execution
                if command_processor.halted:
                    command_processor.halted = False
                    console.print("\n[yellow]Execution stopped[/yellow]")
                else:
                    break
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                if verbose:
                    console.print_exception()
    finally:
        await session.cleanup()
        console.print("\n[yellow]Session ended[/yellow]")


async def execute_single(
    prompt: str,
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str | None = None,
    profile_name: str = "unknown",
):
    """Execute a single prompt and exit."""
    # Create loader with search paths
    loader = ModuleLoader(search_paths=search_paths if search_paths else None)

    # Create session with resolved config, loader, and optional session_id
    session = AmplifierSession(config, loader=loader, session_id=session_id)

    try:
        await session.initialize()

        # Register CLI approval provider if approval hook is active (app-layer policy)
        from .approval_provider import CLIApprovalProvider

        register_provider = session.coordinator.get_capability("approval.register_provider")
        if register_provider:
            approval_provider = CLIApprovalProvider(console)
            register_provider(approval_provider)

        if verbose:
            console.print(f"[dim]Executing: {prompt}[/dim]")

        response = await session.execute(prompt)
        if verbose:
            console.print(f"[dim]Response type: {type(response)}, length: {len(response) if response else 0}[/dim]")
        console.print(response)

        # Save session if session_id was explicitly provided
        if session_id:
            context = session.coordinator.get("context")
            messages = getattr(context, "messages", [])
            if messages:
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

                # Use profile name passed from caller

                store = SessionStore()
                metadata = {
                    "created_at": datetime.now(UTC).isoformat(),
                    "profile": profile_name,
                    "model": model_name,
                    "turn_count": len([m for m in messages if m.get("role") == "user"]),
                }
                store.save(session_id, messages, metadata)
                if verbose:
                    console.print(f"[dim]Session {session_id[:8]}... saved[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)
    finally:
        await session.cleanup()


@cli.group()
def module():
    """Manage Amplifier modules."""
    pass


@module.command("list")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["all", "orchestrator", "provider", "tool", "agent", "context", "hook"]),
    default="all",
    help="Module type to list",
)
def list_modules(type: str):
    """List installed modules."""
    import asyncio

    # Create a loader to discover modules
    loader = ModuleLoader()

    # Get all discovered modules
    modules_info = asyncio.run(loader.discover())

    # Create display table
    table = Table(title="Installed Modules", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Mount Point")
    table.add_column("Description")

    # Filter and display modules
    for module_info in modules_info:
        if type != "all" and type != module_info.type:
            continue

        table.add_row(module_info.id, module_info.type, module_info.mount_point, module_info.description)

    console.print(table)


@module.command("info")
@click.argument("module_name")
def module_info(module_name: str):
    """Show detailed information about a module."""
    import asyncio

    # Create a loader to discover modules
    loader = ModuleLoader()

    # Get all discovered modules
    modules_info = asyncio.run(loader.discover())

    # Find the requested module
    found_module = None
    for module_info in modules_info:
        if module_info.id == module_name:
            found_module = module_info
            break

    if not found_module:
        console.print(f"[red]Module '{module_name}' not found[/red]")
        sys.exit(1)

    # Display module info
    panel_content = f"""[bold]Name:[/bold] {found_module.id}
[bold]Type:[/bold] {found_module.type}
[bold]Description:[/bold] {found_module.description}
[bold]Mount Point:[/bold] {found_module.mount_point}
[bold]Version:[/bold] {found_module.version}"""

    console.print(Panel(panel_content, title=f"Module: {module_name}", border_style="cyan"))


@cli.command()
@click.option("--output", "-o", type=click.Path(), help="Output path for configuration")
def init(output: str | None):
    """Initialize a new Amplifier configuration."""
    config_template = {
        "provider": {"name": "mock", "model": "mock-model"},
        "modules": {"orchestrator": "loop-basic", "context": "context-simple"},
        "hooks": {"enabled": ["backup"]},
        "session": {"max_tokens": 100000, "auto_compact": True, "compact_threshold": 0.9},
    }

    output_path = Path(output if output else "amplifier.toml")

    with open(output_path, "w") as f:
        toml.dump(config_template, f)

    console.print(f"[green]✓[/green] Configuration created at {output_path}")

    # Show the created config
    with open(output_path) as f:
        syntax = Syntax(f.read(), "toml", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title="Configuration", border_style="green"))


# Register logs command
cli.add_command(logs_cmd)


@cli.group()
def sessions():
    """Manage Amplifier sessions."""
    pass


@sessions.command(name="list")
@click.option("--limit", "-n", default=20, help="Number of sessions to show")
def sessions_list(limit: int):
    """List recent sessions."""
    store = SessionStore()
    session_ids = store.list_sessions()[:limit]

    if not session_ids:
        console.print("[yellow]No sessions found.[/yellow]")
        return

    # Create display table
    table = Table(title="Recent Sessions", show_header=True, header_style="bold cyan")
    table.add_column("Session ID", style="green")
    table.add_column("Last Modified", style="yellow")
    table.add_column("Messages")

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

            table.add_row(session_id, modified, message_count)
        except Exception:
            # Skip sessions we can't read
            continue

    console.print(table)


@sessions.command(name="show")
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
        console.print(f"[bold]Created:[/bold] {metadata.get('created', 'Unknown')}")
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


@sessions.command(name="delete")
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


@sessions.command(name="resume")
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


@sessions.command(name="cleanup")
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


async def interactive_chat_with_session(
    config: dict,
    search_paths: list[Path],
    verbose: bool,
    session_id: str,
    initial_transcript: list[dict],
    profile_name: str = "unknown",
):
    """Run an interactive chat session with restored context."""
    # Create loader with search paths
    loader = ModuleLoader(search_paths=search_paths if search_paths else None)

    # Create session with resolved config, loader, and session_id
    session = AmplifierSession(config, loader=loader, session_id=session_id)
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
            "Type '/help' for commands, 'exit' or press Ctrl+C to quit",
            border_style="cyan",
        )
    )

    # Create session store for saving
    store = SessionStore()

    try:
        while True:
            try:
                prompt = console.input("\n[bold green]>[/bold green] ")
                if prompt.lower() in ["exit", "quit"]:
                    break

                if prompt.strip():
                    # Process input for commands
                    action, data = command_processor.process_input(prompt)

                    if action == "prompt":
                        # Normal prompt execution
                        console.print("[dim]Processing...[/dim]")
                        response = await session.execute(data["text"])
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
                    else:
                        # Handle command
                        result = await command_processor.handle_command(action, data)
                        console.print(f"[cyan]{result}[/cyan]")

            except KeyboardInterrupt:
                # Allow /stop command or Ctrl+C to interrupt execution
                if command_processor.halted:
                    command_processor.halted = False
                    console.print("\n[yellow]Execution stopped[/yellow]")
                else:
                    break
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                if verbose:
                    console.print_exception()
    finally:
        await session.cleanup()
        console.print("\n[yellow]Session ended[/yellow]")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
