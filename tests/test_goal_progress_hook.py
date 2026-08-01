"""Tests for GoalProgressHook's rendering of orchestrator:goal_progress events.

The hook prints real Rich renderables (Rule, Table, Padding) rather than
hand-assembled strings, so capturing ``str(arg)`` on each ``console.print``
call isn't enough -- a Table's ``__str__`` doesn't include its cell text.
Instead, this fixture points the module's shared ``console`` at a real
``rich.console.Console`` writing to an in-memory buffer, so every renderable
is actually rendered (with word-wrap etc.) exactly as it would be in the
terminal. Tests then assert on the *information* that must be present in
that rendered text (state labels, counts, reasons) -- not on decorative
characters (dividers, colors, exact spacing) -- so they survive future
cosmetic changes.
"""

import io
import sys
from pathlib import Path

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.goal_progress_hook import GoalProgressHook


@pytest.fixture
def hook(monkeypatch):
    """A GoalProgressHook rendering to a real (buffered) Rich console."""
    buffer = io.StringIO()
    test_console = Console(file=buffer, width=100, force_terminal=False)
    monkeypatch.setattr("amplifier_app_cli.goal_progress_hook.console", test_console)
    h = GoalProgressHook()
    h._buffer = buffer  # type: ignore[attr-defined]
    return h


def _joined(hook) -> str:
    return hook._buffer.getvalue()  # type: ignore[attr-defined]


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


class TestAchievedNoContinuations:
    """continuations == 0: the run succeeded on the first pass. The user
    watched it happen -- the interesting content is approximately zero, so
    this gets a single line and nothing else (no reason, no summary, no
    reason history), regardless of whether reason/summary were provided."""

    @pytest.mark.asyncio
    async def test_renders_single_line(self, hook):
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
        # Exactly one non-empty line: the entire trivial-success message.
        assert len([line for line in out.splitlines() if line.strip()]) == 1
        assert "GOAL ACHIEVED" in out
        assert "no continuations" in out

    @pytest.mark.asyncio
    async def test_does_not_render_reason_or_summary_even_when_present(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "achieved",
                "turn": 1,
                "cap": None,
                "continuations": 0,
                "reason": "a long explanation the user already watched happen",
                "reasons": ["a long explanation the user already watched happen"],
                "summary": "an equally long restatement of the same thing",
            },
        )
        out = _joined(hook)
        assert "a long explanation" not in out
        assert "equally long restatement" not in out


class TestAchievedWithContinuations:
    @pytest.mark.asyncio
    async def test_renders_block_with_continuations_and_prefers_summary(self, hook):
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
        assert "Implemented the feature and verified tests." in out
        # reason and summary overlap heavily -- only one is shown.
        assert "all tests pass" not in out
        # succeeded outright: no reason-history noise.
        assert "reason history" not in out.lower()

    @pytest.mark.asyncio
    async def test_falls_back_to_reason_when_no_summary(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "achieved",
                "turn": 2,
                "cap": None,
                "continuations": 1,
                "reason": "the file now exists with the required content",
                "reasons": ["the file now exists with the required content"],
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "GOAL ACHIEVED" in out
        assert "the file now exists with the required content" in out

    @pytest.mark.asyncio
    async def test_unknown_continuations_still_renders_facts(self, hook):
        """continuations is None (older orchestrator / genuinely unknown) is
        NOT treated as the zero-continuations trivial case -- render what we
        have rather than guessing."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "achieved",
                "turn": 3,
                "cap": 10,
                "continuations": None,
                "reason": "done",
                "reasons": ["done"],
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "GOAL ACHIEVED" in out
        assert "unknown number of continuations" in out
        assert "done" in out


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
        # failure states prefer the concrete last reason over the narrative
        # summary -- only one is shown.
        assert "still working" in out
        assert "Ran out of turns before finishing." not in out

    @pytest.mark.asyncio
    async def test_cap_hit_falls_back_to_summary_when_no_reason(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "turn": 20,
                "cap": 20,
                "continuations": 20,
                "reason": None,
                "reasons": [],
                "summary": "Ran out of turns before finishing.",
            },
        )
        out = _joined(hook)
        assert "Ran out of turns before finishing." in out

    @pytest.mark.asyncio
    async def test_cap_hit_shows_collapsed_reason_history_when_it_struggled(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "turn": 5,
                "cap": 5,
                "continuations": 5,
                "reason": "still not done",
                "reasons": [
                    "blocked on X",
                    "blocked on X",
                    "blocked on X",
                    "still not done",
                ],
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "reason history" in out.lower()
        assert "blocked on X" in out
        # collapsed: the duplicate is annotated with a count, not repeated
        # three separate times.
        assert out.count("blocked on X") == 1
        assert "\u00d73" in out
        # the last (current) reason isn't duplicated into the history too.
        assert out.count("still not done") == 1


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
        assert "reason history" in out.lower()
        assert "blocked A" in out
        assert "GOAL ACHIEVED" not in out

    @pytest.mark.asyncio
    async def test_stalled_with_no_reason_history_is_not_rendered(self, hook):
        """Only one reason total (or none preceding the current one) means
        there's no history worth showing."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "turn": 1,
                "cap": None,
                "continuations": 1,
                "reason": "blocked immediately",
                "reasons": ["blocked immediately"],
                "stall_detail": "no tool calls on the only turn taken",
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "reason history" not in out.lower()

    @pytest.mark.asyncio
    async def test_stalled_caps_very_long_reason_history(self, hook):
        reasons = [f"blocked variant {i}" for i in range(10)] + ["still blocked"]
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "turn": 10,
                "cap": None,
                "continuations": 10,
                "reason": "still blocked",
                "reasons": reasons,
                "stall_detail": "no progress across many turns",
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "reason history" in out.lower()
        assert "earlier" in out.lower()  # truncation is surfaced, not silent


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
