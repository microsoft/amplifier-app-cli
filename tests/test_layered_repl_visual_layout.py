"""Cell-level visual contracts for the layered REPL bottom surface."""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton
from prompt_toolkit.mouse_events import MouseEvent
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.output import DummyOutput

from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices
from amplifier_app_cli.ui.layered_repl_style import LAYERED_REPL_STYLE
from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.task_status import TaskStatusTracker


_WIDTHS = (40, 80, 120, 168)
_HEIGHT = 8


def _make_app(tmp_path, width: int) -> LayeredReplApp:
    output = DummyOutput()
    output.get_size = lambda: Size(rows=_HEIGHT, columns=width)
    return LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / f"history-{width}",
            completion=LayeredReplCompletion(
                CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
            ),
            bundle_name="foundation",
            session_id="face7204",
            output=output,
        ),
        bindings=LayeredReplBindings(
            on_submit=lambda submission: None,
            get_active_mode=lambda: "chat",
        ),
        services=LayeredReplServices(
            trust_state=TrustState(initial="bypass"),
        ),
    )


def _render(app: LayeredReplApp, width: int) -> Screen:
    screen = Screen()
    with set_app(app.application):
        app.application.layout.container.write_to_screen(
            screen,
            MouseHandlers(),
            WritePosition(xpos=0, ypos=0, width=width, height=_HEIGHT),
            parent_style="",
            erase_bg=False,
            z_index=None,
        )
    return screen


def _row_text(screen: Screen, row: int, width: int) -> str:
    return "".join(screen.data_buffer[row][column].char for column in range(width))


def _row_cells(screen: Screen, row: int, width: int):
    return tuple(screen.data_buffer[row][column] for column in range(width))


def _background(cell) -> str:
    return LAYERED_REPL_STYLE.get_attrs_for_style_str(cell.style).bgcolor


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.asyncio
async def test_composer_and_footer_keep_their_visual_hierarchy(
    tmp_path, width: int
) -> None:
    app = _make_app(tmp_path, width)
    app.input_buffer.text = "draft"

    screen = _render(app, width)
    await asyncio.sleep(0)
    composer_row = _HEIGHT - 2
    footer_row = _HEIGHT - 1
    composer = _row_text(screen, composer_row, width)
    footer = _row_text(screen, footer_row, width)

    assert composer.startswith("❯ [chat] draft")
    assert "class:prompt" in screen.data_buffer[composer_row][0].style
    assert any(
        "class:mode.chat" in cell.style
        for cell in _row_cells(screen, composer_row, width)
    )

    composer_background = LAYERED_REPL_STYLE.get_attrs_for_style_str(
        "class:input"
    ).bgcolor
    prompt_background = LAYERED_REPL_STYLE.get_attrs_for_style_str(
        "class:prompt"
    ).bgcolor
    assert composer_background == "353c48"
    assert prompt_background == composer_background
    assert all(
        _background(cell) == composer_background
        for cell in _row_cells(screen, composer_row, width)
    )
    assert "class:input" in screen.data_buffer[composer_row][width - 1].style

    state = (
        "chat/bypass · found · face · $0.00"
        if width == 40
        else "chat · bypass · foundation · face · $0.00"
        if width == 80
        else "chat · bypass permissions on · foundation · face · $0.00"
    )
    assert footer.strip().startswith(state)
    bundle_label = "found" if width == 40 else "foundation"
    assert footer.index(bundle_label) < footer.index("face") < footer.index("$0.00")
    if width == 40:
        assert footer.strip() == state
    else:
        hints = (
            "/ · shift-tab · ctrl-t"
            if width == 80
            else "/ commands · shift-tab mode · ctrl-t tasks"
        )
        assert footer.strip().endswith(hints)
        gap = footer[footer.index(state) + len(state) : footer.index(hints)]
        assert len(gap) >= 2
        assert not gap.strip()

    assert all(
        "class:status" in cell.style for cell in _row_cells(screen, footer_row, width)
    )
    assert all(not _row_text(screen, row, width).strip() for row in range(composer_row))


