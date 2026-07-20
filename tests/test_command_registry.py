from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from amplifier_app_cli.main import CommandProcessor
from amplifier_app_cli.ui.command_palette import CommandPalette
from amplifier_app_cli.ui.command_catalog import BUILTIN_COMMAND_REGISTRY
from amplifier_app_cli.ui.command_registry import CommandAvailability
from amplifier_app_cli.ui.command_registry import CommandOwner
from amplifier_app_cli.ui.command_registry import CommandPhase
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.command_registry import CommandSource
from amplifier_app_cli.ui.command_registry import CommandSpec
from amplifier_app_cli.ui.command_registry import compose_command_registry
from amplifier_app_cli.ui.core_commands import CoreCommandService
from amplifier_app_cli.ui.mcp_commands import McpCommandService
from amplifier_app_cli.ui.repl import SlashCommandCompleter
from amplifier_app_cli.ui.session_commands import SessionCommandResult
from amplifier_app_cli.ui.session_commands import SessionCommandService


def _spec(name: str, *, aliases: tuple[str, ...] = ()) -> CommandSpec:
    return CommandSpec(
        name,
        "test command",
        CommandPhase.DURING,
        CommandSource.BUILTIN,
        "test",
        CommandOwner.PROCESSOR,
        "_test",
        aliases=aliases,
        availability=CommandAvailability.INTERACTIVE,
    )


def _processor(
    *, mcp_prompts: tuple[tuple[str, str, str], ...] = ()
) -> CommandProcessor:
    session = MagicMock()
    session.coordinator.session_state = {"active_mode": None}
    session.coordinator.get_capability.return_value = None
    return CommandProcessor(session, "foundation", mcp_prompts=mcp_prompts)


def _processor_with_discovery(
    *,
    mode_shortcuts: dict[str, str] | None = None,
    skill_shortcuts: dict[str, dict[str, str]] | None = None,
) -> CommandProcessor:
    session = MagicMock()
    session.coordinator.session_state = {"active_mode": None}

    if mode_shortcuts is not None:
        mode_discovery = MagicMock()
        mode_discovery.get_shortcuts.return_value = mode_shortcuts
        mode_discovery.list_modes.return_value = tuple(
            (name, f"{name} mode") for name in mode_shortcuts
        )
        session.coordinator.session_state["mode_discovery"] = mode_discovery

    skill_discovery = None
    if skill_shortcuts is not None:
        skill_discovery = MagicMock()
        skill_discovery.get_shortcuts.return_value = skill_shortcuts
        skill_discovery.list_skills.return_value = tuple(
            (name, metadata.get("description", ""))
            for name, metadata in skill_shortcuts.items()
        )

    session.coordinator.get_capability.side_effect = lambda name: (
        skill_discovery if name == "skills_discovery" else None
    )
    return CommandProcessor(session, "foundation")


def _slash_completions(processor: CommandProcessor, text: str) -> set[str]:
    completer = SlashCommandCompleter(processor.command_registry)
    complete_event = CompleteEvent(completion_requested=True)
    return {
        item.text for item in completer.get_completions(Document(text), complete_event)
    }


def test_registry_is_immutable_and_rejects_duplicate_names_and_aliases() -> None:
    original = _spec("/first", aliases=("/one",))
    registry = CommandRegistry((original,))

    with pytest.raises(FrozenInstanceError):
        original.description = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="duplicate command registration"):
        CommandRegistry((original, _spec("/first")))
    with pytest.raises(ValueError, match="duplicate command registration"):
        CommandRegistry((original, _spec("/second", aliases=("/one",))))

    assert registry.resolve("/one") is original


