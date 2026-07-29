from __future__ import annotations

import asyncio

import pytest

from amplifier_app_cli.ui.approval import ApprovalTimeoutError
from amplifier_app_cli.ui.approval import CLIApprovalSystem
from amplifier_app_cli.ui.interaction_state import TrustState


@pytest.mark.asyncio
async def test_bound_approval_handler_owns_interactive_decision() -> None:
    system = CLIApprovalSystem()
    captured = []

    async def handler(prompt, options, timeout, default):
        captured.append((prompt, options, timeout, default))
        return "Allow once"

    unbind = system.bind_handler(handler)
    choice = await system.request_approval(
        "Allow load_skill?",
        ["Allow once", "Deny"],
        30,
        "deny",
    )
    unbind()

    assert choice == "Allow once"
    assert system.decision_history[-1].choice == "Allow once"
    assert captured == [("Allow load_skill?", ("Allow once", "Deny"), 30, "deny")]


@pytest.mark.asyncio
async def test_bound_approval_handler_is_timeout_bounded() -> None:
    system = CLIApprovalSystem()

    async def handler(prompt, options, timeout, default):
        await asyncio.Event().wait()
        return "Deny"

    system.bind_handler(handler)

    with pytest.raises(ApprovalTimeoutError):
        await system.request_approval(
            "Allow command?",
            ["Allow once", "Deny"],
            0.01,
            "deny",
        )


@pytest.mark.asyncio
async def test_bound_approval_handler_cannot_invent_an_option() -> None:
    system = CLIApprovalSystem()

    async def handler(prompt, options, timeout, default):
        return "anything"

    system.bind_handler(handler)

    with pytest.raises(ValueError, match="unknown option"):
        await system.request_approval(
            "Allow command?",
            ["Allow once", "Deny"],
            30,
            "deny",
        )


@pytest.mark.asyncio
async def test_explicit_bypass_auto_allows_without_opening_handler() -> None:
    system = CLIApprovalSystem()
    called = False

    async def handler(prompt, options, timeout, default):
        nonlocal called
        called = True
        return "Deny"

    system.bind_handler(handler)
    system.set_bypass_permissions(True)

    choice = await system.request_approval(
        "Allow command?", ["Allow once", "Deny"], 30, "deny"
    )

    assert choice == "Allow once"
    assert called is False
    assert system.decision_history[-1].choice == "Allow once"


@pytest.mark.asyncio
async def test_constructor_can_apply_explicit_bypass_policy() -> None:
    system = CLIApprovalSystem(bypass_permissions=True)

    assert system.bypass_permissions is True

    choice = await system.request_approval(
        "Allow command?", ["Allow once", "Deny"], 30, "deny"
    )

    assert choice == "Allow once"


def test_constructor_defaults_to_approval_required() -> None:
    assert CLIApprovalSystem().bypass_permissions is False


@pytest.mark.asyncio
async def test_direct_permission_change_turns_bypass_back_off() -> None:
    system = CLIApprovalSystem()
    trust = TrustState(initial="bypass")
    calls = []

    async def handler(prompt, options, timeout, default):
        calls.append(prompt)
        return "Deny"

    system.bind_handler(handler)

    def sync() -> None:
        system.set_bypass_permissions(trust.active.name == "bypass")

    trust.add_listener(sync)
    sync()
    assert (
        await system.request_approval("first", ["Allow once", "Deny"], 30, "deny")
        == "Allow once"
    )

    trust.activate("chat")
    assert (
        await system.request_approval("second", ["Allow once", "Deny"], 30, "deny")
        == "Deny"
    )
    assert calls == ["second"]
