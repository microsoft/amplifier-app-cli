"""Tests for the /goal repeat circuit breaker.

The motivating incidents, both real, both in the same batch's history:

- lane ``j1e6-ci-tool-web``  -- 855 turns / $202.91 AFTER its deliverable
  (amplifier-module-tool-web PR #17) had already merged hours earlier.
- lane ``hd-browser-bridge`` -- ~887 turns / ~$184 AFTER its PR (#14) had
  already merged hours earlier.

Both lanes repeated ONE evaluator message verbatim, every turn, forever:

    "No verified successful `work_resolve` or `work_release` appears in the
     available transcript, so the required terminal outcome is not
     established."

The orchestrator's own stall detection could not stop it: every trigger it
has is a *pre-filter* for an LLM judge (see loop-streaming's
``_busy_stall_pretrip`` / ``_judge_stall``), and a judge that keeps
answering "resolvable" -- which is the natural reading of "you just have to
call work_resolve" -- vetoes the stop indefinitely. A byte-identical repeat
is a mechanical fact and must not need a model's permission.

``TestScriptedIncidentReproduction`` is the fail-before/pass-after pair: the
same scripted loop, with and without the breaker registered.
"""

import io
import sys
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.goal_circuit_breaker import (
    _DEFAULT_REPEAT_LIMIT,
    GoalCircuitBreakerHook,
    RepeatedReasonDetector,
    register_goal_circuit_breaker,
    repeat_limit_from_env,
)

# Verbatim from the j1e6-ci-tool-web incident record (work item
# model_performance-dumt).
INCIDENT_REASON = (
    "No verified successful `work_resolve` or `work_release` appears in the "
    "available transcript, so the required terminal outcome is not established."
)

# Turns the j1e6 lane was observed producing before a human killed it. Used as
# the scripted loop's own runaway fence, so "the breaker did nothing" shows up
# as the real number rather than as a hang.
OBSERVED_RUNAWAY_TURNS = 855


# --------------------------------------------------------------------------
# Fakes: the smallest shapes the hook actually touches.
# --------------------------------------------------------------------------


class FakeHooks:
    """Minimal HookRegistry: register + await-emit, in registration order."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def register(self, event: str, handler: Any, name: str = "", **_: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        for handler in self.handlers.get(event, []):
            await handler(event, data)


class FakeCoordinator:
    def __init__(self, hooks: FakeHooks) -> None:
        self.session_state: dict[str, Any] = {}
        self._modules = {"hooks": hooks}

    def get(self, name: str) -> Any:
        return self._modules.get(name)


class FakeSession:
    def __init__(self, hooks: FakeHooks) -> None:
        self.coordinator = FakeCoordinator(hooks)


class ScriptedGoalLoop:
    """A miniature of loop-streaming's ``execute()`` goal loop.

    Same three load-bearing moves, same order, nothing else:

    1. re-read ``session_state["goal"]`` at the top of every iteration and
       return when it is gone (this is the seam the breaker uses);
    2. emit ``orchestrator:goal_progress`` with ``state="continuing"`` and
       the evaluator's reason;
    3. run the next agent turn -- the part that costs money.

    ``agent_turns`` is the cost counter: in the incidents it reached 855 and
    ~887 at roughly $0.25/turn.
    """

    def __init__(
        self,
        session: FakeSession,
        hooks: FakeHooks,
        reason: str,
        *,
        max_turns: int = OBSERVED_RUNAWAY_TURNS,
    ) -> None:
        self.session = session
        self.hooks = hooks
        self.reason = reason
        self.max_turns = max_turns
        self.agent_turns = 0
        self.hit_runaway_fence = False

    async def run(self) -> None:
        state = self.session.coordinator.session_state
        state["goal"] = {
            "condition": "resolve model_performance-j1e6 with a user-readable summary",
            "turns_used": 0,
            "last_reason": None,
            "cap": None,
            "reasons": [],
            "continuations": 0,
            "no_tool_turns": 0,
            "escalated": False,
        }
        while True:
            goal = state.get("goal")
            if not goal:
                return
            if self.agent_turns >= self.max_turns:
                self.hit_runaway_fence = True
                return

            goal["turns_used"] += 1
            # The evaluator answers "not satisfied" with the same words every
            # turn -- the whole shape of the incident.
            goal["last_reason"] = self.reason
            goal["reasons"].append(self.reason)
            await self.hooks.emit(
                "orchestrator:goal_progress",
                {
                    "state": "continuing",
                    "turn": goal["turns_used"],
                    "cap": goal.get("cap"),
                    "reason": self.reason,
                },
            )
            goal["continuations"] += 1
            self.agent_turns += 1  # the next turn runs -- this is the spend


@pytest.fixture
def buffered_console(monkeypatch):
    """Point the module's shared console at an in-memory buffer."""
    buffer = io.StringIO()
    monkeypatch.setattr(
        "amplifier_app_cli.goal_circuit_breaker.console",
        Console(file=buffer, width=80, force_terminal=False),
    )
    return buffer