def test_dynamic_commands_use_the_same_typed_registry_model() -> None:
    registry = compose_command_registry(
        BUILTIN_COMMAND_REGISTRY,
        mode_shortcuts={"plan": "plan"},
        skill_shortcuts={
            "release": {
                "name": "release",
                "description": "prepare release",
                "source": "user",
            }
        },
        mcp_prompts=(("github", "triage", "triage an issue"),),
    )

    assert registry.require("/plan").source is CommandSource.MODE
    assert registry.require("/release").source is CommandSource.USER
    assert registry.require("/github:triage").owner is CommandOwner.MCP

    processor = _processor(mcp_prompts=(("github", "triage", "triage an issue"),))
    assert processor.process_input("/github:triage #42") == (
        "session_ui",
        {"args": "#42", "command": "/github:triage"},
    )
    assert "/github:triage" in processor._format_help()


def test_dynamic_commands_are_isolated_between_processor_instances() -> None:
    first = _processor_with_discovery(
        mode_shortcuts={"isolated-mode": "isolated-mode"},
        skill_shortcuts={
            "isolated-skill": {
                "name": "isolated-skill",
                "description": "session-local skill",
            }
        },
    )
    second = _processor_with_discovery()

    for command in ("/isolated-mode", "/isolated-skill"):
        assert first.command_registry.resolve(command) is not None
        assert command in first._format_help()
        assert command in _slash_completions(first, command)

        assert second.command_registry.resolve(command) is None
        assert command not in second._format_help()
        assert command not in _slash_completions(second, command)
        assert second.process_input(command) == (
            "unknown_command",
            {"command": command},
        )

    assert first.process_input("/isolated-mode")[0] == "handle_mode"
    assert first.process_input("/isolated-skill")[0] == "load_skill"
    assert "isolated-mode" in first._get_mode_completion_names()
    assert "isolated-skill" in first._get_skill_completion_names()
    assert "isolated-mode" not in second._get_mode_completion_names()
    assert "isolated-skill" not in second._get_skill_completion_names()

    # Creating or using a clean processor cannot mutate the earlier snapshot.
    assert first.command_registry.resolve("/isolated-mode") is not None
    assert first.command_registry.resolve("/isolated-skill") is not None


@pytest.mark.parametrize(
    ("kwargs", "command"),
    [
        ({"mode_shortcuts": {"help": "help"}}, "/help"),
        ({"skill_shortcuts": {"help": {"name": "help"}}}, "/help"),
        (
            {
                "mcp_prompts": (
                    ("github", "triage", "first"),
                    ("github", "triage", "second"),
                )
            },
            "/github:triage",
        ),
    ],
)
def test_dynamic_command_collisions_fail_loudly(
    kwargs: dict[str, object], command: str
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"duplicate dynamic command registration: {command}",
    ):
        compose_command_registry(BUILTIN_COMMAND_REGISTRY, **kwargs)


def test_advertised_builtins_cannot_drift_between_help_palette_and_dispatch() -> None:
    processor = _processor()
    help_text = processor._format_help()
    palette = CommandPalette.from_registry(BUILTIN_COMMAND_REGISTRY)
    completer = SlashCommandCompleter(BUILTIN_COMMAND_REGISTRY)
    complete_event = CompleteEvent(completion_requested=True)

    for spec in BUILTIN_COMMAND_REGISTRY.specs:
        if not spec.advertised:
            continue
        for name in spec.names:
            action, _ = processor.process_input(name)
            palette_names = {item.name for item in palette.query(name).commands}
            completion_names = {
                item.text
                for item in completer.get_completions(Document(name), complete_event)
            }

            assert action == spec.action
            assert name in help_text
            assert name in palette_names
            assert name in completion_names


def test_registry_handler_ownership_matches_runtime_services() -> None:
    owners = {
        CommandOwner.PROCESSOR: CommandProcessor,
        CommandOwner.CORE: CoreCommandService,
        CommandOwner.SESSION: SessionCommandService,
        CommandOwner.MCP: McpCommandService,
    }

    for spec in BUILTIN_COMMAND_REGISTRY.specs:
        assert hasattr(owners[spec.owner], spec.handler), (
            f"{spec.name} points at missing {spec.owner.value} handler {spec.handler}"
        )

    assert CoreCommandService.COMMANDS == BUILTIN_COMMAND_REGISTRY.names_for_owner(
        CommandOwner.CORE
    )
    assert CommandProcessor.COMMANDS == BUILTIN_COMMAND_REGISTRY.legacy_metadata()


