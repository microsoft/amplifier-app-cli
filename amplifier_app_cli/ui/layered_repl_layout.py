"""Prompt-toolkit layout construction for the layered REPL."""

from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout import VSplit
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.output.defaults import create_output

from .layered_repl_input import pasted_image_attachments
from .layered_repl_style import LAYERED_REPL_STYLE


def build_layered_application(
    owner: Any,
    *,
    output: Any | None,
    input: Any | None,
) -> Application[None]:
    """Build the transient layout and attach its named surfaces to ``owner``."""
    key_bindings = _build_key_bindings(owner)

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
    owner.prompt_window = Window(
        FormattedTextControl(owner._prompt_text),
        width=owner._prompt_width,
        height=owner._input_height,
        style="class:prompt",
    )
    owner.input_window = Window(
        BufferControl(buffer=owner.input_buffer, key_bindings=key_bindings),
        height=owner._input_height,
        wrap_lines=True,
        style="class:input",
    )
    owner.input_row = VSplit(
        [
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

    root = HSplit(
        [
            owner.transcript_container,
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
            owner.approval_container,
            owner.composer_container,
            status_window,
        ],
    )
    app_output = output or create_output(stdout=owner._terminal_file)
    return Application(
        layout=Layout(root, focused_element=owner.input_window),
        key_bindings=key_bindings,
        style=LAYERED_REPL_STYLE,
        full_screen=True,
        mouse_support=True,
        erase_when_done=False,
        refresh_interval=0.2,
        output=app_output,
        input=input,
    )


def _build_key_bindings(owner: Any) -> KeyBindings:
    key_bindings = KeyBindings()

    @key_bindings.add(
        "?",
        filter=Condition(
            lambda: (
                not owner.input_buffer.text
                and not owner._is_running()
                and not owner._approval_visible()
            )
        ),
        eager=True,
    )
    def show_shortcut_help(event):
        owner.show_shortcut_help()
        event.app.invalidate()

    @key_bindings.add("enter", eager=True)
    def submit(event):
        if owner._approval_visible():
            owner._accept_approval()
            return
        if owner._tasks_visible:
            owner.focus_selected_lane()
            return
        if owner._evidence_visible():
            owner._accept_evidence()
            return
        if owner._rewind_visible():
            owner._accept_rewind()
            return
        if owner._palette_visible():
            owner._accept_palette_selection()
            return
        owner.submit_current_input()

    for key, direction in ((Keys.PageUp, -1), (Keys.PageDown, 1)):

        @key_bindings.add(
            key,
            filter=Condition(lambda: not owner._approval_visible()),
            eager=True,
        )
        def scroll_transcript(event, direction=direction):
            owner.scroll_transcript_page(direction)
            event.app.invalidate()

    @key_bindings.add(
        "up",
        filter=Condition(
            lambda: owner._palette_visible() and not owner._approval_visible()
        ),
        eager=True,
    )
    def palette_up(event):
        owner._move_palette(-1)

    @key_bindings.add(
        "down",
        filter=Condition(
            lambda: owner._palette_visible() and not owner._approval_visible()
        ),
        eager=True,
    )
    def palette_down(event):
        owner._move_palette(1)

    for key, delta in (
        ("left", -1),
        ("up", -1),
        ("right", 1),
        ("down", 1),
        ("tab", 1),
    ):

        @key_bindings.add(
            key,
            filter=Condition(owner._approval_visible),
            eager=True,
        )
        def move_approval(event, delta=delta):
            owner._move_approval(delta)

    @key_bindings.add(
        Keys.Any,
        filter=Condition(owner._approval_visible),
        eager=True,
    )
    def ignore_text_during_approval(event):
        """Keep the hidden draft immutable while approval owns keyboard focus."""
        return None

    @key_bindings.add(
        "up",
        filter=Condition(
            lambda: owner._tasks_visible and not owner._approval_visible()
        ),
        eager=True,
    )
    def lane_up(event):
        owner.select_next_lane(-1)

    @key_bindings.add(
        "down",
        filter=Condition(
            lambda: owner._tasks_visible and not owner._approval_visible()
        ),
        eager=True,
    )
    def lane_down(event):
        owner.select_next_lane(1)

    for key, delta in (("left", -1), ("up", -1), ("right", 1), ("down", 1)):

        @key_bindings.add(
            key,
            filter=Condition(
                lambda: owner._rewind_visible() and not owner._approval_visible()
            ),
            eager=True,
        )
        def move_rewind(event, delta=delta):
            owner._move_rewind(delta)

        @key_bindings.add(
            key,
            filter=Condition(
                lambda: owner._evidence_visible() and not owner._approval_visible()
            ),
            eager=True,
        )
        def move_evidence(event, delta=delta):
            owner._move_evidence(delta)

    @key_bindings.add("c-j", eager=True)
    def insert_newline(event):
        event.current_buffer.insert_text("\n")

    @key_bindings.add("c-v", eager=True)
    def paste_image(event):
        owner.paste_clipboard_image()

    @key_bindings.add(Keys.BracketedPaste, eager=True)
    def paste_text_or_image_path(event):
        normalized = event.data.replace("\r\n", "\n").replace("\r", "\n")
        attachments = pasted_image_attachments(normalized)
        if attachments:
            owner._insert_attachments(attachments)
            return
        owner._insert_text_paste(event.data, normalized)

    @key_bindings.add("c-c", eager=True)
    def interrupt(event):
        if owner._on_interrupt and owner._on_interrupt():
            event.app.invalidate()
            return
        owner.append_output("\nUse Ctrl-D or type exit to leave Amplifier.\n")

    @key_bindings.add("c-d", eager=True)
    def exit_repl(event):
        if event.current_buffer.text:
            event.current_buffer.delete()
            return
        owner.request_exit()

    @key_bindings.add(
        "c-t", filter=Condition(lambda: not owner._approval_visible()), eager=True
    )
    def toggle_tasks(event):
        owner.toggle_task_pane()

    @key_bindings.add("c-o", eager=True)
    def expand_latest_tool(event):
        owner.expand_latest_tool()

    @key_bindings.add("c-l", eager=True)
    def show_ledger(event):
        owner.show_ledger()

    @key_bindings.add("c-r", eager=True)
    def open_rewind(event):
        owner.open_rewind_picker()

    @key_bindings.add("c-y", eager=True)
    def show_needs_you(event):
        owner.show_needs_you()

    @key_bindings.add("c-e", eager=True)
    def show_evidence(event):
        owner.open_evidence_picker()

    @key_bindings.add(
        "s-tab", filter=Condition(lambda: not owner._approval_visible()), eager=True
    )
    def cycle_mode(event):
        if owner._on_cycle_mode is None:
            return
        result = owner._on_cycle_mode()
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            owner._submit_tasks.add(task)
            task.add_done_callback(owner._submission_done)
        event.app.invalidate()

    @key_bindings.add(
        "escape",
        filter=Condition(
            lambda: owner._palette_visible() and not owner._approval_visible()
        ),
        eager=True,
    )
    def close_palette(event):
        owner._dismiss_palette()

    @key_bindings.add(
        "escape",
        filter=Condition(
            lambda: owner._rewind_visible() and not owner._approval_visible()
        ),
        eager=True,
    )
    def close_rewind(event):
        owner._dismiss_rewind()

    @key_bindings.add(
        "escape",
        filter=Condition(
            lambda: owner._evidence_visible() and not owner._approval_visible()
        ),
        eager=True,
    )
    def close_evidence(event):
        owner._dismiss_evidence()

    @key_bindings.add("escape", filter=Condition(owner._approval_visible), eager=True)
    def deny_approval(event):
        owner._deny_approval()

    @key_bindings.add(
        "escape",
        filter=Condition(
            lambda: owner._tasks_visible and not owner._approval_visible()
        ),
        eager=True,
    )
    def close_tasks(event):
        owner.leave_agent_focus()

    @key_bindings.add(
        "escape",
        filter=Condition(
            lambda: (
                not owner._tasks_visible
                and not owner._approval_visible()
                and owner._is_running()
            )
        ),
        eager=True,
    )
    def interrupt_with_escape(event):
        if owner._on_interrupt and owner._on_interrupt():
            event.app.invalidate()

    return key_bindings


__all__ = ["build_layered_application"]
