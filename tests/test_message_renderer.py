"""Tests for amplifier_app_cli.ui.message_renderer.

Focused on the show_label parameter added as part of feat/label-in-stream:
- show_label=True (default) must print the 'Amplifier:' label.
- show_label=False must suppress the label (used by live chat when the
  streaming overlay has already printed it permanently).

All existing callers (history display, replay) use the default (True), so
their behaviour is unchanged.
"""

import io

import pytest
from rich.console import Console


def _make_console() -> tuple[Console, io.StringIO]:
    """Return a (console, buffer) pair for output capture."""
    buf = io.StringIO()
    # force_terminal=False + no_color=True ensures Rich doesn't try to do
    # ANSI detection on the StringIO; the text still flows through.
    con = Console(file=buf, highlight=False, no_color=True)
    return con, buf


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