# --------------------------------------------------------------------------
# The reproduction
# --------------------------------------------------------------------------


class TestScriptedIncidentReproduction:
    @pytest.mark.asyncio
    async def test_without_breaker_the_loop_runs_unbounded(self):
        """Fail-before: nothing stops it. This is the $203 shape."""
        hooks = FakeHooks()
        session = FakeSession(hooks)
        loop = ScriptedGoalLoop(session, hooks, INCIDENT_REASON)

        await loop.run()

        assert loop.hit_runaway_fence is True
        assert loop.agent_turns == OBSERVED_RUNAWAY_TURNS
        assert session.coordinator.session_state["goal"] is not None

    @pytest.mark.asyncio
    async def test_with_breaker_it_self_terminates_at_the_threshold(
        self, buffered_console
    ):
        """Pass-after: the same loop stops at the configured threshold."""
        hooks = FakeHooks()
        session = FakeSession(hooks)
        breaker = register_goal_circuit_breaker(session, limit=6)
        assert breaker is not None
        loop = ScriptedGoalLoop(session, hooks, INCIDENT_REASON)

        await loop.run()

        assert loop.hit_runaway_fence is False
        # 6 identical turns trip it; at most one further turn is already in
        # flight when it does (the orchestrator re-reads the goal at the TOP
        # of the next iteration).
        assert loop.agent_turns <= 6 + 1
        assert session.coordinator.session_state["goal"] is None
        assert breaker.trip is not None
        assert breaker.trip.count == 6

    @pytest.mark.asyncio
    async def test_trip_is_reported_with_the_message_quoted_verbatim(
        self, buffered_console
    ):
        hooks = FakeHooks()
        session = FakeSession(hooks)
        register_goal_circuit_breaker(session, limit=4)

        await ScriptedGoalLoop(session, hooks, INCIDENT_REASON).run()

        out = buffered_console.getvalue()
        assert "NEEDS-MANAGER" in out
        # The repeated message must survive verbatim -- it is the only
        # evidence a manager gets about why the lane stopped. Rich soft-wrap
        # never inserts breaks, so this is a straight substring check.
        assert INCIDENT_REASON in out

    @pytest.mark.asyncio
    async def test_trip_is_recorded_in_session_state_for_the_host(
        self, buffered_console
    ):
        hooks = FakeHooks()
        session = FakeSession(hooks)
        register_goal_circuit_breaker(session, limit=3)

        await ScriptedGoalLoop(session, hooks, INCIDENT_REASON).run()

        record = session.coordinator.session_state["goal_circuit_breaker"]
        assert record["state"] == "needs_manager"
        assert record["repeats"] == 3
        assert record["repeated_message"] == INCIDENT_REASON
        assert record["turn"] == 3

    @pytest.mark.asyncio
    async def test_a_goal_that_makes_progress_is_never_tripped(self, buffered_console):
        """A distinct reason every turn must never trip a judge-free stop."""
        hooks = FakeHooks()
        session = FakeSession(hooks)
        breaker = register_goal_circuit_breaker(session, limit=3)

        for turn in range(1, 21):
            await hooks.emit(
                "orchestrator:goal_progress",
                {
                    "state": "continuing",
                    "turn": turn,
                    "reason": f"tests still failing: {turn} remaining",
                },
            )

        assert breaker is not None
        assert breaker.trip is None
        assert "goal" not in session.coordinator.session_state
        assert buffered_console.getvalue() == ""


# --------------------------------------------------------------------------
# The detector (pure)
# --------------------------------------------------------------------------


