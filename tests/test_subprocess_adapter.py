"""Tests for cancellation-safe Foundation subprocess dispatch."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_app_cli.runtime import subprocess_adapter


def test_foundation_subprocess_contract_is_available():
    foundation = subprocess_adapter._foundation()

    for name in subprocess_adapter._REQUIRED_API:
        assert hasattr(foundation, name)


@pytest.mark.asyncio
async def test_cancelled_runner_terminates_and_reaps_real_child(tmp_path, monkeypatch):
    original_create = asyncio.create_subprocess_exec
    created = asyncio.Event()
    processes = []

    async def create_sleeping_child(*args, **kwargs):
        process = await original_create(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        created.set()
        return process

    monkeypatch.setattr(
        subprocess_adapter.asyncio,
        "create_subprocess_exec",
        create_sleeping_child,
    )
    task = asyncio.create_task(
        subprocess_adapter.run_session_in_subprocess(
            config={"session": {}},
            prompt="cancel",
            parent_id="parent",
            project_path=str(tmp_path),
            session_id="child",
        )
    )
    await asyncio.wait_for(created.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)

    assert len(processes) == 1
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_cancellation_during_spawn_still_reaps_child(tmp_path, monkeypatch):
    original_create = asyncio.create_subprocess_exec
    spawn_started = asyncio.Event()
    allow_spawn = asyncio.Event()
    processes = []

    async def delayed_create(*args, **kwargs):
        spawn_started.set()
        await allow_spawn.wait()
        process = await original_create(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(
        subprocess_adapter.asyncio,
        "create_subprocess_exec",
        delayed_create,
    )
    task = asyncio.create_task(
        subprocess_adapter.run_session_in_subprocess(
            config={"session": {}},
            prompt="cancel while spawning",
            parent_id="parent",
            project_path=str(tmp_path),
        )
    )
    await asyncio.wait_for(spawn_started.wait(), timeout=2)

    task.cancel()
    allow_spawn.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)

    assert len(processes) == 1
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_runner_preserves_foundation_result_framing(tmp_path, monkeypatch):
    original_create = asyncio.create_subprocess_exec
    foundation = subprocess_adapter._foundation()
    payload = '{"output":"done","status":"success"}'
    script = (
        f"print({foundation.RESULT_START_MARKER!r}); "
        f"print({payload!r}); print({foundation.RESULT_END_MARKER!r})"
    )

    serialized_policy = {}

    async def create_framed_child(*args, **kwargs):
        config_path = Path(args[3])
        serialized_policy.update(
            json.loads(config_path.read_text())["_amplifier_app_cli"]
        )
        return await original_create(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(
        subprocess_adapter.asyncio,
        "create_subprocess_exec",
        create_framed_child,
    )

    result = await subprocess_adapter.run_session_in_subprocess(
        config={"session": {}},
        prompt="complete",
        parent_id="parent",
        project_path=str(tmp_path),
        session_id="child",
        bypass_permissions=True,
    )

    assert result == payload
    assert serialized_policy == {"bypass_permissions": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("saved_bypass", "expected_bypass"),
    ((None, False), (True, True)),
)
async def test_child_bootstrap_uses_safe_default_and_explicit_bypass(
    tmp_path, saved_bypass, expected_bypass
):
    emitted = []
    created = []

    class Hooks:
        async def emit(self, event, data):
            json.dumps(data)
            emitted.append((event, data))

    class Orchestrator:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            await hooks.emit("provider:response", {"cost": "0.42"})
            return "done"

    class Coordinator:
        def __init__(self, approval_system):
            self.approval_system = approval_system
            self._orchestrator = Orchestrator()

        def get(self, name):
            return self._orchestrator if name == "orchestrator" else None

    class Session:
        def __init__(self, **kwargs):
            self.coordinator = Coordinator(kwargs["approval_system"])
            created.append(self)

        async def initialize(self):
            return None

    foundation = SimpleNamespace(AmplifierSession=Session)

    async def child_runner(_config_path):
        session = foundation.AmplifierSession()
        await session.initialize()
        approval_system = session.coordinator.approval_system
        assert approval_system.bypass_permissions is expected_bypass
        if not expected_bypass:

            async def deny(_prompt, _options, _timeout, _default):
                return "Deny"

            approval_system.bind_handler(deny)
        choice = await session.coordinator.approval_system.request_approval(
            "Allow command?", ["Allow once", "Deny"], 1, "deny"
        )
        assert choice == ("Allow once" if expected_bypass else "Deny")
        return await session.coordinator.get("orchestrator").execute(
            "prompt", None, {}, {}, Hooks(), session.coordinator
        )

    foundation._run_child_session = child_runner
    original = Session
    config_path = tmp_path / "child.json"
    payload = {}
    if saved_bypass is not None:
        payload["_amplifier_app_cli"] = {"bypass_permissions": saved_bypass}
    config_path.write_text(json.dumps(payload))

    result = await subprocess_adapter._run_patched_foundation_child(
        str(config_path), foundation
    )

    assert result == "done"
    assert emitted == [("provider:response", {"cost": "0.42"})]
    assert foundation.AmplifierSession is original
    assert len(created) == 1
