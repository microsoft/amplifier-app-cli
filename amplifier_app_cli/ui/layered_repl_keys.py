"""Key bindings for the layered REPL application.

Handlers are registered by iterating ``KEYMAP`` (``key_bindings_table``), so
the table that drives dispatch here is the same table the footer reads for
its on-screen hint labels — keys and hints cannot drift apart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from prompt_toolkit.filters import Condition, FilterOrBool
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from .key_bindings_table import (
    ALL_CONTEXTS,
    CONTEXT_APPROVAL,
    CONTEXT_COMPOSER,
    CONTEXT_EVIDENCE,
    CONTEXT_PALETTE,
    CONTEXT_REWIND,
    CONTEXT_RUNNING,
    CONTEXT_TASKS,
    KEYMAP,
    NO_APPROVAL_CONTEXTS,
    validate,
)
from .keyboard_protocol import install_shift_enter_sequences
from .layered_repl_input import pasted_image_attachments

_Handler = Callable[[KeyPressEvent, int | None], object]


def build_layered_key_bindings(owner: Any) -> KeyBindings:
    # Make the vt100 parser deliver the enhanced encodings (real shift+enter
    # and friends) before the application starts reading input.
    install_shift_enter_sequences()
    validate(KEYMAP)
    key_bindings = KeyBindings()
    handlers = _build_handlers(owner)
    filters = _context_filters(owner)
    for binding in KEYMAP:
        if not binding.pt_keys:
            continue  # display-only affordance (e.g. "/" opens the palette)
        _register(key_bindings, binding, handlers[binding.action], filters)
    return key_bindings


def _register(
    key_bindings: KeyBindings,
    binding: Any,
    handler: _Handler,
    filters: dict[frozenset[str], FilterOrBool],
) -> None:
    def call(event: KeyPressEvent, handler=handler, arg=binding.arg):
        return handler(event, arg)

    key_bindings.add(
        *binding.pt_keys,
        filter=filters[binding.contexts],
        eager=binding.eager,
    )(call)


def _context_filters(owner: Any) -> dict[frozenset[str], FilterOrBool]:
    """Map each context set used by ``KEYMAP`` to its activation filter."""
    return {
        ALL_CONTEXTS: True,
        NO_APPROVAL_CONTEXTS: Condition(lambda: not owner._approval_visible()),
        frozenset({CONTEXT_APPROVAL}): Condition(owner._approval_visible),
        frozenset({CONTEXT_PALETTE}): Condition(
            lambda: owner._palette_visible() and not owner._approval_visible()
        ),
        frozenset({CONTEXT_TASKS}): Condition(
            lambda: owner._tasks_visible and not owner._approval_visible()
        ),
        frozenset({CONTEXT_REWIND}): Condition(
            lambda: owner._rewind_visible() and not owner._approval_visible()
        ),
        frozenset({CONTEXT_EVIDENCE}): Condition(
            lambda: owner._evidence_visible() and not owner._approval_visible()
        ),
        frozenset({CONTEXT_RUNNING}): Condition(
            lambda: (
                not owner._tasks_visible
                and not owner._approval_visible()
                and owner._is_running()
            )
        ),
        frozenset({CONTEXT_COMPOSER}): Condition(
            lambda: (
                not owner.input_buffer.text
                and not owner._is_running()
                and not owner._approval_visible()
            )
        ),
    }


def _build_handlers(owner: Any) -> dict[str, _Handler]:
    """One handler per action name in ``KEYMAP``; ``arg`` carries deltas."""

    def show_shortcut_help(event, arg):
        owner.show_shortcut_help()
        event.app.invalidate()

    def submit(event, arg):
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

    def queue_message(event, arg):
        """Queue a full next-turn message (spec section 9).

        Terminals with the kitty keyboard protocol or xterm modifyOtherKeys
        report shift+enter distinctly (keyboard_protocol maps both encodings
        to the F21 carrier key); alt+enter is the legacy-terminal fallback.
        """
        owner.queue_current_input()

    def scroll_transcript(event, arg):
        owner.scroll_transcript_page(arg)
        event.app.invalidate()

    def palette_move(event, arg):
        owner._move_palette(arg)

    def approval_move(event, arg):
        owner._move_approval(arg)

    def approval_allow_once(event, arg):
        owner._resolve_approval("allow_once")

    def approval_allow_always(event, arg):
        owner._resolve_approval("allow_always")

    def approval_deny_shortcut(event, arg):
        owner._resolve_approval("deny")

    def approval_show_detail(event, arg):
        owner.show_approval_detail()

    def approval_ignore_text(event, arg):
        """Keep the hidden draft immutable while approval owns keyboard focus."""
        return None

    def lane_move(event, arg):
        owner.select_next_lane(arg)

    def rewind_move(event, arg):
        owner._move_rewind(arg)

    def evidence_move(event, arg):
        owner._move_evidence(arg)

    def insert_newline(event, arg):
        event.current_buffer.insert_text("\n")

    def paste_image(event, arg):
        owner.paste_clipboard_image()

    def paste_text_or_image_path(event, arg):
        normalized = event.data.replace("\r\n", "\n").replace("\r", "\n")
        attachments = pasted_image_attachments(normalized)
        if attachments:
            owner._insert_attachments(attachments)
            return
        owner._insert_text_paste(event.data, normalized)

    def interrupt(event, arg):
        if owner._on_interrupt and owner._on_interrupt():
            event.app.invalidate()
            return
        owner.append_output("\nUse Ctrl-D or type exit to leave Amplifier.\n")

    def exit_repl(event, arg):
        if event.current_buffer.text:
            event.current_buffer.delete()
            return
        owner.request_exit()

    def toggle_tasks(event, arg):
        owner.toggle_task_pane()

    def expand_latest_tool(event, arg):
        owner.expand_latest_tool()

    def show_ledger(event, arg):
        owner.show_ledger()

    def open_rewind(event, arg):
        owner.open_rewind_picker()

    def show_needs_you(event, arg):
        owner.show_needs_you()

    def show_evidence(event, arg):
        owner.open_evidence_picker()

    def cycle_mode(event, arg):
        if owner._on_cycle_mode is None:
            return
        result = owner._on_cycle_mode()
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            owner._submit_tasks.add(task)
            task.add_done_callback(owner._submission_done)
        event.app.invalidate()

    def cycle_permission(event, arg):
        if owner._on_cycle_permission is None:
            return
        result = owner._on_cycle_permission()
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            owner._submit_tasks.add(task)
            task.add_done_callback(owner._submission_done)
        event.app.invalidate()

    def external_edit(event, arg):
        owner.open_external_editor()

    def edit_queued(event, arg):
        owner.edit_last_queued()

    def close_palette(event, arg):
        owner._dismiss_palette()

    def close_rewind(event, arg):
        owner._dismiss_rewind()

    def close_evidence(event, arg):
        owner._dismiss_evidence()

    def deny_approval(event, arg):
        owner._deny_approval()

    def close_tasks(event, arg):
        owner.leave_agent_focus()

    def interrupt_running(event, arg):
        if owner._on_interrupt and owner._on_interrupt():
            event.app.invalidate()

    return {
        "show_shortcut_help": show_shortcut_help,
        "submit": submit,
        "queue_message": queue_message,
        "scroll_transcript": scroll_transcript,
        "palette_move": palette_move,
        "approval_move": approval_move,
        "approval_allow_once": approval_allow_once,
        "approval_allow_always": approval_allow_always,
        "approval_deny_shortcut": approval_deny_shortcut,
        "approval_show_detail": approval_show_detail,
        "approval_ignore_text": approval_ignore_text,
        "lane_move": lane_move,
        "rewind_move": rewind_move,
        "evidence_move": evidence_move,
        "insert_newline": insert_newline,
        "paste_image": paste_image,
        "paste_text_or_image_path": paste_text_or_image_path,
        "interrupt": interrupt,
        "exit": exit_repl,
        "toggle_tasks": toggle_tasks,
        "expand_latest_tool": expand_latest_tool,
        "show_ledger": show_ledger,
        "open_rewind": open_rewind,
        "show_needs_you": show_needs_you,
        "show_evidence": show_evidence,
        "cycle_mode": cycle_mode,
        "cycle_permission": cycle_permission,
        "composer.external_edit": external_edit,
        "composer.edit_queued": edit_queued,
        "close_palette": close_palette,
        "close_rewind": close_rewind,
        "close_evidence": close_evidence,
        "deny_approval": deny_approval,
        "close_tasks": close_tasks,
        "interrupt_running": interrupt_running,
    }


__all__ = ["build_layered_key_bindings"]
