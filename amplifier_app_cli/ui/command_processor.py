"""Registry-backed interactive command processor."""

from __future__ import annotations

import logging
from collections.abc import Mapping
import inspect
from typing import Any

from amplifier_core import AmplifierSession

from amplifier_app_cli.runtime.session_state import coordinator_session_state

from .command_admin import CommandAdminMixin
from .command_catalog import BUILTIN_COMMAND_REGISTRY
from .command_config import CommandConfigMixin
from .command_config_dashboard import CommandConfigDashboardMixin
from .command_modes import CommandModeMixin
from .command_registry import CommandOwner, CommandRegistry, CommandSource, CommandSpec
from .command_registry import compose_command_registry
from .command_sessions import CommandSessionMixin
from .dashboard_renderer import DashboardRenderer
from .dashboard_renderer import _redact_value as _dr_redact_value
from .interaction_runtime_state import interaction_state_for
from .mode_profiles import ModeProfileRegistry
from .session_commands import SessionCommandResult

logger = logging.getLogger(__name__)


class CommandProcessor(
    CommandModeMixin,
    CommandSessionMixin,
    CommandConfigMixin,
    CommandConfigDashboardMixin,
    CommandAdminMixin,
):
    """Process slash commands and special directives."""

    BUILTIN_MODE_PROFILES = ModeProfileRegistry()
    BUILTIN_MODE_NAMES = BUILTIN_MODE_PROFILES.names

    COMMAND_REGISTRY = BUILTIN_COMMAND_REGISTRY
    COMMANDS = COMMAND_REGISTRY.legacy_metadata()

    # Kept for backward compatibility; dashboard_renderer owns the policy.
    _SENSITIVE_KEY_PATTERNS = ("key", "token", "secret", "password", "api_key")

    def _render_config_tree(
        self, console: Any, cfg: dict, indent: str, *, dim: bool = False
    ) -> None:
        """Render a config dict as an indented YAML-like tree (delegates to DashboardRenderer)."""
        DashboardRenderer(console).render_config_tree(cfg, indent, dim=dim)

    def _print_wrapped_items(
        self,
        console: Any,
        label: str,
        items: list,
        indent: str = "        ",
        max_width: int = 78,
        dim: bool = True,
    ) -> None:
        """Print ``label: item1, item2, ...`` with continuation (delegates to DashboardRenderer)."""
        DashboardRenderer(console).print_wrapped_items(
            label, items, indent, max_width, dim
        )

    @staticmethod
    def _redact_value(key: str, value: Any) -> Any:
        """Redact a config value if the key is sensitive and value is long enough.

        Delegates to the module-level function in dashboard_renderer.
        Kept as a static method on CommandProcessor for backward compatibility.
        """
        return _dr_redact_value(key, value)

    def __init__(
        self,
        session: AmplifierSession,
        bundle_name: str = "unknown",
        *,
        mcp_prompts: tuple[tuple[str, str, str], ...] = (),
    ):
        self.session = session
        self.bundle_name = bundle_name
        self.configurator: Any = None
        self._mcp_prompts = mcp_prompts
        # Dynamic commands belong to this session. Never put discovered
        # shortcuts on the class: a later session may use a different bundle.
        self.MODE_SHORTCUTS: dict[str, Any] = {
            name: name for name in self.BUILTIN_MODE_NAMES
        }
        self.SKILL_SHORTCUTS: dict[str, Any] = {}
        interaction_state_for(
            self.session.coordinator,
            ui_modes=self.BUILTIN_MODE_NAMES,
        )
        # Populate mode shortcuts from discovery (if available)
        self._populate_mode_shortcuts()
        # Populate skill shortcuts from discovery (if available)
        self._populate_skill_shortcuts()
        self.command_registry = self._refresh_command_registry()

    def _refresh_command_registry(self) -> CommandRegistry:
        self.command_registry = compose_command_registry(
            self.COMMAND_REGISTRY,
            mode_shortcuts=self.MODE_SHORTCUTS,
            skill_shortcuts=self.SKILL_SHORTCUTS,
            mcp_prompts=self._mcp_prompts,
        )
        return self.command_registry

    def _populate_mode_shortcuts(self) -> None:
        """Populate MODE_SHORTCUTS from mode discovery."""
        discovery = coordinator_session_state(self.session.coordinator).get(
            "mode_discovery"
        )
        if discovery and hasattr(discovery, "get_shortcuts"):
            shortcuts = discovery.get_shortcuts()
            if isinstance(shortcuts, Mapping):
                self.MODE_SHORTCUTS.update(dict(shortcuts))

    def _populate_skill_shortcuts(self) -> None:
        """Populate SKILL_SHORTCUTS from skills discovery."""
        discovery = self.session.coordinator.get_capability("skills_discovery")
        if discovery and hasattr(discovery, "get_shortcuts"):
            shortcuts = discovery.get_shortcuts()
            if isinstance(shortcuts, Mapping):
                self.SKILL_SHORTCUTS.update(
                    {
                        name: dict(metadata)
                        if isinstance(metadata, Mapping)
                        else metadata
                        for name, metadata in shortcuts.items()
                    }
                )

    def _get_mode_completion_names(self) -> list[str]:
        """Return mode names available for REPL completion."""
        discovery = coordinator_session_state(self.session.coordinator).get(
            "mode_discovery"
        )
        if not discovery or not hasattr(discovery, "list_modes"):
            return sorted(self.MODE_SHORTCUTS.keys())

        try:
            return sorted(
                {
                    *self.BUILTIN_MODE_NAMES,
                    *(item[0] for item in discovery.list_modes() if item),
                }
            )
        except Exception:
            logger.debug("Failed to load mode completion names", exc_info=True)
            return sorted(self.MODE_SHORTCUTS.keys())

    def _get_skill_completion_names(self) -> list[str]:
        """Return skill names available for REPL completion."""
        discovery = self.session.coordinator.get_capability("skills_discovery")
        if not discovery or not hasattr(discovery, "list_skills"):
            return []

        try:
            return sorted({item[0] for item in discovery.list_skills() if item})
        except Exception:
            logger.debug("Failed to load skill completion names", exc_info=True)
            return []

    def process_input(self, user_input: str) -> tuple[str, dict[str, Any]]:
        """
        Process user input and extract commands.

        Returns:
            (action, data) tuple
        """
        # Check for commands
        if user_input.startswith("/"):
            self._refresh_command_registry()
            parts = user_input.split(maxsplit=1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            spec = self.command_registry.resolve(command)
            if spec is not None and spec.source is CommandSource.MODE:
                shortcut_name = spec.target or command[1:]
                data = {"args": shortcut_name, "command": command}
                trailing = args.strip()
                if trailing:
                    if trailing.lower() in ("on", "off"):
                        data["args"] = f"{shortcut_name} {trailing}"
                    else:
                        data["args"] = f"{shortcut_name} on"
                        data["trailing_prompt"] = trailing
                return spec.action, data

            if spec is not None and spec.source in {
                CommandSource.SKILL,
                CommandSource.BUNDLE,
                CommandSource.USER,
            }:
                skill_commands, skill_chain, chain_arguments = self._parse_skill_chain(
                    user_input
                )
                if len(skill_chain) > 1:
                    return (
                        "load_skill_chain",
                        {
                            "skill_commands": skill_commands,
                            "skill_names": skill_chain,
                            "arguments": chain_arguments,
                            "command": command,
                        },
                    )
                return (
                    spec.action,
                    {
                        "skill_name": spec.target or command[1:],
                        "arguments": args.strip(),
                        "command": command,
                    },
                )

            if spec is not None:
                data = {"args": args, "command": command}
                # For mode commands, extract trailing prompt text
                if spec.action == "handle_mode" and args.strip():
                    mode_args, trailing = self._split_mode_trailing(args)
                    data["args"] = mode_args
                    if trailing:
                        data["trailing_prompt"] = trailing
                elif spec.action == "load_skill":
                    skill_parts = args.strip().split(maxsplit=1)
                    data["skill_name"] = skill_parts[0] if skill_parts else ""
                    data["arguments"] = skill_parts[1] if len(skill_parts) > 1 else ""
                return spec.action, data

            session_commands = self.session.coordinator.get_capability(
                "ui.session_commands"
            )
            if (
                session_commands is not None
                and session_commands.supports(command) is True
            ):
                return "session_ui", {"args": args, "command": command}

            return "unknown_command", {"command": command}

        # Regular prompt
        active_mode = interaction_state_for(
            self.session.coordinator,
            ui_modes=self.BUILTIN_MODE_NAMES,
        ).bundle_mode
        return "prompt", {"text": user_input, "active_mode": active_mode}

    def _parse_skill_chain(
        self, user_input: str
    ) -> tuple[tuple[str, ...], tuple[str, ...], str]:
        """Parse consecutive skill shortcuts and preserve their trailing context."""
        remaining = user_input.strip()
        commands: list[str] = []
        names: list[str] = []
        while remaining.startswith("/"):
            token, separator, tail = remaining.partition(" ")
            shortcut = token[1:].lower()
            entry = self.SKILL_SHORTCUTS.get(shortcut)
            if entry is None:
                break
            canonical = (
                entry.get("name", shortcut) if isinstance(entry, dict) else shortcut
            )
            commands.append(token.lower())
            names.append(str(canonical))
            if not separator:
                remaining = ""
                break
            remaining = tail.lstrip()
        return tuple(commands), tuple(names), remaining.strip()

    def _split_mode_trailing(self, args: str) -> tuple[str, str | None]:
        """Split /mode args into control portion and optional trailing prompt.

        "on"/"off" are only treated as control words when they are the ENTIRE
        text after the mode name.  This prevents natural-language phrases like
        "on that note, let's do X" from being partially consumed as a control
        word.

        Returns:
            (mode_args, trailing_prompt) where mode_args goes to _handle_mode
            and trailing_prompt (if any) is executed as a follow-up prompt.

        Examples:
            "brainstorm"                            → ("brainstorm", None)
            "brainstorm on"                         → ("brainstorm on", None)
            "brainstorm off"                        → ("brainstorm off", None)
            "brainstorm my great idea"              → ("brainstorm on", "my great idea")
            "brainstorm on that note, do X"         → ("brainstorm on", "on that note, do X")
            "off"                                   → ("off", None)
        """
        if not args.strip():
            return args, None

        words = args.split(maxsplit=1)
        first_word = words[0].strip()
        rest = words[1].strip() if len(words) > 1 else ""

        # "/mode off" — special deactivation syntax (exact match only)
        if first_word.lower() == "off" and not rest:
            return "off", None

        # "/mode <name> ..."
        mode_name = first_word
        if not rest:
            return mode_name, None

        # Only treat "on"/"off" as control words when they stand alone
        if rest.strip().lower() in ("on", "off"):
            return f"{mode_name} {rest.strip()}", None

        # Everything else is trailing prompt — force activation
        return f"{mode_name} on", rest

    async def handle_command(
        self, action: str, data: dict[str, Any]
    ) -> str | SessionCommandResult:
        """Execute the handler owned by the resolved command specification."""
        spec = self._execution_spec(action, data)
        if spec is not None:
            if spec.owner is CommandOwner.PROCESSOR:
                return await self._execute_processor_spec(spec, data)
            return await self._execute_session_spec(spec, data)

        # These are parser outcomes rather than advertised registry commands.
        if action == "load_skill_chain":
            return await self._dispatch_skill_chain(data)

        # Compatibility actions retained for callers predating the command registry.
        if action == "clear_context":
            await self._clear_context()
            return "✓ Context cleared"

        if action == "fork_session":
            return await self._fork_session(str(data.get("args", "")))

        if action == "session_ui":
            return await self._execute_session_command(data)

        if action == "unknown_command":
            return (
                f"Unknown command: {data['command']}. Use /help for available commands."
            )

        return f"Unhandled action: {action}"

    def _execution_spec(
        self, action: str, data: Mapping[str, Any]
    ) -> CommandSpec | None:
        """Resolve command metadata, using action lookup only for legacy callers."""
        command = data.get("command")
        if isinstance(command, str) and command:
            spec = self.command_registry.resolve(command)
            if spec is not None:
                return spec

        builtins = tuple(
            spec
            for spec in self.command_registry.specs
            if spec.action == action and spec.source is CommandSource.BUILTIN
        )
        if len(builtins) == 1:
            return builtins[0]
        if len(builtins) > 1:
            names = ", ".join(spec.name for spec in builtins)
            raise RuntimeError(f"ambiguous command action {action!r}: {names}")
        return None

    async def _execute_processor_spec(
        self, spec: CommandSpec, data: dict[str, Any]
    ) -> str | SessionCommandResult:
        handler = getattr(self, spec.handler, None)
        if not callable(handler):
            raise RuntimeError(
                f"registered command {spec.name} has no callable processor handler "
                f"{spec.handler!r}"
            )
        result = handler(data)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, (str, SessionCommandResult)):
            raise TypeError(
                f"registered command {spec.name} handler {spec.handler!r} returned "
                f"unsupported {type(result).__name__}"
            )
        return result

    async def _execute_session_spec(
        self, spec: CommandSpec, data: dict[str, Any]
    ) -> str | SessionCommandResult:
        routed = dict(data)
        routed.setdefault("command", spec.name)
        return await self._execute_session_command(routed)

    async def _execute_session_command(
        self, data: Mapping[str, Any]
    ) -> str | SessionCommandResult:
        service = self.session.coordinator.get_capability("ui.session_commands")
        if service is None:
            return "Interactive session commands are unavailable."
        return await service.execute(
            str(data.get("command", "")), str(data.get("args", ""))
        )

    async def _dispatch_mode_command(self, data: Mapping[str, Any]) -> str:
        return await self._handle_mode(str(data.get("args", "")))

    async def _dispatch_modes_command(self, data: Mapping[str, Any]) -> str:
        return await self._list_modes()

    async def _dispatch_save_command(self, data: Mapping[str, Any]) -> str:
        path = await self._save_transcript(str(data.get("args", "")))
        return f"✓ Transcript saved to {path}"

    async def _dispatch_status_command(self, data: Mapping[str, Any]) -> str:
        return await self._get_status()

    async def _dispatch_help_command(self, data: Mapping[str, Any]) -> str:
        return self._format_help()

    async def _dispatch_config_command(self, data: Mapping[str, Any]) -> str:
        return await self._get_config_display(str(data.get("args", "")))

    async def _dispatch_tools_command(self, data: Mapping[str, Any]) -> str:
        return await self._list_tools()

    async def _dispatch_agents_command(self, data: Mapping[str, Any]) -> str:
        return await self._list_agents()

    async def _dispatch_allowed_dirs_command(self, data: Mapping[str, Any]) -> str:
        return await self._manage_allowed_dirs(str(data.get("args", "")))

    async def _dispatch_denied_dirs_command(self, data: Mapping[str, Any]) -> str:
        return await self._manage_denied_dirs(str(data.get("args", "")))

    async def _dispatch_rename_command(self, data: Mapping[str, Any]) -> str:
        return await self._rename_session(str(data.get("args", "")))

    async def _dispatch_skills_command(self, data: Mapping[str, Any]) -> str:
        return await self._list_skills()

    async def _dispatch_skill_command(
        self, data: Mapping[str, Any]
    ) -> SessionCommandResult:
        is_prompt, text = await self._load_skill(
            str(data.get("skill_name", "")), str(data.get("arguments", ""))
        )
        return (
            SessionCommandResult(prompt=text)
            if is_prompt
            else SessionCommandResult(text)
        )

    async def _dispatch_skill_chain(
        self, data: Mapping[str, Any]
    ) -> SessionCommandResult:
        names = tuple(str(name) for name in data.get("skill_names", ()))
        commands = tuple(str(name) for name in data.get("skill_commands", ()))
        if not commands:
            commands = ("/skill",) * len(names)
        if not names or len(commands) != len(names):
            return SessionCommandResult("No valid skill chain was provided.")

        prompts: list[str] = []
        for command, skill_name in zip(commands, names, strict=True):
            spec = self.command_registry.resolve(command)
            if (
                spec is None
                or spec.owner is not CommandOwner.PROCESSOR
                or spec.action != "load_skill"
            ):
                return SessionCommandResult(f"Unknown skill shortcut: {command}")
            result = await self._execute_processor_spec(
                spec,
                {
                    "command": command,
                    "skill_name": skill_name,
                    "arguments": str(data.get("arguments", "")),
                },
            )
            if isinstance(result, str):
                return SessionCommandResult(result)
            if not result.prompt:
                return result
            prompts.append(result.prompt)
        return SessionCommandResult(prompt="\n".join(prompts))


__all__ = ["CommandProcessor"]
