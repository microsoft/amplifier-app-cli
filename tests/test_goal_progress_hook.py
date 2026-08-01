"""Tests for GoalProgressHook's rendering of orchestrator:goal_progress events.

No existing test pattern covers hook console-rendering in this repo, so this
file uses a lightweight approach: monkeypatch the module's ``console.print``
to capture emitted lines and assert on their content, rather than snapshotting
Rich's rendered output.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.goal_progress_hook import GoalProgressHook


@pytest.fixture
def hook(monkeypatch):
    """A GoalProgressHook with console.print captured into a list."""
    printed: list[str] = []
    monkeypatch.setattr(
        "amplifier_app_cli.goal_progress_hook.console.print",
        lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)),
    )
    h = GoalProgressHook()
    h._printed = printed  # type: ignore[attr-defined]
    return h


def _joined(hook) -> str:
    return "\n".join(hook._printed)  # type: ignore[attr-defined]


class TestContinuingState:
    @pytest.mark.asyncio
    async def test_continuing_line_unchanged_shape(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "continuing", "turn": 2, "cap": 20, "reason": "not done yet"},
        )
        out = _joined(hook)
        assert "turn 2/20" in out
        assert "not done yet" in out

    @pytest.mark.asyncio
    async def test_continuing_with_no_cap(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "continuing", "turn": 5, "cap": None, "reason": "keep going"},
        )
        out = _joined(hook)
        assert "turn 5" in out
        assert "/" not in out.split("turn 5")[1].split("\u2014")[0]


class TestAchievedState:
    @pytest.mark.asyncio
    async def test_achieved_renders_success_block_with_continuations_and_summary(
        self, hook
    ):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "achieved",
                "turn": 4,
                "cap": 20,
                "continuations": 3,
                "reason": "all tests pass",
                "reasons": ["r1", "r2", "all tests pass"],
                "summary": "Implemented the feature and verified tests.",
                "stall_detail": None,
                "metadata": None,
            },
        )
        out = _joined(hook)
        assert "GOAL ACHIEVED" in out
        assert "3 continuation" in out
        assert "all tests pass" in out
        assert "Implemented the feature and verified tests." in out

    @pytest.mark.asyncio
    async def test_achieved_without_summary_still_renders_facts(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "achieved",
                "turn": 1,
                "cap": None,
                "continuations": 0,
                "reason": "done",
                "reasons": ["done"],
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "GOAL ACHIEVED" in out
        assert "done" in out
        assert "no summary available" in out


class TestCapHitState:
    @pytest.mark.asyncio
    async def test_cap_hit_reads_as_not_successful(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "turn": 20,
                "cap": 20,
                "continuations": 20,
                "reason": "still working",
                "reasons": ["still working"],
                "summary": "Ran out of turns before finishing.",
            },
        )
        out = _joined(hook)
        assert "GOAL STOPPED" in out
        assert "NOT confirmed complete" in out
        assert "achieved" not in out.lower()
        assert "20 continuation" in out


class TestStalledState:
    @pytest.mark.asyncio
    async def test_stalled_reads_as_failure_with_detail_and_history(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "turn": 5,
                "cap": 20,
                "continuations": 4,
                "reason": "still blocked",
                "reasons": ["blocked A", "blocked A", "still blocked"],
                "stall_detail": "repeated the same blocked claim with no tool calls",
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "GOAL FAILED" in out
        assert "stalled" in out.lower()
        assert "repeated the same blocked claim with no tool calls" in out
        assert "reason history" in out
        assert "blocked A" in out
        assert "GOAL ACHIEVED" not in out


class TestCancelledAndErrorStates:
    @pytest.mark.asyncio
    async def test_cancelled_renders_block(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cancelled",
                "turn": 2,
                "cap": 20,
                "continuations": 1,
                "reason": None,
                "reasons": [],
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "GOAL CANCELLED" in out

    @pytest.mark.asyncio
    async def test_error_renders_block(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "error",
                "turn": 2,
                "cap": 20,
                "continuations": 1,
                "reason": "evaluator crashed",
                "reasons": ["evaluator crashed"],
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "GOAL FAILED" in out
        assert "evaluator crashed" in out


class TestDefensiveDefaults:
    @pytest.mark.asyncio
    async def test_missing_new_fields_does_not_raise(self, hook):
        """An older orchestrator emitting only the original fields must
        still render something sane (no KeyError/AttributeError)."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "achieved", "turn": 3, "cap": 10, "reason": "done"},
        )
        out = _joined(hook)
        assert "GOAL ACHIEVED" in out
        assert "unknown number of continuations" in out

    @pytest.mark.asyncio
    async def test_unrecognized_state_falls_back_to_raw_payload(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress", {"state": "mystery", "foo": "bar"}
        )
        out = _joined(hook)
        assert "goal progress" in out
        assert "mystery" in out
