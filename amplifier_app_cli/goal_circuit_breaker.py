"""Mechanical circuit breaker for the /goal auto-continue loop.

WHY THIS EXISTS -- two measured incidents, same shape, same batch:

- lane ``j1e6-ci-tool-web``: **855 turns / $202.91** against a $0 spend
  authority, AFTER its actual deliverable (amplifier-module-tool-web PR #17)
  had already merged hours earlier.
- lane ``hd-browser-bridge``: **~887 turns / ~$184**, likewise after its PR
  (#14) had already merged.

Both repeated ONE evaluator message verbatim, every turn, with zero new tool
calls::

    No verified successful `work_resolve` or `work_release` appears in the
    available transcript, so the required terminal outcome is not established.

The goal was structurally unsatisfiable: the work item was already resolved
and held by a sibling session, and ``work_resolve``/``work_release`` both
refuse a session that never held it. No number of further turns could change
that, and none did.

WHY THE EXISTING DETECTORS DID NOT CATCH IT. The orchestrator
(amplifier-module-loop-streaming) already has stall detection, but every
trigger it owns is a *pre-filter* for an LLM judge: ``no_tool_turns >=
goal_stall_threshold`` and ``_busy_stall_pretrip``'s token overlap only decide
whether to PAY for a ``_judge_stall`` call, and ``is_stalled`` is whatever that
judge answers. A judge reading "you just have to call work_resolve" naturally
answers "resolvable" -- forever. It also fails open on error, and the first
confirmed stall only spends an escalation turn rather than stopping. So a
model's opinion sits between a byte-identical repeat and the decision to stop
spending.

A byte-identical repeat is a mechanical fact. It should not need a model's
permission. That is the entire content of this module.

WHERE THIS SITS. The auto-continue loop itself lives in the orchestrator, not
in this repo (see docs/GOAL_COMMAND.md). This module is the HOST-side backstop:
the CLI already subscribes to every ``orchestrator:goal_progress`` event to
render it (``goal_progress_hook.py``), and the CLI is the process that actually
spends the money. It halts by the one seam the orchestrator re-reads on every
iteration -- ``session_state["goal"]`` -- so no coordination with the
orchestrator is required and nothing outside this repo changes.

COST OF THE HALT. The orchestrator re-reads the goal at the TOP of its next
iteration, so at most ONE further agent turn is already in flight when the
breaker fires. Bounded overshoot of one turn (~$0.25 at the rate measured in
the j1e6 incident) against an observed $202.91.

THRESHOLD (``_DEFAULT_REPEAT_LIMIT`` = 6), argued:

- It must be strictly more conservative than the orchestrator's own
  judge-backed pre-filter (``goal_busy_stall_window`` = 3), because unlike that
  path this one is terminal and asks no model. 6 = 2x that window, which leaves
  room for the full legitimate sequence -- trip, escalation turn, re-arm,
  re-trip -- to run first. The judge-backed path keeps first refusal.
- The two costs are wildly asymmetric. A wrong trip stops a run that a manager
  restarts: bounded, recoverable, and it prints exactly what it saw. A missed
  trip cost $202.91 and $184, measured. That asymmetry argues down, not up.
- Below ~4 it starts colliding with legitimate work whose evaluator reason
  genuinely repeats (re-running the same failing test while fixing it).

SIMILARITY: identical after normalization -- whitespace collapsed and the
orchestrator's own "(xN)" repeat annotation stripped -- and nothing looser.
A judge-free terminal stop needs a near-zero false-positive rate, and any
reason differing by even one content word IS new information from the
evaluator. Fuzzy resemblance is already covered, correctly, by the
judge-backed busy-stall path at 0.5 Jaccard. Two detectors, two jobs: theirs
is "similar enough to ask a model", this one is "provably identical, no model
needed".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .console import console

# Consecutive identical evaluator reasons required to trip. See the
# module docstring for the argument. `< 2` disables the breaker entirely
# (a single reason cannot show repetition).
_DEFAULT_REPEAT_LIMIT = 6

ENV_REPEAT_LIMIT = "AMPLIFIER_GOAL_REPEAT_LIMIT"

# The orchestrator's own "(xN)" repeat-count annotation (same convention
# goal_progress_hook.py parses). Formatting, not content: it must not restart
# a streak.
_REPEAT_SUFFIX = re.compile(r"\s*\(\u00d7(\d+)\)\s*$")
_WHITESPACE = re.compile(r"\s+")


def _normalize(reason: str) -> str:
    """Reduce a reason to its content, so formatting noise cannot mask a repeat.

    Strips the "(xN)" annotation and collapses whitespace runs. Nothing else:
    no lowercasing, no punctuation stripping, no stemming. Two reasons that
    differ in any content character are different reasons.
    """
    text = _REPEAT_SUFFIX.sub("", reason)
    return _WHITESPACE.sub(" ", text).strip()


@dataclass(frozen=True)
class RepeatTrip:
    """One tripped breaker.

    ``message`` is the reason exactly as the evaluator last produced it (only
    outer whitespace stripped) -- it is quoted verbatim in the report, and is
    the only evidence a manager gets about why the lane stopped.
    """

    message: str
    count: int
    turn: int | None


class RepeatedReasonDetector:
    """Counts consecutive identical goal-evaluator reasons. Pure, no I/O.

    ``observe`` returns a ``RepeatTrip`` exactly once, on the observation that
    reaches ``limit``, then latches until ``reset()``: the loop is being
    stopped, so the report is a one-time event, not a per-turn one.
    """

    def __init__(self, limit: int = _DEFAULT_REPEAT_LIMIT) -> None:
        self.limit = limit
        self._key: str | None = None
        self._count = 0
        self._tripped = False

    @property
    def enabled(self) -> bool:
        return self.limit >= 2

    def reset(self) -> None:
        self._key = None
        self._count = 0
        self._tripped = False

    def observe(
        self, reason: str | None, *, turn: int | None = None
    ) -> RepeatTrip | None:
        if not self.enabled or self._tripped:
            return None

        key = _normalize(reason) if reason else ""
        if not key:
            # No reason to compare (older orchestrator, or an evaluator that
            # returned nothing). Fail open: break the streak rather than
            # counting an absence as a repeat.
            self._key = None
            self._count = 0
            return None

        if key == self._key:
            self._count += 1
        else:
            self._key = key
            self._count = 1

        if self._count < self.limit:
            return None

        self._tripped = True
        return RepeatTrip(message=reason.strip(), count=self._count, turn=turn)  # type: ignore[union-attr]


def repeat_limit_from_env(env: dict[str, str] | None = None) -> int:
    """Read ``AMPLIFIER_GOAL_REPEAT_LIMIT``, defaulting to ``_DEFAULT_REPEAT_LIMIT``.

    A malformed value is reported on the console and falls back to the default
    -- never silently, since a silently-ignored safety knob is the same class
    of defect this module exists to close.
    """
    raw = (env if env is not None else os.environ).get(ENV_REPEAT_LIMIT)
    if raw is None or raw.strip() == "":
        return _DEFAULT_REPEAT_LIMIT
    try:
        return int(raw.strip())
    except ValueError:
        console.print(
            f"[yellow]{ENV_REPEAT_LIMIT}={raw!r} is not an integer -- using the "
            f"default of {_DEFAULT_REPEAT_LIMIT}.[/yellow]",
            soft_wrap=True,
        )
        return _DEFAULT_REPEAT_LIMIT


class GoalCircuitBreakerHook:
    """Halts a /goal loop that is repeating itself, and says so.

    Contract:
    - Listens for: ``orchestrator:goal_progress`` events, ``continuing`` only
      (every other state already ends the run).
    - Side effects, on trip only: clears ``session_state["goal"]``, records
      ``session_state["goal_circuit_breaker"]``, prints a NEEDS-MANAGER report.
    - Never raises: a hook that crashes a session is worse than the runaway it
      was watching for.
    """

    def __init__(self, session: Any, limit: int | None = None) -> None:
        self.session = session
        self.detector = RepeatedReasonDetector(
            limit if limit is not None else repeat_limit_from_env()
        )
        self.trip: RepeatTrip | None = None

    async def on_goal_progress(self, event: str, data: dict[str, Any]) -> None:
        try:
            if not isinstance(data, dict) or data.get("state") != "continuing":
                return
            trip = self.detector.observe(data.get("reason"), turn=data.get("turn"))
            if trip is None:
                return
            self.trip = trip
            self._halt(trip)
            _render(trip)
        except Exception as e:  # pragma: no cover - defensive, see contract
            console.print(f"[yellow]goal circuit breaker error: {e}[/yellow]")

    def _halt(self, trip: RepeatTrip) -> None:
        """Stop the loop at the one seam the orchestrator re-reads every turn.

        The orchestrator's goal loop begins each iteration with
        ``goal = coordinator.session_state.get("goal")`` and returns when it is
        falsy, so clearing it here ends the pursuit with no further evaluator,
        stall-judge, or summary calls. Best-effort: a host that exposes no
        coordinator still gets the report (see ``on_goal_progress``), which is
        the part its operator needs.
        """
        coordinator = getattr(self.session, "coordinator", None)
        state = getattr(coordinator, "session_state", None)
        if state is None:
            return
        state["goal"] = None
        state["goal_circuit_breaker"] = {
            "state": "needs_manager",
            "repeated_message": trip.message,
            "repeats": trip.count,
            "turn": trip.turn,
        }


def _render(trip: RepeatTrip) -> None:
    """Print the NEEDS-MANAGER report.

    Width-agnostic, same discipline as goal_progress_hook.py: no Rule/Table/
    Padding, no console-width reads, every line printed with ``soft_wrap=True``
    so the terminal reflows it rather than this module baking in line breaks.
    """
    turn = f" at goal turn {trip.turn}" if trip.turn else ""
    console.print()
    console.print(
        f"[red]\u26d4 NEEDS-MANAGER \u2014 goal stopped{turn}: "
        f"same message {trip.count} turns running[/red]",
        soft_wrap=True,
    )
    console.print(f"  repeated verbatim: {trip.message}", soft_wrap=True)
    console.print(
        "  The goal was neither met nor refuted -- the loop stopped producing "
        "new information, so it stopped spending. A human decides what happens "
        "next.",
        soft_wrap=True,
    )


def register_goal_circuit_breaker(
    session: Any, limit: int | None = None
) -> GoalCircuitBreakerHook | None:
    """Register the breaker on ``session``.

    Mirrors ``register_goal_progress_hook``: safe to call unconditionally, a
    no-op when hooks aren't available.
    """
    coordinator = getattr(session, "coordinator", None)
    hooks = coordinator.get("hooks") if coordinator is not None else None
    if not hooks or not hasattr(hooks, "register"):
        return None

    hook = GoalCircuitBreakerHook(session, limit=limit)
    hooks.register(
        "orchestrator:goal_progress",
        hook.on_goal_progress,
        name="goal_circuit_breaker",
    )
    return hook
