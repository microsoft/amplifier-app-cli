"""Prompt-toolkit layout construction for the layered REPL."""

from __future__ import annotations

import os
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout import ConditionalContainer
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout import VSplit
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import AfterInput
from prompt_toolkit.layout.processors import ConditionalProcessor
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.output.defaults import create_output

from .layered_repl_keys import build_layered_key_bindings
from .layered_repl_style import LAYERED_REPL_STYLE
from .layered_repl_style import TOKENS

_COMPOSER_PLACEHOLDER = FormattedText(
    [
        (f"fg:{TOKENS['dim']}", "Message Amplifier…  "),
        (
            f"fg:{TOKENS['dimmer']}",
            "( / commands · shift+tab mode · ctrl-p perms · enter send · "
            "type mid-turn to steer )",
        ),
    ]
)

_EDGE_ACCENT_MODES = frozenset({"plan", "brainstorm", "build", "auto", "bypass"})


def build_layered_application(
    owner: Any,
    *,
    output: Any | None,
    input: Any | None,
) -> Application[None]:
    """Build the transient layout and attach its named surfaces to ``owner``."""
    key_bindings = build_layered_key_bindings(owner)

    owner.transcript_window = Window(
        owner._transcript_view.control,
        height=Dimension(weight=1),
        wrap_lines=True,
        always_hide_cursor=True,
        style="class:output",
    )
    owner.transcript_container = HSplit(
        [owner.transcript_window],
        height=Dimension(weight=1),
    )
    owner.preview_window = Window(
        FormattedTextControl(owner._stream_preview_text),
        height=owner._preview_height,
        wrap_lines=True,
        always_hide_cursor=True,
        style="class:output",
    )
    owner.preview_container = ConditionalContainer(
        content=owner.preview_window,
        filter=Condition(owner._preview_visible),
    )
    owner.plan_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._plan_text),
            height=owner._plan_height,
            wrap_lines=True,
            style="class:plan",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._plan_visible),
    )
    owner.steering_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._steering_text),
            height=1,
            wrap_lines=False,
            style="class:steering",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._steering_visible),
    )
    owner.tool_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._running_tools_text),
            height=owner._running_tools_height,
            wrap_lines=True,
            style="class:tools",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._running_tools_visible),
    )
    owner.work_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._working_text),
            height=owner._working_height,
            wrap_lines=False,
            style="class:working",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._work_visible),
    )
    owner.notice_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._notice_text),
            height=1,
            style="class:notice",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._notice_visible),
    )
    owner.palette_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._palette_text),
            height=owner._palette_height,
            wrap_lines=False,
            style="class:palette",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._palette_visible),
    )
    owner.rewind_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._rewind_text),
            height=1,
            wrap_lines=False,
            style="class:rewind",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._rewind_visible),
    )
    owner.evidence_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._evidence_text),
            height=1,
            wrap_lines=False,
            style="class:evidence",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._evidence_visible),
    )
    owner.queued_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._queued_text),
            height=1,
            wrap_lines=False,
            style="class:queued",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._queued_visible),
    )
    owner.approval_container = ConditionalContainer(
        content=Window(
            FormattedTextControl(owner._approval_text),
            height=1,
            wrap_lines=False,
            style="class:approval",
            always_hide_cursor=True,
        ),
        filter=Condition(owner._approval_visible),
    )
    status_window = Window(
        FormattedTextControl(owner._status_text),
        height=1,
        style="class:status",
        always_hide_cursor=True,
    )
    task_window = Window(
        FormattedTextControl(owner._task_pane_text),
        height=owner._task_pane_height,
        wrap_lines=True,
        style="class:tasks",
        always_hide_cursor=True,
    )
    owner.task_container = ConditionalContainer(
        content=task_window,
        filter=Condition(lambda: owner._tasks_visible),
    )

    def composer_edge_style() -> str:
        """Mode-accent left edge on the composer; ``rule`` color for chat."""
        mode = owner._active_mode()
        if mode in _EDGE_ACCENT_MODES:
            return f"class:input class:mode.{mode}"
        return "class:input class:rule"

    owner.composer_edge_window = Window(
        width=1,
        height=owner._input_height,
        char="▌",
        style=composer_edge_style,
    )
    owner.prompt_window = Window(
        FormattedTextControl(owner._prompt_text),
        width=owner._prompt_width,
        height=owner._input_height,
        style="class:prompt",
    )
    owner.input_window = Window(
        BufferControl(
            buffer=owner.input_buffer,
            key_bindings=key_bindings,
            input_processors=[
                ConditionalProcessor(
                    AfterInput(_COMPOSER_PLACEHOLDER),
                    filter=Condition(lambda: not owner.input_buffer.text),
                ),
            ],
        ),
        height=owner._input_height,
        wrap_lines=True,
        style="class:input",
    )
    owner.input_row = VSplit(
        [
            owner.composer_edge_window,
            owner.prompt_window,
            owner.input_window,
            Window(width=1, height=owner._input_height, char=" ", style="class:input"),
        ],
        height=owner._input_height,
    )
    owner.composer_container = ConditionalContainer(
        content=owner.input_row,
        filter=Condition(lambda: not owner._approval_visible()),
    )

    # Spec section 5: the mockup draws a border-top rule above the bottom
    # stack; in the terminal that is one full-width ─ row in the rule color.
    owner.separator_window = Window(height=1, char="─", style="class:rule")
    root = HSplit(
        [
            owner.transcript_container,
            owner.separator_window,
            owner.plan_container,
            owner.steering_container,
            owner.preview_container,
            owner.tool_container,
            owner.task_container,
            owner.work_container,
            owner.notice_container,
            owner.palette_container,
            owner.rewind_container,
            owner.evidence_container,
            owner.queued_container,
            owner.approval_container,
            owner.composer_container,
            status_window,
        ],
    )
    app_output = output or create_output(stdout=owner._terminal_file)
    # The slate palette's bg_term/bg_chrome distinction quantizes away at 256
    # colors; honor truecolor terminals so the footer chrome reads as chrome.
    color_depth = (
        ColorDepth.DEPTH_24_BIT
        if os.environ.get("COLORTERM", "").lower() in {"truecolor", "24bit"}
        else None
    )
    application: Application[None] = Application(
        layout=Layout(root, focused_element=owner.input_window),
        key_bindings=key_bindings,
        style=LAYERED_REPL_STYLE,
        full_screen=True,
        mouse_support=True,
        erase_when_done=False,
        refresh_interval=0.2,
        color_depth=color_depth,
        output=app_output,
        input=input,
    )
    # Bare Esc is a prefix of the alt+enter queue binding; keep both flush
    # timeouts short so Esc-to-interrupt stays snappy. (``ttimeoutlen`` flushes
    # a lone escape byte, ``timeoutlen`` resolves the prefix-of-longer-match
    # wait in the key processor.)
    application.ttimeoutlen = 0.15
    application.timeoutlen = 0.15
    return application


__all__ = ["build_layered_application"]
