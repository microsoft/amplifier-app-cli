"""Cross-repo integration check for the partial-result path.

Foundation (tool-delegate) is the CONSUMER of the partial; app-cli
(session_spawner) is the PRODUCER. Each half is unit-tested in its own repo
against a fake counterpart; this check wires the two real halves together so
the contract cannot drift silently between them.

Run: PYTHONPATH=<scratch_module>:<itest> python -m pytest test_partial_roundtrip.py

--------------------------------------------------------------------------
EXTENDED for model_performance-eem.

The original check (`test_producer_and_consumer_agree_on_the_partial_contract`,
kept below verbatim) passed on the parent commit and the feature was still
inert in production. Two reasons, and both are fixed here:

  1. its fixture sub-session emitted TEXT blocks. k64 measured 18 real delegate
     legs: a leg emits at most ONE text block, in the final 0.19-0.72 s of a
     5.4-222.0 s leg. A timeout fires in the text-free phase ~99.5% of the
     time, so the fixture tested the case that does not occur;
  2. it called `_seal_partial` with a hand-built `{"chunks": [...]}` record,
     which BYPASSES `_open_partial`'s accumulator entirely -- the very filter
     that was the defect. A test that never runs the accumulator cannot fail
     when the accumulator is wrong.

The added cases drive the REAL accumulator hook with the REAL block shapes
(measured from k64's captures) and emit NO text block at all.
--------------------------------------------------------------------------
"""

import asyncio

import pytest
from amplifier_core.events import CONTENT_BLOCK_END

from amplifier_app_cli.session_spawner import _open_partial
from amplifier_app_cli.session_spawner import _seal_partial
from amplifier_app_cli.session_spawner import get_partial_output
from amplifier_module_tool_delegate import DelegateTool
from amplifier_module_tool_delegate import _NO_PARTIAL_GUIDANCE
from amplifier_module_tool_delegate import _PARTIAL_GUIDANCE


class FakeSession:
    def __init__(self):
        self.session_id = "parent-session"
        self.config = {}


class FakeCoordinator:
    def __init__(self, capabilities):
        self.session_id = "parent-session"
        self.session = FakeSession()
        self.config = {"agents": {"explorer": {}}}
        self._capabilities = capabilities

    def get_capability(self, name):
        return self._capabilities.get(name)


class FakeHooks:
    def __init__(self):
        self.events = []

    async def emit(self, event, data):
        self.events.append((event, data))
        return None


class ChildHooks:
    """A hook coordinator the real `_open_partial` can register against."""

    def __init__(self):
        self.handlers = {}

    def register(self, event, handler, priority=0, name=None):
        self.handlers.setdefault(event, []).append(handler)

        def _unregister():
            if handler in self.handlers.get(event, []):
                self.handlers[event].remove(handler)

        return _unregister

    async def block(self, block):
        for handler in list(self.handlers.get(CONTENT_BLOCK_END, [])):
            await handler(CONTENT_BLOCK_END, {"block": block})


async def _run_delegate(spawn_fn):
    tool = DelegateTool(
        FakeCoordinator(
            {
                "session.spawn": spawn_fn,
                "session.partial": get_partial_output,  # the REAL app-side reader
            }
        ),
        {"settings": {"timeout": 1}},
    )
    return await tool._spawn_new_session(
        agent_name="explorer",
        instruction="find the anchors",
        context_depth="none",
        context_scope="conversation",
        context_turns=0,
        provider_preferences=None,
        hooks=FakeHooks(),
        agents={"explorer": {}},
    )


@pytest.mark.asyncio
async def test_producer_and_consumer_agree_on_the_partial_contract():
    captured_ids = {}

    async def _never_finishes(**kwargs):
        # Stand in for a straggler: record the id the delegate assigned, seal
        # partial text against it the way the real accumulator does, then hang.
        captured_ids["sub"] = kwargs["sub_session_id"]
        _seal_partial(
            kwargs["sub_session_id"],
            {"chunks": ["anchor A1 confirmed. ", "anchor A2 confirmed. "]},
        )
        await asyncio.sleep(3600)

    result = await _run_delegate(_never_finishes)

    assert result.success is False
    assert result.output["status"] == "timeout"
    assert result.output["partial_available"] is True
    assert (
        result.output["partial_response"]
        == "anchor A1 confirmed. anchor A2 confirmed. "
    )
    assert result.output["partial_segments"] == 2
    assert result.output["partial_source"] == "spawn-accumulator"
    assert "response" not in result.output

    # Reads are destructive: the registry does not leak across delegate calls.
    assert get_partial_output(captured_ids["sub"]) is None


