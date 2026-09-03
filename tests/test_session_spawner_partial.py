"""Tests for the partial-output producer path (`session.partial`).

PRODUCER half of a cross-repo contract. The CONSUMER shipped in
amplifier-foundation `f42f48c` (tool-delegate): on a per-delegate wall-clock
timeout it returns rather than raises, and calls an optional app-layer
``session.partial`` capability::

    (sub_session_id: str) -> {"text": str, "segments": int, "source": str} | None

Absent / None / malformed / raising -> ``partial_available: false``. Everything
here exists so that boolean can be TRUE for a real timeout: without a producer,
a cancelled sub-session's work is discarded and every timeout reports
``partial_available: false``.

The two properties under test are opposites and both matter:

* a CANCELLED sub-session's assistant text survives and is readable;
* a NORMALLY COMPLETED sub-session is untouched -- the result dict gains no
  key, and nothing is left in the registry.
"""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from amplifier_core.events import CONTENT_BLOCK_END

from amplifier_app_cli import session_spawner
from amplifier_app_cli.session_spawner import _seal_partial
from amplifier_app_cli.session_spawner import get_partial_output
from amplifier_app_cli.session_spawner import spawn_sub_session

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class FakeHooks:
    """Hook registry keyed by event, with multiple handlers per event.

    The spawner registers three handlers (orchestrator:complete, the
    provider:request transcript checkpoint, and the content_block:end partial
    accumulator), so a single-slot fake would let the last writer win.
    """

    def __init__(self):
        self.handlers: dict[str, list] = {}

    def register(self, event, handler, priority=0, name=None):
        self.handlers.setdefault(event, []).append(handler)

        def _unregister():
            if handler in self.handlers.get(event, []):
                self.handlers[event].remove(handler)

        return _unregister

    async def emit(self, event, data):
        for handler in list(self.handlers.get(event, [])):
            await handler(event, data)

    async def fire_text_block(self, text: str):
        await self.emit(CONTENT_BLOCK_END, {"block": {"type": "text", "text": text}})


def _parent_session():
    parent_coordinator = MagicMock()
    parent_coordinator.get.return_value = None
    parent_coordinator.get_capability.return_value = None
    parent_coordinator.display_system = MagicMock()
    parent_coordinator.cancellation = MagicMock()
    parent_coordinator.cancellation.register_child = MagicMock()
    parent_coordinator.cancellation.unregister_child = MagicMock()

    parent_session = MagicMock()
    parent_session.coordinator = parent_coordinator
    parent_session.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"},
    }
    parent_session.session_id = "parent-123"
    parent_session.trace_id = "trace-abc"
    parent_session.loader = None
    return parent_session


def _child_session(hooks, execute_body):
    """A child session whose execute() runs ``execute_body(hooks)``."""
    child_coordinator = MagicMock()
    child_coordinator.registered_capabilities = {}

    def _register_capability(name, fn):
        child_coordinator.registered_capabilities[name] = fn

    child_coordinator.register_capability = MagicMock(side_effect=_register_capability)
    child_coordinator.get_capability.return_value = None
    child_coordinator.display_system = MagicMock()

    def child_get(name):
        if name == "hooks":
            return hooks
        if name == "context":
            ctx = AsyncMock()
            ctx.get_messages = AsyncMock(return_value=[])
            ctx.add_message = AsyncMock()
            return ctx
        return None

    child_coordinator.get = child_get
    child_coordinator.mount = AsyncMock()
    child_coordinator.collect_contributions = AsyncMock(return_value=[])

    child_session = MagicMock()
    child_session.coordinator = child_coordinator
    child_session.initialize = AsyncMock()
    child_session.execute = AsyncMock(side_effect=execute_body)
    child_session.cleanup = AsyncMock()
    child_session.session_id = "child-001"
    return child_session


async def _spawn(child_session, sub_session_id="child-001"):
    with patch(
        "amplifier_app_cli.session_spawner.AmplifierSession",
        return_value=child_session,
    ):
        with patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value=sub_session_id,
        ):
            with patch("amplifier_app_cli.paths.create_foundation_resolver"):
                with patch("amplifier_app_cli.session_store.SessionStore.save"):
                    return await spawn_sub_session(
                        agent_name="test-agent",
                        instruction="Do something",
                        parent_session=_parent_session(),
                        agent_configs={"test-agent": {"description": "A test agent"}},
                    )


@pytest.fixture(autouse=True)
def _clean_registry():
    session_spawner._PARTIAL_OUTPUTS.clear()
    yield
    session_spawner._PARTIAL_OUTPUTS.clear()


# ---------------------------------------------------------------------------
# The property this whole item exists for
# ---------------------------------------------------------------------------


async def test_cancelled_spawn_preserves_partial_text():
    """FAIL-BEFORE: on the parent commit the cancelled agent's text is discarded.

    This is the single fact k64's gate G-D4 turns on: without it every timeout
    carries ``partial_available: false``.
    """
    hooks = FakeHooks()

    async def _cancelled_midway(instruction):
        await hooks.fire_text_block("anchor A1 confirmed. ")
        await hooks.fire_text_block("anchor A2 confirmed. ")
        raise TimeoutError("wall clock")

    with pytest.raises(TimeoutError):
        await _spawn(_child_session(hooks, _cancelled_midway))

    partial = get_partial_output("child-001")
    assert partial is not None, "the cancelled sub-session's work was discarded"
    assert partial["text"] == "anchor A1 confirmed. anchor A2 confirmed. "
    assert partial["segments"] == 2
    assert partial["source"] == "spawn-accumulator"


