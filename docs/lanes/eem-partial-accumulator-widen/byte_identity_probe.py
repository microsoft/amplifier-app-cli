"""Serialize what a delegate leg hands back, so parent vs patched can be DIFFED.

The deliverable is "normal completions byte-identical -- show it, do not assert
it". An assertion on a key set proves the keys did not change; it does not
prove the bytes did not. This probe drives `spawn_sub_session` against whichever
`amplifier_app_cli` is first on `sys.path` and dumps, canonically:

  1. NORMAL COMPLETION -- the full result dict a finished delegate returns,
     for a leg that emitted thinking + tool_call + text blocks (i.e. one whose
     blocks the widening now observes).
  2. TIMEOUT, TEXT PRESENT -- the `session.partial` record for a leg that DID
     emit assistant text. This is the case foundation's shipped guidance
     string already describes correctly, so it must not move either.
  3. TIMEOUT, NO TEXT -- the real leg shape (k64: thinking + tool_call, no
     text). Expected to differ: `null` on the parent, a record on the patched
     build. That difference IS the fix.

Run under each build with PYTHONPATH pointed at a `cp -rL` copy; diff the two
JSON files. See evidence/03-byte-identity.txt.
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_core.events import CONTENT_BLOCK_END

from amplifier_app_cli import session_spawner
from amplifier_app_cli.session_spawner import get_partial_output, spawn_sub_session


class FakeHooks:
    def __init__(self):
        self.handlers = {}

    def register(self, event, handler, priority=0, name=None):
        self.handlers.setdefault(event, []).append(handler)

        def _unregister():
            if handler in self.handlers.get(event, []):
                self.handlers[event].remove(handler)

        return _unregister

    async def emit(self, event, data):
        for handler in list(self.handlers.get(event, [])):
            await handler(event, data)

    async def block(self, block):
        await self.emit(CONTENT_BLOCK_END, {"block": block})


def _parent_session():
    coord = MagicMock()
    coord.get.return_value = None
    coord.get_capability.return_value = None
    coord.display_system = MagicMock()
    coord.cancellation = MagicMock()
    session = MagicMock()
    session.coordinator = coord
    session.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"}
    }
    session.session_id = "parent-123"
    session.trace_id = "trace-abc"
    session.loader = None
    return session


def _child_session(hooks, body):
    coord = MagicMock()
    coord.register_capability = MagicMock()
    coord.get_capability.return_value = None
    coord.display_system = MagicMock()

    def _get(name):
        if name == "hooks":
            return hooks
        if name == "context":
            ctx = AsyncMock()
            ctx.get_messages = AsyncMock(return_value=[])
            ctx.add_message = AsyncMock()
            return ctx
        return None

    coord.get = _get
    coord.mount = AsyncMock()
    coord.collect_contributions = AsyncMock(return_value=[])

    child = MagicMock()
    child.coordinator = coord
    child.initialize = AsyncMock()
    child.execute = AsyncMock(side_effect=body)
    child.cleanup = AsyncMock()
    child.session_id = "child-001"
    return child


async def _spawn(child):
    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value="child-001",
        ),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore.save"),
    ):
        return await spawn_sub_session(
            agent_name="test-agent",
            instruction="Do something",
            parent_session=_parent_session(),
            agent_configs={"test-agent": {"description": "A test agent"}},
        )


THINKING = {"type": "thinking", "text": "the router loads a matrix; check it"}
TOOL_CALL = {
    "type": "tool_call",
    "id": "call_1",
    "name": "read_file",
    "input": {"file_path": "/repo/router.py", "limit": 40, "search": ""},
    "visibility": None,
}
TEXT = {"type": "text", "text": "the finished answer"}


async def main():
    out = {}

    # 1. NORMAL COMPLETION -----------------------------------------------
    hooks = FakeHooks()

    async def _completes(instruction):
        await hooks.block(THINKING)
        await hooks.block(TOOL_CALL)
        await hooks.block(TEXT)
        await hooks.emit(
            "orchestrator:complete",
            {"status": "success", "turn_count": 5, "metadata": {"o": "loop-basic"}},
        )
        return "agent response"

    session_spawner._PARTIAL_OUTPUTS.clear()
    out["normal_completion_result"] = await _spawn(_child_session(hooks, _completes))
    out["normal_completion_registry_after"] = sorted(session_spawner._PARTIAL_OUTPUTS)
    out["normal_completion_partial"] = get_partial_output("child-001")

    # 2. TIMEOUT, TEXT PRESENT -------------------------------------------
    hooks = FakeHooks()

    async def _text_then_dies(instruction):
        await hooks.block(THINKING)
        await hooks.block(TOOL_CALL)
        await hooks.block({"type": "text", "text": "anchor A1 confirmed. "})
        await hooks.block({"type": "text", "text": "anchor A2 confirmed. "})
        raise TimeoutError("wall clock")

    session_spawner._PARTIAL_OUTPUTS.clear()
    try:
        await _spawn(_child_session(hooks, _text_then_dies))
    except TimeoutError:
        pass
    out["timeout_with_text_partial"] = get_partial_output("child-001")

    # 3. TIMEOUT, NO TEXT (the real leg shape) ----------------------------
    hooks = FakeHooks()

    async def _no_text(instruction):
        await hooks.block(THINKING)
        await hooks.block(TOOL_CALL)
        raise TimeoutError("wall clock")

    session_spawner._PARTIAL_OUTPUTS.clear()
    try:
        await _spawn(_child_session(hooks, _no_text))
    except TimeoutError:
        pass
    out["timeout_no_text_partial"] = get_partial_output("child-001")

    print(json.dumps(out, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    print(f"# amplifier_app_cli from: {session_spawner.__file__}", file=sys.stderr)
    asyncio.run(main())
