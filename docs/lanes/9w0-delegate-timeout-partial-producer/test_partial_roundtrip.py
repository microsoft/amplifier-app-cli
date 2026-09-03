"""Cross-repo contract check for the partial-result path.

amplifier-foundation `tool-delegate` is the CONSUMER of the partial;
amplifier-app-cli `session_spawner` is the PRODUCER. Each half is unit-tested in
its own repo against a fake counterpart; this check wires the two REAL halves
together so the contract cannot drift silently between them.

Unlike 37n's original draft, this version does not import the producer's private
`_seal_partial` and hand the id over by side channel. It drives the REAL app-layer
`session.spawn` capability -- exactly the one `register_session_spawning()` puts on
a root session -- through the REAL `DelegateTool`, and lets the delegate mint the
`sub_session_id` itself. That is what makes the check meaningful: if the two sides
disagree about the key, the signature, or the payload shape, this fails.

It therefore runs unchanged against BOTH the parent commit (no producer:
`partial_available: false` -- k64's G-D4 stop condition) and the patched tree
(`partial_available: true`).

Run per APPLY.md, against overlaid COPIES of both packages, never the repos:

    PYTHONPATH=<tool-delegate-copy>:<app-cli-copy> python -m pytest \
        test_partial_roundtrip.py -q
"""

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from amplifier_core.events import CONTENT_BLOCK_END
from amplifier_module_tool_delegate import DelegateTool

from amplifier_app_cli.session_runner import register_session_spawning

pytestmark = pytest.mark.anyio

PARTIAL_CHUNKS = ["anchor A1 confirmed. ", "anchor A2 confirmed. "]


class FakeHooks:
    def __init__(self):
        self.handlers: dict[str, list] = {}
        self.events: list = []

    def register(self, event, handler, priority=0, name=None):
        self.handlers.setdefault(event, []).append(handler)
        return lambda: self.handlers[event].remove(handler)

    async def emit(self, event, data):
        self.events.append((event, data))
        for handler in list(self.handlers.get(event, [])):
            await handler(event, data)


def _app_capabilities():
    """Exactly what the app registers on a real root session."""
    registered: dict = {}
    session = MagicMock()
    session.coordinator.register_capability = MagicMock(
        side_effect=lambda name, fn: registered.__setitem__(name, fn)
    )
    register_session_spawning(session)
    return registered


def _parent_session():
    parent = MagicMock()
    parent.coordinator.get.return_value = None
    parent.coordinator.get_capability.return_value = None
    parent.coordinator.display_system = MagicMock()
    parent.coordinator.cancellation = MagicMock()
    parent.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"}
    }
    parent.session_id = "parent-session"
    parent.trace_id = "trace-abc"
    parent.loader = None
    return parent


def _straggler_child(child_hooks):
    """A child session that produces two text blocks and then never finishes."""
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()
    coordinator.get_capability.return_value = None
    coordinator.display_system = MagicMock()
    coordinator.mount = AsyncMock()
    coordinator.collect_contributions = AsyncMock(return_value=[])

    def _get(name):
        if name == "hooks":
            return child_hooks
        if name == "context":
            ctx = AsyncMock()
            ctx.get_messages = AsyncMock(return_value=[])
            ctx.add_message = AsyncMock()
            return ctx
        return None

    coordinator.get = _get

    async def _never_finishes(instruction):
        for chunk in PARTIAL_CHUNKS:
            await child_hooks.emit(
                CONTENT_BLOCK_END, {"block": {"type": "text", "text": chunk}}
            )
        await asyncio.sleep(3600)

    child = MagicMock()
    child.coordinator = coordinator
    child.initialize = AsyncMock()
    child.execute = AsyncMock(side_effect=_never_finishes)
    child.cleanup = AsyncMock()
    child.session_id = "child-session"
    return child


class FakeCoordinator:
    def __init__(self, capabilities, parent_session):
        self.session_id = parent_session.session_id
        self.session = parent_session
        self.config = {"agents": {"explorer": {}}}
        self._capabilities = capabilities

    def get_capability(self, name):
        return self._capabilities.get(name)


async def _run_timeout_leg():
    parent = _parent_session()
    caps = _app_capabilities()
    tool = DelegateTool(
        FakeCoordinator(caps, parent), {"settings": {"timeout": 1}}
    )

    child_hooks = FakeHooks()
    with patch(
        "amplifier_app_cli.session_spawner.AmplifierSession",
        return_value=_straggler_child(child_hooks),
    ):
        with patch("amplifier_app_cli.paths.create_foundation_resolver"):
            with patch("amplifier_app_cli.session_store.SessionStore.save"):
                return (
                    await tool._spawn_new_session(
                        agent_name="explorer",
                        instruction="find the anchors",
                        context_depth="none",
                        context_scope="conversation",
                        context_turns=0,
                        provider_preferences=None,
                        hooks=FakeHooks(),
                        agents={"explorer": {}},
                    ),
                    caps,
                )


async def test_producer_and_consumer_agree_on_the_partial_contract():
    result, caps = await _run_timeout_leg()

    # The timeout invariant the consumer half pins: never success, on either channel.
    assert result.success is False
    assert result.output["status"] == "timeout"
    assert "response" not in result.output

    # The producer half: the straggler's own work survived and was handed over.
    assert result.output["partial_available"] is True, (
        "session.partial produced nothing -- k64's G-D4 would record "
        "PARTIAL-PATH-NOT-EXERCISED here"
    )
    assert result.output["partial_response"] == "".join(PARTIAL_CHUNKS)
    assert result.output["partial_segments"] == len(PARTIAL_CHUNKS)
    assert result.output["partial_source"] == "spawn-accumulator"
    assert result.output["partial_truncated"] is False
    assert result.output["partial_chars_total"] == len("".join(PARTIAL_CHUNKS))

    # Reads are destructive: the registry does not leak across delegate calls.
    sub_session_id = result.output["session_id"]
    assert caps["session.partial"](sub_session_id) is None


async def test_a_second_timeout_leg_does_not_inherit_the_first_partial():
    """Two legs, two ids -- the second must carry only its own work."""
    first, _ = await _run_timeout_leg()
    second, _ = await _run_timeout_leg()

    assert first.output["session_id"] != second.output["session_id"]
    assert second.output["partial_response"] == "".join(PARTIAL_CHUNKS)
    assert second.output["partial_segments"] == len(PARTIAL_CHUNKS)
