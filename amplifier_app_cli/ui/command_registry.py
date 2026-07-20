"""Typed source of truth for interactive slash commands."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any


def _command_token(value: object) -> str:
    token = str(value).strip().lower()
    body = token.removeprefix("/")
    if (
        not token.startswith("/")
        or not body
        or any(character.isspace() for character in token)
        or any(
            not (character.isalnum() or character in {"-", "_", ":"})
            for character in body
        )
    ):
        raise ValueError("command names must be slash-prefixed tokens")
    return token


def _clean_line(value: object, *, limit: int = 240) -> str:
    clean = "".join(character for character in str(value) if ord(character) >= 32)
    return " ".join(clean.split())[:limit]


def _clean_token(value: object) -> str:
    return "".join(
        character
        for character in _clean_line(value, limit=128)
        if character.isalnum() or character in {"-", "_"}
    )


class CommandPhase(str, Enum):
    SETUP = "Setup"
    DURING = "During"
    PARALLEL = "Parallel"
    SHIP = "Ship"
    BETWEEN = "Between"
    REPAIR = "Repair"


class CommandSource(str, Enum):
    BUILTIN = "built-in"
    MODE = "mode"
    SKILL = "skill"
    BUNDLE = "bundle"
    USER = "user"
    MCP = "mcp"


class CommandOwner(str, Enum):
    PROCESSOR = "processor"
    CORE = "core"
    SESSION = "session"
    MCP = "mcp"


class CommandAvailability(str, Enum):
    INTERACTIVE = "interactive"
    SESSION = "session"
    CAPABILITY = "capability"


class CompletionProvider(str, Enum):
    MODE = "mode"
    MODEL = "model"
    SKILL = "skill"


@dataclass(frozen=True, slots=True)
class CompletionSpec:
    values: tuple[str, ...] = ()
    provider: CompletionProvider | None = None

    def __post_init__(self) -> None:
        values = tuple(dict.fromkeys(_clean_token(value) for value in self.values))
        if any(not value for value in values):
            raise ValueError("command completion values must be non-empty tokens")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    description: str
    phase: CommandPhase
    source: CommandSource
    action: str
    owner: CommandOwner
    handler: str
    aliases: tuple[str, ...] = ()
    availability: CommandAvailability = CommandAvailability.INTERACTIVE
    completion: CompletionSpec | None = None
    target: str = ""
    advertised: bool = True

    def __post_init__(self) -> None:
        name = _command_token(self.name)
        aliases = tuple(_command_token(alias) for alias in self.aliases)
        if name in aliases or len(set(aliases)) != len(aliases):
            raise ValueError(f"duplicate aliases registered for {name}")
        description = _clean_line(self.description)
        action = _clean_token(self.action)
        handler = self.handler.strip()
        if not description:
            raise ValueError(f"command {name} requires a description")
        if not action:
            raise ValueError(f"command {name} requires an action")
        if not handler:
            raise ValueError(f"command {name} requires a handler")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "handler", handler)
        object.__setattr__(self, "target", _clean_line(self.target, limit=128))

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class CommandRegistry:
    """Immutable command snapshot with strict name and alias validation."""

    __slots__ = ("_by_name", "_specs")

    def __init__(self, specs: Iterable[CommandSpec]) -> None:
        ordered: list[CommandSpec] = []
        by_name: dict[str, CommandSpec] = {}
        for spec in specs:
            if not isinstance(spec, CommandSpec):
                raise TypeError("command registries only accept CommandSpec values")
            collisions = [name for name in spec.names if name in by_name]
            if collisions:
                names = ", ".join(collisions)
                raise ValueError(f"duplicate command registration: {names}")
            ordered.append(spec)
            by_name.update({name: spec for name in spec.names})
        self._specs = tuple(ordered)
        self._by_name = MappingProxyType(by_name)

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        return self._specs

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name)

    def resolve(self, name: str) -> CommandSpec | None:
        try:
            token = _command_token(name)
        except (TypeError, ValueError):
            return None
        return self._by_name.get(token)

    def require(self, name: str) -> CommandSpec:
        spec = self.resolve(name)
        if spec is None:
            raise KeyError(name)
        return spec

    def supports(self, name: str, *, owner: CommandOwner | None = None) -> bool:
        spec = self.resolve(name)
        return spec is not None and (owner is None or spec.owner is owner)

    def names_for_owner(self, owner: CommandOwner) -> frozenset[str]:
        return frozenset(
            name for name, spec in self._by_name.items() if spec.owner is owner
        )

    def legacy_metadata(self) -> dict[str, dict[str, Any]]:
        """Project typed specs into the historical mapping API."""
        result: dict[str, dict[str, Any]] = {}
        for spec in self._specs:
            for name in spec.names:
                result[name] = {
                    "action": spec.action,
                    "description": spec.description,
                    "phase": spec.phase.value,
                    "source": spec.source.value,
                    "owner": spec.owner.value,
                    "handler": spec.handler,
                    "availability": spec.availability.value,
                    "completion": (
                        {
                            "values": spec.completion.values,
                            "provider": (
                                spec.completion.provider.value
                                if spec.completion.provider is not None
                                else None
                            ),
                        }
                        if spec.completion is not None
                        else None
                    ),
                    "canonical": spec.name,
                    "target": spec.target,
                }
        return result

    @classmethod
    def from_legacy(cls, commands: Mapping[str, Mapping[str, Any]]) -> CommandRegistry:
        specs: list[CommandSpec] = []
        seen_canonical: set[str] = set()
        for name, metadata in commands.items():
            canonical = str(metadata.get("canonical") or name)
            if canonical in seen_canonical:
                continue
            aliases = tuple(
                command_name
                for command_name, candidate in commands.items()
                if command_name != canonical
                and str(candidate.get("canonical") or command_name) == canonical
            )
            completion_data = metadata.get("completion")
            completion = None
            if isinstance(completion_data, Mapping):
                provider_value = completion_data.get("provider")
                completion = CompletionSpec(
                    tuple(str(value) for value in completion_data.get("values") or ()),
                    CompletionProvider(str(provider_value)) if provider_value else None,
                )
            elif completion_data is None:
                completion = _default_completion_for(canonical)
            action = str(metadata.get("action") or "command")
            owner_value = metadata.get("owner")
            owner = (
                CommandOwner(str(owner_value))
                if owner_value
                else _owner_for_action(action)
            )
            specs.append(
                CommandSpec(
                    canonical,
                    str(metadata.get("description") or canonical.removeprefix("/")),
                    _enum_or_default(
                        CommandPhase,
                        metadata.get("phase"),
                        default_phase_for(canonical),
                    ),
                    _enum_or_default(
                        CommandSource,
                        metadata.get("source"),
                        CommandSource.BUILTIN,
                    ),
                    action,
                    owner,
                    str(metadata.get("handler") or action),
                    aliases=aliases,
                    availability=_enum_or_default(
                        CommandAvailability,
                        metadata.get("availability"),
                        CommandAvailability.INTERACTIVE,
                    ),
                    completion=completion,
                    target=str(metadata.get("target") or ""),
                )
            )
            seen_canonical.add(canonical)
        return cls(specs)


def default_phase_for(name: str) -> CommandPhase:
    command = name.split(":", maxsplit=1)[0]
    if command in {"/init", "/permissions", "/mcp"}:
        return CommandPhase.SETUP
    if command in {"/tasks", "/fork", "/background", "/agents"}:
        return CommandPhase.PARALLEL
    if command in {"/diff", "/review", "/ledger", "/save"}:
        return CommandPhase.SHIP
    if command in {"/rewind", "/resume", "/clear", "/branch", "/export"}:
        return CommandPhase.BETWEEN
    if command in {"/doctor", "/improve", "/feedback", "/config"}:
        return CommandPhase.REPAIR
    return CommandPhase.DURING


def _default_completion_for(name: str) -> CompletionSpec | None:
    if name == "/mode":
        return CompletionSpec(provider=CompletionProvider.MODE)
    if name == "/model":
        return CompletionSpec(provider=CompletionProvider.MODEL)
    if name == "/skill":
        return CompletionSpec(provider=CompletionProvider.SKILL)
    if name in {"/effort", "/strength"}:
        return CompletionSpec(
            ("none", "minimal", "low", "medium", "high", "xhigh", "max")
        )
    if name == "/config":
        return CompletionSpec(
            (
                "show",
                "context",
                "tools",
                "hooks",
                "providers",
                "agents",
                "behaviors",
                "diff",
                "save",
                "set",
            )
        )
    return None


def compose_command_registry(
    builtins: CommandRegistry | Mapping[str, Mapping[str, Any]],
    *,
    mode_shortcuts: Mapping[str, Any] | None = None,
    skill_shortcuts: Mapping[str, Any] | None = None,
    mcp_prompts: Iterable[tuple[str, str, str]] = (),
) -> CommandRegistry:
    """Merge dynamic command descriptors into one typed snapshot.

    Every collision is rejected so discovery cannot silently shadow or hide a
    command already advertised by another source.
    """
    base = (
        builtins
        if isinstance(builtins, CommandRegistry)
        else CommandRegistry.from_legacy(builtins)
    )
    specs = list(base.specs)
    names = set(base.names)

    def append(spec: CommandSpec) -> None:
        collisions = names.intersection(spec.names)
        if collisions:
            rendered = ", ".join(sorted(collisions))
            raise ValueError(f"duplicate dynamic command registration: {rendered}")
        specs.append(spec)
        names.update(spec.names)

    for shortcut, target in (mode_shortcuts or {}).items():
        target_name = target if isinstance(target, str) else shortcut
        append(
            CommandSpec(
                f"/{str(shortcut).removeprefix('/')}",
                f"activate {target_name} mode",
                CommandPhase.DURING,
                CommandSource.MODE,
                "handle_mode",
                CommandOwner.PROCESSOR,
                "_dispatch_mode_command",
                target=str(target_name),
            )
        )

    for shortcut, metadata in (skill_shortcuts or {}).items():
        entry = metadata if isinstance(metadata, Mapping) else {}
        description = entry.get("description") or entry.get("name") or "run skill"
        target = str(entry.get("name") or shortcut)
        append(
            CommandSpec(
                f"/{str(shortcut).removeprefix('/')}",
                str(description),
                default_phase_for(f"/{shortcut}"),
                _skill_source(entry.get("source")),
                "load_skill",
                CommandOwner.PROCESSOR,
                "_dispatch_skill_command",
                target=target,
            )
        )

    for server, prompt, description in mcp_prompts:
        server_name = _clean_token(server)
        prompt_name = _clean_token(prompt)
        if not server_name or not prompt_name:
            continue
        append(
            CommandSpec(
                f"/{server_name}:{prompt_name}",
                description or f"run {server_name}:{prompt_name}",
                CommandPhase.DURING,
                CommandSource.MCP,
                "session_ui",
                CommandOwner.MCP,
                "execute",
                availability=CommandAvailability.CAPABILITY,
                target=f"{server_name}:{prompt_name}",
            )
        )
    return CommandRegistry(specs)


def _skill_source(value: object) -> CommandSource:
    source = str(value or "").lower()
    if "user" in source or "personal" in source:
        return CommandSource.USER
    if "bundle" in source:
        return CommandSource.BUNDLE
    return CommandSource.SKILL


def _owner_for_action(action: str) -> CommandOwner:
    return CommandOwner.SESSION if action == "session_ui" else CommandOwner.PROCESSOR


def _enum_or_default(enum_type: type[Enum], value: object, default: Any) -> Any:
    if value is None:
        return default
    try:
        return enum_type(str(value))
    except ValueError:
        return default


__all__ = [
    "CommandAvailability",
    "CommandOwner",
    "CommandPhase",
    "CommandRegistry",
    "CommandSource",
    "CommandSpec",
    "CompletionProvider",
    "CompletionSpec",
    "compose_command_registry",
    "default_phase_for",
]
