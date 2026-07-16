"""Editor, paste, and attachment behavior for the layered REPL."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import tempfile
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol
from urllib.parse import unquote
from urllib.parse import urlsplit

from prompt_toolkit.application import in_terminal
from prompt_toolkit.application.current import set_app
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
        _external_editor_task: asyncio.Task[None] | None
        _keyboard_enhancements_active: bool
        _terminal_file: Any

        def _visible_editor_text(self, text: str) -> str: ...

        def _expand_text_pastes(self, text: str) -> str: ...

        def _submission_done(self, task: asyncio.Task[object]) -> None: ...

        def _run_external_editor(
            self, command: list[str]
        ) -> Coroutine[Any, Any, None]: ...

        def _run_editor_process(
            self, command: list[str], filename: str
        ) -> Awaitable[int | None]: ...

        def _keyboard_enhancement_pop_sequence(self) -> str: ...

        def request_exit(self) -> None: ...

        def submit_current_input(self, *, queue: bool = False) -> None: ...


logger = logging.getLogger(__name__)

_PASTE_MARKER = "\u2063"

# Codex external_editor.rs parity: the draft round-trips through a markdown
# tempfile so editors pick up prose highlighting.
_EDITOR_TEMPFILE_SUFFIX = ".md"


class LayeredReplInputMixin:
    """Implement editor submission without owning prompt-toolkit layout."""

    # One editor round-trip at a time; ``None`` between round-trips.
    _external_editor_task: asyncio.Task[None] | None = None

    def open_external_editor(self: _LayeredReplInputOwner) -> asyncio.Task[None] | None:
        """Edit the draft in $VISUAL/$EDITOR (action ``composer.external_edit``).

        Verified against prompt_toolkit's ``Buffer.open_in_editor``: its
        ``run_in_terminal`` suspend (leave the alternate screen, cooked mode,
        detached input, editor subprocess on the real terminal fds) is correct
        for this full-screen application, and it never fights the
        ``TranscriptOutputBridge``, which only patches ``sys.stdout``/``stderr``.
        What it cannot do is pop this app's progressive keyboard enhancements:
        the app re-pushes them on every render (``after_render``), so a disable
        written before the suspend would be re-enabled by the very next frame
        and the editor would receive kitty/CSI-u encodings. The round-trip
        therefore runs through the same ``in_terminal`` suspend the background
        shell uses (``layered_repl_terminal``), popping the enhancements inside
        the suspended window; the resume render pushes them back.
        """
        active = self._external_editor_task
        if active is not None and not active.done():
            self._notices.show("editor already open")
            return active
        command = editor_command()
        if command is None:
            self._notices.show(
                "set $VISUAL or $EDITOR to edit the draft", kind=NoticeKind.ERROR
            )
            return None
        expanded = self._expand_text_pastes(self.input_buffer.text)
        if expanded != self.input_buffer.text:
            # Hand the editor real content, not collapsed paste stubs.
            self.input_buffer.set_document(
                Document(expanded, cursor_position=len(expanded))
            )
        self.input_buffer.tempfile_suffix = _EDITOR_TEMPFILE_SUFFIX
        task = asyncio.create_task(self._run_external_editor(command))
        self._external_editor_task = task
        return task

    async def _run_external_editor(
        self: _LayeredReplInputOwner, command: list[str]
    ) -> None:
        """Draft -> tempfile -> editor -> replace draft on clean exit."""
        suffix = self.input_buffer.tempfile_suffix or _EDITOR_TEMPFILE_SUFFIX
        descriptor, filename = tempfile.mkstemp(suffix=str(suffix))
        draft = self.input_buffer.text
        try:
            os.write(descriptor, draft.encode("utf-8"))
        finally:
            os.close(descriptor)
        try:
            returncode = await self._run_editor_process(command, filename)
            if returncode is None:
                return  # launch failed; its error notice is already showing
            if returncode != 0:
                self._notices.show("editor exited unsaved · draft unchanged")
                return
            text = Path(filename).read_text(encoding="utf-8")
            # Editors append a trailing newline; the composer does not want it.
            text = text.removesuffix("\n")
            if text != draft:
                self.input_buffer.set_document(
                    Document(text, cursor_position=len(text))
                )
                self._notices.show("draft updated from editor")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(filename)
            self._external_editor_task = None
            self.application.invalidate()

    async def _run_editor_process(
        self: _LayeredReplInputOwner, command: list[str], filename: str
    ) -> int | None:
        """Run the editor over the suspended application; return its exit code.

        Returns ``None`` when the editor could not be launched at all.
        """
        process: asyncio.subprocess.Process | None = None
        try:
            with set_app(self.application):
                async with in_terminal(render_cli_done=False):
                    if self._keyboard_enhancements_active:
                        # Hand the editor a legacy keyboard; the resume render
                        # pushes the enhancements again (layered_repl_terminal).
                        # Pop exactly what was pushed (mirrors
                        # LayeredReplTerminalMixin._run_background_shell): a
                        # probed terminal also gets focus tracking (mode 1004)
                        # pushed, so a blind KEYBOARD_ENHANCEMENT_DISABLE would
                        # leave it enabled while the editor owns the terminal.
                        self._terminal_file.write(
                            self._keyboard_enhancement_pop_sequence()
                        )
                        self._terminal_file.flush()
                        self._keyboard_enhancements_active = False
                    try:
                        process = await asyncio.create_subprocess_exec(
                            *command, filename
                        )
                    except OSError as error:
                        self._notices.show(
                            f"could not launch editor: {error}",
                            kind=NoticeKind.ERROR,
                        )
                        return None
                    return await process.wait()
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            raise

    def edit_last_queued(self: _LayeredReplInputOwner) -> bool:
        """Pop the newest queued message back into the composer.

        Action ``composer.edit_queued`` (Codex pending_input_preview.rs
        parity): only when the composer is empty, so a draft in progress is
        never clobbered. The popped text still carries its ``[Image #N]``
        placeholders, so the popped attachments are restored alongside it.
        """
        if self.input_buffer.text:
            return False
        # Wired by LayeredReplBindings.pop_last_queued; getattr keeps embedders
        # without the binding (and pre-wiring construction) safe.
        supplier = getattr(self, "_pop_last_queued", None)
        popped = supplier() if supplier is not None else None
        if popped is None:
            return False
        text, attachments = popped
        # The empty composer cannot reference attachments; drop any orphans so
        # the restored placeholder indices line up.
        self._attachments.clear()
        self._attachments.extend(attachments)
        self.input_buffer.set_document(Document(text, cursor_position=len(text)))
        self._notices.show("queued message recalled")
        self.application.invalidate()
        return True

    def queue_current_input(self: _LayeredReplInputOwner) -> None:
        """Queue the draft as a full next-turn message (spec queue-vs-steer)."""
        self.submit_current_input(queue=True)

    def submit_current_input(
        self: _LayeredReplInputOwner, *, queue: bool = False
    ) -> None:
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
                queue=queue,
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


def editor_command() -> list[str] | None:
    """Resolve the external editor: ``$VISUAL`` over ``$EDITOR``, shell-split.

    Returns ``None`` when neither variable holds a usable command (Codex
    external_editor.rs parity: missing, empty, or unparseable).
    """
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    try:
        parts = shlex.split(raw)
    except ValueError:
        return None
    return parts or None


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


__all__ = [
    "LayeredReplInputMixin",
    "editor_command",
    "load_history",
    "pasted_image_attachments",
]
