from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from amplifier_app_cli.runtime.execution_interrupt import ExecutionInterruptController
from amplifier_app_cli.ui.notices import NoticeKind


def test_interrupt_escalates_from_graceful_to_immediate() -> None:
    cancellation = MagicMock()
    cancellation.is_cancelled = False
    cancellation.running_tool_names = ["write_file"]
    event = asyncio.Event()
    notices: list[tuple[str, NoticeKind]] = []
    controller = ExecutionInterruptController(
        cancellation=cancellation,
        is_running=lambda: True,
        immediate_event=event,
        notify=lambda text, kind: notices.append((text, kind)),
    )

    assert controller.request() is True
    cancellation.request_graceful.assert_called_once()
    assert notices[-1][0].startswith("stopping after write_file")
    assert event.is_set() is False

    cancellation.is_cancelled = True
    assert controller.request() is True
    cancellation.request_immediate.assert_called_once()
    assert event.is_set() is True
    assert notices[-1] == ("cancelling immediately", NoticeKind.ERROR)


def test_interrupt_is_ignored_while_idle() -> None:
    cancellation = MagicMock()
    controller = ExecutionInterruptController(
        cancellation=cancellation,
        is_running=lambda: False,
        immediate_event=asyncio.Event(),
        notify=lambda text, kind: None,
    )

    assert controller.request() is False
    cancellation.request_graceful.assert_not_called()
