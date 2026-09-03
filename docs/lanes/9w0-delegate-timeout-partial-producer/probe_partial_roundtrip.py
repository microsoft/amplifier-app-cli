"""Behavioural probe for the partial-result path -- prints, does not assert.

Two modes, both run against whatever `amplifier_app_cli` is on PYTHONPATH, so the
same script produces comparable output for the parent commit and the patched tree:

    normal   -- spawn a sub-session that COMPLETES; print the returned dict as
                canonical JSON. Byte-identity between parent and patched is the
                "normal completions unchanged" deliverable, shown rather than
                asserted.
    timeout  -- drive the REAL foundation consumer (tool-delegate f42f48c) over
                the REAL app producer; print the delegate's model-visible output.
                `partial_available` is k64 gate G-D4's deciding fact.

Usage:
    PYTHONPATH=<tool-delegate-copy>:<app-cli-copy> python probe_partial_roundtrip.py normal
    PYTHONPATH=<tool-delegate-copy>:<app-cli-copy> python probe_partial_roundtrip.py timeout
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from amplifier_core.events import CONTENT_BLOCK_END

from amplifier_app_cli.session_runner import register_session_spawning
from amplifier_app_cli.session_spawner import spawn_sub_session

FIXED_SUB_SESSION_ID = "parent-session-0000000000000000_explorer"
PARTIAL_CHUNKS = ["anchor A1 confirmed. ", "anchor A2 confirmed. "]


class FakeHooks:
    def __init__(self):
        self.handlers: dict[str, list] = {}

    def register(self, event, handler, priority=0, name=None):
        self.handlers.setdefault(event, []).append(handler)
        return lambda: self.handlers[event].remove(handler)

    async def emit(self, event, data):
        for handler in list(self.handlers.get(event, [])):
            await handler(event, data)

    async def text(self, chunk):
        await self.emit(CONTENT_BLOCK_END, {"block": {"type": "text", "text": chunk}})


def parent_session():
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


def child_session(hooks, execute_body):
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()
    coordinator.get_capability.return_value = None
    coordinator.display_system = MagicMock()
    coordinator.mount = AsyncMock()
    coordinator.collect_contributions = AsyncMock(return_value=[])

    def _get(name):
        if name == "hooks":
            return hooks
        if name == "context":
            ctx = AsyncMock()
            ctx.get_messages = AsyncMock(return_value=[])
            ctx.add_message = AsyncMock()
            return ctx
        return None

    coordinator.get = _get
    child = MagicMock()
    child.coordinator = coordinator
    child.initialize = AsyncMock()
    child.execute = AsyncMock(side_effect=execute_body)
    child.cleanup = AsyncMock()
    child.session_id = "child-session"
    return child


def patched_env(child):
    return (
        patch(
            "amplifier_app_cli.session_spawner.AmplifierSession", return_value=child
        ),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value=FIXED_SUB_SESSION_ID,
        ),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore.save"),
    )


async def run_normal():
    hooks = FakeHooks()

    async def _completes(instruction):
        await hooks.text("the finished answer")
        await hooks.emit(
            "orchestrator:complete",
            {"status": "success", "turn_count": 5, "metadata": {"o": "loop-basic"}},
        )
        return "agent response"

    a, b, c, d = patched_env(child_session(hooks, _completes))
    with a, b, c, d:
        result = await spawn_sub_session(
            agent_name="explorer",
            instruction="do the thing",
            parent_session=parent_session(),
            agent_configs={"explorer": {"description": "an agent"}},
        )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


class FakeCoordinator:
    def __init__(self, capabilities, parent):
        self.session_id = parent.session_id
        self.session = parent
        self.config = {"agents": {"explorer": {}}}
        self._capabilities = capabilities

    def get_capability(self, name):
        return self._capabilities.get(name)


async def run_timeout():
    from amplifier_module_tool_delegate import DelegateTool

    registered: dict = {}
    session = MagicMock()
    session.coordinator.register_capability = MagicMock(
        side_effect=lambda name, fn: registered.__setitem__(name, fn)
    )
    register_session_spawning(session)
    print(
        "app-registered capabilities: "
        + ", ".join(sorted(registered))
        + "\nsession.partial registered: "
        + str("session.partial" in registered)
        + "\n"
    )

    hooks = FakeHooks()

    async def _never_finishes(instruction):
        for chunk in PARTIAL_CHUNKS:
            await hooks.text(chunk)
        await asyncio.sleep(3600)

    tool = DelegateTool(
        FakeCoordinator(registered, parent_session()), {"settings": {"timeout": 1}}
    )
    a, b, c, d = patched_env(child_session(hooks, _never_finishes))
    with a, b, c, d:
        result = await tool._spawn_new_session(
            agent_name="explorer",
            instruction="find the anchors",
            context_depth="none",
            context_scope="conversation",
            context_turns=0,
            provider_preferences=None,
            hooks=FakeHooks(),
            agents={"explorer": {}},
        )
    print("ToolResult.success = " + str(result.success))
    print(json.dumps(result.output, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    asyncio.run(run_normal() if mode == "normal" else run_timeout())
