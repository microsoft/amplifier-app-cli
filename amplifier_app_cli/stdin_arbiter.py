"""Coordinates exclusive stdin access between approval prompts and the
mid-turn steering reader. Approval is the priority owner: while an approval is
in flight, the steering reader suspends entirely.
"""

from __future__ import annotations


class StdinArbiter:
    def __init__(self) -> None:
        self._approval_active = False

    @property
    def approval_active(self) -> bool:
        return self._approval_active

    def begin_approval(self) -> None:
        self._approval_active = True

    def end_approval(self) -> None:
        self._approval_active = False