class TestRepeatedReasonDetector:
    def test_trips_exactly_at_the_limit(self):
        d = RepeatedReasonDetector(limit=5)
        for turn in range(1, 5):
            assert d.observe(INCIDENT_REASON, turn=turn) is None
        trip = d.observe(INCIDENT_REASON, turn=5)
        assert trip is not None
        assert trip.count == 5
        assert trip.turn == 5
        assert trip.message == INCIDENT_REASON

    def test_latches_after_tripping(self):
        d = RepeatedReasonDetector(limit=2)
        assert d.observe("same", turn=1) is None
        assert d.observe("same", turn=2) is not None
        assert d.observe("same", turn=3) is None  # reported once, not per turn

    def test_a_different_reason_restarts_the_streak(self):
        d = RepeatedReasonDetector(limit=3)
        d.observe("a")
        d.observe("a")
        assert d.observe("b") is None
        assert d.observe("b") is None
        assert d.observe("b") is not None

    def test_near_identical_but_not_identical_does_not_trip(self):
        """One content word of difference is new information from the
        evaluator -- that case belongs to the orchestrator's judge-backed
        busy-stall path, not to this judge-free terminal stop."""
        d = RepeatedReasonDetector(limit=3)
        assert d.observe("blocked on 3 failing tests") is None
        assert d.observe("blocked on 2 failing tests") is None
        assert d.observe("blocked on 1 failing test") is None

    def test_whitespace_and_repeat_annotation_are_normalized_away(self):
        d = RepeatedReasonDetector(limit=3)
        assert d.observe("  blocked on the   same thing\n") is None
        assert d.observe("blocked on the same thing") is None
        # The orchestrator's own "(xN)" repeat annotation is formatting, not
        # content: it must not restart the streak.
        assert d.observe("blocked on the same thing (\u00d72)") is not None

    def test_empty_or_missing_reason_resets_rather_than_counting(self):
        d = RepeatedReasonDetector(limit=2)
        d.observe("same")
        assert d.observe(None) is None
        assert d.observe("same") is None  # streak restarted, not tripped
        assert d.observe("same") is not None

    @pytest.mark.parametrize("limit", [0, 1, -1])
    def test_disabled_at_a_limit_below_two(self, limit):
        d = RepeatedReasonDetector(limit=limit)
        assert d.enabled is False
        for _ in range(50):
            assert d.observe(INCIDENT_REASON) is None

    def test_reset_clears_the_latch_and_the_streak(self):
        d = RepeatedReasonDetector(limit=2)
        d.observe("same")
        assert d.observe("same") is not None
        d.reset()
        assert d.observe("same") is None
        assert d.observe("same") is not None


class TestConfiguration:
    def test_default_limit_is_six(self):
        assert _DEFAULT_REPEAT_LIMIT == 6
        assert RepeatedReasonDetector().limit == 6

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AMPLIFIER_GOAL_REPEAT_LIMIT", "9")
        assert repeat_limit_from_env() == 9

    def test_env_zero_disables(self, monkeypatch):
        monkeypatch.setenv("AMPLIFIER_GOAL_REPEAT_LIMIT", "0")
        assert repeat_limit_from_env() == 0
        assert RepeatedReasonDetector(limit=0).enabled is False

    def test_malformed_env_falls_back_loudly(self, monkeypatch, buffered_console):
        monkeypatch.setenv("AMPLIFIER_GOAL_REPEAT_LIMIT", "later")
        assert repeat_limit_from_env() == _DEFAULT_REPEAT_LIMIT
        out = buffered_console.getvalue()
        assert "AMPLIFIER_GOAL_REPEAT_LIMIT" in out
        assert "later" in out


class TestHookRobustness:
    @pytest.mark.asyncio
    async def test_terminal_states_are_ignored(self, buffered_console):
        hook = GoalCircuitBreakerHook(FakeSession(FakeHooks()), limit=2)
        for _ in range(10):
            await hook.on_goal_progress(
                "orchestrator:goal_progress",
                {"state": "achieved", "reason": INCIDENT_REASON},
            )
        assert hook.trip is None

    @pytest.mark.asyncio
    async def test_never_raises_on_a_malformed_payload(self, buffered_console):
        hook = GoalCircuitBreakerHook(FakeSession(FakeHooks()), limit=2)
        await hook.on_goal_progress("orchestrator:goal_progress", {})
        await hook.on_goal_progress("orchestrator:goal_progress", {"state": None})
        assert hook.trip is None

    @pytest.mark.asyncio
    async def test_never_raises_when_the_session_has_no_coordinator(
        self, buffered_console
    ):
        class Bare:
            pass

        hook = GoalCircuitBreakerHook(Bare(), limit=2)
        for turn in (1, 2):
            await hook.on_goal_progress(
                "orchestrator:goal_progress",
                {"state": "continuing", "turn": turn, "reason": INCIDENT_REASON},
            )
        # Still reports -- a host it cannot halt is exactly the host whose
        # operator most needs to see the message.
        assert hook.trip is not None
        assert "NEEDS-MANAGER" in buffered_console.getvalue()

    def test_register_is_a_no_op_without_a_hook_registry(self):
        class CoordinatorWithoutHooks:
            def __init__(self) -> None:
                self.session_state: dict[str, Any] = {}

            def get(self, _name: str) -> None:
                return None

        class SessionWithoutHooks:
            def __init__(self) -> None:
                self.coordinator = CoordinatorWithoutHooks()

        assert register_goal_circuit_breaker(SessionWithoutHooks()) is None
