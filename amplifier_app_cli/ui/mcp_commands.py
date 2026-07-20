"""MCP server management and slash-prompt discovery for the interactive CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
from typing import Any
from uuid import uuid4

from .core_commands import CommandOutcome

_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class McpConfigError(ValueError):
    """Raised when the project MCP configuration cannot be used safely."""


class McpConfigStore:
    """Read and atomically update the project ``mcpServers`` registry."""

    def __init__(self, config_path: Path) -> None:
        self.path = config_path.resolve()

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"mcpServers": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as error:
            raise McpConfigError(
                f"Could not read MCP config {self.path}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise McpConfigError(
                f"Could not read MCP config {self.path}: {error}"
            ) from error
        if not isinstance(data, dict):
            raise McpConfigError(
                f"Invalid MCP config {self.path}: root must be an object."
            )
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise McpConfigError("Invalid MCP config: mcpServers must be an object.")
        return data

    def servers(self) -> dict[str, Any]:
        return dict(self.read().get("mcpServers", {}))

    def add_server(self, name: str, value: dict[str, Any]) -> bool:
        _validate_server_name(name)
        config = self.read()
        servers = config.setdefault("mcpServers", {})
        if name in servers:
            return False
        servers[name] = value
        self.write(config)
        return True

    def remove_server(self, name: str) -> bool:
        _validate_server_name(name)
        config = self.read()
        servers = config.get("mcpServers", {})
        if name not in servers:
            return False
        del servers[name]
        self.write(config)
        return True

    def write(self, config: dict[str, Any]) -> None:
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise McpConfigError("Invalid MCP config: mcpServers must be an object.")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
            temporary.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as error:
            raise McpConfigError(
                f"Could not write MCP config {self.path}: {error}"
            ) from error


@dataclass(frozen=True, slots=True)
class McpPromptDescriptor:
    command: str
    server: str
    prompt: str
    description: str
    wrapper: Any


class McpCommandService:
    """Expose mounted MCP prompts and manage the project MCP configuration."""

    def __init__(self, coordinator: Any | None, cwd: Path) -> None:
        self._coordinator = coordinator
        self._cwd = cwd.resolve()
        self._config_path = self._cwd / ".amplifier" / "mcp.json"
        self._store = McpConfigStore(self._config_path)
        self._prompts = self._discover_prompts()

    @property
    def palette_prompts(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (item.server, item.prompt, item.description)
            for item in self._prompts.values()
        )

    def supports(self, command: str) -> bool:
        return command in self._prompts

    async def execute(self, command: str, args: str) -> CommandOutcome:
        if command == "/mcp":
            return self._manage(args.strip())
        prompt = self._prompts.get(command)
        if prompt is None:
            return CommandOutcome(f"Unknown MCP prompt: {command}")
        parsed = _parse_prompt_arguments(prompt.wrapper, args.strip())
        if isinstance(parsed, str):
            return CommandOutcome(parsed)
        try:
            result = prompt.wrapper.execute(parsed)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as error:
            return CommandOutcome(f"MCP prompt {command} failed: {error}")
        if not bool(getattr(result, "success", False)):
            error = getattr(result, "error", None) or getattr(result, "output", None)
            return CommandOutcome(f"MCP prompt {command} failed: {error}")
        output = getattr(result, "output", None)
        messages = output.get("messages") if isinstance(output, dict) else None
        if not isinstance(messages, str) or not messages.strip():
            return CommandOutcome(f"MCP prompt {command} returned no prompt messages.")
        return CommandOutcome(prompt=messages)

    def _manage(self, args: str) -> CommandOutcome:
        try:
            parts = shlex.split(args)
        except ValueError as error:
            return CommandOutcome(f"Invalid /mcp arguments: {error}")
        if not parts or parts == ["list"]:
            return self._list()
        if parts[0] == "add":
            return self._add(parts[1:])
        if parts[0] == "remove":
            return self._remove(parts[1:])
        if parts[0] == "reload":
            return CommandOutcome(
                "MCP hot reload is not exposed by the mounted module. Configuration changes "
                "take effect in the next Amplifier session."
            )
        return CommandOutcome(
            "Usage: /mcp [list|add <name> <command> [args...]|remove <name>|reload]"
        )

    def _list(self) -> CommandOutcome:
        config, error = self._read_config()
        if error:
            return CommandOutcome(error)
        configured = config.get("mcpServers", {})
        lines = [f"MCP servers · config {self._config_path}"]
        if isinstance(configured, dict):
            for name, value in sorted(configured.items()):
                kind = (
                    "url" if isinstance(value, dict) and value.get("url") else "command"
                )
                lines.append(f"{name} · configured {kind}")
        mounted_servers = sorted({item.server for item in self._prompts.values()})
        if mounted_servers:
            lines.append(f"mounted prompts · {', '.join(mounted_servers)}")
        if len(lines) == 1:
            lines.append("No project MCP servers or mounted prompts.")
        lines.append("Changes apply to the next session.")
        return CommandOutcome("\n".join(lines))

    def _add(self, parts: list[str]) -> CommandOutcome:
        if len(parts) < 2 or not _SERVER_NAME.fullmatch(parts[0]):
            return CommandOutcome("Usage: /mcp add <name> <command> [args...]")
        name, command, *command_args = parts
        try:
            added = self._store.add_server(
                name, {"command": command, "args": command_args}
            )
        except McpConfigError as error:
            return CommandOutcome(str(error))
        if not added:
            return CommandOutcome(
                f"MCP server {name} already exists; remove it before replacing it."
            )
        return CommandOutcome(
            f"MCP server {name} added · starts in the next session", transient=True
        )

    def _remove(self, parts: list[str]) -> CommandOutcome:
        if len(parts) != 1 or not _SERVER_NAME.fullmatch(parts[0]):
            return CommandOutcome("Usage: /mcp remove <name>")
        try:
            removed = self._store.remove_server(parts[0])
        except McpConfigError as error:
            return CommandOutcome(str(error))
        if not removed:
            return CommandOutcome(
                f"MCP server {parts[0]} is not in {self._config_path}."
            )
        return CommandOutcome(
            f"MCP server {parts[0]} removed · stops after this session", transient=True
        )

    def _discover_prompts(self) -> dict[str, McpPromptDescriptor]:
        tools = (
            self._coordinator.get("tools") if self._coordinator is not None else None
        )
        if not isinstance(tools, dict):
            return {}
        prompts: dict[str, McpPromptDescriptor] = {}
        for wrapper in tools.values():
            server = _token(getattr(wrapper, "server_name", ""))
            prompt = _token(getattr(wrapper, "prompt_name", ""))
            if not server or not prompt or not hasattr(wrapper, "execute"):
                continue
            command = f"/{server}:{prompt}".lower()
            prompts.setdefault(
                command,
                McpPromptDescriptor(
                    command,
                    server,
                    prompt,
                    str(getattr(wrapper, "description", "") or "MCP prompt"),
                    wrapper,
                ),
            )
        return prompts

    def _read_config(self) -> tuple[dict[str, Any], str]:
        try:
            return self._store.read(), ""
        except McpConfigError as error:
            return {}, str(error)

    def _write_config(self, config: dict[str, Any]) -> str:
        try:
            self._store.write(config)
        except McpConfigError as error:
            return str(error)
        return ""


def _validate_server_name(name: str) -> None:
    if not isinstance(name, str) or not _SERVER_NAME.fullmatch(name):
        raise McpConfigError("Invalid MCP server name.")


def _parse_prompt_arguments(wrapper: Any, args: str) -> dict[str, str] | str:
    schema = getattr(wrapper, "input_schema", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(properties, dict):
        properties = {}
    if not args:
        missing = [name for name in required if name in properties]
        return f"Required MCP prompt arguments: {', '.join(missing)}" if missing else {}
    if args.startswith("{"):
        try:
            value = json.loads(args)
        except json.JSONDecodeError as error:
            return f"Invalid MCP prompt JSON: {error}"
        return (
            value if isinstance(value, dict) else "MCP prompt JSON must be an object."
        )
    if len(properties) == 1:
        return {next(iter(properties)): args}
    try:
        tokens = shlex.split(args)
    except ValueError as error:
        return f"Invalid MCP prompt arguments: {error}"
    values: dict[str, str] = {}
    for token in tokens:
        name, separator, value = token.partition("=")
        if not separator or name not in properties:
            return "Use key=value arguments: " + ", ".join(properties)
        values[name] = value
    missing = [name for name in required if not values.get(name)]
    return f"Required MCP prompt arguments: {', '.join(missing)}" if missing else values


def _token(value: Any) -> str:
    return "".join(
        character
        for character in str(value)
        if character.isalnum() or character in {"-", "_"}
    )[:128]


__all__ = [
    "McpCommandService",
    "McpConfigError",
    "McpConfigStore",
    "McpPromptDescriptor",
]
