"""Registry-backed state for the inline slash-command palette."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .command_registry import CommandPhase
from .command_registry import CommandRegistry
from .command_registry import CommandSource
from .command_registry import compose_command_registry

_MAX_RESULTS = 8
_MAX_COMMANDS = 2_000
_MAX_NAME_CHARS = 128
_MAX_DESCRIPTION_CHARS = 240


@dataclass(frozen=True, slots=True)
class PaletteCommand:
    name: str
    description: str
    phase: CommandPhase
    source: CommandSource
    target: str = ""
    order: int = 1_000_000

    def __post_init__(self) -> None:
        name = _clean_line(self.name, _MAX_NAME_CHARS)
        body = name.removeprefix("/")
        if (
            not name.startswith("/")
            or not body
            or any(character.isspace() for character in name)
            or any(
                not (character.isalnum() or character in {"-", "_", ":"})
                for character in body
            )
        ):
            raise ValueError("palette command names must be slash-prefixed tokens")
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self, "description", _clean_line(self.description, _MAX_DESCRIPTION_CHARS)
        )
        object.__setattr__(self, "target", _clean_line(self.target, _MAX_NAME_CHARS))


@dataclass(frozen=True, slots=True)
class PaletteSnapshot:
    query: str
    commands: tuple[PaletteCommand, ...]
    selected_index: int = 0

    @property
    def selected(self) -> PaletteCommand | None:
        if not self.commands:
            return None
        return self.commands[self.selected_index]


class CommandPalette:
    """Filter a unified command registry without opening a modal surface."""

    def __init__(
        self,
        commands: Iterable[PaletteCommand],
        *,
        max_results: int = _MAX_RESULTS,
    ) -> None:
        if isinstance(max_results, bool) or not 1 <= max_results <= _MAX_RESULTS:
            raise ValueError("max_results must be between 1 and 8")
        unique: dict[str, PaletteCommand] = {}
        for command in commands:
            if len(unique) >= _MAX_COMMANDS:
                break
            unique.setdefault(command.name, command)
        phase_order = {phase: index for index, phase in enumerate(CommandPhase)}
        self._commands = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    phase_order[item.phase],
                    item.order,
                    item.name,
                ),
            )
        )
        self._max_results = max_results

    @classmethod
    def from_registries(
        cls,
        builtins: CommandRegistry | Mapping[str, Mapping[str, Any]],
        *,
        mode_shortcuts: Mapping[str, Any] | None = None,
        skill_shortcuts: Mapping[str, Any] | None = None,
        mcp_prompts: Iterable[tuple[str, str, str]] = (),
    ) -> CommandPalette:
        registry = compose_command_registry(
            builtins,
            mode_shortcuts=mode_shortcuts,
            skill_shortcuts=skill_shortcuts,
            mcp_prompts=mcp_prompts,
        )
        return cls.from_registry(registry)

    @classmethod
    def from_registry(cls, registry: CommandRegistry) -> CommandPalette:
        commands: list[PaletteCommand] = []
        for order, spec in enumerate(registry.specs):
            if not spec.advertised:
                continue
            for name in spec.names:
                commands.append(
                    PaletteCommand(
                        name,
                        spec.description,
                        spec.phase,
                        spec.source,
                        spec.target or spec.action,
                        order,
                    )
                )
        return cls(commands)

    def query(self, input_text: str, *, selected_index: int = 0) -> PaletteSnapshot:
        if not input_text.startswith("/") or "\n" in input_text:
            return PaletteSnapshot("", ())
        token = input_text.split(maxsplit=1)[0].lower()
        terms = [term for term in token.removeprefix("/").split(":") if term]

        def matches(command: PaletteCommand) -> bool:
            haystack = (
                f"{command.name} {command.description} {command.source.value}".lower()
            )
            return all(term in haystack for term in terms)

        matching = tuple(command for command in self._commands if matches(command))
        commands = (
            self._phase_overview(matching)
            if not terms
            else matching[: self._max_results]
        )
        if not commands:
            return PaletteSnapshot(token, ())
        index = max(0, min(selected_index, len(commands) - 1))
        return PaletteSnapshot(token, commands, index)

    def _phase_overview(
        self, commands: tuple[PaletteCommand, ...]
    ) -> tuple[PaletteCommand, ...]:
        selected: list[PaletteCommand] = []
        for phase in CommandPhase:
            representative = next(
                (command for command in commands if command.phase == phase), None
            )
            if representative is not None:
                selected.append(representative)
        selected.extend(command for command in commands if command not in selected)
        return tuple(selected[: self._max_results])

    def move(self, snapshot: PaletteSnapshot, delta: int) -> PaletteSnapshot:
        if not snapshot.commands:
            return snapshot
        index = (snapshot.selected_index + delta) % len(snapshot.commands)
        return PaletteSnapshot(snapshot.query, snapshot.commands, index)


def _clean_line(value: object, limit: int) -> str:
    clean = "".join(character for character in str(value) if ord(character) >= 32)
    return " ".join(clean.split())[:limit]


__all__ = [
    "CommandPalette",
    "CommandPhase",
    "CommandSource",
    "PaletteCommand",
    "PaletteSnapshot",
]