@pytest.mark.asyncio
async def test_every_advertised_processor_command_executes_registered_handler() -> None:
    processor = _processor()

    for spec in processor.command_registry.specs:
        if spec.owner is not CommandOwner.PROCESSOR or not spec.advertised:
            continue
        expected = f"handled {spec.name}"
        handler = AsyncMock(return_value=expected)
        setattr(processor, spec.handler, handler)
        data = {
            "command": spec.name,
            "args": "payload",
            "skill_name": "sample",
            "arguments": "details",
        }

        result = await processor.handle_command("stale-action", data)

        assert result == expected
        handler.assert_awaited_once_with(data)


@pytest.mark.asyncio
async def test_changed_handler_metadata_changes_processor_method_invoked() -> None:
    processor = _processor()
    original = processor.command_registry.require("/modes")
    replacement = replace(original, handler="_dispatch_skills_command")
    processor.command_registry = CommandRegistry(
        replacement if spec is original else spec
        for spec in processor.command_registry.specs
    )
    processor._list_modes = AsyncMock(return_value="modes")
    processor._list_skills = AsyncMock(return_value="skills")

    result = await processor.handle_command(
        original.action,
        {"command": original.name, "args": ""},
    )

    assert result == "skills"
    processor._list_skills.assert_awaited_once_with()
    processor._list_modes.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_processor_command_with_missing_handler_fails_loudly() -> None:
    processor = _processor()
    original = processor.command_registry.require("/modes")
    replacement = replace(original, handler="_missing_registered_handler")
    processor.command_registry = CommandRegistry(
        replacement if spec is original else spec
        for spec in processor.command_registry.specs
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "registered command /modes has no callable processor handler "
            "'_missing_registered_handler'"
        ),
    ):
        await processor.handle_command(
            original.action,
            {"command": original.name, "args": ""},
        )


@pytest.mark.asyncio
async def test_changed_owner_metadata_routes_through_session_command_service() -> None:
    processor = _processor()
    original = processor.command_registry.require("/modes")
    replacement = replace(original, owner=CommandOwner.SESSION)
    processor.command_registry = CommandRegistry(
        replacement if spec is original else spec
        for spec in processor.command_registry.specs
    )
    service = MagicMock()
    service.execute = AsyncMock(return_value="session-owned")
    processor.session.coordinator.get_capability.return_value = service
    processor._dispatch_modes_command = AsyncMock(return_value="processor-owned")

    result = await processor.handle_command(
        original.action,
        {"command": original.name, "args": "details"},
    )

    assert result == "session-owned"
    service.execute.assert_awaited_once_with("/modes", "details")
    processor._dispatch_modes_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_result_adapters_preserve_legacy_shapes() -> None:
    processor = _processor()
    processor._save_transcript = AsyncMock(return_value="session/transcript.json")
    processor._clear_context = AsyncMock()
    processor._load_skill = AsyncMock(
        side_effect=((True, "first prompt"), (True, "second prompt"))
    )

    saved = await processor.handle_command("save_transcript", {"args": ""})
    cleared = await processor.handle_command("clear_context", {})
    chained = await processor.handle_command(
        "load_skill_chain",
        {"skill_names": ("first", "second"), "arguments": "focus"},
    )
    unknown = await processor.handle_command("unknown_command", {"command": "/missing"})

    assert saved == "✓ Transcript saved to session/transcript.json"
    assert cleared == "✓ Context cleared"
    assert isinstance(chained, SessionCommandResult)
    assert chained.prompt == "first prompt\nsecond prompt"
    assert unknown == "Unknown command: /missing. Use /help for available commands."
