"""Resize reflow: retained blocks re-wrap history at the new terminal width."""

from __future__ import annotations

import asyncio
from typing import Any
from typing import cast

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from amplifier_app_cli.ui.block_render_cache import BlockRenderCache
from amplifier_app_cli.ui.bottom_stdout import TranscriptOutput
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices
from amplifier_app_cli.ui.layered_transcript import LayeredTranscriptView
from amplifier_app_cli.ui.stream_status import StreamStatusTracker
from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
from amplifier_app_cli.ui.transcript_blocks import Telemetry
from amplifier_app_cli.ui.transcript_blocks import ToolBlock
from amplifier_app_cli.ui.transcript_blocks import ToolStatus
from amplifier_app_cli.ui.transcript_blocks import TurnTerminatorBlock
from amplifier_app_cli.ui.transcript_blocks import UserBlock
from amplifier_app_cli.ui.transcript_click_spans import ClickSpanRegistry
from amplifier_app_cli.ui.transcript_reflow import TranscriptReflowController
from amplifier_app_cli.ui.ui_events import UiEventDispatcher

_RAW_CHUNK = "raw \x1b[31mred\x1b[0m fragment\nsecond raw line\n"

_LONG_ANSWER = (
    "A deliberately long markdown answer that wraps very differently at "
    "eighty columns than it does at one hundred and twenty columns, with "
    "**bold emphasis** and `identifiers` that must survive every rewrap. "
) * 3


class _Pipeline:
    """The emit path the app uses: dispatcher -> Rich -> TerminalTranscript."""

    def __init__(self, width: int) -> None:
        self.width = width
        self.view = LayeredTranscriptView(
            stream_status=None,
            render_width=lambda: self.width,
        )
        output = TranscriptOutput(self._sink)
        console = Console(
            file=cast(Any, output),
            force_terminal=True,
            color_system="truecolor",
            width=width,
            legacy_windows=False,
        )
        self.dispatcher = UiEventDispatcher(console)
        self.cache = BlockRenderCache()
        self.view.set_block_renderer(self._render_block)

    def _sink(self, text: str) -> None:
        self.view.append_output(
            text,
            action=self.dispatcher.active_click_action,
            block=self.dispatcher.active_block,
        )

    def _render_block(self, block: object, width: int) -> str:
        return self.cache.render(
            block,
            width,
            lambda source, target: self.dispatcher.render_to_ansi(
                cast(Any, source), width=target
            ),
        )

    def emit_conversation(self) -> None:
        for block in _conversation_blocks():
            self.dispatcher.emit(block)
        self.view.append_output(_RAW_CHUNK)


def _conversation_blocks() -> tuple[Any, ...]:
    return (
        UserBlock("Please verify the resize behavior end to end", mode="build"),
        AnswerBlock(_LONG_ANSWER, label="Amplifier"),
        ToolBlock(
            "Ran 1 shell command",
            ToolStatus.COMPLETED,
            output=("1214 passed", "build succeeded"),
        ),
        TurnTerminatorBlock(
            Telemetry(elapsed_seconds=68, tokens=83_900),
            outcome="answer",
        ),
    )


def _rows_for(view: LayeredTranscriptView, kind: str) -> list[int]:
    rows = []
    for row in range(view.history_line_count):
        action = view.click_action_at_row(row)
        if isinstance(action, tuple) and action and action[0] == kind:
            rows.append(row)
    return rows


class _FakeScheduler:
    """Deterministic replacement for the asyncio trailing-debounce timer."""

    def __init__(self) -> None:
        self.armed: list[list[Any]] = []

    def __call__(self, delay: float, fire: Any) -> Any:
        entry = [delay, fire, False]
        self.armed.append(entry)

        def cancel() -> None:
            entry[2] = True

        return cancel

    def fire_due(self) -> None:
        due, self.armed = self.armed, []
        for _delay, fire, cancelled in due:
            if not cancelled:
                fire()


# --- Reflow correctness ----------------------------------------------------


def test_reflow_rewraps_history_exactly_like_a_fresh_render() -> None:
    resized = _Pipeline(120)
    fresh = _Pipeline(80)
    resized.emit_conversation()
    fresh.emit_conversation()
    assert resized.view.plain_text() != fresh.view.plain_text()

    assert resized.view.reflow_to_width(80)

    assert resized.view.plain_text() == fresh.view.plain_text()


def test_reflow_at_the_emitted_width_changes_nothing() -> None:
    pipeline = _Pipeline(80)
    pipeline.emit_conversation()
    before = pipeline.view.plain_text()

    assert pipeline.view.reflow_to_width(80)

    assert pipeline.view.plain_text() == before
    assert pipeline.view.following_tail is True


