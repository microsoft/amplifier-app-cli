"""Bounded, in-memory state for lossless text-paste placeholders."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypeAlias

DEFAULT_LONG_PASTE_LINE_THRESHOLD = 10
DEFAULT_LONG_PASTE_CHAR_THRESHOLD = 800
MAX_TEXT_PASTE_BYTES = 2 * 1024 * 1024
MAX_TEXT_PASTES = 32
MAX_TEXT_PASTE_TOTAL_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True, eq=False)
class TextPasteReference:
    """Opaque reference to text retained by a :class:`LosslessTextPasteState`."""

    paste_id: int
    line_count: int
    stub: str
    _owner: object = field(repr=False)


TextPastePart: TypeAlias = str | TextPasteReference


@dataclass(frozen=True, slots=True)
class _StoredTextPaste:
    payload: str
    byte_count: int
    reference: TextPasteReference


class LosslessTextPasteState:
    """Retain large text pastes while exposing compact editor placeholders.

    Editor integrations should keep ``TextPasteReference`` objects as structured
    parts instead of replacing their visible stub text. This lets literal user
    text that happens to match a stub remain ordinary text during expansion.
    """

    def __init__(
        self,
        *,
        line_threshold: int = DEFAULT_LONG_PASTE_LINE_THRESHOLD,
        char_threshold: int = DEFAULT_LONG_PASTE_CHAR_THRESHOLD,
        max_pastes: int = MAX_TEXT_PASTES,
        max_paste_bytes: int = MAX_TEXT_PASTE_BYTES,
        max_total_bytes: int = MAX_TEXT_PASTE_TOTAL_BYTES,
    ) -> None:
        _require_positive_int("line_threshold", line_threshold)
        _require_positive_int("char_threshold", char_threshold)
        _require_positive_int("max_pastes", max_pastes)
        _require_positive_int("max_paste_bytes", max_paste_bytes)
        _require_positive_int("max_total_bytes", max_total_bytes)
        if max_paste_bytes > max_total_bytes:
            raise ValueError("max_paste_bytes cannot exceed max_total_bytes")

        self._line_threshold = line_threshold
        self._char_threshold = char_threshold
        self._max_pastes = max_pastes
        self._max_paste_bytes = max_paste_bytes
        self._max_total_bytes = max_total_bytes
        self._owner = object()
        self._next_paste_id = 1
        self._total_bytes = 0
        self._pastes: dict[int, _StoredTextPaste] = {}

    @property
    def line_threshold(self) -> int:
        """Largest line count that remains inline in the editor."""
        return self._line_threshold

    @property
    def max_pastes(self) -> int:
        """Maximum number of retained text pastes."""
        return self._max_pastes

    @property
    def char_threshold(self) -> int:
        """Largest character count that remains inline in the editor."""
        return self._char_threshold

    @property
    def max_paste_bytes(self) -> int:
        """Maximum UTF-8 byte count of one text paste."""
        return self._max_paste_bytes

    @property
    def max_total_bytes(self) -> int:
        """Maximum aggregate UTF-8 byte count of retained text pastes."""
        return self._max_total_bytes

    @property
    def paste_count(self) -> int:
        """Number of retained long pastes."""
        return len(self._pastes)

    @property
    def total_bytes(self) -> int:
        """Aggregate UTF-8 storage attributed to retained long pastes."""
        return self._total_bytes

    def capture(self, payload: str) -> TextPastePart:
        """Return text inline, or retain and reference it when it is long."""
        byte_count = self._validate_payload(payload)
        line_count = _text_line_count(payload)
        if not should_collapse_text_paste(
            payload,
            line_threshold=self.line_threshold,
            char_threshold=self.char_threshold,
        ):
            return payload
        return self._store(payload, byte_count=byte_count, line_count=line_count)

    def retain(self, payload: str) -> TextPasteReference:
        """Retain text regardless of its line count and return an opaque reference."""
        byte_count = self._validate_payload(payload)
        return self._store(
            payload,
            byte_count=byte_count,
            line_count=_text_line_count(payload),
        )

    def render(self, parts: Iterable[TextPastePart]) -> str:
        """Render structured editor parts with compact paste stubs."""
        rendered: list[str] = []
        for part in parts:
            if isinstance(part, str):
                rendered.append(part)
            elif isinstance(part, TextPasteReference):
                rendered.append(self._lookup(part).reference.stub)
            else:
                raise TypeError(
                    "paste parts must be strings or TextPasteReference values"
                )
        return "".join(rendered)

    def expand(self, parts: Iterable[TextPastePart]) -> str:
        """Resolve structured editor parts to the exact text for submission."""
        expanded: list[str] = []
        for part in parts:
            if isinstance(part, str):
                expanded.append(part)
            elif isinstance(part, TextPasteReference):
                expanded.append(self._lookup(part).payload)
            else:
                raise TypeError(
                    "paste parts must be strings or TextPasteReference values"
                )
        return "".join(expanded)

    def payload(self, reference: TextPasteReference) -> str:
        """Return the exact retained payload for one reference."""
        return self._lookup(reference).payload

    def remove(self, reference: TextPasteReference) -> str:
        """Remove one retained paste and return its exact payload."""
        stored = self._lookup(reference)
        del self._pastes[reference.paste_id]
        self._total_bytes -= stored.byte_count
        return stored.payload

    def discard(self, reference: TextPasteReference) -> bool:
        """Remove a retained paste, returning whether it was present."""
        self._validate_reference(reference)
        stored = self._pastes.get(reference.paste_id)
        if stored is None or stored.reference is not reference:
            return False
        del self._pastes[reference.paste_id]
        self._total_bytes -= stored.byte_count
        return True

    def clear(self) -> None:
        """Forget every retained paste without reusing paste identifiers."""
        self._pastes.clear()
        self._total_bytes = 0

    def _validate_payload(self, payload: str) -> int:
        if not isinstance(payload, str):
            raise TypeError("text paste payload must be a string")
        byte_count = len(payload.encode("utf-8", errors="surrogatepass"))
        if byte_count > self.max_paste_bytes:
            raise ValueError("text paste exceeds the per-paste size limit")
        return byte_count

    def _store(
        self, payload: str, *, byte_count: int, line_count: int
    ) -> TextPasteReference:
        if len(self._pastes) >= self.max_pastes:
            raise ValueError("text paste count limit reached")
        if self._total_bytes + byte_count > self.max_total_bytes:
            raise ValueError("text pastes exceed the aggregate size limit")

        paste_id = self._next_paste_id
        self._next_paste_id += 1
        descriptor = _paste_descriptor(payload, line_count=line_count)
        stub = f"[Pasted #{paste_id} \u00b7 {descriptor}]"
        reference = TextPasteReference(
            paste_id=paste_id,
            line_count=line_count,
            stub=stub,
            _owner=self._owner,
        )
        self._pastes[paste_id] = _StoredTextPaste(
            payload=payload,
            byte_count=byte_count,
            reference=reference,
        )
        self._total_bytes += byte_count
        return reference

    def _validate_reference(self, reference: TextPasteReference) -> None:
        if not isinstance(reference, TextPasteReference):
            raise TypeError("reference must be a TextPasteReference")
        if reference._owner is not self._owner:
            raise ValueError("text paste reference belongs to a different state")

    def _lookup(self, reference: TextPasteReference) -> _StoredTextPaste:
        self._validate_reference(reference)
        stored = self._pastes.get(reference.paste_id)
        if stored is None or stored.reference is not reference:
            raise KeyError(f"text paste #{reference.paste_id} is not retained")
        return stored


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _text_line_count(payload: str) -> int:
    return payload.count("\n") + 1


def should_collapse_text_paste(
    payload: str,
    *,
    line_threshold: int = DEFAULT_LONG_PASTE_LINE_THRESHOLD,
    char_threshold: int = DEFAULT_LONG_PASTE_CHAR_THRESHOLD,
) -> bool:
    """Return whether pasted text is too large to remain useful inline."""
    if not isinstance(payload, str):
        raise TypeError("text paste payload must be a string")
    _require_positive_int("line_threshold", line_threshold)
    _require_positive_int("char_threshold", char_threshold)
    return _text_line_count(payload) > line_threshold or len(payload) > char_threshold


def compact_text_paste_display(
    payload: str,
    *,
    line_threshold: int = DEFAULT_LONG_PASTE_LINE_THRESHOLD,
    char_threshold: int = DEFAULT_LONG_PASTE_CHAR_THRESHOLD,
    preview_chars: int = 72,
) -> str:
    """Collapse a visually large user payload while retaining a useful preview."""
    if not should_collapse_text_paste(
        payload,
        line_threshold=line_threshold,
        char_threshold=char_threshold,
    ):
        return payload
    _require_positive_int("preview_chars", preview_chars)
    line_count = _text_line_count(payload)
    descriptor = _paste_descriptor(payload, line_count=line_count, include_chars=True)
    preview = " ".join(payload.split())
    if len(preview) > preview_chars:
        preview = preview[: preview_chars - 3].rstrip() + "..."
    return f"[Pasted text \u00b7 {descriptor}] {preview}".rstrip()


def _paste_descriptor(
    payload: str,
    *,
    line_count: int,
    include_chars: bool = False,
) -> str:
    if line_count == 1:
        return f"{len(payload):,} chars"
    lines = f"{line_count:,} lines"
    if include_chars:
        return f"{lines} \u00b7 {len(payload):,} chars"
    return lines


__all__ = [
    "compact_text_paste_display",
    "DEFAULT_LONG_PASTE_CHAR_THRESHOLD",
    "DEFAULT_LONG_PASTE_LINE_THRESHOLD",
    "LosslessTextPasteState",
    "MAX_TEXT_PASTE_BYTES",
    "MAX_TEXT_PASTES",
    "MAX_TEXT_PASTE_TOTAL_BYTES",
    "TextPastePart",
    "TextPasteReference",
    "should_collapse_text_paste",
]
