"""Tests for amplifier_app_cli.ui.message_renderer.

Focused on the show_label parameter added as part of feat/label-in-stream:
- show_label=True (default) must print the 'Amplifier:' label.
- show_label=False must suppress the label (used by live chat when the
  streaming overlay has already printed it permanently).

All existing callers (history display, replay) use the default (True), so
their behaviour is unchanged.
"""

import copy
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console


def _make_console() -> tuple[Console, io.StringIO]:
    """Return a (console, buffer) pair for output capture."""
    buf = io.StringIO()
    # force_terminal=False + no_color=True ensures Rich doesn't try to do
    # ANSI detection on the StringIO; the text still flows through.
    con = Console(file=buf, highlight=False, no_color=True)
    return con, buf


def _persisted_reminder(content: object, *, role: str = "user") -> dict:
    return {
        "role": role,
        "content": content,
        "metadata": {"ephemeral": True, "persisted": True},
    }


def _capture_history(transcript: list[dict], *, max_messages: int) -> str:
    import amplifier_app_cli.commands.session as session_commands

    original_console = session_commands.console
    console, buffer = _make_console()
    session_commands.console = console
    try:
        session_commands._display_session_history(
            transcript,
            {
                "session_id": "test-session",
                "created": "2026-09-08T00:00:00+00:00",
                "bundle": "test",
                "model": "test/model",
            },
            max_messages=max_messages,
        )
        return buffer.getvalue()
    finally:
        session_commands.console = original_console


# ---------------------------------------------------------------------------
# render_message — show_label default / True
# ---------------------------------------------------------------------------


def test_render_message_prints_label_by_default():
    """render_message prints 'Amplifier:' for assistant messages by default."""
    from amplifier_app_cli.ui.message_renderer import render_message

    con, buf = _make_console()
    render_message({"role": "assistant", "content": "Hello"}, con)

    output = buf.getvalue()
    assert "Amplifier:" in output, (
        f"Expected 'Amplifier:' in output with show_label=True (default); got: {output!r}"
    )


def test_render_message_prints_label_when_show_label_true():
    """render_message prints 'Amplifier:' when show_label=True is explicit."""
    from amplifier_app_cli.ui.message_renderer import render_message

    con, buf = _make_console()
    render_message({"role": "assistant", "content": "Hello"}, con, show_label=True)

    output = buf.getvalue()
    assert "Amplifier:" in output, (
        f"Expected 'Amplifier:' in output with show_label=True; got: {output!r}"
    )


# ---------------------------------------------------------------------------
# render_message — show_label=False
# ---------------------------------------------------------------------------


def test_render_message_suppresses_label_when_show_label_false():
    """render_message does NOT print 'Amplifier:' when show_label=False.

    This is the overlay-active code path: the streaming overlay has already
    printed the label permanently, so app-cli skips it to avoid duplication.
    """
    from amplifier_app_cli.ui.message_renderer import render_message

    con, buf = _make_console()
    render_message(
        {"role": "assistant", "content": "Hello"},
        con,
        show_label=False,
    )

    output = buf.getvalue()
    assert "Amplifier:" not in output, (
        f"'Amplifier:' should be suppressed when show_label=False; got: {output!r}"
    )


def test_render_message_still_renders_content_when_label_suppressed():
    """Content is rendered even when show_label=False — only the label is gone."""
    from amplifier_app_cli.ui.message_renderer import render_message

    con, buf = _make_console()
    render_message(
        {"role": "assistant", "content": "Answer text here"},
        con,
        show_label=False,
    )

    output = buf.getvalue()
    assert "Answer text here" in output, (
        f"Content should still render with show_label=False; got: {output!r}"
    )
    assert "Amplifier:" not in output


# ---------------------------------------------------------------------------
# show_label is irrelevant for user messages and empty assistant messages
# ---------------------------------------------------------------------------


def test_render_message_user_role_unaffected_by_show_label():
    """show_label has no effect on user messages (they never print 'Amplifier:')."""
    from amplifier_app_cli.ui.message_renderer import render_message

    con, buf = _make_console()
    render_message(
        {"role": "user", "content": "What is 2+2?"},
        con,
        show_label=False,
    )

    output = buf.getvalue()
    assert "Amplifier:" not in output
    assert "What is 2+2?" in output


def test_render_message_tool_only_assistant_skips_label():
    """Tool-only assistant messages (empty text) skip rendering entirely."""
    from amplifier_app_cli.ui.message_renderer import render_message

    con, buf = _make_console()
    # A tool_use-only content list has no text or thinking blocks.
    render_message(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": "bash", "input": {}}],
        },
        con,
        show_label=True,
    )

    output = buf.getvalue()
    assert "Amplifier:" not in output, (
        f"Tool-only message should not print label; got: {output!r}"
    )


# ---------------------------------------------------------------------------
# is_displayable_session_message — persisted reminder display policy
# ---------------------------------------------------------------------------


