"""Inline approval and clipboard behavior for the layered REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.utils import get_cwidth

from .clipboard import ImageAttachment
from .clipboard_availability import ClipboardAvailabilitySnapshot
from .inline_approval import ApprovalDefault
from .notices import NoticeKind
from .repl import summarize_cell_text

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from .inline_approval import InlineApprovalState
    from .notices import TransientNoticeState

    class _LayeredReplApprovalOwner(Protocol):
        application: Application[Any]
        _approval_state: InlineApprovalState
        _notices: TransientNoticeState

        def _copy_text(self, text: str) -> bool: ...

        def _dismiss_evidence(self) -> None: ...

        def _dismiss_palette(self) -> None: ...

        def _dismiss_rewind(self) -> None: ...

        def _insert_attachments(
            self, attachments: tuple[ImageAttachment, ...]
        ) -> bool: ...

        def _read_clipboard_image(self) -> ImageAttachment | None: ...

        def _terminal_size(self) -> tuple[int, int]: ...

        def close_task_pane(self) -> None: ...


class LayeredReplApprovalMixin:
    """Coordinate approvals and clipboard actions with the active composer."""

    async def request_approval(
        self: _LayeredReplApprovalOwner,
        prompt: str,
        options: tuple[str, ...],
        timeout: float,
        default: ApprovalDefault,
    ) -> str:
        """Resolve a hook approval through the active layered input surface."""
        self._dismiss_palette()
        self._dismiss_rewind()
        self._dismiss_evidence()
        self.close_task_pane()
        return await self._approval_state.request(prompt, options, timeout, default)

    def _approval_state_changed(self: _LayeredReplApprovalOwner) -> None:
        application = getattr(self, "application", None)
        if application is not None:
            application.invalidate()

    def _approval_visible(self: _LayeredReplApprovalOwner) -> bool:
        return self._approval_state.visible

    def _move_approval(self: _LayeredReplApprovalOwner, offset: int) -> None:
        self._approval_state.move(offset)

    def _accept_approval(self: _LayeredReplApprovalOwner) -> None:
        self._approval_state.accept()

    def _deny_approval(self: _LayeredReplApprovalOwner) -> None:
        self._approval_state.deny()

    def _clipboard_availability_changed(
        self: _LayeredReplApprovalOwner,
        snapshot: ClipboardAvailabilitySnapshot,
    ) -> None:
        message = "Image in clipboard · ctrl+v to paste"
        if snapshot.image_available:
            if self._notices.current() is None:
                self._notices.show(message)
        else:
            current = self._notices.current()
            if current is not None and current.text == message:
                self._notices.clear()
        self.application.invalidate()

    def paste_clipboard_image(self: _LayeredReplApprovalOwner) -> bool:
        """Attach the current clipboard image and insert a visible placeholder."""
        attachment = self._read_clipboard_image()
        if attachment is None:
            self._notices.show(
                "clipboard does not contain a supported image",
                kind=NoticeKind.WARNING,
            )
            return False
        return self._insert_attachments((attachment,))

    def _copy_transcript_selection(self: _LayeredReplApprovalOwner, text: str) -> bool:
        copied = self._copy_text(text)
        if copied:
            count = len(text)
            suffix = "character" if count == 1 else "characters"
            self._notices.show(
                f"copied {count} {suffix} to clipboard",
                kind=NoticeKind.SUCCESS,
                duration_seconds=2.0,
            )
        else:
            self._notices.show(
                "system clipboard is unavailable",
                kind=NoticeKind.WARNING,
            )
        return copied

    def _approval_text(self: _LayeredReplApprovalOwner) -> FormattedText:
        snapshot = self._approval_state.snapshot()
        if snapshot is None:
            return FormattedText()
        columns = max(1, self._terminal_size()[1])
        option_labels = [
            summarize_cell_text(option, max_cells=18) for option in snapshot.options
        ]
        prefix = " Approval required · "
        options_width = sum(get_cwidth(option) + 4 for option in option_labels)
        if options_width > columns - min(get_cwidth(prefix), columns):
            ratio = f"{snapshot.selected_index + 1}/{len(option_labels)}"
            option_budget = max(3, columns - min(get_cwidth(prefix), columns) - 1)
            label_budget = max(1, option_budget - get_cwidth(ratio) - 1)
            selected = summarize_cell_text(
                option_labels[snapshot.selected_index], max_cells=label_budget
            )
            option_labels = [f"{selected} {ratio}"]
            selected_index = 0
        else:
            selected_index = snapshot.selected_index
        options_width = sum(get_cwidth(option) + 4 for option in option_labels)
        prefix = summarize_cell_text(
            prefix,
            max_cells=max(1, columns - options_width),
        )
        question_width = max(0, columns - get_cwidth(prefix) - options_width - 1)
        question = (
            summarize_cell_text(snapshot.prompt, max_cells=question_width)
            if question_width
            else ""
        )
        fragments: list[tuple[str, str]] = [("class:approval.focus", prefix)]
        if question:
            fragments.append(("class:approval", f"{question} "))
        for index, option in enumerate(option_labels):
            style = (
                "class:approval.selected"
                if index == selected_index
                else "class:approval.option"
            )
            marker = "›" if index == selected_index else " "
            fragments.append((style, f" {marker} {option} "))
        return FormattedText(fragments)


__all__ = ["LayeredReplApprovalMixin"]