async def test_asyncio_cancellation_also_preserves_partial_text():
    """The real timeout path raises CancelledError, a BaseException."""
    import asyncio

    hooks = FakeHooks()

    async def _hard_cancelled(instruction):
        await hooks.fire_text_block("half a finding")
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _spawn(_child_session(hooks, _hard_cancelled))

    partial = get_partial_output("child-001")
    assert partial is not None
    assert partial["text"] == "half a finding"


async def test_partial_is_readable_before_the_child_has_unwound():
    """The ordering property the cross-repo round trip caught.

    tool-delegate's `_await_child_with_deadline` cancels the child, DETACHES it,
    and reads `session.partial` immediately -- it deliberately does not wait for
    a slow unwind. So the partial must be readable at the instant of cancel,
    before the child's own `except` handler has been scheduled. Publishing only
    from that handler passes both halves' unit tests and still reports
    `partial_available: false` in production.
    """
    import asyncio

    hooks = FakeHooks()
    started = asyncio.Event()

    async def _produces_then_hangs(instruction):
        await hooks.fire_text_block("work in progress")
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_spawn(_child_session(hooks, _produces_then_hangs)))
    await started.wait()
    task.cancel()  # the child has NOT yet run any exception handler

    partial = get_partial_output("child-001")
    assert partial is not None, "unreadable until the child unwinds -- too late"
    assert partial["text"] == "work in progress"

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_partial_capability_is_registered_on_the_child_session():
    """A grandchild that times out under this child must be recoverable too."""
    hooks = FakeHooks()

    async def _ok(instruction):
        return "done"

    child = _child_session(hooks, _ok)
    await _spawn(child)

    registered = child.coordinator.registered_capabilities
    assert "session.partial" in registered
    assert registered["session.partial"] is get_partial_output


# ---------------------------------------------------------------------------
# The inverse guard: normal completion is untouched
# ---------------------------------------------------------------------------


async def test_normal_completion_result_gains_no_key():
    """A sub-session that finishes normally returns exactly today's shape."""
    hooks = FakeHooks()

    async def _completes(instruction):
        await hooks.fire_text_block("the finished answer")
        await hooks.emit(
            "orchestrator:complete",
            {"status": "success", "turn_count": 5, "metadata": {"o": "loop-basic"}},
        )
        return "agent response"

    result = await _spawn(_child_session(hooks, _completes))

    assert set(result) == {"output", "session_id", "status", "turn_count", "metadata"}
    assert result["output"] == "agent response"
    assert result["session_id"] == "child-001"
    assert result["status"] == "success"
    assert result["turn_count"] == 5


async def test_normal_completion_leaves_nothing_in_the_registry():
    """No seal on the success path -- the registry does not accumulate."""
    hooks = FakeHooks()

    async def _completes(instruction):
        await hooks.fire_text_block("the finished answer")
        return "agent response"

    await _spawn(_child_session(hooks, _completes))

    assert session_spawner._PARTIAL_OUTPUTS == {}
    assert get_partial_output("child-001") is None


# ---------------------------------------------------------------------------
# Registry mechanics the consumer relies on
# ---------------------------------------------------------------------------


async def test_reads_are_destructive():
    _seal_partial("s1", {"chunks": ["a", "b"]})
    assert get_partial_output("s1")["text"] == "ab"
    assert get_partial_output("s1") is None


async def test_unknown_session_returns_none():
    assert get_partial_output("never-existed") is None


async def test_empty_accumulator_is_not_sealed():
    """Nothing produced -> nothing to offer; the consumer degrades to false."""
    _seal_partial("s2", {"chunks": []})
    assert get_partial_output("s2") is None


async def test_registry_is_capped():
    """A long-lived root cannot leak unbounded partial records."""
    for i in range(session_spawner._PARTIAL_MAX_SESSIONS + 10):
        _seal_partial(f"s{i}", {"chunks": ["x"]})
    assert (
        len(session_spawner._PARTIAL_OUTPUTS) <= session_spawner._PARTIAL_MAX_SESSIONS
    )


async def test_no_hooks_coordinator_does_not_break_spawn():
    """A session with no hooks registry still spawns; it just offers no partial."""

    async def _completes(instruction):
        return "agent response"

    child = _child_session(None, _completes)
    child.coordinator.get = lambda name: (
        AsyncMock(get_messages=AsyncMock(return_value=[]), add_message=AsyncMock())
        if name == "context"
        else None
    )
    result = await _spawn(child)
    assert result["output"] == "agent response"


# ---------------------------------------------------------------------------
# Root-session registration
# ---------------------------------------------------------------------------


async def test_root_session_registers_session_partial():
    from amplifier_app_cli.session_runner import register_session_spawning

    session = MagicMock()
    registered: dict = {}
    session.coordinator.register_capability = MagicMock(
        side_effect=lambda name, fn: registered.__setitem__(name, fn)
    )

    register_session_spawning(session)

    assert registered["session.partial"] is get_partial_output
