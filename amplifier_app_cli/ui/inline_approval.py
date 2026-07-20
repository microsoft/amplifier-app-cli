"""Bounded state for approvals owned by the layered prompt surface.

Decisions are typed (`ApprovalDecision`) the way the Codex TUI's
``approval_overlay.rs`` types them; option *labels* remain plain strings at
the kernel boundary (the hook contract passes ``list[str]`` options and gets
one of those strings back), so ``option_from_label``/``decision_for_choice``
form the compatibility shim between the two worlds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic
from typing import Literal

ApprovalDefault = Literal["allow", "deny"]
ApprovalDecision = Literal["allow_once", "allow_always", "deny"]

_MAX_PENDING = 8
_MAX_OPTIONS = 8
_MAX_PROMPT_CHARS = 512
_MAX_OPTION_CHARS = 80
_MAX_SHORTCUT_CHARS = 1
_MAX_DETAIL_CHARS = 4_096
_MAX_DETAIL_FIELDS = 8
_MAX_DETAIL_FIELD_NAME_CHARS = 64
_MAX_DETAIL_FIELD_CHARS = 2_048
_MAX_STAGED_DETAILS = 8

# Per-decision shortcut letters (Codex approval_overlay.rs: y/a/d, esc=deny).
# The KEYMAP entries in ``key_bindings_table.py`` must use the same letters.
DECISION_SHORTCUTS: dict[ApprovalDecision, str] = {
    "allow_once": "y",
    "allow_always": "a",
    "deny": "d",
}


class ApprovalQueueFullError(RuntimeError):
    """Raised when the bounded approval surface cannot accept more work."""


@dataclass(frozen=True, slots=True)
class ApprovalOption:
    """One selectable approval outcome: label shown, decision meant."""

    label: str
    decision: ApprovalDecision
    shortcut: str | None = None


STANDARD_APPROVAL_OPTIONS: tuple[ApprovalOption, ...] = (
    ApprovalOption("Allow once", "allow_once", DECISION_SHORTCUTS["allow_once"]),
    ApprovalOption("Allow always", "allow_always", DECISION_SHORTCUTS["allow_always"]),
    ApprovalOption("Deny", "deny", DECISION_SHORTCUTS["deny"]),
)


def decision_for_label(label: object) -> ApprovalDecision:
    """Classify a bare option label from the kernel boundary."""
    folded = str(label).casefold()
    if "deny" in folded:
        return "deny"
    if "always" in folded:
        return "allow_always"
    return "allow_once"


def option_from_label(label: str) -> ApprovalOption:
    """Compatibility shim: lift one kernel-boundary label into a typed option."""
    decision = decision_for_label(label)
    return ApprovalOption(label, decision, DECISION_SHORTCUTS[decision])


def option_labels(options: Iterable[ApprovalOption]) -> tuple[str, ...]:
    """Project typed options back to the kernel's plain-string option list."""
    return tuple(option.label for option in options)


def decision_for_choice(
    options: Iterable[ApprovalOption], choice: str
) -> ApprovalDecision:
    """Map a resolved label back to its typed decision (exact match first)."""
    for option in options:
        if option.label == choice:
            return option.decision
    return decision_for_label(choice)


def _bounded_text(value: object, limit: int) -> str:
    text = " ".join(
        "".join(
            character if ord(character) >= 32 else " " for character in str(value)
        ).split()
    )
    return text[:limit]


def _detail_text(value: object, limit: int) -> str:
    """Bound multi-line detail text: keep newlines, drop other control chars."""
    lines = str(value).splitlines()
    cleaned = "\n".join(
        "".join(char for char in line if ord(char) >= 32).rstrip() for line in lines
    )
    return cleaned.strip()[:limit]


