"""Queued-next bar quotes queued text; newest queued message is recallable."""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.output import DummyOutput

from amplifier_app_cli.runtime.interactive_session import InteractiveSessionRuntime
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig


def _make_app(
    tmp_path,
    *,
    queued_count: int = 0,
    queued_preview: tuple[str, ...] | None = None,
) -> LayeredReplApp:
    registry = CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
    app = LayeredReplApp(
        config=LayeredReplConfig(
            history_path=tmp_path / "history",
            completion=LayeredReplCompletion(registry),
            output=DummyOutput(),
        ),
        bindings=LayeredReplBindings(
            on_submit=lambda submission: None,
            get_queued_count=lambda: queued_count,
        ),
    )
    if queued_preview is not None:
        # Mirrors the LayeredReplBindings.get_queued_preview wiring; the
        # surface mixin reads this attribute via getattr with a () fallback.
        setattr(app, "_get_queued_preview", lambda: queued_preview)
    return app


def _bar_text(app: LayeredReplApp) -> str:
    return "".join(fragment[1] for fragment in app._queued_text())


def _blocking_runtime(
    started: asyncio.Event,
    release: asyncio.Event,
    executed: list[str] | None = None,
) -> InteractiveSessionRuntime[str]:
    async def execute(prompt: str, attachments: tuple[str, ...]) -> bool:
        if executed is not None:
            executed.append(prompt)
        started.set()
        await release.wait()
        return True

    return InteractiveSessionRuntime[str](
        execute_turn=execute,
        on_error=lambda error: None,
        on_idle_exit=lambda: None,
    )


def test_queued_bar_quotes_single_message_text(tmp_path) -> None:
    app = _make_app(tmp_path, queued_count=1, queued_preview=("ship the notes",))

    text = _bar_text(app)

    assert text.startswith(
        '  ▹ queued next: "ship the notes" · runs when this turn ends'
    )
    assert text.endswith(" · alt+up edit")


def test_queued_bar_shows_first_text_plus_more_count(tmp_path) -> None:
    app = _make_app(
        tmp_path,
        queued_count=3,
        queued_preview=("first msg", "second message", "third message"),
    )

    text = _bar_text(app)

    assert '"first msg" (+2 more)' in text
    assert " · runs when this turn ends" in text
    assert text.endswith(" · alt+up edit")
    assert "second message" not in text


def test_queued_bar_truncates_long_message_to_terminal_width(tmp_path) -> None:
    app = _make_app(tmp_path, queued_count=1, queued_preview=("carry " * 20,))

    text = _bar_text(app)

    # DummyOutput reports 80 columns; the bar never exceeds them.
    assert len(text) <= 80
    assert '"carry' in text
    assert '..."' in text


def test_queued_bar_falls_back_to_count_without_preview_supplier(tmp_path) -> None:
    app = _make_app(tmp_path, queued_count=2)

    assert _bar_text(app).startswith(
        "  ▹ queued next: 2 message(s) · runs when this turn ends"
    )


def test_edit_last_queued_recalls_message_into_empty_composer(tmp_path) -> None:
    app = _make_app(tmp_path, queued_count=1, queued_preview=("recall me",))
    setattr(app, "_pop_last_queued", lambda: ("recall me · full text", ()))

    assert app.edit_last_queued() is True

    assert app.input_buffer.text == "recall me · full text"
    notice = app._notices.current()
    assert notice is not None
    assert notice.text == "queued message recalled"


def test_edit_last_queued_never_clobbers_a_draft(tmp_path) -> None:
    app = _make_app(tmp_path, queued_count=1, queued_preview=("queued",))
    setattr(app, "_pop_last_queued", lambda: ("queued", ()))
    app.input_buffer.text = "draft in progress"

    assert app.edit_last_queued() is False

    assert app.input_buffer.text == "draft in progress"


def test_edit_last_queued_with_empty_queue_is_a_noop(tmp_path) -> None:
    app = _make_app(tmp_path)
    setattr(app, "_pop_last_queued", lambda: None)

    assert app.edit_last_queued() is False

    assert app.input_buffer.text == ""


def test_edit_last_queued_without_binding_is_safe(tmp_path) -> None:
    app = _make_app(tmp_path)

    assert app.edit_last_queued() is False


@pytest.mark.asyncio
async def test_runtime_preview_snapshots_waiting_prompts() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    runtime = _blocking_runtime(started, release)

    await runtime.enqueue("active turn")
    await started.wait()
    await runtime.enqueue("second prompt")
    await runtime.enqueue("third prompt")

    assert runtime.queued_preview() == ("second prompt", "third prompt")
    assert runtime.queued_count == 2

    release.set()
    await runtime.wait()

    assert runtime.queued_preview() == ()
    assert runtime.queued_count == 0


@pytest.mark.asyncio
async def test_runtime_preview_sanitizes_control_chars_and_caps_length() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    runtime = _blocking_runtime(started, release)

    await runtime.enqueue("active turn")
    await started.wait()
    await runtime.enqueue("fix\x1b[31m the\nbug\tnow \x9b6n\x07\x00" + "x" * 200)

    (preview,) = runtime.queued_preview()

    assert preview.startswith("fix the bug now 6nx")
    assert "\x1b" not in preview
    assert "\x9b" not in preview
    assert "\x07" not in preview
    assert "\x00" not in preview
    assert "\n" not in preview
    assert len(preview) == 80
    assert preview.endswith("…")

    release.set()
    await runtime.wait()


@pytest.mark.asyncio
async def test_runtime_preview_caps_message_count() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    runtime = _blocking_runtime(started, release)

    await runtime.enqueue("active turn")
    await started.wait()
    for index in range(10):
        await runtime.enqueue(f"queued {index}")

    preview = runtime.queued_preview()

    assert runtime.queued_count == 10
    assert len(preview) == 8
    assert preview[0] == "queued 0"
    assert preview[-1] == "queued 7"

    release.set()
    await runtime.wait()


@pytest.mark.asyncio
async def test_pop_last_queued_returns_newest_waiting_prompt() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    executed: list[str] = []
    runtime = _blocking_runtime(started, release, executed)

    await runtime.enqueue("active turn")
    await started.wait()
    await runtime.enqueue("second prompt", ("attachment-a",))
    await runtime.enqueue("third prompt", ("attachment-b",))

    assert runtime.pop_last_queued() == ("third prompt", ("attachment-b",))
    assert runtime.queued_preview() == ("second prompt",)
    assert runtime.queued_count == 1

    release.set()
    await runtime.wait()

    # The popped prompt never executes; the remaining queue drains in order.
    assert executed == ["active turn", "second prompt"]


@pytest.mark.asyncio
async def test_pop_last_queued_returns_none_when_nothing_waits() -> None:
    runtime = _blocking_runtime(asyncio.Event(), asyncio.Event())

    assert runtime.pop_last_queued() is None


@pytest.mark.asyncio
async def test_pop_last_queued_never_pops_the_active_turn() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    executed: list[str] = []
    runtime = _blocking_runtime(started, release, executed)

    await runtime.enqueue("active turn")
    await started.wait()

    # The drain task already picked the active turn up; nothing is waiting.
    assert runtime.pop_last_queued() is None

    release.set()
    await runtime.wait()

    assert executed == ["active turn"]
