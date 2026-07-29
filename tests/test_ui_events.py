from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from amplifier_app_cli.ui.message_renderer import render_message
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_blocks import DebugBlock
from amplifier_app_cli.ui.transcript_blocks import NarrationBlock
from amplifier_app_cli.ui.transcript_blocks import ToolBlock
from amplifier_app_cli.ui.transcript_blocks import ToolStatus
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.ui_events import UiEventDispatcher


def test_dispatcher_owns_block_rendering_and_structural_gaps() -> None:
    output = StringIO()
    events = UiEventDispatcher(
        Console(file=output, no_color=True, width=80, highlight=False)
    )

    events.emit(NarrationBlock("Checking ownership"))
    events.gap()
    events.emit(AnswerBlock("**Done.**"))

    rendered = output.getvalue()
    assert "● Checking ownership" in rendered
    assert "Done." in rendered
    assert "\n\n" in rendered


def test_debug_flushes_coalesce_until_the_next_user_turn() -> None:
    output = StringIO()
    events = UiEventDispatcher(
        Console(file=output, no_color=True, width=80, highlight=False)
    )
    events.emit(DebugBlock(("stale debug",)))
    events.emit(
        ToolBlock(
            "Ran command",
            ToolStatus.COMPLETED,
            output=("newer tool output",),
        )
    )

    events.emit(DebugBlock(("latest debug",)))
    assert events.expand_latest_debug() is True
    assert events.expand_latest_debug() is False
    assert output.getvalue().count("lines · ctrl-o expand") == 1
    assert output.getvalue().count("stale debug") == 1
    assert output.getvalue().count("latest debug") == 1

    events.emit(UserBlock("next turn"))
    events.emit(DebugBlock(("next turn debug",)))
    assert output.getvalue().count("lines · ctrl-o expand") == 2


def test_visible_debug_policy_does_not_coalesce_output() -> None:
    output = StringIO()
    events = UiEventDispatcher(
        Console(file=output, no_color=True, width=80, highlight=False),
        show_debug=True,
    )

    events.emit(DebugBlock(("first",)))
    events.emit(DebugBlock(("second",)))

    assert "first" in output.getvalue()
    assert "second" in output.getvalue()


def test_message_renderer_can_use_the_existing_session_dispatcher() -> None:
    events = MagicMock(spec=UiEventDispatcher)

    render_message(
        {"role": "assistant", "content": "Final answer"},
        show_label=False,
        dispatcher=events,
    )

    block = events.emit.call_args.args[0]
    assert block == AnswerBlock("Final answer")


def test_interactive_session_has_no_second_transcript_renderer() -> None:
    source = Path("amplifier_app_cli/runtime/interactive_host.py").read_text(
        encoding="utf-8"
    )

    assert "TranscriptRenderer(" not in source
    assert "transcript_renderer.render" not in source
    assert "event_dispatcher=ui_events" in source