@pytest.mark.asyncio
@pytest.mark.parametrize("width", _WIDTHS)
async def test_approval_replaces_composer_without_overlap(tmp_path, width: int) -> None:
    app = _make_app(tmp_path, width)
    app.input_buffer.text = "hidden draft"
    decision = asyncio.create_task(
        app.request_approval(
            "Allow potentially destructive write outside project?",
            ("Allow once", "Deny"),
            30,
            "deny",
        )
    )
    await asyncio.sleep(0)

    try:
        screen = _render(app, width)
        approval_row = _HEIGHT - 2
        footer_row = _HEIGHT - 1
        approval = _row_text(screen, approval_row, width)
        footer = _row_text(screen, footer_row, width)

        assert approval.startswith("Approval required")
        assert "›" in approval
        assert "Allow" in approval
        assert "❯" not in approval
        assert "hidden draft" not in approval
        assert all(
            "class:approval" in cell.style
            for cell in _row_cells(screen, approval_row, width)
        )
        approval_background = LAYERED_REPL_STYLE.get_attrs_for_style_str(
            "class:approval"
        ).bgcolor
        assert approval_background
        assert all(
            _background(cell) for cell in _row_cells(screen, approval_row, width)
        )

        controls = (
            "enter · esc" if width == 40 else "arrows select · enter confirm · esc deny"
        )
        assert footer.strip().endswith(controls)
        assert "tab complete" not in footer
        assert "esc interrupt" not in footer
        assert all(
            "class:status" in cell.style
            for cell in _row_cells(screen, footer_row, width)
        )
        assert all(
            not _row_text(screen, row, width).strip() for row in range(approval_row)
        )
        assert app.input_buffer.text == "hidden draft"
    finally:
        app._deny_approval()
        assert await decision == "Deny"


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.asyncio
async def test_live_agent_tree_stays_above_stable_composer_and_footer(
    tmp_path, width: int
) -> None:
    output = DummyOutput()
    output.get_size = lambda: Size(rows=_HEIGHT, columns=width)
    tracker = TaskStatusTracker("face7204")
    for index, (agent, instruction) in enumerate(
        (
            ("amplifier-expert", "Review architecture"),
            ("zen-architect", "Design mission flow"),
            ("old-engineer", "Challenge risks"),
        )
    ):
        tracker.consume(
            "delegate:agent_spawned",
            {
                "sub_session_id": f"child-{index}",
                "parent_session_id": "face7204",
                "agent": agent,
                "instruction": instruction,
            },
        )
    app = LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / f"tree-history-{width}",
            completion=LayeredReplCompletion(
                CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
            ),
            bundle_name="foundation",
            session_id="face7204",
            output=output,
        ),
        bindings=LayeredReplBindings(
            on_submit=lambda submission: None,
            get_active_mode=lambda: "chat",
            get_is_running=lambda: True,
            get_task_title=lambda: "Evaluate the Amplifier flagship proposal",
        ),
        services=LayeredReplServices(
            task_tracker=tracker,
            trust_state=TrustState(initial="bypass"),
        ),
    )
    app.input_buffer.text = "steer here"

    screen = _render(app, width)
    await asyncio.sleep(0)
    rows = [_row_text(screen, row, width) for row in range(_HEIGHT)]

    assert "Working" in rows[-6]
    assert "amplifier-expert" in rows[-5]
    assert "zen-architect" in rows[-4]
    assert "old-engineer" in rows[-3]
    assert rows[-2].startswith("❯ [chat] steer here")
    assert "foundation" in rows[-1] or "found" in rows[-1]
    assert "steer here" not in "".join(rows[:-2])
    assert app._working_height().preferred == 4
    assert app._input_height().preferred == 1


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.asyncio
async def test_scrolling_transcript_never_moves_composer_or_footer(
    tmp_path, width: int
) -> None:
    app = _make_app(tmp_path, width)
    app.append_output("\n".join(f"ROW-{index:03d}" for index in range(200)))
    app.input_buffer.text = "DRAFT_SENTINEL"

    tail_screen = _render(app, width)
    await asyncio.sleep(0)
    composer_row = _HEIGHT - 2
    footer_row = _HEIGHT - 1
    stable_rows = (
        _row_text(tail_screen, composer_row, width),
        _row_text(tail_screen, footer_row, width),
    )
    stable_cells = (
        _row_cells(tail_screen, composer_row, width),
        _row_cells(tail_screen, footer_row, width),
    )
    assert "ROW-199" in "".join(
        _row_text(tail_screen, row, width) for row in range(composer_row)
    )

    app.scroll_transcript_page(-1)
    assert app._transcript_view.following_tail is False
    paused_cursor = app._transcript_view.buffer.cursor_position
    paused_screen = _render(app, width)
    paused_transcript = tuple(
        _row_text(paused_screen, row, width) for row in range(composer_row)
    )
    assert "ROW-199" not in "".join(paused_transcript)
    assert (
        _row_text(paused_screen, composer_row, width),
        _row_text(paused_screen, footer_row, width),
    ) == stable_rows
    assert (
        _row_cells(paused_screen, composer_row, width),
        _row_cells(paused_screen, footer_row, width),
    ) == stable_cells
    assert app.input_buffer.text == "DRAFT_SENTINEL"

    app.append_output("ROW-200")
    assert app._transcript_view.buffer.cursor_position == paused_cursor
    appended_screen = _render(app, width)
    assert (
        tuple(_row_text(appended_screen, row, width) for row in range(composer_row))
        == paused_transcript
    )

    app._transcript_view.control.mouse_handler(
        MouseEvent(
            position=Point(x=1, y=1),
            event_type=MouseEventType.SCROLL_DOWN,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
    )
    app._transcript_view.scroll_page(1, 1_000)
    assert app._transcript_view.following_tail is True
    restored_screen = _render(app, width)
    assert "ROW-200" in "".join(
        _row_text(restored_screen, row, width) for row in range(composer_row)
    )
    assert (
        _row_text(restored_screen, composer_row, width),
        _row_text(restored_screen, footer_row, width),
    ) == stable_rows


@pytest.mark.asyncio
async def test_dragging_transcript_copies_without_stealing_composer_focus(
    tmp_path, monkeypatch
) -> None:
    copied: list[str] = []
    monkeypatch.setattr(
        "amplifier_app_cli.ui.layered_repl.copy_text_to_clipboard",
        lambda text, **kwargs: copied.append(text) or True,
    )
    app = _make_app(tmp_path, 80)
    app.append_output("alpha beta gamma")
    app.input_buffer.text = "DRAFT_SENTINEL"
    _render(app, 80)
    await asyncio.sleep(0)
    input_control = app.input_window.content

    app._transcript_view.control.mouse_handler(
        MouseEvent(
            position=Point(x=0, y=0),
            event_type=MouseEventType.MOUSE_DOWN,
            button=MouseButton.LEFT,
            modifiers=frozenset(),
        )
    )
    app._transcript_view.control.mouse_handler(
        MouseEvent(
            position=Point(x=5, y=0),
            event_type=MouseEventType.MOUSE_MOVE,
            button=MouseButton.LEFT,
            modifiers=frozenset(),
        )
    )
    app.append_output("late output must not cancel the drag")
    assert app._transcript_view.buffer.selection_state is not None
    app._transcript_view.control.mouse_handler(
        MouseEvent(
            position=Point(x=5, y=0),
            event_type=MouseEventType.MOUSE_UP,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
    )

    assert copied == ["alpha"]
    assert app.input_buffer.text == "DRAFT_SENTINEL"
    assert app.application.layout.current_control is input_control
    assert app._transcript_view.following_tail is False
    selected_screen = _render(app, 80)
    assert any(
        "class:selected" in cell.style
        for row in range(_HEIGHT - 2)
        for cell in _row_cells(selected_screen, row, 80)
    )


@pytest.mark.asyncio
async def test_transcript_click_without_drag_preserves_tail_and_draft(tmp_path) -> None:
    app = _make_app(tmp_path, 80)
    app.append_output("alpha beta gamma")
    app.input_buffer.text = "DRAFT_SENTINEL"
    _render(app, 80)
    await asyncio.sleep(0)
    original_cursor = app._transcript_view.buffer.cursor_position

    for event_type, button in (
        (MouseEventType.MOUSE_DOWN, MouseButton.LEFT),
        (MouseEventType.MOUSE_UP, MouseButton.NONE),
    ):
        app._transcript_view.control.mouse_handler(
            MouseEvent(
                position=Point(x=2, y=0),
                event_type=event_type,
                button=button,
                modifiers=frozenset(),
            )
        )

    assert app.input_buffer.text == "DRAFT_SENTINEL"
    assert app._transcript_view.buffer.cursor_position == original_cursor
    assert app._transcript_view.buffer.selection_state is None
    assert app._transcript_view.following_tail is True


@pytest.mark.asyncio
async def test_unreported_mouse_release_recovers_selection_and_tail(tmp_path) -> None:
    app = _make_app(tmp_path, 80)
    app.append_output("alpha beta gamma")
    app.input_buffer.text = "DRAFT_SENTINEL"
    _render(app, 80)
    await asyncio.sleep(0)
    original_cursor = app._transcript_view.buffer.cursor_position
    control = app._transcript_view.control

    control.mouse_handler(
        MouseEvent(
            position=Point(x=0, y=0),
            event_type=MouseEventType.MOUSE_DOWN,
            button=MouseButton.LEFT,
            modifiers=frozenset(),
        )
    )
    control.mouse_handler(
        MouseEvent(
            position=Point(x=5, y=0),
            event_type=MouseEventType.MOUSE_MOVE,
            button=MouseButton.LEFT,
            modifiers=frozenset(),
        )
    )
    generation = control._selection_generation
    control._expire_selection(generation)

    assert control.selection_in_progress is False
    assert app._transcript_view.buffer.selection_state is None
    assert app._transcript_view.buffer.cursor_position == original_cursor
    assert app._transcript_view.following_tail is True
    assert app.input_buffer.text == "DRAFT_SENTINEL"