@dataclass(frozen=True, slots=True)
class ApprovalDetail:
    """Full request payload kept beyond the inline 512-char summary."""

    prompt: str
    fields: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt", _detail_text(self.prompt, _MAX_DETAIL_CHARS))
        cleaned = tuple(
            (
                _bounded_text(name, _MAX_DETAIL_FIELD_NAME_CHARS),
                _detail_text(value, _MAX_DETAIL_FIELD_CHARS),
            )
            for name, value in self.fields[:_MAX_DETAIL_FIELDS]
        )
        object.__setattr__(
            self,
            "fields",
            tuple((name, value) for name, value in cleaned if name and value),
        )


class _ApprovalDetailStage:
    """Bounded side-channel pairing full payloads with summary prompts.

    The kernel's approval contract only carries ``(prompt, options, timeout,
    default)``, so producers that know the full request (governance hook,
    approval provider) stage the payload here, keyed by the prompt they send;
    the inline surface claims it when the same prompt arrives.
    """

    def __init__(self) -> None:
        self._staged: dict[str, ApprovalDetail] = {}

    def stage(self, prompt: object, detail: ApprovalDetail) -> None:
        key = _bounded_text(prompt, _MAX_PROMPT_CHARS)
        if not key:
            return
        self._staged.pop(key, None)
        self._staged[key] = detail
        while len(self._staged) > _MAX_STAGED_DETAILS:
            del self._staged[next(iter(self._staged))]

    def claim(self, prompt: object) -> ApprovalDetail | None:
        return self._staged.pop(_bounded_text(prompt, _MAX_PROMPT_CHARS), None)


_DETAIL_STAGE = _ApprovalDetailStage()


def stage_approval_detail(prompt: object, detail: ApprovalDetail) -> None:
    """Stage the full request payload for the next approval with *prompt*."""
    _DETAIL_STAGE.stage(prompt, detail)


@dataclass(frozen=True, slots=True)
class InlineApprovalSnapshot:
    """Immutable view consumed by the prompt-toolkit renderer."""

    prompt: str
    options: tuple[ApprovalOption, ...]
    selected_index: int
    remaining_seconds: float

    @property
    def selected_option(self) -> ApprovalOption:
        return self.options[self.selected_index]

    @property
    def labels(self) -> tuple[str, ...]:
        return option_labels(self.options)


@dataclass(slots=True)
class _PendingApproval:
    prompt: str
    options: tuple[ApprovalOption, ...]
    default: ApprovalDefault
    deadline: float
    selected_index: int
    future: asyncio.Future[str]
    detail: ApprovalDetail = field(default_factory=lambda: ApprovalDetail(""))


def _normalized_option(option: str | ApprovalOption) -> ApprovalOption:
    if isinstance(option, ApprovalOption):
        label = _bounded_text(option.label, _MAX_OPTION_CHARS)
        shortcut = (
            _bounded_text(option.shortcut, _MAX_SHORTCUT_CHARS).lower()
            if option.shortcut
            else None
        )
        return ApprovalOption(label, option.decision, shortcut or None)
    return option_from_label(_bounded_text(option, _MAX_OPTION_CHARS))