def test_is_displayable_session_message_hides_only_complete_persisted_envelopes():
    """Canonical singular and plural persisted reminders are display-only."""
    from amplifier_app_cli.ui import is_displayable_session_message

    singular = (
        '<system-reminder source="hook">Use the current workspace.</system-reminder>'
    )
    plural = (
        "<system-reminders>\n"
        "The blocks below were injected by the system, not the user.\n"
        '<system-reminder source="hooks-status-context">Today is Tuesday.</system-reminder>\n'
        '<system-reminder source="memory">Remember arbitrary merged hook text.</system-reminder>\n'
        "</system-reminders>"
    )
    bare_plural = (
        "<system-reminders>\nMerged hook prose without child tags.\n</system-reminders>"
    )
    text_blocks = [
        {"type": "text", "text": " \n\t"},
        {"type": "text", "text": f"\n  {singular}\t"},
        {"type": "text", "text": f"  {plural}\n"},
    ]

    for content in (
        f"\n  {singular}\t",
        "<system-reminder>Source is optional.</system-reminder>",
        plural,
        bare_plural,
        text_blocks,
    ):
        assert is_displayable_session_message(_persisted_reminder(content)) is False


@pytest.mark.parametrize(
    "message",
    [
        {"role": "user", "content": "<system-reminder>quoted</system-reminder>"},
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {"ephemeral": True},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {"ephemeral": True, "persisted": False},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {"ephemeral": True, "persisted": 1},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {"ephemeral": True, "persisted": "true"},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {"ephemeral": False, "persisted": True},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": {"ephemeral": "true", "persisted": True},
        },
        {
            "role": "user",
            "content": "<system-reminder>quoted</system-reminder>",
            "metadata": ["ephemeral", "persisted"],
        },
        _persisted_reminder("Please explain <system-reminder> as literal text."),
        _persisted_reminder(
            "<system-reminder>quoted tag</system-reminder> before my question"
        ),
        _persisted_reminder(
            [
                {"type": "text", "text": "My question is:"},
                {"type": "text", "text": "<system-reminder>quoted</system-reminder>"},
            ]
        ),
        _persisted_reminder(
            [
                {"type": "text", "text": "<system-reminder>only</system-reminder>"},
                {"type": "tool_use", "name": "not text"},
            ]
        ),
        _persisted_reminder([]),
        _persisted_reminder(123),
        _persisted_reminder("<system-reminderish>not a reminder</system-reminderish>"),
        _persisted_reminder(
            '<system-reminders source="x">attribute not allowed</system-reminders>'
        ),
        _persisted_reminder("<system-reminder>missing close"),
        _persisted_reminder("<system-reminder>wrong close</system-reminders>"),
        _persisted_reminder(
            "<system-reminder>first</system-reminder> human "
            "<system-reminder>second</system-reminder>"
        ),
        _persisted_reminder(
            "<system-reminder>first</system-reminder>\n\n"
            "<system-reminder>second</system-reminder>"
        ),
        _persisted_reminder(
            "<system-reminder>outer <system-reminder>nested</system-reminder>"
            "</system-reminder>"
        ),
        _persisted_reminder(
            "<system-reminders><system-reminders>nested</system-reminders>"
            "</system-reminders>"
        ),
        _persisted_reminder(
            '<system-reminder source="unterminated>not valid</system-reminder>'
        ),
        _persisted_reminder(
            '<system-reminder source="hook" extra="attribute">not valid</system-reminder>'
        ),
    ],
)
def test_is_displayable_session_message_fails_open_for_human_or_malformed_content(
    message,
):
    """Anything ambiguous remains visible rather than hiding human content."""
    from amplifier_app_cli.ui import is_displayable_session_message

    assert is_displayable_session_message(message) is True


def test_is_displayable_session_message_always_keeps_assistant_messages():
    """Assistant content remains visible even if it quotes a reminder tag."""
    from amplifier_app_cli.ui import is_displayable_session_message

    message = _persisted_reminder(
        "<system-reminder>assistant quoted this</system-reminder>", role="assistant"
    )
    assert is_displayable_session_message(message) is True


def test_history_hides_persisted_reminders_before_the_last_ten_and_preserves_transcript():
    """Rich history renders ten visible rows and reports only visible skipped rows."""
    reminders = [
        _persisted_reminder(f"<system-reminder>HIDDEN_{number}</system-reminder>")
        for number in range(8)
    ]
    transcript = (
        [{"role": "user", "content": f"EARLY_VISIBLE_{number}"} for number in range(3)]
        + reminders
        + [
            {"role": "assistant", "content": f"LATE_VISIBLE_{number}"}
            for number in range(10)
        ]
    )
    original = copy.deepcopy(transcript)

    output = _capture_history(transcript, max_messages=10)

    assert "... 3 earlier messages" in output
    assert "EARLY_VISIBLE_0" not in output
    assert "LATE_VISIBLE_0" in output
    assert "LATE_VISIBLE_9" in output
    assert all(f"HIDDEN_{number}" not in output for number in range(8))
    assert transcript == original


