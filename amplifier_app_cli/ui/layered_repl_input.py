"""Editor, paste, and attachment behavior for the layered REPL."""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol
from urllib.parse import unquote
from urllib.parse import urlsplit

from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.history import InMemoryHistory

from .clipboard import ChatSubmission
from .clipboard import ImageAttachment
from .clipboard import MAX_CLIPBOARD_ATTACHMENTS
from .clipboard import MAX_CLIPBOARD_TOTAL_BYTES
from .clipboard import read_image_file
from .notices import NoticeKind

if TYPE_CHECKING:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer

    from .clipboard import LosslessTextPasteState
    from .clipboard import TextPasteReference
    from .notices import TransientNoticeState

    class _LayeredReplInputOwner(Protocol):
        input_buffer: Buffer
        application: Application[Any]
        _attachments: list[ImageAttachment]
        _on_submit: Callable[[ChatSubmission], Awaitable[None] | None]
        _submit_tasks: set[asyncio.Task[Any]]
        _paste_tokens: dict[str, TextPasteReference]
        _text_pastes: LosslessTextPasteState
        _notices: TransientNoticeState
        _exit_when_submitted: bool

        def _visible_editor_text(self, text: str) -> str: ...

        def _expand_text_pastes(self, text: str) -> str: ...

        def _submission_done(self, task: asyncio.Task[object]) -> None: ...

        def request_exit(self) -> None: ...


logger = logging.getLogger(__name__)

_PASTE_MARKER = "\u2063"


class LayeredReplInputMixin:
    """Implement editor submission without owning prompt-toolkit layout."""

    def submit_current_input(self: _LayeredReplInputOwner) -> None:
        editor_text = self.input_buffer.text
        if not editor_text.strip():
            self.input_buffer.reset()
            return

        display_text = self._visible_editor_text(editor_text)
        text = self._expand_text_pastes(editor_text)

        if not self._attachments:
            path_attachments = pasted_image_attachments(text)
            if path_attachments:
                self._attachments.extend(path_attachments)
                text = " ".join(
                    f"[Image #{index}]" for index in range(1, len(path_attachments) + 1)
                )
                display_text = text

        self.input_buffer.text = display_text
        self.input_buffer.append_to_history()
        self.input_buffer.reset()
        attachments = tuple(
            attachment
            for index, attachment in enumerate(self._attachments, start=1)
            if f"[Image #{index}]" in text
        )
        self._attachments.clear()
        result = self._on_submit(
            ChatSubmission(
                text,
                attachments,
                display_text=display_text if display_text != text else None,
            )
        )
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            self._submit_tasks.add(task)
            task.add_done_callback(self._submission_done)
        self.application.invalidate()

    def _insert_text_paste(
        self: _LayeredReplInputOwner, raw_text: str, normalized_text: str
    ) -> None:
        for token, reference in tuple(self._paste_tokens.items()):
            if token not in self.input_buffer.text:
                continue
            if self._text_pastes.payload(reference) != raw_text:
                continue
            expanded = self.input_buffer.text.replace(token, normalized_text, 1)
            self._text_pastes.discard(reference)
            del self._paste_tokens[token]
            self.input_buffer.set_document(
                Document(expanded, cursor_position=len(expanded))
            )
            self._notices.show("paste expanded")
            return
        try:
            part = self._text_pastes.capture(raw_text)
        except (TypeError, ValueError) as error:
            self._notices.show(str(error), kind=NoticeKind.ERROR)
            return
        if isinstance(part, str):
            self.input_buffer.insert_text(normalized_text)
            return
        token = f"{_PASTE_MARKER}{part.stub}{_PASTE_MARKER}"
        self._paste_tokens[token] = part
        self.input_buffer.insert_text(token)
        self._notices.show(f"paste collapsed · {part.line_count} lines")

    def _visible_editor_text(self: _LayeredReplInputOwner, text: str) -> str:
        return text.replace(_PASTE_MARKER, "")

    def _expand_text_pastes(self: _LayeredReplInputOwner, text: str) -> str:
        expanded = text
        for token, reference in tuple(self._paste_tokens.items()):
            if token in expanded:
                expanded = expanded.replace(
                    token, self._text_pastes.payload(reference), 1
                )
            self._text_pastes.discard(reference)
        self._paste_tokens.clear()
        return expanded.replace(_PASTE_MARKER, "")

    def _insert_attachments(
        self: _LayeredReplInputOwner, attachments: tuple[ImageAttachment, ...]
    ) -> bool:
        if len(self._attachments) + len(attachments) > MAX_CLIPBOARD_ATTACHMENTS:
            self._notices.show("image attachment limit reached", kind=NoticeKind.ERROR)
            return False
        total_bytes = sum(len(image.data) for image in self._attachments)
        total_bytes += sum(len(image.data) for image in attachments)
        if total_bytes > MAX_CLIPBOARD_TOTAL_BYTES:
            self._notices.show(
                "image attachment size limit reached", kind=NoticeKind.ERROR
            )
            return False
        first_index = len(self._attachments) + 1
        self._attachments.extend(attachments)
        placeholders = " ".join(
            f"[Image #{index}]"
            for index in range(first_index, first_index + len(attachments))
        )
        self.input_buffer.insert_text(placeholders)
        count = len(attachments)
        suffix = "image" if count == 1 else "images"
        self._notices.show(f"{count} {suffix} attached", kind=NoticeKind.SUCCESS)
        self.application.invalidate()
        return True

    def _submission_done(
        self: _LayeredReplInputOwner, task: asyncio.Task[object]
    ) -> None:
        self._submit_tasks.discard(task)
        if self._exit_when_submitted and not self._submit_tasks:
            self._exit_when_submitted = False
            self.request_exit()


def load_history(history_path: Path):
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return FileHistory(str(history_path))
    except OSError as error:
        logger.warning(
            "Could not load history from %s: %s. Using in-memory history.",
            history_path,
            error,
        )
        return InMemoryHistory()


def pasted_image_attachments(text: str) -> tuple[ImageAttachment, ...]:
    """Convert a pasted or dragged local image path list into attachments."""
    value = text.strip()
    if not value:
        return ()

    direct = _read_image_path(value.strip("'\""))
    if direct is not None:
        return (direct,)

    try:
        tokens = shlex.split(value)
    except ValueError:
        return ()
    if not 1 <= len(tokens) <= MAX_CLIPBOARD_ATTACHMENTS:
        return ()

    attachments: list[ImageAttachment] = []
    total_bytes = 0
    for token in tokens:
        attachment = _read_image_path(token)
        if attachment is None:
            return ()
        total_bytes += len(attachment.data)
        if total_bytes > MAX_CLIPBOARD_TOTAL_BYTES:
            return ()
        attachments.append(attachment)
    return tuple(attachments)


def _read_image_path(value: str) -> ImageAttachment | None:
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            return None
        candidate = unquote(parsed.path)
    else:
        if not value.startswith(("/", "~/", "./", "../")):
            return None
        candidate = value
    return read_image_file(Path(candidate).expanduser())


__all__ = ["LayeredReplInputMixin", "load_history", "pasted_image_attachments"]