# ---------------------------------------------------------------------------
# THE CASE THAT ACTUALLY OCCURS: a sub-session that emits NO text block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_leg_that_never_emits_text_still_round_trips_a_partial():
    """FAIL-BEFORE on app-cli 26e5f10: this returned `partial_available: false`.

    Block shapes are measured, not assumed -- taken from
    treatment-validation/20260903-k64-delegate-timeout captures
    (236 thinking, 53 tool_call, 38 text blocks inspected):

        thinking  {"type": "thinking",  "text": ...}
        tool_call {"type": "tool_call", "id": ..., "name": ...,
                   "input": {...}, "visibility": ...}
    """
    captured_ids = {}

    async def _thinks_and_calls_tools_then_hangs(**kwargs):
        sub_id = kwargs["sub_session_id"]
        captured_ids["sub"] = sub_id
        child_hooks = ChildHooks()
        # The REAL accumulator, registered exactly as spawn_sub_session does.
        _open_partial(sub_id, child_hooks)
        await child_hooks.block(
            {"type": "thinking", "text": "the anchors are probably in the PR body"}
        )
        await child_hooks.block(
            {
                "type": "tool_call",
                "id": "call_1",
                "name": "grep",
                "input": {"pattern": "#281", "path": "/repo", "search": ""},
                "visibility": None,
            }
        )
        await child_hooks.block(
            {"type": "thinking", "text": "no hit; try the changelog instead"}
        )
        # No text block, ever -- this is the ~99.5% real case.
        await asyncio.sleep(3600)

    result = await _run_delegate(_thinks_and_calls_tools_then_hangs)

    assert result.success is False
    assert result.output["status"] == "timeout"
    assert result.output["partial_available"] is True, (
        "a leg with 2 thinking blocks and a tool call recovered nothing -- "
        "this is exactly the defect model_performance-eem closes"
    )
    assert result.output["partial_segments"] == 3
    assert result.output["partial_source"] == "spawn-accumulator:reasoning"
    assert "grep" in result.output["partial_response"]
    assert "changelog" in result.output["partial_response"]

    # The contract's own invariants still hold on this path.
    assert "response" not in result.output
    assert result.output["completed"] is False
    assert get_partial_output(captured_ids["sub"]) is None


@pytest.mark.asyncio
async def test_guidance_string_is_unchanged_for_the_text_case():
    """The case foundation's shipped guidance already describes correctly."""

    async def _text_then_hangs(**kwargs):
        sub_id = kwargs["sub_session_id"]
        child_hooks = ChildHooks()
        _open_partial(sub_id, child_hooks)
        await child_hooks.block({"type": "thinking", "text": "private reasoning"})
        await child_hooks.block({"type": "text", "text": "anchor A1 confirmed. "})
        await asyncio.sleep(3600)

    result = await _run_delegate(_text_then_hangs)

    assert result.output["partial_source"] == "spawn-accumulator"
    assert result.output["partial_response"] == "anchor A1 confirmed. "
    assert result.output["guidance"] == _PARTIAL_GUIDANCE
    assert "private reasoning" not in result.output["partial_response"]


@pytest.mark.asyncio
async def test_guidance_string_for_the_reasoning_case_is_foundations_to_change():
    """PINS THE GAP THIS LANE DOES NOT CROSS.

    foundation picks its guidance from `bool(text)` alone, so a recovered
    REASONING partial is currently described by `_PARTIAL_GUIDANCE` -- "...is
    unfinished work salvaged from the agent mid-flight -- it has NOT been
    checked, concluded, or self-reviewed...". That is a stronger claim than
    raw thinking supports: unfinished prose was at least addressed to a
    reader; private reasoning never was.

    amplifier-foundation is a different repo and this lane stops at the
    boundary (see DONE-NOTE.md, "The guidance string"). What the PRODUCER can
    do it does: `partial_source` distinguishes the two kinds without parsing,
    and the payload labels itself at head AND tail. This test asserts today's
    real behaviour so the day foundation makes the string kind-aware, this
    check fails loudly instead of drifting.
    """

    async def _thinks_then_hangs(**kwargs):
        child_hooks = ChildHooks()
        _open_partial(kwargs["sub_session_id"], child_hooks)
        await child_hooks.block({"type": "thinking", "text": "maybe the loader"})
        await asyncio.sleep(3600)

    result = await _run_delegate(_thinks_then_hangs)

    # Current, shipped consumer behaviour -- text-shaped guidance.
    assert result.output["guidance"] == _PARTIAL_GUIDANCE
    assert result.output["guidance"] != _NO_PARTIAL_GUIDANCE

    # The producer-side honesty that compensates for it, until it changes.
    assert result.output["partial_source"] == "spawn-accumulator:reasoning"
    body = result.output["partial_response"]
    assert body.lstrip().startswith("[RECOVERED FROM AN UNFINISHED DELEGATE")
    assert body.rstrip().endswith("not a partial answer]")


@pytest.mark.asyncio
async def test_a_leg_that_produced_nothing_still_degrades_to_false():
    """The widening must not manufacture a partial out of an empty accumulator."""

    async def _produces_nothing(**kwargs):
        _open_partial(kwargs["sub_session_id"], ChildHooks())
        await asyncio.sleep(3600)

    result = await _run_delegate(_produces_nothing)

    assert result.output["partial_available"] is False
    assert result.output["partial_response"] is None
    assert result.output["partial_source"] == "none"
    assert result.output["guidance"] == _NO_PARTIAL_GUIDANCE