@pytest.mark.asyncio
async def test_replay_skips_reminder_timing_and_interrupted_remainder():
    """Replay neither delays nor renders reminders, including after Ctrl-C."""
    import amplifier_app_cli.commands.session as session_commands

    complete_transcript = [
        {"role": "user", "content": "COMPLETE_FIRST_VISIBLE"},
        _persisted_reminder("<system-reminder>COMPLETE_REMINDER</system-reminder>"),
        {"role": "assistant", "content": "COMPLETE_FINAL_VISIBLE"},
    ]
    complete_original = copy.deepcopy(complete_transcript)
    complete_sleeps = 0

    async def count_sleep(_: float) -> None:
        nonlocal complete_sleeps
        complete_sleeps += 1

    original_console = session_commands.console
    complete_console, complete_buffer = _make_console()
    session_commands.console = complete_console
    try:
        with patch.object(session_commands.asyncio, "sleep", new=count_sleep):
            await session_commands._replay_session_history(
                complete_transcript,
                {
                    "session_id": "test-session",
                    "created": "2026-09-08T00:00:00+00:00",
                    "bundle": "test",
                    "model": "test/model",
                },
                speed=1000,
            )
    finally:
        session_commands.console = original_console

    assert complete_sleeps == 2
    assert "COMPLETE_REMINDER" not in complete_buffer.getvalue()
    assert complete_transcript == complete_original

    interrupted_transcript = [
        {"role": "user", "content": "FIRST_VISIBLE"},
        {"role": "assistant", "content": "INTERRUPTED_ASSISTANT"},
        _persisted_reminder("<system-reminder>REMAINDER_REMINDER</system-reminder>"),
        {"role": "user", "content": "REMAINDER_VISIBLE"},
        {"role": "assistant", "content": "FINAL_VISIBLE"},
    ]
    original = copy.deepcopy(interrupted_transcript)
    console, buffer = _make_console()
    sleeps = 0

    async def interrupt_second_sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    session_commands.console = console
    try:
        with patch.object(
            session_commands.asyncio, "sleep", new=interrupt_second_sleep
        ):
            await session_commands._replay_session_history(
                interrupted_transcript,
                {
                    "session_id": "test-session",
                    "created": "2026-09-08T00:00:00+00:00",
                    "bundle": "test",
                    "model": "test/model",
                },
                speed=1000,
            )
    finally:
        session_commands.console = original_console

    output = buffer.getvalue()
    assert sleeps == 2
    assert "Skipped to end" in output
    assert "INTERRUPTED_ASSISTANT" not in output
    assert "REMAINDER_REMINDER" not in output
    assert "REMAINDER_VISIBLE" in output
    assert "FINAL_VISIBLE" in output
    assert interrupted_transcript == original


@pytest.mark.asyncio
async def test_loaded_transcript_hides_reminders_but_session_runner_restores_them(
    tmp_path,
):
    """Display filtering never removes persisted reminder context from a resumed session."""
    from amplifier_app_cli.session_runner import (
        SessionConfig,
        create_initialized_session,
    )
    from amplifier_app_cli.session_store import SessionStore

    reminder = _persisted_reminder(
        "<system-reminders>\n"
        "This explanatory preamble was injected by the system.\n"
        '<system-reminder source="hook">PERSISTED_CONTEXT</system-reminder>\n'
        "</system-reminders>"
    )
    transcript = [
        {"role": "user", "content": "VISIBLE_CONTEXT"},
        reminder,
        {"role": "assistant", "content": "VISIBLE_RESPONSE"},
    ]
    store = SessionStore(base_dir=tmp_path / "sessions")
    store.save("resume-reminder-test", transcript, {"bundle": "test"})
    loaded_transcript, _ = store.load("resume-reminder-test")
    original = copy.deepcopy(loaded_transcript)

    history = _capture_history(loaded_transcript, max_messages=10)
    assert "VISIBLE_CONTEXT" in history
    assert "VISIBLE_RESPONSE" in history
    assert "PERSISTED_CONTEXT" not in history

    class RecordingContext:
        def __init__(self):
            self.messages = []

        async def get_messages(self):
            return self.messages

        async def set_messages(self, messages):
            self.messages = messages

    context = RecordingContext()
    session = MagicMock()
    session.config = {}
    session.coordinator.get.side_effect = lambda name: (
        context if name == "context" else None
    )
    session.coordinator.get_capability.return_value = None
    config = SessionConfig(
        config={},
        search_paths=[],
        verbose=False,
        session_id="resume-reminder-test",
        initial_transcript=loaded_transcript,
    )

    with (
        patch(
            "amplifier_app_cli.session_runner._create_bundle_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch("amplifier_app_cli.commands.init.check_first_run", return_value=False),
        patch("amplifier_app_cli.project_utils.get_project_slug", return_value="test"),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        patch("amplifier_app_cli.session_runner.SessionStore", return_value=store),
        patch("amplifier_app_cli.session_runner.InitializedSession"),
    ):
        await create_initialized_session(config, MagicMock())

    assert context.messages == original
    assert context.messages[1] == reminder
    assert loaded_transcript == original
