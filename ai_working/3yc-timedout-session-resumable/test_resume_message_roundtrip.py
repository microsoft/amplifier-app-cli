"""Cross-repo contract check: does the "NOT resumable" wording reach the MODEL?

WHY THIS FILE LIVES HERE AND NOT IN tests/

It depends on a tool-delegate patch that has NOT landed
(`PATCH-foundation-surface-resume-detail.diff`). Putting it in the app-cli
suite would make CI fail against unpatched foundation, so it ships beside the
patch and is run manually -- the same convention the w3/37n lane used for
`test_partial_roundtrip.py`.

WHAT IT PROVES

The acceptance's second branch says the result must state "explicitly that it
is not resumable and direct the caller to re-delegate". amplifier-app-cli's
`resume_sub_session` now raises exactly that sentence -- but tool-delegate's
`except FileNotFoundError` handler built a HARDCODED message and discarded
`str(e)`, so the wording never reached the ToolResult the model actually reads.

This test wires the REAL app-side `resume_sub_session` into the REAL
tool-delegate resume path and asserts the wording survives all the way into
`ToolResult`. It is a genuine round trip, not two halves asserted separately.

HOW TO RUN

    # PATCHED (expected: 2 passed)
    cp -rL ~/dev/amplifier-foundation/modules/tool-delegate /tmp/td-patched
    git -C ~/dev/amplifier-foundation apply --check \
        PATCH-foundation-surface-resume-detail.diff        # exit 0
    # ...apply the diff inside /tmp/td-patched, then:
    cd <amplifier-app-cli>
    PYTHONPATH=/tmp/td-patched uv run pytest \
        ai_working/3yc-timedout-session-resumable/test_resume_message_roundtrip.py \
        -q -p no:randomly --asyncio-mode=auto

    # UNPATCHED (expected: test_wording_reaches_the_model FAILS)
    PYTHONPATH=~/dev/amplifier-foundation/modules/tool-delegate uv run pytest ... same file

That asymmetry IS the result. It is what makes the foundation diff necessary
rather than cosmetic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool

from amplifier_app_cli.session_spawner import resume_sub_session

pytestmark = pytest.mark.asyncio

MISSING_SESSION_ID = "parent0000000000-child00000000000_test-agent"


def _make_delegate_tool(resume_fn) -> DelegateTool:
    """Minimal DelegateTool wired for the resume path (mirrors the module's own tests)."""
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {"test-agent": {"description": "t"}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    capabilities = {
        "session.spawn": AsyncMock(return_value={"output": "done"}),
        "session.resume": resume_fn,
        "agents.list": lambda: coordinator.config["agents"],
        "agents.get": lambda name: coordinator.config["agents"].get(name),
        "self_delegation_depth": 0,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    return DelegateTool(coordinator, {"features": {}, "settings": {"exclude_tools": []}})


async def test_app_side_really_raises_the_wording(tmp_path, monkeypatch):
    """Guard: the app half genuinely produces the sentence (no patch needed)."""
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(FileNotFoundError) as excinfo:
        await resume_sub_session(MISSING_SESSION_ID, "carry on")

    message = str(excinfo.value).lower()
    assert "not resumable" in message
    assert "re-delegate" in message


async def test_wording_reaches_the_model(tmp_path, monkeypatch):
    """The round trip: app-side wording must survive into the ToolResult.

    FAILS against unpatched tool-delegate -- the handler replaces the detail
    with "Agent session '<id>' not found. May have expired or never existed.",
    which leaves "retry the resume" a plausible reading for the model.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    tool = _make_delegate_tool(resume_fn=resume_sub_session)

    result = await tool.execute(
        {"session_id": MISSING_SESSION_ID, "instruction": "carry on"}
    )

    assert result.success is False

    # Read BOTH channels the model can see: ToolResult.output is derived from
    # error["message"] by ToolResult.model_post_init, so a regression in either
    # is caught here.
    serialized = f"{result.error} {result.output}".lower()

    assert "not resumable" in serialized, (
        "the acceptance's second branch requires the MODEL-visible result to "
        "state explicitly that the session is not resumable; got: "
        f"{result.error!r}"
    )
    assert "re-delegate" in serialized, (
        "the acceptance's second branch requires the MODEL-visible result to "
        f"direct the caller to re-delegate; got: {result.error!r}"
    )
