"""Real spawner config assembly with only session execution replaced.

The subprocess runner is an execution boundary shared with the in-process
constructor: merge and caller overrides happen before either dispatch.
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.session_spawner import spawn_sub_session


@pytest.fixture
def spawn_probe(monkeypatch):
    parent = MagicMock()
    parent.session_id = "parent-isolation"
    parent.config = {
        "session": {"orchestrator": {"config": {"extended_thinking": True}}},
        "tools": [{"module": "tool-filesystem"}, {"module": "tool-delegate"}],
        "agents": {"worker": {"tools": [{"module": "tool-filesystem"}]}},
    }
    parent.coordinator.config = parent.config
    parent.coordinator.get_capability.return_value = None
    parent.coordinator.get.return_value = None
    configs = {}

    async def run_child(**kwargs):
        # Keep the real object, not a defensive snapshot that could hide aliasing.
        configs[kwargs["session_id"]] = kwargs["config"]
        await asyncio.sleep(0)  # let parallel spawns interleave
        return '{"output": "done", "status": "success"}'

    monkeypatch.setattr(
        "amplifier_foundation.subprocess_runner.run_session_in_subprocess", run_child
    )
    monkeypatch.setattr(
        "amplifier_app_cli.session_spawner._extract_bundle_context", lambda _: None
    )

    async def spawn(sid, overrides=None, agent="worker"):
        return await spawn_sub_session(
            agent_name=agent,
            instruction="Probe config assembly",
            parent_session=parent,
            agent_configs=parent.config["agents"],
            sub_session_id=sid,
            use_subprocess=True,
            orchestrator_config=overrides,
            session_metadata={"agent": sid},
            tool_inheritance={"exclude_tools": ["tool-delegate"]},
        )

    return parent, configs, spawn


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["worker", "self"])
async def test_child_override_does_not_limit_next_sibling(spawn_probe, agent):
    parent, configs, spawn = spawn_probe
    original = copy.deepcopy(parent.config)
    await spawn("limited", {"max_iterations": 2}, agent)
    await spawn("uncapped", agent=agent)

    assert configs["limited"]["session"]["orchestrator"]["config"]["max_iterations"] == 2
    assert "max_iterations" not in configs["uncapped"]["session"]["orchestrator"]["config"]
    assert configs["limited"]["session"]["metadata"] == {"agent": "limited"}
    assert configs["uncapped"]["session"]["metadata"] == {"agent": "uncapped"}
    assert parent.config == original
    assert [t["module"] for t in configs["uncapped"]["tools"]] == ["tool-filesystem"]


@pytest.mark.asyncio
async def test_parallel_children_keep_distinct_budgets_and_metadata(spawn_probe):
    parent, configs, spawn = spawn_probe
    original = copy.deepcopy(parent.config)
    await asyncio.gather(
        spawn("two", {"max_iterations": 2}),
        spawn("seven", {"max_iterations": 7}),
        spawn("uncapped"),
    )
    assert configs["two"]["session"]["orchestrator"]["config"]["max_iterations"] == 2
    assert configs["seven"]["session"]["orchestrator"]["config"]["max_iterations"] == 7
    assert "max_iterations" not in configs["uncapped"]["session"]["orchestrator"]["config"]
    for sid, config in configs.items():
        assert config["session"]["metadata"] == {"agent": sid}
    assert parent.config == original


@pytest.mark.asyncio
async def test_explicit_override_does_not_replace_parent_config(spawn_probe):
    parent, configs, spawn = spawn_probe
    parent.config["session"]["orchestrator"]["config"]["max_iterations"] = 11
    original = copy.deepcopy(parent.config)
    await spawn("override", {"max_iterations": 7})
    await spawn("inherited")
    assert configs["override"]["session"]["orchestrator"]["config"]["max_iterations"] == 7
    assert configs["inherited"]["session"]["orchestrator"]["config"]["max_iterations"] == 11
    assert parent.config == original