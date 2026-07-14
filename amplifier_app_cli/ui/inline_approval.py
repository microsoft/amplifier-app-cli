"""Bounded state for approvals owned by the layered prompt surface."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Literal

ApprovalDefault = Literal["allow", "deny"]

_MAX_PENDING = 8
_MAX_OPTIONS = 8
_MAX_PROMPT_CHARS = 512
_MAX_OPTION_CHARS = 80


class ApprovalQueueFullError(RuntimeError):
    """Raised when the bounded approval surface cannot accept more work."""


@dataclass(frozen=True, slots=True)
class InlineApprovalSnapshot:
    """Immutable view consumed by the prompt-toolkit renderer."""

    prompt: str
    options: tuple[str, ...]
    selected_index: int
    remaining_seconds: float

    @property
    def selected_option(self) -> str:
        return self.options[self.selected_index]


@dataclass(slots=True)
class _PendingApproval:
    prompt: str
    options: tuple[str, ...]
    default: ApprovalDefault
    deadline: float
    selected_index: int
    future: asyncio.Future[str]


def _bounded_text(value: object, limit: int) -> str:
    text = " ".join(
        "".join(
            character if ord(character) >= 32 else " " for character in str(value)
        ).split()
    )
    return text[:limit]


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

    async def request(
        self,
        prompt: str,
        options: tuple[str, ...],
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
            _bounded_text(option, _MAX_OPTION_CHARS) for option in supplied_options
        )
        if not normalized_options or any(not option for option in normalized_options):
            raise ValueError("approval options must contain non-empty labels")
        if len(set(normalized_options)) != len(normalized_options):
            raise ValueError("approval options must be unique")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        request = _PendingApproval(
            prompt=_bounded_text(prompt, _MAX_PROMPT_CHARS) or "Approval required",
            options=normalized_options,
            default=default,
            deadline=monotonic() + timeout,
            selected_index=self._initial_selection(normalized_options),
            future=future,
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
        self._resolve(request, request.options[request.selected_index])
        return True

    def deny(self) -> bool:
        if not self._pending:
            return False
        request = self._pending[0]
        self._resolve(request, self._deny_option(request.options))
        return True

    def close(self) -> None:
        """Resolve every waiter conservatively before the application exits."""
        if self._closed:
            return
        self._closed = True
        for request in tuple(self._pending):
            self._resolve(request, self._deny_option(request.options), notify=False)
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
    def _initial_selection(options: tuple[str, ...]) -> int:
        return next(
            (
                index
                for index, option in enumerate(options)
                if "deny" not in option.casefold()
            ),
            0,
        )

    @staticmethod
    def _deny_option(options: tuple[str, ...]) -> str:
        return next(
            (option for option in options if "deny" in option.casefold()),
            options[-1],
        )

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()


__all__ = [
    "ApprovalDefault",
    "ApprovalQueueFullError",
    "InlineApprovalSnapshot",
    "InlineApprovalState",
]