def test_untagged_raw_output_survives_reflow_with_its_styles() -> None:
    pipeline = _Pipeline(120)
    pipeline.emit_conversation()

    assert pipeline.view.reflow_to_width(80)

    plain_lines = pipeline.view.plain_text().splitlines()
    raw_row = next(
        row for row, line in enumerate(plain_lines) if "raw" in line and "red" in line
    )
    styles = {
        fragment[0] for fragment in pipeline.view.formatted_line(raw_row) if fragment[1]
    }
    assert any("ansired" in style for style in styles)
    assert "second raw line" in plain_lines[raw_row + 1]


def test_raw_fragments_written_without_newlines_survive_reflow() -> None:
    pipeline = _Pipeline(100)
    pipeline.view.append_output("foo")
    pipeline.view.append_output("bar")
    pipeline.view.append_output("\n")
    before = pipeline.view.plain_text()

    assert pipeline.view.reflow_to_width(80)

    assert pipeline.view.plain_text() == before == "foobar"


# --- Click spans -----------------------------------------------------------


def test_click_spans_are_remapped_to_the_reflowed_rows() -> None:
    resized = _Pipeline(120)
    fresh = _Pipeline(80)
    resized.emit_conversation()
    fresh.emit_conversation()
    assert _rows_for(resized.view, "tool") != _rows_for(fresh.view, "tool")

    assert resized.view.reflow_to_width(80)

    for kind in ("tool", "terminator", "answer"):
        rows = _rows_for(resized.view, kind)
        assert rows == _rows_for(fresh.view, kind)
    tool_rows = _rows_for(resized.view, "tool")
    assert "Ran 1 shell command" in pipeline_line(resized.view, tool_rows[0])


def pipeline_line(view: LayeredTranscriptView, row: int) -> str:
    return view.plain_text().splitlines()[row]


# --- Viewport restoration --------------------------------------------------


def test_a_tailing_viewport_returns_to_the_tail_after_reflow() -> None:
    pipeline = _Pipeline(120)
    pipeline.emit_conversation()
    assert pipeline.view.following_tail is True

    assert pipeline.view.reflow_to_width(80)

    assert pipeline.view.following_tail is True
    assert pipeline.view.global_cursor_row == pipeline.view.history_line_count - 1


def test_a_paused_viewport_stays_anchored_to_its_block_after_reflow() -> None:
    pipeline = _Pipeline(120)
    pipeline.emit_conversation()
    tool_row = _rows_for(pipeline.view, "tool")[0]
    pipeline.view.scroll_to_row(tool_row)
    assert pipeline.view.following_tail is False

    assert pipeline.view.reflow_to_width(80)

    assert pipeline.view.following_tail is False
    assert pipeline.view.global_cursor_row == _rows_for(pipeline.view, "tool")[0]


# --- Bounded retention -----------------------------------------------------


def test_bounded_retention_drops_oldest_blocks_with_a_dropped_count_line() -> None:
    pipeline = _Pipeline(100)
    pipeline.view._click_spans = ClickSpanRegistry(capacity=8)
    for index in range(12):
        pipeline.dispatcher.emit(
            ToolBlock(f"Ran command {index}", ToolStatus.COMPLETED)
        )
    assert pipeline.view.retained_span_count == 8
    assert pipeline.view.dropped_span_count == 4

    assert pipeline.view.reflow_to_width(80)

    plain_lines = pipeline.view.plain_text().splitlines()
    assert "4 earlier transcript chunks dropped" in plain_lines[0]
    text = pipeline.view.plain_text()
    assert "Ran command 3" not in text
    assert "Ran command 4" in text
    assert "Ran command 11" in text


def test_registry_merges_same_block_chunks_and_counts_drops() -> None:
    registry = ClickSpanRegistry(capacity=3)
    block = object()
    action = ("tool", 1)
    registry.record(0, 1, action, block=block, raw="a\n")
    registry.record(2, 3, action, block=block, raw="b\n")
    assert len(registry.spans) == 1
    assert registry.spans[0].raw == "a\nb\n"
    assert registry.spans[0].end_row == 3

    for row in range(4, 8):
        registry.record(row, row, None, block=object(), raw="x\n")

    assert len(registry.spans) == 3
    assert registry.dropped_count == 2


# --- Debounce and stream deferral -----------------------------------------


