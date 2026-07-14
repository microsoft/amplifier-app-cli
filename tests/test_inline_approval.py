"""Deterministic tests for the bounded inline approval state."""

from __future__ import annotations

import asyncio

import pytest

from amplifier_app_cli.ui.inline_approval import ApprovalQueueFullError
from amplifier_app_cli.ui.inline_approval import InlineApprovalState


@pytest.mark.asyncio
async def test_default_selection_accepts_allow_once() -> None:
    state = InlineApprovalState()
    decision = asyncio.create_task(
        state.request("Allow load_skill?", ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    assert state.snapshot().selected_option == "Allow once"
    assert state.accept() is True
    assert await decision == "Allow once"
    assert state.visible is False


@pytest.mark.asyncio
async def test_requests_are_serialized_and_escape_path_denies_current() -> None:
    state = InlineApprovalState()
    first = asyncio.create_task(
        state.request("First?", ("Allow once", "Deny"), 30, "deny")
    )
    second = asyncio.create_task(
        state.request("Second?", ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    assert state.pending_count == 2
    assert state.snapshot().prompt == "First?"
    assert state.deny() is True
    assert await first == "Deny"
    assert state.snapshot().prompt == "Second?"
    assert state.accept() is True
    assert await second == "Allow once"


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed_and_next_request_becomes_visible() -> None:
    state = InlineApprovalState()
    first = asyncio.create_task(state.request("First?", ("Allow", "Deny"), 30, "deny"))
    second = asyncio.create_task(
        state.request("Second?", ("Allow", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert state.snapshot().prompt == "Second?"
    state.deny()
    assert await second == "Deny"


@pytest.mark.asyncio
async def test_close_resolves_every_waiter_conservatively() -> None:
    state = InlineApprovalState()
    decisions = [
        asyncio.create_task(
            state.request(f"Request {index}?", ("Proceed", "Deny"), 30, "allow")
        )
        for index in range(3)
    ]
    await asyncio.sleep(0)

    state.close()

    assert await asyncio.gather(*decisions) == ["Deny", "Deny", "Deny"]
    assert state.visible is False
    with pytest.raises(RuntimeError, match="closed"):
        await state.request("Late?", ("Allow", "Deny"), 30, "deny")


@pytest.mark.asyncio
async def test_queue_and_option_counts_are_bounded() -> None:
    state = InlineApprovalState()
    pending = [
        asyncio.create_task(
            state.request(f"Request {index}?", ("Allow", "Deny"), 30, "deny")
        )
        for index in range(8)
    ]
    await asyncio.sleep(0)

    with pytest.raises(ApprovalQueueFullError):
        await state.request("Overflow?", ("Allow", "Deny"), 30, "deny")

    state.close()
    await asyncio.gather(*pending)

    fresh = InlineApprovalState()
    with pytest.raises(ValueError, match="at most 8"):
        await fresh.request(
            "Too many?", tuple(f"Option {index}" for index in range(9)), 30, "deny"
        )
