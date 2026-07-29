"""Click affordances on transcript blocks: tools, turn rules, and answers."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.mouse_events import MouseButton
from prompt_toolkit.mouse_events import MouseEvent
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.output import DummyOutput

from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices
from amplifier_app_cli.ui.layered_transcript import LayeredTranscriptView
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.outcome_ledger import OutcomeYield
from amplifier_app_cli.ui.outcome_ledger import TurnOutcome
from amplifier_app_cli.ui.outcome_ledger import YieldKind
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_click_spans import ClickSpanRegistry
from amplifier_app_cli.ui.transcript_blocks import Telemetry
from amplifier_app_cli.ui.transcript_blocks import TurnTerminatorBlock

_SESSION = "12345678-abcdef"
_HEIGHT = 12
_WIDTH = 80


def _make_app(tmp_path, **services) -> LayeredReplApp:
    output = DummyOutput()
    output.get_size = lambda: Size(rows=_HEIGHT, columns=_WIDTH)
    return LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / "history",
            completion=LayeredReplCompletion(
                CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
            ),
            bundle_name="foundation",
            session_id=_SESSION,
            output=output,
        ),
        bindings=LayeredReplBindings(
            on_submit=lambda submission: None,
            get_active_mode=lambda: "chat",
        ),
        services=LayeredReplServices(**services),
    )


def _render(app: LayeredReplApp) -> Screen:
    screen = Screen()
    with set_app(app.application):
        app.application.layout.container.write_to_screen(
            screen,
            MouseHandlers(),
            WritePosition(xpos=0, ypos=0, width=_WIDTH, height=_HEIGHT),
            parent_style="",
            erase_bg=False,
            z_index=None,
        )
    return screen


def _click(app: LayeredReplApp, row: int, *, column: int = 2) -> None:
    _render(app)
    for event_type, button in (
        (MouseEventType.MOUSE_DOWN, MouseButton.LEFT),
        (MouseEventType.MOUSE_UP, MouseButton.NONE),
    ):
        app._transcript_view.control.mouse_handler(
            MouseEvent(
                position=Point(x=column, y=row),
                event_type=event_type,
                button=button,
                modifiers=frozenset(),
            )
        )


def _rows_for(app: LayeredReplApp, kind: str) -> list[int]:
    view = app._transcript_view
    rows = []
    for row in range(view.history_line_count):
        action = view.click_action_at_row(row)
        if isinstance(action, tuple) and action and action[0] == kind:
            rows.append(row)
    return rows


def _terminal_shell_tool(runtime: RuntimeStatusTracker, call_id: str) -> None:
    runtime.consume(
        "tool:pre",
        {
            "session_id": _SESSION,
            "tool_call_id": call_id,
            "tool_name": "shell",
            "tool_input": {"command": "uv run pytest -q"},
        },
    )
    runtime.consume(
        "tool:post",
        {
            "session_id": _SESSION,
            "tool_call_id": call_id,
            "tool_name": "shell",
            "result": {"output": {"stdout": "all tests passed", "exit_code": 0}},
        },
    )


@pytest.mark.asyncio
async def test_clicking_a_collapsed_tool_line_expands_its_output(tmp_path) -> None:
    runtime = RuntimeStatusTracker(_SESSION)
    app = _make_app(tmp_path, runtime_status=runtime)
    _terminal_shell_tool(runtime, "call-1")
    await asyncio.sleep(0)
    tool_rows = _rows_for(app, "tool")
    assert tool_rows
    assert "all tests passed" not in app._transcript_view.plain_text()

    _click(app, tool_rows[0])

    transcript = app._transcript_view.plain_text()
    assert "all tests passed" in transcript

    # The keyboard path must not re-expand the tool the click already expanded.
    app.expand_latest_tool()
    assert app._transcript_view.plain_text().count("all tests passed") == 1


@pytest.mark.asyncio
async def test_clicking_a_turn_rule_opens_rewind_at_that_checkpoint(tmp_path) -> None:
    ledger = OutcomeLedger()
    app = _make_app(tmp_path, outcome_ledger=ledger)
    for index in (1, 2):
        ledger.record(
            TurnOutcome(
                f"turn-{index}",
                f"checkpoint-{index}",
                Decimal("0.10"),
                1.0,
                100,
                yields=(OutcomeYield(YieldKind.ANSWER, "answer"),),
            )
        )
        app._ui_events.emit(
            TurnTerminatorBlock(Telemetry(elapsed_seconds=1.0), outcome="answer")
        )
    await asyncio.sleep(0)
    terminator_rows = _rows_for(app, "terminator")
    assert len(terminator_rows) >= 2
    assert app._rewind_visible() is False

    _click(app, terminator_rows[0])

    assert app._rewind_visible() is True
    assert "checkpoint-1" in "".join(fragment[1] for fragment in app._rewind_text())

    app._dismiss_rewind()
    _click(app, terminator_rows[-1])
    assert "checkpoint-2" in "".join(fragment[1] for fragment in app._rewind_text())


@pytest.mark.asyncio
async def test_clicking_a_final_answer_reveals_its_evidence(tmp_path) -> None:
    runtime = RuntimeStatusTracker(_SESSION)
    _terminal_shell_tool(runtime, "tests-1")
    evidence = EvidenceLinkModel()
    evidence.record("answer-1", "All tests passed.", runtime.tool_snapshot())
    app = _make_app(tmp_path, runtime_status=runtime, evidence_model=evidence)
    app._ui_events.emit(AnswerBlock("All tests passed."))
    await asyncio.sleep(0)
    answer_rows = _rows_for(app, "answer")
    assert answer_rows
    assert app._evidence_visible() is False

    _click(app, answer_rows[0])

    assert app._evidence_visible() is True
    assert "¹" in app._transcript_view.plain_text()


@pytest.mark.asyncio
async def test_answers_without_an_evidence_record_are_not_click_targets(
    tmp_path,
) -> None:
    evidence = EvidenceLinkModel()
    evidence.record("answer-1", "A recorded answer.", ())
    app = _make_app(tmp_path, evidence_model=evidence)
    app._ui_events.emit(AnswerBlock("/help output that was never recorded"))
    await asyncio.sleep(0)

    assert _rows_for(app, "answer") == []


@pytest.mark.asyncio
async def test_dragging_over_a_click_target_still_selects_text(
    tmp_path, monkeypatch
) -> None:
    copied: list[str] = []
    monkeypatch.setattr(
        "amplifier_app_cli.ui.layered_repl.copy_text_to_clipboard",
        lambda text, **kwargs: copied.append(text) or True,
    )
    runtime = RuntimeStatusTracker(_SESSION)
    app = _make_app(tmp_path, runtime_status=runtime)
    _terminal_shell_tool(runtime, "call-1")
    await asyncio.sleep(0)
    row = _rows_for(app, "tool")[0]
    _render(app)

    events = (
        (MouseEventType.MOUSE_DOWN, MouseButton.LEFT, 2),
        (MouseEventType.MOUSE_MOVE, MouseButton.LEFT, 7),
        (MouseEventType.MOUSE_UP, MouseButton.NONE, 7),
    )
    for event_type, button, column in events:
        app._transcript_view.control.mouse_handler(
            MouseEvent(
                position=Point(x=column, y=row),
                event_type=event_type,
                button=button,
                modifiers=frozenset(),
            )
        )

    assert copied  # the drag copied text ...
    assert "all tests passed" not in app._transcript_view.plain_text()  # ... only


@pytest.mark.asyncio
async def test_clicking_plain_output_rows_does_nothing(tmp_path) -> None:
    app = _make_app(tmp_path)
    app.append_output("plain transcript output")
    await asyncio.sleep(0)

    _click(app, 0)

    assert app._transcript_view.plain_text().splitlines()[0].startswith("plain")
    assert app._rewind_visible() is False
    assert app._evidence_visible() is False


def test_click_span_registry_is_bounded_and_invalidates_rewritten_tails() -> None:
    view = LayeredTranscriptView(stream_status=None)
    view._click_spans = ClickSpanRegistry(capacity=256)
    for index in range(600):
        view.append_output(f"row-{index}\n", action=("tool", index))

    # The registry is bounded: the oldest spans fall away first.
    assert view.click_action_at_row(0) is None
    last_row = view.history_line_count - 1
    last_action = view.click_action_at_row(last_row)
    assert isinstance(last_action, tuple) and last_action[1] == 599

    registry = ClickSpanRegistry(capacity=4)
    marker = ("tool", "kept")
    registry.record(0, 1, marker)
    registry.record(2, 4, ("answer", "old"))
    # A chunk that rewrites rows 3.. invalidates the span it overlaps.
    registry.record(3, 5, ("terminator", "new"))
    assert registry.action_at(1) is marker
    assert registry.action_at(2) is None
    assert registry.action_at(4) == ("terminator", "new")