def test_reflow_waits_for_the_debounce_and_defers_while_streaming() -> None:
    reflowed: list[int] = []
    width = {"value": 120}
    streaming = {"value": True}
    scheduler = _FakeScheduler()
    controller = TranscriptReflowController(
        observe_width=lambda: width["value"],
        reflow=lambda target: reflowed.append(target) or True,
        stream_active=lambda: streaming["value"],
        schedule=scheduler,
    )

    controller.observe()  # First width initializes the baseline only.
    assert not scheduler.armed
    width["value"] = 80
    controller.observe()
    assert len(scheduler.armed) == 1
    assert reflowed == []  # Trailing debounce: nothing happens synchronously.

    scheduler.fire_due()  # A live stream defers the rebuild ...
    assert reflowed == []
    assert controller.deferred_for_stream is True
    scheduler.fire_due()  # ... for as long as the turn keeps running.
    assert reflowed == []

    streaming["value"] = False
    scheduler.fire_due()  # Turn completion releases exactly one rebuild.
    assert reflowed == [80]
    assert controller.reflowed_width == 80
    assert controller.deferred_for_stream is False

    controller.observe()  # The settled width schedules no further work.
    assert not scheduler.armed


def test_a_resize_drag_reflows_once_at_the_final_width() -> None:
    reflowed: list[int] = []
    width = {"value": 120}
    scheduler = _FakeScheduler()
    controller = TranscriptReflowController(
        observe_width=lambda: width["value"],
        reflow=lambda target: reflowed.append(target) or True,
        schedule=scheduler,
    )
    controller.observe()
    for dragged in (110, 100, 90, 80):
        width["value"] = dragged
        controller.observe()

    scheduler.fire_due()

    assert reflowed == [80]
    assert controller.reflowed_width == 80


def test_returning_to_the_original_width_cancels_the_pending_reflow() -> None:
    reflowed: list[int] = []
    width = {"value": 120}
    scheduler = _FakeScheduler()
    controller = TranscriptReflowController(
        observe_width=lambda: width["value"],
        reflow=lambda target: reflowed.append(target) or True,
        schedule=scheduler,
    )
    controller.observe()
    width["value"] = 80
    controller.observe()
    width["value"] = 120
    controller.observe()

    scheduler.fire_due()

    assert reflowed == []
    assert controller.pending is False


def test_reflow_is_not_deferred_when_running_but_nothing_is_streaming(
    tmp_path,
) -> None:
    """A turn can be 'running' for a long stretch with nothing new appended
    to the transcript yet (e.g. mid-tool-call, waiting on a shell command).
    A resize during that window must reflow immediately -- only actively
    streamed output should hold the rebuild, not the turn's running flag."""
    tracker = StreamStatusTracker(root_session_id="12345678-abcdef")
    app = _make_app(tmp_path, get_is_running=lambda: True, stream_status=tracker)
    try:
        assert tracker.preview is None  # Sanity: nothing has streamed yet.
        assert app._reflow_stream_active() is False
    finally:
        app.exit()


def test_reflow_is_still_deferred_while_a_stream_preview_is_present(
    tmp_path,
) -> None:
    """Guard the original guarantee: don't repaint transcript history under
    live streamed output. This must hold on its own merits (independent of
    the turn's running flag), so ``get_is_running`` is False here."""
    tracker = StreamStatusTracker(root_session_id="12345678-abcdef")
    tracker.consume(
        "llm:stream_block_start",
        {
            "session_id": "12345678-abcdef",
            "block_index": 0,
            "block_type": "text",
        },
    )
    tracker.consume(
        "llm:stream_block_delta",
        {
            "session_id": "12345678-abcdef",
            "block_index": 0,
            "text": "partial answer...",
        },
    )
    app = _make_app(tmp_path, get_is_running=lambda: False, stream_status=tracker)
    try:
        assert tracker.preview is not None  # Sanity: a live preview exists.
        assert app._reflow_stream_active() is True
    finally:
        app.exit()


def test_close_cancels_any_armed_reflow() -> None:
    reflowed: list[int] = []
    width = {"value": 120}
    scheduler = _FakeScheduler()
    controller = TranscriptReflowController(
        observe_width=lambda: width["value"],
        reflow=lambda target: reflowed.append(target) or True,
        schedule=scheduler,
    )
    controller.observe()
    width["value"] = 80
    controller.observe()

    controller.close()
    scheduler.fire_due()

    assert reflowed == []


# --- Wide terminals (> 240 columns) ----------------------------------------


def test_current_render_width_is_not_clamped_above_240_columns() -> None:
    view = LayeredTranscriptView(stream_status=None, render_width=lambda: 300)

    assert view.current_render_width() == 300


