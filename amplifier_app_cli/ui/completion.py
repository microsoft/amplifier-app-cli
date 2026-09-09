"""Slash-command completion for the interactive REPL.

The candidate engine is deliberately independent of prompt_toolkit.  A snapshot
is assembled between prompts, then the completer only reads that in-memory
snapshot while the user presses keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONFIG_CATEGORIES = (
    "context",
    "tools",
    "hooks",
    "providers",
    "agents",
    "behaviors",
)
_CONFIG_LIST_METHODS = {
    "context": "context_list",
    "tools": "tools_list",
    "hooks": "hooks_list",
    "providers": "providers_list",
    "agents": "agents_list",
    "behaviors": "behaviors_list",
}


def _description(value: Any) -> str:
    """Return prompt-safe, single-line metadata for a completion menu."""
    text = _CONTROL_CHARS.sub(" ", str(value or "")).replace("\n", " ").strip()
    return text[:80]


def _item_name(item: Any) -> str | None:
    """Extract an ItemRecord-compatible name without importing foundation."""
    value = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
    return str(value) if value else None


def _item_enabled(item: Any) -> bool:
    return bool(item.get("enabled")) if isinstance(item, dict) else bool(
        getattr(item, "enabled", False)
    )


@dataclass(frozen=True)
class Candidate:
    """A display value and text to insert for one completion."""

    value: str
    description: str = ""
    append_space: bool = True

    @property
    def insertion(self) -> str:
        return self.value + (" " if self.append_space else "")


@dataclass
class CompletionSnapshot:
    """All data allowed to be read while completion is active."""

    commands: dict[str, str] = field(default_factory=dict)
    mode_shortcuts: dict[str, str] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    skills: list[dict[str, Any]] = field(default_factory=list)
    skill_shortcuts: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: tuple[str, ...] = ()
    config_items: dict[str, tuple[tuple[str, bool], ...]] = field(default_factory=dict)


class SlashCompletionEngine:
    """Pure, snapshot-backed slash completion candidate engine."""

    def __init__(self, snapshot: CompletionSnapshot | None = None):
        self.snapshot = snapshot or CompletionSnapshot()

    def refresh(self, snapshot: CompletionSnapshot) -> None:
        self.snapshot = snapshot

    @staticmethod
    def _tokens(before_cursor: str) -> tuple[list[str], str]:
        """Return complete words and the word currently being edited."""
        if not before_cursor or before_cursor[-1].isspace():
            return before_cursor.split(), ""
        words = before_cursor.split()
        return words[:-1], words[-1] if words else ""

    @staticmethod
    def _matching(
        values: Iterable[Candidate], prefix: str, *, case_sensitive: bool = False
    ) -> list[Candidate]:
        """Filter candidates, preserving grammar-specific matching rules."""
        if case_sensitive:
            return [candidate for candidate in values if candidate.value.startswith(prefix)]
        return [
            candidate
            for candidate in values
            if candidate.value.lower().startswith(prefix.lower())
        ]

    def complete(self, text: str, cursor_position: int | None = None) -> list[Candidate]:
        """Return candidates for text before *cursor_position*, never doing I/O."""
        cursor = len(text) if cursor_position is None else cursor_position
        before, after = text[:cursor], text[cursor:]
        if not text.startswith("/") or "\n" in before or (after and not after[:1].isspace()):
            # A native Completion cannot safely replace a token suffix.  The
            # adapter only exposes end-of-token candidates, avoiding duplicate
            # text such as ``/providervider``.
            return []
        complete_words, current = self._tokens(before)
        if not complete_words and not current:
            return []
        command = (complete_words[0] if complete_words else current).lower()
        if len(complete_words) == 0:
            return self._top_level(current)
        args = complete_words[1:]
        return self._arguments(command, args, current)

    def _top_level(self, prefix: str) -> list[Candidate]:
        candidates: dict[str, Candidate] = {
            command: Candidate(command, description)
            for command, description in self.snapshot.commands.items()
        }
        # Dispatcher precedence is built-in > mode > skill.
        for alias, canonical in self.snapshot.mode_shortcuts.items():
            value = f"/{alias}"
            candidates.setdefault(value, Candidate(value, self.snapshot.modes.get(canonical, "")))
        for alias, entry in self.snapshot.skill_shortcuts.items():
            value = f"/{alias}"
            candidates.setdefault(
                value,
                Candidate(value, self._skill_description(alias, entry)),
            )
        return self._matching(
            (candidates[name] for name in sorted(candidates)), prefix
        )

    def _arguments(self, command: str, args: list[str], current: str) -> list[Candidate]:
        if command == "/provider":
            return self._provider(args, current)
        if command == "/mode":
            return self._mode(args, current)
        if command == "/config":
            return self._config(args, current)
        if command == "/goal":
            return self._goal(args, current)
        if command in {"/allowed-dirs", "/denied-dirs"}:
            return self._directory_command(args, current)
        if command == "/skill":
            return self._skill_command(args, current)
        # Built-in commands always win over dynamic mode or skill names, even
        # when this built-in has no argument completion grammar of its own.
        if command in self.snapshot.commands:
            return []
        direct_mode = command[1:]
        if direct_mode in self.snapshot.mode_shortcuts:
            return self._direct_mode(args, current)
        if command.startswith("/"):
            skill_name = command[1:]
            if skill_name in self.snapshot.skill_shortcuts:
                return self._skill_arguments(skill_name, args, current)
        return []

    def _directory_command(self, args: list[str], current: str) -> list[Candidate]:
        """Complete only the bounded verbs; directory paths remain free-form."""
        if not args:
            return self._matching(
                [
                    Candidate("list", "List configured directories"),
                    Candidate("add", "Add a directory"),
                    Candidate("remove", "Remove a directory"),
                ],
                current,
            )
        return []

    def _provider(self, args: list[str], current: str) -> list[Candidate]:
        if not args:
            return self._matching(
                [
                    Candidate("auto", "Use automatic provider selection"),
                    Candidate("use", "Pin a mounted provider"),
                    Candidate("test", "Test mounted provider connectivity"),
                    Candidate("models", "List models for a mounted provider"),
                ],
                current,
            )
        if args[0].lower() in {"use", "test", "models"} and len(args) == 1:
            return self._matching((Candidate(name) for name in self.snapshot.providers), current)
        return []

    def _mode(self, args: list[str], current: str) -> list[Candidate]:
        names = [
            Candidate(name, description)
            for name, description in sorted(self.snapshot.modes.items())
        ]
        if not args:
            return self._matching([Candidate("off"), Candidate("info"), *names], current)
        if args[0].lower() == "info" and len(args) == 1:
            return self._matching(names, current)
        if len(args) == 1 and args[0].lower() in self.snapshot.modes:
            return self._matching([Candidate("on"), Candidate("off")], current)
        return []

    def _direct_mode(self, args: list[str], current: str) -> list[Candidate]:
        """Complete direct mode shortcuts after dispatcher precedence wins."""
        if not args:
            return self._matching([Candidate("on"), Candidate("off")], current)
        return []

    def _goal(self, args: list[str], current: str) -> list[Candidate]:
        if not args:
            return self._matching(
                [
                    Candidate("clear"),
                    Candidate("stop"),
                    Candidate("off"),
                    Candidate("reset"),
                    Candidate("none"),
                    Candidate("cancel"),
                    Candidate("--max-turns"),
                ],
                current,
            )
        # A cap's numeric value and every condition are user free-form text.
        return []

    def _config_flags(self, args: list[str], current: str) -> list[Candidate]:
        if args and args[-1] == "--format":
            return self._matching([Candidate("text"), Candidate("json")], current)
        used = set(args)
        choices: list[Candidate] = []
        if "--compact" not in used:
            choices.append(Candidate("--compact"))
        if "--detailed" not in used and "--trees" not in used:
            choices.append(Candidate("--detailed"))
            choices.append(Candidate("--trees"))
        if "--format" not in used:
            choices.append(Candidate("--format"))
        return self._matching(choices, current)

    def _config(self, args: list[str], current: str) -> list[Candidate]:
        if args and args[0] == "set":
            return []  # value is free-form; never complete it as a command.
        positional = self._strip_config_flags(args)
        if positional and positional[0] == "save":
            if args and args[-1] == "--scope":
                return self._matching([Candidate("project"), Candidate("global")], current)
            if len(positional) == 1:
                return self._matching([Candidate("--scope")], current)
            return []
        # --scope belongs only to `/config save`; it is not a display flag and
        # must never be swallowed before the save verb has been entered.
        if "--scope" in args or current.startswith("--scope"):
            return []
        flag_matches = self._config_flags(args, current)
        if args and args[-1] == "--format":
            return flag_matches
        if current.startswith("-"):
            return flag_matches
        if not positional:
            return self._matching(
                [
                    Candidate("show"),
                    Candidate("diff"),
                    Candidate("save"),
                    Candidate("set"),
                    *(Candidate(name) for name in _CONFIG_CATEGORIES),
                    *flag_matches,
                ],
                current,
            )
        head = positional[0]
        if head == "show":
            if len(positional) == 1:
                return self._matching(
                    [*(Candidate(name) for name in _CONFIG_CATEGORIES), *flag_matches],
                    current,
                )
            if len(positional) == 2 and positional[1] in _CONFIG_CATEGORIES:
                return self._matching(
                    [*self._config_names(positional[1]), *flag_matches], current
                )
            return []
        if head in _CONFIG_CATEGORIES:
            if len(positional) == 1:
                controls = (
                    []
                    if head == "hooks"
                    else [Candidate("enable"), Candidate("disable")]
                )
                return self._matching(
                    [
                        *controls,
                        *self._config_names(head),
                        *flag_matches,
                    ],
                    current,
                )
            if len(positional) == 2 and positional[1] in {"enable", "disable"}:
                enabled = positional[1] == "disable"
                return self._matching(self._config_names(head, enabled=enabled), current)
        return []

    @staticmethod
    def _strip_config_flags(args: list[str]) -> list[str]:
        remaining: list[str] = []
        index = 0
        while index < len(args):
            value = args[index]
            if value in {"--compact", "--detailed", "--trees"}:
                index += 1
                continue
            if value == "--format":
                index += 2
                continue
            remaining.append(value)
            index += 1
        return remaining

    def _config_names(self, category: str, enabled: bool | None = None) -> list[Candidate]:
        return [
            Candidate(name)
            for name, is_enabled in self.snapshot.config_items.get(category, ())
            if enabled is None or is_enabled is enabled
        ]

    def _skill_command(self, args: list[str], current: str) -> list[Candidate]:
        if not args:
            candidates: list[Candidate] = []
            for record in self.snapshot.skills:
                description = self._skill_description(str(record["name"]), record)
                candidates.append(Candidate(str(record["name"]), description))
                candidates.extend(Candidate(str(alias), description) for alias in record.get("aliases", []))
            return self._matching(candidates, current)
        return self._skill_arguments(args[0], args[1:], current)

    def _skill_description(self, selected_name: str, entry: dict[str, Any]) -> str:
        """Keep display-only hints visible even beside long descriptions."""
        record = self._find_skill(selected_name) or entry
        hint = record.get("argument_hint")
        description = _description(record.get("description", entry.get("description", "")))
        return _description(f"{hint} — {description}") if hint else description

    def _skill_arguments(self, selected_name: str, args: list[str], current: str) -> list[Candidate]:
        record = self._find_skill(selected_name)
        if record is None:
            return []
        completed = args
        result: list[Candidate] = []
        for rule in record.get("arguments", []):
            if rule.get("after", []) == completed:
                result.extend(Candidate(str(value)) for value in rule.get("values", []))
        # Skill completion specs define literal values. Their matching remains
        # case-sensitive so the displayed grammar and accepted values agree.
        return self._matching(result, current, case_sensitive=True)

    def _find_skill(self, selected_name: str) -> dict[str, Any] | None:
        canonical = self.snapshot.skill_shortcuts.get(selected_name, {}).get(
            "name", selected_name
        )
        for record in self.snapshot.skills:
            if record.get("name") == canonical:
                return record
        return None


class SlashCompleter(Completer):
    """Thin prompt_toolkit adapter around :class:`SlashCompletionEngine`."""

    def __init__(self, engine: SlashCompletionEngine | None = None):
        self.engine = engine or SlashCompletionEngine()

    def refresh(self, snapshot: CompletionSnapshot) -> None:
        """Replace the in-memory completion snapshot at a safe REPL boundary."""
        self.engine.refresh(snapshot)

    def get_completions(self, document: Document, complete_event: Any):
        text = document.text
        cursor = document.cursor_position
        prefix = text[:cursor].split()[-1] if text[:cursor] and not text[:cursor][-1].isspace() else ""
        for candidate in self.engine.complete(text, cursor):
            yield Completion(
                candidate.insertion,
                start_position=-len(prefix),
                display=candidate.value,
                display_meta=candidate.description,
            )


def build_completion_snapshot(command_processor: Any) -> CompletionSnapshot:
    """Read cached session state once, immediately before prompting.

    This is intentionally the only bridge from the live session to completion.
    It may call discovery/configurator cache APIs, but completion keypresses do
    not invoke this function.
    """
    commands = {
        command: _description(info.get("description", ""))
        for command, info in command_processor.COMMANDS.items()
    }
    command_processor._populate_mode_shortcuts()
    command_processor._populate_skill_shortcuts()
    mode_shortcuts = dict(getattr(command_processor, "_mode_shortcuts", {}))
    skill_shortcuts = dict(getattr(command_processor, "_skill_shortcuts", {}))
    modes: dict[str, str] = {}
    discovery = command_processor.session.coordinator.session_state.get("mode_discovery")
    if discovery and hasattr(discovery, "list_modes"):
        for item in discovery.list_modes():
            # ModeDiscovery returns ModeListing(name, description, source,
            # advertised), a NamedTuple.  Attribute access keeps that public
            # shape explicit while retaining old tuple compatibility.
            name = getattr(item, "name", None)
            description = getattr(item, "description", None)
            if name is None:
                name = item[0]
            if description is None:
                description = item[1] if len(item) > 1 else ""
            name = str(name)
            modes[name] = _description(description)
    for canonical in mode_shortcuts.values():
        modes.setdefault(canonical, "")

    skills: list[dict[str, Any]] = []
    discovery = command_processor.session.coordinator.get_capability("skills_discovery")
    if discovery and hasattr(discovery, "get_completion_catalog"):
        skills = list(discovery.get_completion_catalog())
    elif discovery:
        listed = discovery.list_skills() if hasattr(discovery, "list_skills") else []
        descriptions = {name: description for name, description in listed}
        seen: set[str] = set()
        for alias, entry in skill_shortcuts.items():
            canonical = entry.get("name", alias) if isinstance(entry, dict) else alias
            if canonical in seen:
                continue
            seen.add(canonical)
            aliases = [
                candidate
                for candidate, candidate_entry in skill_shortcuts.items()
                if (candidate_entry.get("name", candidate) if isinstance(candidate_entry, dict) else candidate)
                == canonical
                and candidate != canonical
            ]
            skills.append(
                {
                    "name": canonical,
                    "aliases": aliases,
                    "description": descriptions.get(canonical, entry.get("description", "") if isinstance(entry, dict) else ""),
                    "argument_hint": None,
                    "arguments": [],
                }
            )

    providers = tuple(
        sorted((command_processor.session.coordinator.get("providers") or {}).keys())
    )
    config_items: dict[str, tuple[tuple[str, bool], ...]] = {}
    configurator = getattr(command_processor, "configurator", None)
    if configurator is not None:
        for category, method_name in _CONFIG_LIST_METHODS.items():
            method = getattr(configurator, method_name, None)
            if method is not None:
                config_items[category] = tuple(
                    (name, _item_enabled(item))
                    for item in method()
                    if (name := _item_name(item)) is not None
                )
    return CompletionSnapshot(
        commands=commands,
        mode_shortcuts=mode_shortcuts,
        modes=modes,
        skills=skills,
        skill_shortcuts=skill_shortcuts,
        providers=providers,
        config_items=config_items,
    )