class InlineApprovalState:
    """Serialize approval questions without taking ownership of terminal input."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._pending: list[_PendingApproval] = []
        self._on_change = on_change
        self._closed = False

    @property
    def visible(self) -> bool:
        return bool(self._pending)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def snapshot(self) -> InlineApprovalSnapshot | None:
        if not self._pending:
            return None
        request = self._pending[0]
        return InlineApprovalSnapshot(
            prompt=request.prompt,
            options=request.options,
            selected_index=request.selected_index,
            remaining_seconds=max(0.0, request.deadline - monotonic()),
        )

    def detail(self) -> ApprovalDetail | None:
        """Full payload of the visible approval (ctrl-a full-detail view)."""
        if not self._pending:
            return None
        return self._pending[0].detail

    async def request(
        self,
        prompt: str,
        options: Sequence[str | ApprovalOption],
        timeout: float,
        default: ApprovalDefault,
    ) -> str:
        """Queue one approval and wait until the layered surface resolves it."""
        if self._closed:
            raise RuntimeError("approval surface is closed")
        if len(self._pending) >= _MAX_PENDING:
            raise ApprovalQueueFullError("approval queue is full")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("approval timeout must be finite and positive")
        if default not in {"allow", "deny"}:
            raise ValueError("approval default must be 'allow' or 'deny'")

        supplied_options = tuple(options)
        if len(supplied_options) > _MAX_OPTIONS:
            raise ValueError(f"approval supports at most {_MAX_OPTIONS} options")
        normalized_options = tuple(
            _normalized_option(option) for option in supplied_options
        )
        if not normalized_options or any(
            not option.label for option in normalized_options
        ):
            raise ValueError("approval options must contain non-empty labels")
        labels = option_labels(normalized_options)
        if len(set(labels)) != len(labels):
            raise ValueError("approval options must be unique")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        summary = _bounded_text(prompt, _MAX_PROMPT_CHARS) or "Approval required"
        detail = _DETAIL_STAGE.claim(prompt) or ApprovalDetail(prompt=str(prompt))
        request = _PendingApproval(
            prompt=summary,
            options=normalized_options,
            default=default,
            deadline=monotonic() + timeout,
            selected_index=self._initial_selection(normalized_options),
            future=future,
            detail=detail,
        )
        self._pending.append(request)
        self._changed()
        try:
            return await future
        finally:
            if request in self._pending:
                self._pending.remove(request)
                self._changed()

    def move(self, offset: int) -> bool:
        if not self._pending or not offset:
            return False
        request = self._pending[0]
        request.selected_index = (request.selected_index + offset) % len(
            request.options
        )
        self._changed()
        return True

    def accept(self) -> bool:
        if not self._pending:
            return False
        request = self._pending[0]
        self._resolve(request, request.options[request.selected_index].label)
        return True

    def resolve_decision(self, decision: ApprovalDecision) -> bool:
        """Resolve via shortcut semantics: only if an option carries *decision*."""
        if not self._pending:
            return False
        request = self._pending[0]
        option = next(
            (option for option in request.options if option.decision == decision),
            None,
        )
        if option is None:
            return False
        self._resolve(request, option.label)
        return True

    def deny(self) -> bool:
        """Esc/close path: deny, falling back conservatively to the last option."""
        if not self._pending:
            return False
        request = self._pending[0]
        self._resolve(request, self._deny_option(request.options).label)
        return True

    def close(self) -> None:
        """Resolve every waiter conservatively before the application exits."""
        if self._closed:
            return
        self._closed = True
        for request in tuple(self._pending):
            self._resolve(
                request, self._deny_option(request.options).label, notify=False
            )
        self._pending.clear()
        self._changed()

    def _resolve(
        self, request: _PendingApproval, choice: str, *, notify: bool = True
    ) -> None:
        if request in self._pending:
            self._pending.remove(request)
        if not request.future.done():
            request.future.set_result(choice)
        if notify:
            self._changed()

    @staticmethod
    def _initial_selection(options: tuple[ApprovalOption, ...]) -> int:
        return next(
            (
                index
                for index, option in enumerate(options)
                if option.decision != "deny"
            ),
            0,
        )

    @staticmethod
    def _deny_option(options: tuple[ApprovalOption, ...]) -> ApprovalOption:
        return next(
            (option for option in options if option.decision == "deny"),
            options[-1],
        )

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()


__all__ = [
    "ApprovalDecision",
    "ApprovalDefault",
    "ApprovalDetail",
    "ApprovalOption",
    "ApprovalQueueFullError",
    "DECISION_SHORTCUTS",
    "InlineApprovalSnapshot",
    "InlineApprovalState",
    "STANDARD_APPROVAL_OPTIONS",
    "decision_for_choice",
    "decision_for_label",
    "option_from_label",
    "option_labels",
    "stage_approval_detail",
]
