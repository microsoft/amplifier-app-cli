import pytest

from amplifier_app_cli.ui.command_palette import CommandPalette
from amplifier_app_cli.ui.command_palette import CommandPhase
from amplifier_app_cli.ui.command_palette import CommandSource
from amplifier_app_cli.ui.command_palette import PaletteCommand


def _palette() -> CommandPalette:
    return CommandPalette.from_registries(
        {
            "/permissions": {
                "action": "permissions",
                "description": "edit trust slots",
            },
            "/mode": {"action": "mode", "description": "set posture"},
            "/agents": {"action": "agents", "description": "show agent lanes"},
            "/config": {"action": "config", "description": "repair configuration"},
        },
        mode_shortcuts={"plan": "plan"},
        skill_shortcuts={
            "release": {
                "name": "release",
                "description": "prepare release",
                "source": "user",
            },
            "research": {
                "name": "research",
                "description": "deep research",
                "source": "bundle:foundation",
            },
        },
        mcp_prompts=[("github", "triage", "triage an issue")],
    )


def test_palette_only_opens_for_line_start_slash() -> None:
    palette = _palette()

    assert palette.query("hello /").commands == ()
    assert palette.query("\n/help").commands == ()
    assert palette.query("/").commands


def test_palette_filters_and_preserves_source_tags() -> None:
    palette = _palette()

    assert palette.query("/rel").selected.source == CommandSource.USER
    assert palette.query("/research").selected.source == CommandSource.BUNDLE
    assert palette.query("/github:tri").selected.source == CommandSource.MCP


def test_palette_prioritizes_an_exact_name_without_losing_fuzzy_matches() -> None:
    palette = CommandPalette(
        [
            PaletteCommand(
                "/btw",
                "add context while the current turn runs",
                CommandPhase.DURING,
                CommandSource.BUILTIN,
            ),
            PaletteCommand(
                "/context",
                "show context usage",
                CommandPhase.REPAIR,
                CommandSource.BUILTIN,
            ),
        ]
    )

    snapshot = palette.query("/context")

    assert snapshot.selected is not None
    assert snapshot.selected.name == "/context"
    assert "/btw" in {command.name for command in snapshot.commands}
    assert palette.query("/current").selected.name == "/btw"


def test_palette_phase_groups_match_command_lifecycle() -> None:
    palette = _palette()
    commands = {command.name: command for command in palette.query("/").commands}

    assert commands["/agents"].phase == CommandPhase.PARALLEL
    assert commands["/config"].phase == CommandPhase.REPAIR
    assert commands["/mode"].phase == CommandPhase.DURING
    assert commands["/permissions"].phase == CommandPhase.SETUP


def test_palette_caps_visible_rows_at_eight() -> None:
    palette = CommandPalette(
        [
            PaletteCommand(
                f"/command-{index}",
                "description",
                CommandPhase.DURING,
                CommandSource.BUILTIN,
            )
            for index in range(20)
        ]
    )

    assert len(palette.query("/").commands) == 8


def test_unfiltered_palette_represents_each_session_phase() -> None:
    palette = CommandPalette.from_registries(
        {
            "/permissions": {"description": "trust"},
            "/mode": {"description": "posture"},
            "/tasks": {"description": "lanes"},
            "/diff": {"description": "changes"},
            "/rewind": {"description": "checkpoint"},
            "/doctor": {"description": "health"},
            "/help": {"description": "help"},
            "/context": {"description": "usage"},
        }
    )

    phases = {command.phase for command in palette.query("/").commands}

    assert phases == set(CommandPhase)


def test_palette_selection_wraps_and_clamps_after_filter() -> None:
    palette = _palette()
    snapshot = palette.query("/", selected_index=99)
    assert snapshot.selected_index == len(snapshot.commands) - 1

    moved = palette.move(snapshot, 1)
    assert moved.selected_index == 0
    assert palette.move(moved, -1).selected_index == len(snapshot.commands) - 1


@pytest.mark.parametrize("name", ["mode", "/bad name", "//"])
def test_palette_rejects_invalid_command_names(name: str) -> None:
    with pytest.raises(ValueError):
        PaletteCommand(
            name,
            "description",
            CommandPhase.DURING,
            CommandSource.BUILTIN,
        )
