"""Tests for the layered interactive terminal application."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import redirect_stdout
from io import StringIO
from decimal import Decimal
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.document import Document
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from rich.console import Console

from amplifier_app_cli.console import Markdown
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.ui.clipboard import ChatSubmission
from amplifier_app_cli.ui.clipboard import ImageAttachment
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices
from amplifier_app_cli.ui.layered_repl_layout import _build_key_bindings
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.stream_status import StreamStatusTracker
from amplifier_app_cli.ui.stream_status import RuntimeStatusTracker
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.interaction_state import PermissionDecision, PermissionSlot
from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.terminal_transcript import TerminalTranscript
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger, OutcomeYield
from amplifier_app_cli.ui.outcome_ledger import TurnOutcome, YieldKind
from amplifier_app_cli.ui.task_status import TaskStatusTracker
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock


class RecordingOutput(DummyOutput):
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, data: str) -> None:
        self.chunks.append(data)

    def write_raw(self, data: str) -> None:
        self.chunks.append(data)


def _make_app(
    tmp_path,
    *,
    on_submit=None,
    max_output_lines: int = 260,
    input=None,
    task_tracker=None,
    bundle_name="foundation",
    get_active_mode=None,
    get_render_profile=None,
    get_is_running=None,
    stream_status=None,
    runtime_status=None,
    output=None,
    notice_state=None,
    trust_state=None,
    commands=None,
    steering_queue=None,
    outcome_ledger=None,
    on_rewind=None,
    evidence_model=None,
    get_task_title=None,
):
    registry = CommandRegistry.from_legacy(
        commands or {"/help": {"description": "Show help"}}
    )
    return LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / "history",
            completion=LayeredReplCompletion(registry),
            bundle_name=bundle_name,
            session_id="12345678-abcdef",
            max_output_lines=max_output_lines,
            output=output or DummyOutput(),
            input=input,
        ),
        bindings=LayeredReplBindings(
            on_submit=on_submit or (lambda submission: None),
            on_rewind=on_rewind,
            get_task_title=get_task_title,
            get_active_mode=get_active_mode,
            get_render_profile=get_render_profile,
            get_is_running=get_is_running,
        ),
        services=LayeredReplServices(
            task_tracker=task_tracker,
            stream_status=stream_status,
            runtime_status=runtime_status,
            notice_state=notice_state,
            trust_state=trust_state,
            steering_queue=steering_queue,
            outcome_ledger=outcome_ledger,
            evidence_model=evidence_model,
        ),
    )


def test_layered_app_constructor_exposes_only_cohesive_runtime_objects() -> None:
    parameters = inspect.signature(LayeredReplApp.__init__).parameters

    assert tuple(parameters) == ("self", "config", "bindings", "services")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in parameters.items()
        if name != "self"
    )


def test_layered_output_is_retained_by_the_single_transcript_owner(tmp_path):
    app = _make_app(tmp_path)
    output = StringIO()
    with redirect_stdout(output):
        app.append_output("from native output")

    assert output.getvalue() == ""
    assert app._transcript_view.plain_text() == "from native output"
    assert not hasattr(app, "output_buffer")
    assert not hasattr(app, "output_window")


def test_layered_capture_context_preserves_explicit_console_file(tmp_path):
    app = _make_app(tmp_path)
    original_file = StringIO()
    rich_console = Console(file=original_file, force_terminal=False)

    with app.capture_output(rich_console):
        rich_console.print("from console")

    assert rich_console.file is original_file
    assert "from console" in original_file.getvalue()


def test_layered_capture_collapses_untyped_console_output(tmp_path):
    app = _make_app(tmp_path)
    rich_console = Console(no_color=True)

    with app.capture_output(rich_console):
        rich_console.print(Markdown("**captured answer**"))

    assert "captured answer" not in app._transcript_view.plain_text()
    assert "1 lines · ctrl-o expand" in app._transcript_view.plain_text()
    app.expand_latest_tool()
    assert "captured answer" in app._transcript_view.plain_text()


def test_layered_capture_reports_omitted_untyped_output(tmp_path):
    app = _make_app(tmp_path)

    app._capture_untyped_output("\n".join(f"raw-{index}" for index in range(250)))

    transcript = app._transcript_view.plain_text()
    assert "250 lines · ctrl-o expand" in transcript
    app.expand_latest_tool()
    transcript = app._transcript_view.plain_text()
    assert "raw-199" in transcript
    assert "raw-200" not in transcript
    assert "50 additional lines omitted (250 total)" in transcript


@pytest.mark.asyncio
async def test_worker_thread_output_is_marshaled_to_the_application_loop(tmp_path):
    terminal = StringIO()
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        app._terminal_file = terminal
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        def write_from_worker() -> None:
            app.append_output("thread append")
            app._typed_output.write("thread flush\n")
            app._typed_output.flush()

        await asyncio.to_thread(write_from_worker)
        async with asyncio.timeout(1):
            while "thread flush" not in app._transcript_view.plain_text():
                await asyncio.sleep(0.01)

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)

    assert "thread append" in app._transcript_view.plain_text()
    assert "thread flush" in app._transcript_view.plain_text()
    assert "thread append" in terminal.getvalue()
    assert "thread flush" in terminal.getvalue()


@pytest.mark.asyncio
async def test_flush_output_waits_for_typed_transcript_commit(tmp_path):
    terminal = StringIO()
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        app._terminal_file = terminal
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        app._emit_ui_event(AnswerBlock("answer before the next prompt"))
        await app.flush_output()

        assert "answer before the next prompt" in app._transcript_view.plain_text()
        assert terminal.getvalue() == ""

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_flush_output_drains_output_added_after_completed_commit(tmp_path):
    terminal = StringIO()
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        app._terminal_file = terminal
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        app.append_output("first")
        await app.flush_output()
        app.append_output("second")
        await app.flush_output()

        transcript = app._transcript_view.plain_text()
        assert transcript.index("first") < transcript.index("second")
        assert terminal.getvalue() == ""

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)


def test_batched_transcript_events_commit_to_the_transcript(tmp_path):
    app = _make_app(tmp_path)

    with app.batch_transcript_output():
        app._emit_ui_event(AnswerBlock("restored one"))
        app._emit_ui_event(AnswerBlock("restored two"))

    assert "restored one" in app._transcript_view.plain_text()
    assert "restored two" in app._transcript_view.plain_text()


def test_exit_flush_boundary_excludes_seeded_history_and_flushes_new_output(tmp_path):
    app = _make_app(tmp_path)
    terminal = StringIO()
    app._terminal_file = terminal
    app.append_output("restored history")
    app.mark_exit_flush_boundary()
    app.append_output("new session output")

    app._flush_transcript_on_exit()
    app._flush_transcript_on_exit()

    assert terminal.getvalue() == "new session output\n"
    assert "restored history" in app._transcript_view.plain_text()


def test_exit_flush_tolerates_closed_terminal_stream(tmp_path):
    app = _make_app(tmp_path)
    terminal = StringIO()
    terminal.close()
    app._terminal_file = terminal
    app.append_output("must not mask shutdown")

    app._flush_transcript_on_exit()


def test_exit_flush_tolerates_broken_application_output(tmp_path):
    class BrokenRestoreOutput(DummyOutput):
        def enable_autowrap(self) -> None:
            raise BrokenPipeError

    terminal = StringIO()
    app = _make_app(tmp_path, output=BrokenRestoreOutput())
    app._terminal_file = terminal
    app.append_output("transcript survives restore failure")

    app._flush_transcript_on_exit()

    assert terminal.getvalue() == "transcript survives restore failure\n"


def test_transcript_page_keys_are_bound_to_the_internal_viewport(tmp_path):
    app = _make_app(tmp_path)
    bindings = _build_key_bindings(app)
    explicit_keys = {binding.keys for binding in bindings.bindings}

    assert (Keys.PageUp,) in explicit_keys
    assert (Keys.PageDown,) in explicit_keys


def test_layered_layout_pins_transcript_above_transient_surfaces_and_input(tmp_path):
    app = _make_app(tmp_path)

    root = app.application.layout.container
    children = root.children

    assert len(children) == 14
    assert children[0] is app.transcript_container
    assert children[1:12] == [
        app.plan_container,
        app.steering_container,
        app.preview_container,
        app.tool_container,
        app.task_container,
        app.work_container,
        app.notice_container,
        app.palette_container,
        app.rewind_container,
        app.evidence_container,
        app.approval_container,
    ]
    assert children[12] is app.composer_container
    assert app.input_row.children[0] is app.prompt_window
    assert app.input_row.children[1] is app.input_window
    assert app.input_row.children[2].style == "class:input"
    assert children[13].style == "class:status"


@pytest.mark.asyncio
async def test_idle_transient_layout_contains_only_composer_and_footer(tmp_path):
    output = DummyOutput()
    output.get_size = lambda: Size(rows=30, columns=120)
    app = _make_app(tmp_path, output=output)
    screen = Screen()

    with set_app(app.application):
        app.application.layout.container.write_to_screen(
            screen,
            MouseHandlers(),
            WritePosition(xpos=0, ypos=0, width=120, height=30),
            parent_style="",
            erase_bg=False,
            z_index=None,
        )
    await asyncio.sleep(0)

    rows = [
        "".join(screen.data_buffer[row][column].char for column in range(120)).rstrip()
        for row in range(30)
    ]
    assert rows[-2] == "❯"
    assert screen.data_buffer[28][119].char == " "
    assert "manual mode on · foundation · 1234 · $0.00" in rows[-1]
    assert all(not row for row in rows[:-2])
    assert app.application.full_screen is True


@pytest.mark.asyncio
async def test_prelaunch_transcript_is_inside_the_scrollable_viewport(tmp_path):
    output = DummyOutput()
    output.get_size = lambda: Size(rows=30, columns=120)
    app = _make_app(tmp_path, output=output)
    app.append_output("first answer line\nsecond answer line")
    screen = Screen()

    with set_app(app.application):
        app.application.layout.container.write_to_screen(
            screen,
            MouseHandlers(),
            WritePosition(xpos=0, ypos=0, width=120, height=30),
            parent_style="",
            erase_bg=False,
            z_index=None,
        )
    await asyncio.sleep(0)
    rows = [
        "".join(screen.data_buffer[row][column].char for column in range(120)).rstrip()
        for row in range(30)
    ]

    assert app._transcript_view.plain_text().startswith("first answer line")
    assert any("first answer line" in row for row in rows[:-2])
    assert rows[-2] == "❯"
    assert "foundation" in rows[-1]


@pytest.mark.asyncio
async def test_inline_approval_enter_allows_without_losing_typed_input(tmp_path):
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        app.input_buffer.text = "draft steer"
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)
        decision = asyncio.create_task(
            app.request_approval(
                "Allow load_skill?", ("Allow once", "Deny"), 30, "deny"
            )
        )
        await asyncio.sleep(0)

        assert app._approval_visible() is True
        pipe_input.send_text("must not enter the hidden draft")
        await asyncio.sleep(0.05)
        pipe_input.send_text("\r")
        assert await asyncio.wait_for(decision, timeout=1) == "Allow once"
        assert app.input_buffer.text == "draft steer"

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_inline_approval_tab_selects_deny(tmp_path):
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)
        decision = asyncio.create_task(
            app.request_approval("Allow write?", ("Allow once", "Deny"), 30, "deny")
        )
        await asyncio.sleep(0)

        pipe_input.send_bytes(b"\t\r")
        assert await asyncio.wait_for(decision, timeout=1) == "Deny"

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_inline_approval_escape_denies_and_exit_denies_pending(tmp_path):
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)
        escaped = asyncio.create_task(
            app.request_approval("Allow net?", ("Allow once", "Deny"), 30, "deny")
        )
        await asyncio.sleep(0)

        pipe_input.send_bytes(b"\x1b")
        assert await asyncio.wait_for(escaped, timeout=1) == "Deny"

        pending = asyncio.create_task(
            app.request_approval("Allow spend?", ("Allow once", "Deny"), 30, "deny")
        )
        await asyncio.sleep(0)
        app.exit()
        assert await asyncio.wait_for(pending, timeout=1) == "Deny"
        await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_inline_approval_surface_is_one_line_and_width_bounded(tmp_path):
    app = _make_app(tmp_path)
    app.application.output.get_size = lambda: Size(rows=24, columns=36)
    decision = asyncio.create_task(
        app.request_approval(
            "Allow a very long and consequential operation?",
            ("Allow once", "Deny"),
            30,
            "deny",
        )
    )
    await asyncio.sleep(0)

    rendered = "".join(text for _, text in app._approval_text())
    footer = "".join(text for _, text in app._status_text())
    assert "\n" not in rendered
    assert get_cwidth(rendered) <= 36
    assert "❯" not in rendered
    assert "enter" in footer
    assert "esc" in footer

    app._deny_approval()
    assert await decision == "Deny"


@pytest.mark.asyncio
async def test_running_application_updates_internal_transcript_above_prompt(tmp_path):
    recording = RecordingOutput()
    terminal = StringIO()
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input, output=recording)
        app._terminal_file = terminal
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        app.append_output("internal transcript line")
        await asyncio.sleep(0.25)
        assert terminal.getvalue() == ""
        assert "internal transcript line" in app._transcript_view.plain_text()
        app.exit()
        await asyncio.wait_for(run_task, timeout=1)

    assert terminal.getvalue().count("internal transcript line") == 1


def test_layered_input_height_tracks_typed_lines_without_footer_gap(tmp_path):
    app = _make_app(tmp_path)

    assert app._input_height().preferred == 1

    app.input_buffer.text = "one\ntwo\nthree"

    assert app._input_height().preferred == 3


def test_layered_input_height_counts_soft_wrapped_visual_rows(tmp_path):
    app = _make_app(tmp_path)
    input_width = 80 - (app._prompt_width().preferred or 0)
    text = "x" * (input_width + 1)
    app.input_buffer.set_document(Document(text, cursor_position=len(text)))

    assert app._input_height().preferred == 2


def test_layered_input_height_reserves_cursor_cell_at_exact_wrap(tmp_path):
    app = _make_app(tmp_path)
    input_width = 80 - (app._prompt_width().preferred or 0)
    text = "x" * input_width
    app.input_buffer.set_document(Document(text, cursor_position=len(text)))

    assert app._input_height().preferred == 2


def test_layered_input_height_uses_unicode_cell_width(tmp_path):
    app = _make_app(tmp_path)
    input_width = 80 - (app._prompt_width().preferred or 0)
    wide_text = "界" * (input_width // 2)
    app.input_buffer.set_document(Document(wide_text, cursor_position=len(wide_text)))
    assert app._input_height().preferred == 2

    combined_text = "e\u0301" * (input_width - 1)
    app.input_buffer.set_document(
        Document(combined_text, cursor_position=len(combined_text))
    )
    assert app._input_height().preferred == 1


def test_layered_output_retains_long_transcript_history(tmp_path):
    app = _make_app(tmp_path, max_output_lines=40)
    lines = [f"line {index}" for index in range(45)]
    output = StringIO()

    with redirect_stdout(output):
        app.append_output("\n".join(lines))

    assert output.getvalue() == ""
    text = app._transcript_view.plain_text()
    assert "earlier output lines hidden" not in text
    assert "line 0" in text
    assert "line 44" in text


def test_layered_output_retains_and_scrolls_past_legacy_history_cap(tmp_path):
    app = _make_app(tmp_path, max_output_lines=40)
    line_count = 20_025

    app.append_output("\n".join(f"HISTORY-{index:05d}" for index in range(line_count)))

    document = app._transcript_view.buffer.document
    assert app._transcript_view.history_line_count == line_count
    assert (
        app._transcript_view.loaded_line_count <= app._transcript_view.window_capacity
    )
    assert len(document.lines) == app._transcript_view.loaded_line_count
    assert document.lines[0] != "HISTORY-00000"
    assert document.lines[-1] == "HISTORY-20024"
    assert app._exit_transcript.omitted_line_count == 0
    assert app._exit_transcript.plain_lines[0] == "HISTORY-00000"
    assert app._transcript_view.plain_text().startswith("HISTORY-00000\n")

    app._transcript_view.scroll_to_row(0)
    assert app._transcript_view.following_tail is False
    assert app._transcript_view.window_start == 0
    assert app._transcript_view.global_cursor_row == 0
    assert app._transcript_view.buffer.document.lines[0] == "HISTORY-00000"
    assert app._transcript_view.formatted_line(0) == [("", "HISTORY-00000")]

    paused_text = app._transcript_view.buffer.text
    app.append_output("HISTORY-20025")
    assert app._transcript_view.history_line_count == line_count + 1
    assert app._transcript_view.global_cursor_row == 0
    assert app._transcript_view.buffer.text == paused_text

    app._transcript_view.scroll_to_row(line_count - 1)
    assert app._transcript_view.following_tail is False
    assert app._transcript_view.global_cursor_row == line_count - 1

    app._transcript_view.scroll_to_row(line_count)
    assert app._transcript_view.following_tail is True
    assert app._transcript_view.global_cursor_row == line_count
    assert app._transcript_view.buffer.document.lines[-1] == "HISTORY-20025"


def test_chunked_appends_keep_prompt_buffer_bounded(tmp_path, monkeypatch):
    app = _make_app(tmp_path)

    def reject_full_history_materialization(_transcript):
        raise AssertionError("append path materialized the complete transcript")

    monkeypatch.setattr(
        TerminalTranscript,
        "plain_text",
        property(reject_full_history_materialization),
    )
    for index in range(2_000):
        app.append_output(f"chunk-{index:05d}")

    assert app._transcript_view.history_line_count == 2_000
    assert (
        app._transcript_view.loaded_line_count <= app._transcript_view.window_capacity
    )
    assert (
        len(app._transcript_view.buffer.document.lines)
        <= app._transcript_view.window_capacity
    )


def test_page_navigation_crosses_loaded_windows_and_reaches_both_ends(tmp_path):
    app = _make_app(tmp_path)
    line_count = 1_500
    app.append_output("\n".join(f"page-{index:04d}" for index in range(line_count)))

    for _ in range(20):
        app._transcript_view.scroll_page(-1, 128)

    assert app._transcript_view.global_cursor_row == 0
    assert app._transcript_view.window_start == 0
    assert app._transcript_view.buffer.document.lines[0] == "page-0000"
    assert app._transcript_view.following_tail is False

    for _ in range(20):
        app._transcript_view.scroll_page(1, 128)

    assert app._transcript_view.global_cursor_row == line_count - 1
    assert app._transcript_view.buffer.document.lines[-1] == "page-1499"
    assert app._transcript_view.following_tail is True


def test_typed_console_output_renders_markdown_before_transcript_commit(tmp_path):
    app = _make_app(tmp_path)
    output = StringIO()
    rich_console = Console(file=output, no_color=True)

    with app.capture_output(rich_console):
        rich_console.print(
            Markdown("**Bold** [docs](https://example.com)\n\n- one\n- two")
        )

    text = output.getvalue()
    assert "**Bold**" not in text
    assert "Bold" in text
    assert "docs" in text
    assert "one" in text
    assert "two" in text
    assert app._transcript_view.plain_text() == ""


def test_native_console_preserves_rich_terminal_styles(tmp_path, monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    output = StringIO()

    Console(file=output, force_terminal=True).print(
        Markdown("**dynamic bold** and `code`")
    )

    assert "dynamic bold" in output.getvalue()
    assert "\x1b[" in output.getvalue()


def test_live_preview_sanitizes_controls_and_renders_markdown(tmp_path):
    stream = StreamStatusTracker("12345678-abcdef")
    app = _make_app(tmp_path, stream_status=stream)
    stream.consume(
        "llm:stream_block_start",
        {
            "session_id": "12345678-abcdef",
            "request_id": "request",
            "block_index": 0,
            "block_type": "text",
        },
    )
    stream.consume(
        "llm:stream_block_delta",
        {
            "session_id": "12345678-abcdef",
            "request_id": "request",
            "block_index": 0,
            "text": "**Bold** [docs](https://example.com)\x1b[31m\rnext",
        },
    )

    preview = app._transcript_view.preview_plain_text()
    assert "\x1b" not in preview
    assert "\r" not in preview
    assert "**Bold**" not in preview
    assert "docs (https://example.com)" in preview


def test_live_preview_uses_current_terminal_width(tmp_path):
    stream = StreamStatusTracker("12345678-abcdef")
    app = _make_app(tmp_path, stream_status=stream)
    app._terminal_size = lambda: (24, 120)
    stream.consume(
        "llm:stream_block_delta",
        {
            "session_id": "12345678-abcdef",
            "request_id": "request",
            "block_index": 0,
            "text": "x" * 100,
        },
    )

    assert "x" * 100 in app._transcript_view.preview_plain_text()


def test_running_status_carries_elapsed_tokens_and_interrupt_hint(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path, get_is_running=lambda: True)
    monkeypatch.setattr("amplifier_app_cli.ui.layered_repl.monotonic", lambda: 0.0)
    app._running_started_at = 0.0
    monkeypatch.setattr("amplifier_app_cli.ui.layered_repl.monotonic", lambda: 2.0)
    status = "".join(text for _, text in app._working_text())

    assert status == "✧ working · 2.0s · ↓ 0 tok · cost pending · esc to interrupt"
    assert app.application.refresh_interval == 0.2


def test_agent_activity_drives_spinner_when_root_is_idle(tmp_path, monkeypatch):
    tracker = TaskStatusTracker("12345678-abcdef")
    app = _make_app(tmp_path, task_tracker=tracker)
    monkeypatch.setattr("amplifier_app_cli.ui.layered_repl.monotonic", lambda: 0.0)

    assert app._work_visible() is False
    tracker.consume(
        "delegate:agent_spawned",
        {"agent": "reviewer", "sub_session_id": "child-reviewer"},
    )
    assert app._work_visible() is True
    assert "working" in "".join(text for _, text in app._working_text()).lower()
    tracker.consume(
        "delegate:agent_completed",
        {"agent": "reviewer", "sub_session_id": "child-reviewer"},
    )
    assert app._work_visible() is False


def test_working_surface_shows_root_task_and_live_agent_tree(tmp_path, monkeypatch):
    tracker = TaskStatusTracker("12345678-abcdef")
    runtime = RuntimeStatusTracker("12345678-abcdef")
    for session_id, agent, instruction in (
        ("child-expert", "amplifier-expert", "Review the architecture"),
        ("child-architect", "zen-architect", "Design the mission flow"),
        ("child-critic", "crusty-old-engineer", "Challenge the risks"),
    ):
        tracker.consume(
            "delegate:agent_spawned",
            {
                "sub_session_id": session_id,
                "parent_session_id": "12345678-abcdef",
                "agent": agent,
                "instruction": instruction,
            },
        )
    runtime.consume(
        "tool:pre",
        {
            "session_id": "child-expert",
            "tool_call_id": "read-1",
            "tool_name": "read",
            "tool_input": {"description": "Inspecting the flagship spec"},
        },
    )
    app = _make_app(
        tmp_path,
        task_tracker=tracker,
        runtime_status=runtime,
        get_is_running=lambda: True,
        get_task_title=lambda: "Evaluate Amplifier Flagship missions",
    )
    app._terminal_size = lambda: (40, 168)
    app._running_started_at = 0.0
    monkeypatch.setattr("amplifier_app_cli.ui.layered_repl.monotonic", lambda: 23.0)

    working = "".join(text for _, text in app._working_text())
    lines = working.splitlines()

    assert "Working on Evaluate Amplifier Flagship missions" in lines[0]
    assert "3 agents" in lines[0]
    assert "amplifier-expert" in lines[1]
    assert "Inspecting the flagship spec" in lines[1]
    assert "zen-architect" in lines[2]
    assert "Design the mission flow" in lines[2]
    assert "crusty-old-engineer" in lines[3]
    assert "Challenge the risks" in lines[3]
    assert "▶" not in working
    assert app._working_height().preferred == 4


def test_runtime_tool_lifecycle_renders_running_and_collapsed_done_blocks(tmp_path):
    runtime = RuntimeStatusTracker("12345678-abcdef")
    app = _make_app(tmp_path, runtime_status=runtime)

    runtime.consume(
        "tool:pre",
        {
            "session_id": "12345678-abcdef",
            "tool_call_id": "call-1",
            "tool_name": "shell",
            "tool_input": {"command": "uv run pytest -q"},
        },
    )
    running = "".join(text for _, text in app._running_tools_text())
    assert "└ uv run pytest -q" in running

    runtime.consume(
        "tool:post",
        {
            "session_id": "12345678-abcdef",
            "tool_call_id": "call-1",
            "tool_name": "shell",
            "result": {"output": {"stdout": "all passed", "exit_code": 0}},
        },
    )

    transcript = app._transcript_view.plain_text()
    assert "Ran 1 shell command" in transcript
    assert "uv run pytest -q" not in transcript
    assert "ctrl-o expand" not in transcript
    assert len(transcript.splitlines()) == 1


def test_ctrl_o_expands_newest_tool_once_after_older_debug_output(tmp_path):
    runtime = RuntimeStatusTracker("12345678-abcdef")
    app = _make_app(tmp_path, runtime_status=runtime)
    app._capture_untyped_output("older raw detail")
    runtime.consume(
        "tool:pre",
        {
            "session_id": "12345678-abcdef",
            "tool_call_id": "call-newest",
            "tool_name": "shell",
            "tool_input": {"command": "printf newer"},
        },
    )
    runtime.consume(
        "tool:post",
        {
            "session_id": "12345678-abcdef",
            "tool_call_id": "call-newest",
            "tool_name": "shell",
            "result": {"output": {"stdout": "newer tool output", "exit_code": 0}},
        },
    )

    app.expand_latest_tool()
    transcript = app._transcript_view.plain_text()
    assert "newer tool output" in transcript
    assert "older raw detail" not in transcript

    app.expand_latest_tool()
    assert app._transcript_view.plain_text().count("newer tool output") == 1


def test_evidence_key_surface_reveals_claim_and_expands_selected_tool(tmp_path):
    runtime = RuntimeStatusTracker("12345678-abcdef")
    runtime.consume(
        "tool:pre",
        {
            "session_id": "12345678-abcdef",
            "tool_call_id": "tests-1",
            "tool_name": "shell",
            "tool_input": {"command": "uv run pytest -q"},
        },
    )
    runtime.consume(
        "tool:post",
        {
            "session_id": "12345678-abcdef",
            "tool_call_id": "tests-1",
            "tool_name": "shell",
            "result": {"output": {"stdout": "all tests passed", "exit_code": 0}},
        },
    )
    evidence = EvidenceLinkModel()
    evidence.record("answer-1", "All tests passed.", runtime.tool_snapshot())
    app = _make_app(tmp_path, runtime_status=runtime, evidence_model=evidence)
    assert app.open_evidence_picker() is True
    assert "¹" in app._transcript_view.plain_text()
    assert app._transcript_view.plain_text().count("All tests passed.") == 1
    assert "enter expand" in "".join(text for _, text in app._evidence_text())
    app._accept_evidence()

    assert "all tests passed" in app._transcript_view.plain_text()
    assert app._evidence_visible() is False
    assert app._running_tools_visible() is False


def test_runtime_telemetry_feeds_footer_cost_and_working_tokens(tmp_path):
    runtime = RuntimeStatusTracker("12345678-abcdef")
    app = _make_app(
        tmp_path,
        runtime_status=runtime,
        get_is_running=lambda: True,
    )
    runtime.consume(
        "llm:response",
        {
            "session_id": "12345678-abcdef",
            "provider": "openai",
            "model": "gpt",
            "duration_ms": 6100,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 250,
                "cache_read_tokens": 800,
                "cost_usd": "0.04",
            },
        },
    )

    footer = "".join(text for _, text in app._status_text())
    working = "".join(text for _, text in app._working_text())
    assert "$0.04" in footer
    assert "↓ 1.2k tok" in working
    assert "$0.04" in working


def test_streaming_status_estimates_progress_before_final_usage(tmp_path):
    runtime = RuntimeStatusTracker("12345678-abcdef")
    stream = StreamStatusTracker("12345678-abcdef")
    app = _make_app(
        tmp_path,
        runtime_status=runtime,
        stream_status=stream,
        get_is_running=lambda: True,
    )
    runtime.consume(
        "llm:response",
        {
            "session_id": "12345678-abcdef",
            "usage": {"total_tokens": 1000, "cost_usd": "0.10"},
        },
    )
    runtime.consume("prompt:submit", {"session_id": "12345678-abcdef"})
    stream.consume(
        "llm:stream_block_delta",
        {
            "session_id": "12345678-abcdef",
            "block_type": "text",
            "text": "x" * 400,
        },
    )

    working = "".join(text for _, text in app._working_text())
    assert "↓ 100 tok" in working
    assert "~$0.01" in working


def test_command_palette_is_inline_bounded_and_source_tagged(tmp_path):
    app = _make_app(
        tmp_path,
        commands={
            f"/command-{index}": {
                "action": f"action-{index}",
                "description": f"description {index}",
            }
            for index in range(12)
        },
    )
    app.input_buffer.text = "/"

    assert app._palette_visible() is True
    assert app._palette_height().preferred == 8
    rendered = "".join(text for _, text in app._palette_text())
    assert "During" in rendered
    assert "[built-in]" in rendered
    assert len(rendered.splitlines()) == 8


def test_command_palette_moves_accepts_and_dismisses(tmp_path):
    submissions = []
    app = _make_app(
        tmp_path,
        commands={
            "/alpha": {"action": "alpha", "description": "first"},
            "/beta": {"action": "beta", "description": "second"},
        },
        on_submit=submissions.append,
    )
    app.input_buffer.text = "/"

    app._move_palette(1)
    selected = app._palette_snapshot().selected
    assert selected is not None
    assert selected.name == "/beta"
    app._accept_palette_selection()
    assert submissions[0].text == "/beta"

    app.input_buffer.text = "/"
    app._dismiss_palette()
    assert app._palette_visible() is False
    app.input_buffer.text = "/a"
    assert app._palette_visible() is True


def test_pending_steer_is_pinned_until_step_boundary_consumes_it(tmp_path):
    queue = SteeringQueue()
    app = _make_app(tmp_path, steering_queue=queue)

    queue.enqueue("use sqlite, not json")

    assert app._steering_visible() is True
    text = "".join(fragment for _, fragment in app._steering_text())
    assert 'steer queued: "use sqlite, not json"' in text
    assert "next step boundary" in text

    queue.consume_next()
    assert app._steering_visible() is False


def test_completed_plan_commits_once_to_transcript_and_clears(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    app = _make_app(tmp_path, task_tracker=tracker)
    tracker.consume(
        "tool:pre",
        {
            "session_id": "12345678-abcdef",
            "tool_name": "todo",
            "tool_input": {
                "todos": [
                    {
                        "content": "Audit paths",
                        "activeForm": "Auditing paths",
                        "status": "in_progress",
                    }
                ]
            },
        },
    )
    tracker.consume(
        "tool:post",
        {
            "session_id": "12345678-abcdef",
            "tool_name": "todo",
            "result": {"todos": [{"content": "Audit paths", "status": "in_progress"}]},
        },
    )
    assert app._plan_visible() is True

    tracker.consume(
        "tool:pre",
        {
            "session_id": "12345678-abcdef",
            "tool_name": "todo",
            "tool_input": {
                "todos": [{"content": "Audit paths", "status": "completed"}]
            },
        },
    )
    tracker.consume(
        "tool:post",
        {
            "session_id": "12345678-abcdef",
            "tool_name": "todo",
            "result": {"todos": [{"content": "Audit paths", "status": "completed"}]},
        },
    )

    transcript = app._transcript_view.plain_text()
    assert "Plan complete" in transcript
    assert "Audit paths" in transcript
    assert app._plan_visible() is False
    app.exit()
    assert "Plan incomplete" not in app._transcript_view.plain_text()


def test_exit_commits_incomplete_plan_before_chrome_closes(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.set_todos([{"content": "Finish verification", "status": "in_progress"}])
    app = _make_app(tmp_path, task_tracker=tracker)

    app.exit()

    transcript = app._transcript_view.plain_text()
    assert "Plan incomplete" in transcript
    assert "■ Finish verification" in transcript


def test_rewind_picker_selects_an_addressable_turn_rule(tmp_path):
    ledger = OutcomeLedger()
    for index in range(1, 3):
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
    selected = []
    app = _make_app(
        tmp_path,
        outcome_ledger=ledger,
        on_rewind=selected.append,
    )

    assert app.open_rewind_picker() is True
    assert "checkpoint-2" in "".join(text for _, text in app._rewind_text())
    app._move_rewind(-1)
    assert "checkpoint-1" in "".join(text for _, text in app._rewind_text())
    app._accept_rewind()

    assert selected == [ledger.entries[0]]
    assert app._rewind_visible() is False


def test_rewind_picker_reports_empty_ledger_without_opening(tmp_path):
    app = _make_app(tmp_path, outcome_ledger=OutcomeLedger())

    assert app.open_rewind_picker() is False
    assert app._rewind_visible() is False


def test_ambient_state_colors_tab_and_background_completion_notifies(tmp_path):
    app = _make_app(tmp_path)
    terminal = StringIO()
    app._terminal_file = terminal

    app.emit_ambient_state(is_running=True, needs_count=0)
    app.emit_ambient_state(is_running=False, needs_count=1)
    app.mark_backgrounded()
    app.notify_turn_complete("tests ✔")

    output = terminal.getvalue()
    assert "bg;red;brightness;224" in output
    assert "777;notify;Amplifier turn complete;tests ✔" in output


@pytest.mark.asyncio
async def test_background_command_suspends_tui_through_owned_shell_task(
    tmp_path, monkeypatch
):
    started = asyncio.Event()
    keep_open = asyncio.Event()
    unwound = asyncio.Event()
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)

        async def fake_background_shell():
            started.set()
            try:
                await keep_open.wait()
            finally:
                await asyncio.sleep(0)
                unwound.set()

        monkeypatch.setattr(app, "_run_background_shell", fake_background_shell)
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        assert app.mark_backgrounded() is True
        await asyncio.wait_for(started.wait(), timeout=1)
        app.exit()
        await asyncio.wait_for(run_task, timeout=1)
        assert unwound.is_set()
        assert app._background_shell_task is None


def test_background_shell_keeps_output_in_internal_transcript(tmp_path):
    app = _make_app(tmp_path)
    app._background_terminal_active = True

    app.append_output("background output")

    assert "background output" in app._transcript_view.plain_text()


def test_agent_lane_board_shows_cost_and_enter_esc_focus(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    runtime = RuntimeStatusTracker("12345678-abcdef")
    tracker.consume(
        "delegate:agent_spawned",
        {
            "sub_session_id": "child-coder",
            "parent_session_id": "12345678-abcdef",
            "agent": "coder",
            "instruction": "Migrating store",
        },
    )
    runtime.consume(
        "llm:response",
        {
            "session_id": "child-coder",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cost_usd": "0.31",
            },
        },
    )
    app = _make_app(
        tmp_path,
        task_tracker=tracker,
        runtime_status=runtime,
    )

    app.toggle_task_pane()
    board = "".join(text for _, text in app._task_pane_text())
    assert "Agent lanes" in board
    assert "coder" in board
    assert "Migrating store" in board
    assert "$0.31" in board

    app._session_store = SessionStore(tmp_path / "sessions")
    app._session_store.save(
        "child-coder",
        [
            {"role": "user", "content": "Implement the store"},
            {"role": "assistant", "content": "child result"},
        ],
        {},
    )
    app.focus_selected_lane()
    assert app._agent_lanes.focused_session_id == "child-coder"
    assert "Implement the store" in app._transcript_view.plain_text()
    assert "child result" in app._transcript_view.plain_text()
    app.leave_agent_focus()
    assert app._agent_lanes.focused_session_id == "12345678-abcdef"
    assert app.tasks_visible is True
    app.leave_agent_focus()
    assert app.tasks_visible is False


def test_focused_agent_transcript_follows_new_messages_without_replaying(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.consume(
        "delegate:agent_spawned",
        {
            "sub_session_id": "child-coder",
            "parent_session_id": "12345678-abcdef",
            "agent": "coder",
        },
    )
    app = _make_app(tmp_path, task_tracker=tracker)
    app._session_store = SessionStore(tmp_path / "sessions")
    app._session_store.save(
        "child-coder",
        [{"role": "assistant", "content": "first child update"}],
        {},
    )

    app.focus_selected_lane()
    app._session_store.save(
        "child-coder",
        [
            {"role": "assistant", "content": "first child update"},
            {"role": "assistant", "content": "second child update"},
        ],
        {},
    )
    assert app._sync_focused_child_transcript() == 1
    assert app._sync_focused_child_transcript() == 0

    transcript = app._transcript_view.plain_text()
    assert transcript.count("first child update") == 1
    assert transcript.count("second child update") == 1


def test_agent_focus_loads_complete_conversation_not_fixed_excerpt(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.consume(
        "delegate:agent_spawned",
        {"sub_session_id": "child-long", "agent": "researcher"},
    )
    app = _make_app(tmp_path, task_tracker=tracker)
    app._session_store = SessionStore(tmp_path / "sessions")
    app._session_store.save(
        "child-long",
        [
            {"role": "assistant", "content": f"child message {index}"}
            for index in range(15)
        ],
        {},
    )

    app.focus_selected_lane()

    transcript = app._transcript_view.plain_text()
    assert "child message 0" in transcript
    assert "child message 14" in transcript


@pytest.mark.asyncio
async def test_agent_focus_follows_persisted_updates_while_active(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.consume(
        "delegate:agent_spawned",
        {"sub_session_id": "child-live", "agent": "coder"},
    )
    app = _make_app(tmp_path, task_tracker=tracker)
    app._session_store = SessionStore(tmp_path / "sessions")
    app._session_store.save(
        "child-live",
        [{"role": "assistant", "content": "initial update"}],
        {},
    )
    app._owner_loop = asyncio.get_running_loop()
    app.focus_selected_lane()
    app._session_store.save(
        "child-live",
        [
            {"role": "assistant", "content": "initial update"},
            {"role": "assistant", "content": "live update"},
        ],
        {},
    )

    await asyncio.sleep(0.3)

    assert "live update" in app._transcript_view.plain_text()
    app._stop_focused_transcript_follow()
    app._owner_loop = None


def test_agent_focus_scopes_tool_noise_to_active_transcript(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    runtime = RuntimeStatusTracker("12345678-abcdef")
    tracker.consume(
        "delegate:agent_spawned",
        {
            "sub_session_id": "child-coder",
            "parent_session_id": "12345678-abcdef",
            "agent": "coder",
        },
    )
    app = _make_app(tmp_path, task_tracker=tracker, runtime_status=runtime)
    for session_id, call_id, command in (
        ("12345678-abcdef", "parent-call", "echo parent"),
        ("child-coder", "child-call", "echo child-internal"),
    ):
        runtime.consume(
            "tool:pre",
            {
                "session_id": session_id,
                "tool_call_id": call_id,
                "tool_name": "shell",
                "tool_input": {"command": command},
            },
        )
        runtime.consume(
            "tool:post",
            {
                "session_id": session_id,
                "tool_call_id": call_id,
                "tool_name": "shell",
                "result": {"output": {"stdout": command, "exit_code": 0}},
            },
        )

    parent_transcript = app._transcript_view.plain_text()
    assert parent_transcript.count("Ran 1 shell command") == 1
    assert ("child-coder", "child-call") not in app._rendered_terminal_tools

    app.focus_selected_lane()

    assert app._transcript_view.plain_text().count("Ran 1 shell command") == 2
    assert ("child-coder", "child-call") in app._rendered_terminal_tools


@pytest.mark.parametrize(
    ("lifecycle", "label"),
    (
        ("interrupted", "Plan interrupted"),
        ("failed", "Plan failed"),
        ("incomplete", "Plan incomplete"),
    ),
)
def test_non_terminal_plan_lifecycle_commits_to_transcript(tmp_path, lifecycle, label):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.set_todos(
        [
            {"content": "Audit paths", "status": "completed"},
            {"content": "Implement fix", "status": "in_progress"},
            {"content": "Run tests", "status": "pending"},
        ]
    )
    app = _make_app(tmp_path, task_tracker=tracker)

    assert app.commit_plan_state(lifecycle) is True
    assert app.commit_plan_state(lifecycle) is False

    transcript = app._transcript_view.plain_text()
    assert label in transcript
    assert "✔ Audit paths" in transcript
    assert "■ Implement fix" in transcript
    assert "□ Run tests" in transcript


@pytest.mark.asyncio
async def test_escape_interrupts_running_turn_without_disabling_input(tmp_path):
    interrupts = []
    running = True

    def on_interrupt() -> bool:
        interrupts.append(True)
        return True

    with create_pipe_input() as pipe_input:
        app = _make_app(
            tmp_path,
            input=pipe_input,
            get_is_running=lambda: running,
        )
        app._on_interrupt = on_interrupt
        app.input_buffer.text = "steer while running"
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        pipe_input.send_bytes(b"\x1b")
        await asyncio.sleep(0.6)
        assert interrupts == [True]
        assert app.input_buffer.text == "steer while running"

        running = False
        app.exit()
        await asyncio.wait_for(run_task, timeout=1)


def test_plan_widget_stays_separate_from_compact_agent_lanes(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.set_todos(
        [
            {
                "content": "Inspect input",
                "activeForm": "Inspecting input",
                "status": "in_progress",
            }
        ]
    )
    tracker.consume(
        "delegate:agent_spawned",
        {"agent": "reviewer", "sub_session_id": "child_reviewer"},
    )
    app = _make_app(tmp_path, task_tracker=tracker)

    plan = "".join(text for _, text in app._plan_text())
    lanes = "".join(text for _, text in app._task_pane_text())

    assert "Inspecting input" in plan
    assert "Agent lanes" in lanes
    assert "reviewer" in lanes
    assert "working" in lanes
    assert "Inspecting input" not in lanes


def test_task_pane_toggle_and_close_preserve_typed_input(tmp_path):
    app = _make_app(tmp_path)
    app.input_buffer.text = "draft message"

    app.toggle_task_pane()
    assert app.tasks_visible is True
    assert app.input_buffer.text == "draft message"

    app.close_task_pane()
    assert app.tasks_visible is False
    assert app.input_buffer.text == "draft message"


@pytest.mark.asyncio
async def test_task_pane_keyboard_shortcuts_preserve_typed_input(tmp_path):
    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, input=pipe_input)
        app.input_buffer.text = "draft message"
        run_task = asyncio.create_task(app.run_async())

        await asyncio.sleep(0.05)
        pipe_input.send_bytes(b"\x14")
        await asyncio.sleep(0.05)
        assert app.tasks_visible is True
        assert app.input_buffer.text == "draft message"

        pipe_input.send_bytes(b"\x1b")
        await asyncio.sleep(0.6)
        assert app.tasks_visible is False
        assert app.input_buffer.text == "draft message"

        app.exit()
        await asyncio.wait_for(run_task, timeout=1)


def test_layered_plan_is_live_while_footer_only_advertises_task_toggle(tmp_path):
    tracker = TaskStatusTracker("12345678-abcdef")
    tracker.set_todos(
        [{"content": "Test", "activeForm": "Testing", "status": "completed"}]
    )
    app = _make_app(tmp_path, task_tracker=tracker)

    rendered = "".join(text for _, text in app._status_text())
    plan = "".join(text for _, text in app._plan_text())

    assert "ctrl-t" in rendered
    assert "todo 1/1" not in rendered
    assert "✔ Test" in plan


def test_layered_footer_keeps_task_shortcut_visible_at_60_columns(tmp_path):
    app = _make_app(tmp_path, bundle_name="foundation-" * 12)
    app.application.output.get_size = lambda: SimpleNamespace(rows=24, columns=60)

    rendered = "".join(text for _, text in app._status_text())

    assert rendered.strip().startswith("manual")
    assert "tab" in rendered
    assert "ctrl-t" in rendered
    assert len(rendered) <= 60


def test_active_mode_prompt_uses_its_rendered_width(tmp_path):
    app = _make_app(tmp_path, get_active_mode=lambda: "plan")

    prompt_text = "".join(text for _, text in app._prompt_text())

    assert prompt_text == "❯ [plan] "
    assert app._prompt_width().preferred == len(prompt_text)


def test_prompt_badge_uses_the_normative_mode_color(tmp_path):
    app = _make_app(tmp_path, get_active_mode=lambda: "auto")

    fragments = list(app._prompt_text())

    assert ("class:mode.auto", "[auto] ") in fragments


def test_only_risky_footer_mode_receives_red_treatment(tmp_path):
    trust = TrustState(initial="build")
    app = _make_app(tmp_path, get_active_mode=lambda: "build", trust_state=trust)
    assert all(style != "class:status.risk" for style, _ in app._status_text())

    trust.set_slot(PermissionSlot.SPEND, PermissionDecision.AUTO)
    fragments = list(app._status_text())

    assert fragments[0][0] == "class:status.risk"
    assert fragments[0][1].strip().startswith("build ·")
    assert "a:" in fragments[0][1]
    assert fragments[1][1].lstrip().startswith("· foundation")


def test_bypass_footer_colors_the_permission_posture_not_only_mode(tmp_path):
    trust = TrustState(initial="bypass")
    app = _make_app(tmp_path, get_active_mode=lambda: "chat", trust_state=trust)

    fragments = list(app._status_text())

    assert fragments[0][0] == "class:status.risk"
    assert fragments[0][1].strip() == "chat · bypass"
    assert fragments[1][1].lstrip().startswith("· foundation")


def test_wide_active_mode_keeps_prompt_marker_visible(tmp_path):
    app = _make_app(tmp_path, get_active_mode=lambda: "界" * 20)
    app.application.output.get_size = lambda: SimpleNamespace(rows=24, columns=40)

    prompt_text = "".join(text for _, text in app._prompt_text())

    assert prompt_text.startswith("❯ [")
    assert prompt_text.endswith("] ")
    assert app._prompt_width().preferred <= 32


@pytest.mark.asyncio
async def test_layered_submit_clears_input_and_calls_async_handler(tmp_path):
    submitted: list[ChatSubmission] = []

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)

    app = _make_app(tmp_path, on_submit=on_submit)
    app.input_buffer.text = "queued prompt"

    app.submit_current_input()
    await asyncio.sleep(0)

    assert submitted == [ChatSubmission("queued prompt")]
    assert app.input_buffer.text == ""


@pytest.mark.asyncio
async def test_rapid_enter_then_ctrl_d_waits_for_async_submission(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    submitted: list[ChatSubmission] = []

    async def on_submit(submission: ChatSubmission) -> None:
        started.set()
        await release.wait()
        submitted.append(submission)

    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, on_submit=on_submit, input=pipe_input)
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        pipe_input.send_text("do not lose me\r")
        pipe_input.send_bytes(b"\x04")
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not run_task.done()

        release.set()
        await asyncio.wait_for(run_task, timeout=1)

    assert submitted == [ChatSubmission("do not lose me")]


@pytest.mark.asyncio
async def test_rapid_exit_callback_runs_after_submission_callback(tmp_path):
    events = []
    app_holder: dict[str, LayeredReplApp] = {}

    async def on_submit(submission: ChatSubmission) -> None:
        events.append(("submit", submission.text))

    def on_exit() -> None:
        events.append(("exit", ""))
        app_holder["app"].exit()

    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, on_submit=on_submit, input=pipe_input)
        app._on_exit = on_exit
        app_holder["app"] = app
        run_task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        pipe_input.send_text("queued first\r")
        pipe_input.send_bytes(b"\x04")
        await asyncio.wait_for(run_task, timeout=1)

    assert events == [("submit", "queued first"), ("exit", "")]


def test_large_native_output_is_not_bounded_by_layout_line_budget(tmp_path):
    app = _make_app(tmp_path, max_output_lines=40)
    output = StringIO()

    with redirect_stdout(output):
        app.append_output("\n".join(f"line {index}" for index in range(500)))

    assert output.getvalue() == ""
    assert "line 0" in app._transcript_view.plain_text()
    assert "line 499" in app._transcript_view.plain_text()
    assert "hidden" not in app._transcript_view.plain_text()


def test_page_navigation_pauses_and_restores_transcript_tail_follow(tmp_path):
    app = _make_app(tmp_path)
    app.append_output("\n".join(f"line {index}" for index in range(100)))

    app._transcript_view.scroll_page(-1, 20)
    paused_cursor = app._transcript_view.buffer.cursor_position
    assert app._transcript_view.following_tail is False

    app.append_output("line 100")
    assert app._transcript_view.buffer.cursor_position == paused_cursor

    app._transcript_view.scroll_page(1, 200)
    assert app._transcript_view.following_tail is True
    assert app._transcript_view.buffer.cursor_position == len(
        app._transcript_view.buffer.text
    )


@pytest.mark.asyncio
async def test_clipboard_image_placeholder_submits_matching_attachment(
    tmp_path, monkeypatch
):
    submitted: list[ChatSubmission] = []
    attachment = ImageAttachment(b"\x89PNG\r\n\x1a\nimage", "image/png")
    monkeypatch.setattr(
        "amplifier_app_cli.ui.layered_repl.read_clipboard_image",
        lambda: attachment,
    )

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)

    app = _make_app(tmp_path, on_submit=on_submit)
    assert app.paste_clipboard_image() is True
    assert app.input_buffer.text == "[Image #1]"

    app.submit_current_input()
    await asyncio.sleep(0)

    assert submitted == [ChatSubmission("[Image #1]", (attachment,))]
    assert "ctrl-v" not in "".join(text for _, text in app._status_text())
    assert app._notices.current().text == "1 image attached"


@pytest.mark.asyncio
async def test_clipboard_image_count_is_bounded(tmp_path, monkeypatch):
    attachment = ImageAttachment(b"\x89PNG\r\n\x1a\nimage", "image/png")
    monkeypatch.setattr(
        "amplifier_app_cli.ui.layered_repl.read_clipboard_image",
        lambda: attachment,
    )
    app = _make_app(tmp_path)

    results = [app.paste_clipboard_image() for _ in range(5)]
    await asyncio.sleep(0)

    assert results == [True, True, True, True, False]
    assert len(app._attachments) == 4


@pytest.mark.asyncio
async def test_shell_escaped_local_image_path_submits_as_attachment(tmp_path):
    submitted: list[ChatSubmission] = []
    image_data = b"\x89PNG\r\n\x1a\nlocal-image"
    image_path = tmp_path / "Screenshot 2026-07-10 at 6.25.31 PM.png"
    image_path.write_bytes(image_data)

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)

    app = _make_app(tmp_path, on_submit=on_submit)
    app.input_buffer.text = str(image_path).replace(" ", "\\ ")
    app.submit_current_input()
    await asyncio.sleep(0)

    assert submitted == [
        ChatSubmission(
            "[Image #1]",
            (ImageAttachment(image_data, "image/png"),),
        )
    ]


@pytest.mark.asyncio
async def test_non_image_slash_input_is_not_converted_to_attachment(tmp_path):
    submitted: list[ChatSubmission] = []

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)

    app = _make_app(tmp_path, on_submit=on_submit)
    app.input_buffer.text = "/help"
    app.submit_current_input()
    await asyncio.sleep(0)

    assert submitted == [ChatSubmission("/help")]


@pytest.mark.asyncio
async def test_enter_key_submits_in_running_application(tmp_path):
    submitted: list[ChatSubmission] = []
    app_holder: dict[str, LayeredReplApp] = {}

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)
        app_holder["app"].exit()

    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, on_submit=on_submit, input=pipe_input)
        app_holder["app"] = app
        run_task = asyncio.create_task(app.run_async())

        await asyncio.sleep(0.05)
        pipe_input.send_text("hello from pipe\r")
        await asyncio.wait_for(run_task, timeout=1)

    assert submitted == [ChatSubmission("hello from pipe")]


@pytest.mark.asyncio
async def test_bracketed_multiline_paste_waits_for_enter(tmp_path):
    submitted: list[ChatSubmission] = []
    app_holder: dict[str, LayeredReplApp] = {}

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)
        app_holder["app"].exit()

    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, on_submit=on_submit, input=pipe_input)
        app_holder["app"] = app
        run_task = asyncio.create_task(app.run_async())

        await asyncio.sleep(0.05)
        pipe_input.send_bytes(b"\x1b[200~line one\nline two\x1b[201~")
        await asyncio.sleep(0.05)
        assert app.input_buffer.text == "line one\nline two"
        assert submitted == []

        pipe_input.send_text("\r")
        await asyncio.wait_for(run_task, timeout=1)

    assert submitted == [ChatSubmission("line one\nline two")]


@pytest.mark.asyncio
async def test_long_paste_displays_stub_but_submits_exact_payload(tmp_path):
    submitted: list[ChatSubmission] = []

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)

    app = _make_app(tmp_path, on_submit=on_submit)
    raw = "\r\n".join(f"line {index}" for index in range(11))
    normalized = raw.replace("\r\n", "\n")

    app._insert_text_paste(raw, normalized)

    assert app._visible_editor_text(app.input_buffer.text) == "[Pasted #1 · 11 lines]"
    assert raw not in app.input_buffer.text
    app.submit_current_input()
    await asyncio.sleep(0)

    assert submitted == [
        ChatSubmission(
            raw,
            display_text="[Pasted #1 · 11 lines]",
        )
    ]


@pytest.mark.asyncio
async def test_430_line_paste_stub_round_trips_every_byte(tmp_path):
    submitted: list[ChatSubmission] = []

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)

    app = _make_app(tmp_path, on_submit=on_submit)
    raw = "\r\n".join(
        f"line {index:03d} · payload {index * 17}" for index in range(430)
    )
    normalized = raw.replace("\r\n", "\n")

    app._insert_text_paste(raw, normalized)

    assert app._visible_editor_text(app.input_buffer.text) == (
        "[Pasted #1 · 430 lines]"
    )
    assert "line 429" not in app.input_buffer.text
    app.submit_current_input()
    await asyncio.sleep(0)

    assert len(submitted) == 1
    assert submitted[0].text == raw
    assert submitted[0].text.encode() == raw.encode()
    assert submitted[0].display_text == "[Pasted #1 · 430 lines]"


@pytest.mark.asyncio
async def test_repeating_same_long_paste_expands_it_in_editor(tmp_path):
    app = _make_app(tmp_path)
    raw = "\n".join(f"line {index}" for index in range(11))

    app._insert_text_paste(raw, raw)
    app._insert_text_paste(raw, raw)
    await asyncio.sleep(0)

    assert app.input_buffer.text == raw
    assert app._text_pastes.paste_count == 0


@pytest.mark.asyncio
async def test_bracketed_image_path_becomes_placeholder_before_submit(tmp_path):
    submitted: list[ChatSubmission] = []
    image_data = b"\x89PNG\r\n\x1a\nbracketed-image"
    image_path = tmp_path / "Screenshot with spaces.png"
    image_path.write_bytes(image_data)
    app_holder: dict[str, LayeredReplApp] = {}

    async def on_submit(submission: ChatSubmission) -> None:
        submitted.append(submission)
        app_holder["app"].exit()

    with create_pipe_input() as pipe_input:
        app = _make_app(tmp_path, on_submit=on_submit, input=pipe_input)
        app_holder["app"] = app
        run_task = asyncio.create_task(app.run_async())

        await asyncio.sleep(0.05)
        pipe_input.send_text("review ")
        escaped_path = str(image_path).replace(" ", "\\ ").encode()
        pipe_input.send_bytes(b"\x1b[200~" + escaped_path + b"\x1b[201~")
        await asyncio.sleep(0.05)
        assert app.input_buffer.text == "review [Image #1]"

        pipe_input.send_text("\r")
        await asyncio.wait_for(run_task, timeout=1)

    assert submitted == [
        ChatSubmission(
            "review [Image #1]",
            (ImageAttachment(image_data, "image/png"),),
        )
    ]


@pytest.mark.asyncio
async def test_interactive_chat_preserves_explicit_resume_display_policy(
    tmp_path, monkeypatch
):
    import importlib

    main_module = importlib.import_module("amplifier_app_cli.main")
    runs = []

    async def run_one(**kwargs):
        runs.append(kwargs)
        return None

    monkeypatch.setattr(main_module, "_interactive_chat_session", run_one)

    transcript = [{"role": "user", "content": "context only"}]
    await main_module.interactive_chat(
        {},
        [tmp_path],
        False,
        initial_transcript=transcript,
        initial_display_transcript=[],
        initial_show_thinking=True,
    )

    assert runs[0]["initial_transcript"] is transcript
    assert runs[0]["initial_display_transcript"] == []
    assert runs[0]["initial_show_thinking"] is True


@pytest.mark.asyncio
async def test_interactive_resume_switches_iteratively_with_target_context(
    tmp_path, monkeypatch
):
    import importlib

    import amplifier_app_cli.commands.session as session_commands

    main_module = importlib.import_module("amplifier_app_cli.main")

    runs = []
    requests = iter(("session-b", "session-a", None))

    async def run_one(**kwargs):
        runs.append(kwargs)
        return next(requests)

    contexts = {
        "session-b": (
            "session-b",
            [{"role": "user", "content": "B question"}],
            {"bundle": "bundle-b"},
            {"config": "b"},
            [tmp_path / "b"],
            object(),
            "bundle-b",
            "bundle:bundle-b",
        ),
        "session-a": (
            "session-a",
            [{"role": "assistant", "content": "A answer"}],
            {"bundle": "bundle-a"},
            {"config": "a-restored"},
            [tmp_path / "a"],
            object(),
            "bundle-a",
            "bundle:bundle-a",
        ),
    }
    displayed = []

    monkeypatch.setattr(main_module, "_interactive_chat_session", run_one)
    monkeypatch.setattr(
        session_commands,
        "_prepare_resume_context",
        lambda session_id, *_args, **_kwargs: contexts[session_id],
    )
    monkeypatch.setattr(
        session_commands,
        "_display_session_history",
        lambda transcript, metadata, **kwargs: displayed.append(
            (transcript, metadata, kwargs)
        ),
    )

    await main_module.interactive_chat(
        {"config": "a"},
        [tmp_path],
        False,
        session_id="session-a",
        bundle_name="bundle:bundle-a",
        initial_transcript=[{"role": "user", "content": "initial"}],
    )

    assert [run["session_id"] for run in runs] == [
        "session-a",
        "session-b",
        "session-a",
    ]
    assert [run["config"] for run in runs] == [
        {"config": "a"},
        {"config": "b"},
        {"config": "a-restored"},
    ]
    assert [entry[0][0]["content"] for entry in displayed] == [
        "B question",
        "A answer",
    ]
    assert all(entry[2] == {"max_messages": 10} for entry in displayed)
    assert [run["initial_display_transcript"] for run in runs] == [
        [{"role": "user", "content": "initial"}],
        [{"role": "user", "content": "B question"}],
        [{"role": "assistant", "content": "A answer"}],
    ]
    assert all(run["initial_show_thinking"] is False for run in runs)
