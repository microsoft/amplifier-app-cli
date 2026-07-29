"""Deterministic tests for the bounded inline approval state."""

from __future__ import annotations

import asyncio

import pytest

from amplifier_app_cli.ui.inline_approval import STANDARD_APPROVAL_OPTIONS
from amplifier_app_cli.ui.inline_approval import ApprovalDetail
from amplifier_app_cli.ui.inline_approval import ApprovalOption
from amplifier_app_cli.ui.inline_approval import ApprovalQueueFullError
from amplifier_app_cli.ui.inline_approval import InlineApprovalState
from amplifier_app_cli.ui.inline_approval import decision_for_choice
from amplifier_app_cli.ui.inline_approval import decision_for_label
from amplifier_app_cli.ui.inline_approval import option_from_label
from amplifier_app_cli.ui.inline_approval import option_labels
from amplifier_app_cli.ui.inline_approval import InlineApprovalSnapshot
from amplifier_app_cli.ui.inline_approval import stage_approval_detail


def _snapshot(state: InlineApprovalState) -> InlineApprovalSnapshot:
    snapshot = state.snapshot()
    assert snapshot is not None
    return snapshot


def _detail(state: InlineApprovalState) -> ApprovalDetail:
    detail = state.detail()
    assert detail is not None
    return detail


@pytest.mark.asyncio
async def test_default_selection_accepts_allow_once() -> None:
    state = InlineApprovalState()
    decision = asyncio.create_task(
        state.request("Allow load_skill?", ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    assert _snapshot(state).selected_option.label == "Allow once"
    assert _snapshot(state).selected_option.decision == "allow_once"
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
    assert _snapshot(state).prompt == "First?"
    assert state.deny() is True
    assert await first == "Deny"
    assert _snapshot(state).prompt == "Second?"
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

    assert _snapshot(state).prompt == "Second?"
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


def test_label_shim_classifies_decisions_and_assigns_shortcuts() -> None:
    assert decision_for_label("Allow once") == "allow_once"
    assert decision_for_label("Allow always") == "allow_always"
    assert decision_for_label("Deny") == "deny"
    assert decision_for_label("Proceed") == "allow_once"

    assert option_from_label("Allow once") == ApprovalOption(
        "Allow once", "allow_once", "y"
    )
    assert option_from_label("Allow always").shortcut == "a"
    assert option_from_label("Deny").shortcut == "d"

    assert option_labels(STANDARD_APPROVAL_OPTIONS) == (
        "Allow once",
        "Allow always",
        "Deny",
    )
    assert decision_for_choice(STANDARD_APPROVAL_OPTIONS, "Allow always") == (
        "allow_always"
    )
    # Unknown labels fall back to classification, never crash.
    assert decision_for_choice(STANDARD_APPROVAL_OPTIONS, "deny it") == "deny"


@pytest.mark.asyncio
async def test_typed_options_survive_the_round_trip() -> None:
    state = InlineApprovalState()
    options = (
        ApprovalOption("Run it", "allow_once", "y"),
        ApprovalOption("Refuse", "deny", "d"),
    )
    decision = asyncio.create_task(state.request("Run tests?", options, 30, "deny"))
    await asyncio.sleep(0)

    snapshot = _snapshot(state)
    assert snapshot.options == options
    assert snapshot.labels == ("Run it", "Refuse")
    # Esc denies via the typed decision, not a label substring.
    assert state.deny() is True
    assert await decision == "Refuse"


@pytest.mark.asyncio
async def test_shortcut_decisions_resolve_before_navigation() -> None:
    state = InlineApprovalState()
    once = asyncio.create_task(
        state.request("One?", ("Allow once", "Allow always", "Deny"), 30, "deny")
    )
    always = asyncio.create_task(
        state.request("Two?", ("Allow once", "Allow always", "Deny"), 30, "deny")
    )
    deny = asyncio.create_task(
        state.request("Three?", ("Allow once", "Allow always", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    assert state.resolve_decision("allow_once") is True
    assert await once == "Allow once"
    assert state.resolve_decision("allow_always") is True
    assert await always == "Allow always"
    assert state.resolve_decision("deny") is True
    assert await deny == "Deny"
    assert state.resolve_decision("deny") is False


@pytest.mark.asyncio
async def test_shortcut_without_matching_option_is_ignored() -> None:
    state = InlineApprovalState()
    decision = asyncio.create_task(
        state.request("Pick?", ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    assert state.resolve_decision("allow_always") is False
    assert state.visible is True
    state.deny()
    assert await decision == "Deny"


@pytest.mark.asyncio
async def test_detail_keeps_full_prompt_beyond_inline_summary() -> None:
    state = InlineApprovalState()
    long_prompt = "Allow " + "x" * 900 + "?"
    decision = asyncio.create_task(
        state.request(long_prompt, ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    assert len(_snapshot(state).prompt) == 512
    detail = _detail(state)
    assert detail.prompt == long_prompt
    state.deny()
    await decision


@pytest.mark.asyncio
async def test_staged_detail_is_claimed_by_matching_prompt() -> None:
    state = InlineApprovalState()
    prompt = "Allow rm -rf build?"
    stage_approval_detail(
        prompt,
        ApprovalDetail(
            prompt=prompt,
            fields=(
                ("command", "rm -rf build"),
                ("cwd", "/repo"),
                ("rule", "shell requires approval in build mode"),
                ("", "dropped"),
                ("empty-value", ""),
            ),
        ),
    )
    decision = asyncio.create_task(
        state.request(prompt, ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)

    detail = _detail(state)
    assert detail.fields == (
        ("command", "rm -rf build"),
        ("cwd", "/repo"),
        ("rule", "shell requires approval in build mode"),
    )
    state.deny()
    await decision
    # Claimed exactly once: a second identical prompt falls back to itself.
    follow_up = asyncio.create_task(
        state.request(prompt, ("Allow once", "Deny"), 30, "deny")
    )
    await asyncio.sleep(0)
    assert _detail(state).fields == ()
    state.deny()
    await follow_up


def test_approval_detail_is_bounded_and_sanitized() -> None:
    detail = ApprovalDetail(
        prompt="line one\r\nline two\x07 bell",
        fields=tuple((f"name-{index}", "v" * 5_000) for index in range(12)),
    )
    assert detail.prompt == "line one\nline two bell"
    assert len(detail.fields) == 8
    assert all(len(value) == 2_048 for _, value in detail.fields)