def test_reflow_rewraps_correctly_at_widths_above_240_columns() -> None:
    resized = _Pipeline(300)
    fresh = _Pipeline(260)
    resized.emit_conversation()
    fresh.emit_conversation()
    assert resized.view.plain_text() != fresh.view.plain_text()

    assert resized.view.reflow_to_width(260)

    assert resized.view.plain_text() == fresh.view.plain_text()


def test_repeated_resizes_above_240_columns_each_trigger_a_reflow() -> None:
    """Regression test: before the fix, `current_render_width` clamped to 240,
    so once a terminal exceeded 240 columns `TranscriptReflowController.observe`
    always compared the same clamped value (240) and reflow silently stopped
    firing for any further resize above that ceiling."""
    width = {"value": 200}
    view = LayeredTranscriptView(
        stream_status=None, render_width=lambda: width["value"]
    )
    reflowed: list[int] = []
    scheduler = _FakeScheduler()
    controller = TranscriptReflowController(
        observe_width=view.current_render_width,
        reflow=lambda target: reflowed.append(target) or True,
        schedule=scheduler,
    )

    controller.observe()  # baseline at 200

    width["value"] = 300  # resize above 240
    controller.observe()
    scheduler.fire_due()
    assert reflowed == [300]

    width["value"] = 260  # resize again, still above 240
    controller.observe()
    scheduler.fire_due()
    assert reflowed == [300, 260]


# --- Render cache ----------------------------------------------------------


def test_block_render_cache_is_a_bounded_lru() -> None:
    cache = BlockRenderCache(capacity=2)
    calls: list[tuple[object, int]] = []

    def render(block: object, width: int) -> str:
        calls.append((block, width))
        return f"{block}:{width}"

    assert cache.render("a", 80, render) == "a:80"
    assert cache.render("a", 80, render) == "a:80"
    assert calls == [("a", 80)]

    cache.render("b", 80, render)
    cache.render("a", 80, render)  # Refresh "a" so "b" is least recent.
    cache.render("c", 80, render)  # Evicts "b".
    calls.clear()
    cache.render("a", 80, render)
    assert calls == []
    cache.render("b", 80, render)
    assert calls == [("b", 80)]


def test_block_render_cache_bypasses_unhashable_blocks() -> None:
    cache = BlockRenderCache(capacity=2)
    calls: list[object] = []

    def render(block: object, width: int) -> str:
        calls.append(block)
        return "rendered"

    unhashable: list[str] = []
    assert cache.render(unhashable, 80, render) == "rendered"
    assert cache.render(unhashable, 80, render) == "rendered"
    assert len(calls) == 2
    assert len(cache) == 0


def test_reflow_reuses_cached_renders_for_unchanged_blocks() -> None:
    pipeline = _Pipeline(120)
    pipeline.emit_conversation()
    renders: list[tuple[object, int]] = []
    original = pipeline.dispatcher.render_to_ansi

    def counting(block: Any, *, width: int) -> str:
        renders.append((block, width))
        return original(block, width=width)

    pipeline.dispatcher.render_to_ansi = counting  # type: ignore[method-assign]
    assert pipeline.view.reflow_to_width(80)
    first_pass = len(renders)
    assert first_pass > 0

    assert pipeline.view.reflow_to_width(80)

    assert len(renders) == first_pass  # Second pass at 80 was fully cached.


# --- Application wiring ----------------------------------------------------


def _make_app(
    tmp_path,
    *,
    get_is_running=None,
    stream_status=None,
) -> LayeredReplApp:
    output = DummyOutput()
    output.get_size = lambda: Size(rows=12, columns=80)
    return LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / "history",
            completion=LayeredReplCompletion(
                CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
            ),
            bundle_name="foundation",
            session_id="12345678-abcdef",
            output=output,
        ),
        bindings=LayeredReplBindings(
            on_submit=lambda submission: None,
            get_active_mode=lambda: "chat",
            get_is_running=get_is_running,
        ),
        services=LayeredReplServices(stream_status=stream_status),
    )


@pytest.mark.asyncio
async def test_the_app_observes_width_after_render_and_can_reflow(tmp_path) -> None:
    app = _make_app(tmp_path)
    try:
        handlers = getattr(app.application.after_render, "_handlers", [])
        assert app._transcript_reflow.observe in list(handlers)

        app._ui_events.emit(
            ToolBlock(
                "Ran 1 shell command", ToolStatus.COMPLETED, output=("all passed",)
            )
        )
        await asyncio.sleep(0)
        assert _rows_for(app._transcript_view, "tool")

        assert app._transcript_view.reflow_to_width(60)

        rows = _rows_for(app._transcript_view, "tool")
        assert rows
        assert "Ran 1 shell command" in app._transcript_view.plain_text()
    finally:
        app.exit()
