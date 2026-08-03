"""Tests for GoalProgressHook's rendering of orchestrator:goal_progress events.

Rendering is plain text (no Rule/Table/Padding) printed with
``soft_wrap=True``, so what's captured in the buffer is exactly the string
this module built -- Rich never reflows it based on console width. This
fixture points the module's shared ``console`` at a real ``rich.console.
Console`` writing to an in-memory buffer at a *parametrized* width, so the
regression this module exists to prevent -- width-dependent output -- is
directly testable: render the same event at several widths and diff the
result.

Tests assert on the *information* that must be present (status phrase,
glyph, counts, cause, prose) -- not on decorative characters -- so they
survive future cosmetic changes. The width-agnostic tests additionally
assert byte-for-byte equality of the rendered output across widths, which
is the actual guard against reintroducing width-dependent rendering.
"""

import io
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.goal_progress_hook import (
    _SUMMARY_DISPLAY_MAX_CHARS,
    GoalProgressHook,
    _clip_for_display,
)

WIDTHS = (40, 80, 200)


def _make_hook(monkeypatch, width: int) -> tuple[GoalProgressHook, io.StringIO]:
    buffer = io.StringIO()
    test_console = Console(file=buffer, width=width, force_terminal=False)
    monkeypatch.setattr("amplifier_app_cli.goal_progress_hook.console", test_console)
    return GoalProgressHook(), buffer


@pytest.fixture
def hook(monkeypatch):
    """A GoalProgressHook rendering to a real (buffered) Rich console."""
    h, buffer = _make_hook(monkeypatch, width=80)
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

    @pytest.mark.asyncio
    async def test_continuing_with_no_reason(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "continuing", "turn": 1, "cap": None, "reason": None},
        )
        out = _joined(hook)
        assert "turn 1" in out
        assert "\u2014" not in out


class TestAchievedState:
    """achieved never gets prose (no reason/summary shown), regardless of
    whether they're present in the payload. The only variation is the
    optional continuation-count suffix on the single header line."""

    @pytest.mark.asyncio
    async def test_bare_success_no_continuations(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "achieved", "turn": 1, "cap": None, "continuations": 0},
        )
        out = _joined(hook)
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1
        assert "Goal met" in out
        assert "\u2713" in out
        assert "sent back" not in out

    @pytest.mark.asyncio
    async def test_zero_continuations_omits_count_house_convention(self, hook):
        """Never print a count of zero."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "achieved", "continuations": 0},
        )
        out = _joined(hook)
        assert "0" not in out

    @pytest.mark.asyncio
    async def test_one_continuation_says_once(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "achieved", "continuations": 1},
        )
        out = _joined(hook)
        assert "sent back once" in out

    @pytest.mark.asyncio
    async def test_two_continuations_says_twice(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "achieved", "continuations": 2},
        )
        out = _joined(hook)
        assert "sent back twice" in out

    @pytest.mark.asyncio
    async def test_many_continuations_says_n_times(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "achieved", "continuations": 4},
        )
        out = _joined(hook)
        assert "sent back 4 times" in out

    @pytest.mark.asyncio
    async def test_never_renders_reason_or_summary(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "achieved",
                "continuations": 3,
                "reason": "all tests pass",
                "reasons": ["r1", "r2", "all tests pass"],
                "summary": "Implemented the feature and verified tests.",
            },
        )
        out = _joined(hook)
        assert "Goal met" in out
        assert "sent back" in out
        assert "all tests pass" not in out
        assert "Implemented the feature" not in out
        # single line: no blank-line-before-block, no body lines
        assert len([line for line in out.splitlines() if line.strip()]) == 1

    @pytest.mark.asyncio
    async def test_no_blank_line_before_success(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress", {"state": "achieved", "continuations": 0}
        )
        out = _joined(hook)
        assert not out.startswith("\n")


class TestStalledState:
    @pytest.mark.asyncio
    async def test_verdict_is_not_met_never_achieved(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "continuations": 4,
                "reasons": ["blocked A", "blocked A", "blocked A", "blocked A"],
            },
        )
        out = _joined(hook)
        assert "Goal not met" in out
        assert "\u2717" in out
        assert "stalled" in out.lower()
        assert "Goal met" not in out

    @pytest.mark.asyncio
    async def test_same_blocker_every_turn(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "continuations": 4,
                "reasons": [
                    "no 16-digit code can be derived from anything given",
                    "no 16-digit code can be derived from anything given",
                    "no 16-digit code can be derived from anything given",
                    "no 16-digit code can be derived from anything given",
                ],
            },
        )
        out = _joined(hook)
        assert "same blocker 4 turns running" in out
        assert "no 16-digit code can be derived from anything given" in out
        # never the raw list -- exactly one occurrence of the blocker text
        assert out.count("no 16-digit code can be derived") == 1

    @pytest.mark.asyncio
    async def test_reads_preexisting_repeat_count_annotation(self, hook):
        """The orchestrator's own dedupe may pre-collapse with a "(\u00d7N)"
        suffix; this hook must read that defensively rather than treating
        it as yet another distinct blocker."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "reasons": ["blocked on X (\u00d73)"],
            },
        )
        out = _joined(hook)
        assert "same blocker 3 turns running: blocked on X" in out
        assert "(\u00d73)" not in out  # annotation consumed, not re-printed raw

    @pytest.mark.asyncio
    async def test_different_blockers_reads_as_flailing(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "continuations": 4,
                "reasons": ["blocked A", "blocked B", "blocked C", "blocked D"],
            },
        )
        out = _joined(hook)
        assert "4 turns, 4 different blockers, none resolved" in out
        # never dumps the actual list of distinct blockers
        assert "blocked A" not in out
        assert "blocked B" not in out

    @pytest.mark.asyncio
    async def test_falls_back_to_stall_detail_when_no_reasons(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "stalled",
                "reasons": [],
                "stall_detail": "no tool calls across the last 3 turns",
            },
        )
        out = _joined(hook)
        assert "no tool calls across the last 3 turns" in out

    @pytest.mark.asyncio
    async def test_falls_back_to_bare_reason_for_older_orchestrator(self, hook):
        """Older payload shape: only `reason`, no `reasons`/`stall_detail`."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "stalled", "reason": "still blocked"},
        )
        out = _joined(hook)
        assert "still blocked" in out

    @pytest.mark.asyncio
    async def test_no_data_at_all_renders_header_only(self, hook):
        await hook.on_goal_progress("orchestrator:goal_progress", {"state": "stalled"})
        out = _joined(hook)
        assert "Goal not met" in out

    @pytest.mark.asyncio
    async def test_long_blocker_passes_through_untruncated(self, hook):
        """No character budget: a long blocker survives verbatim, with no
        ellipsis and nothing lost -- the terminal wraps it, this hook
        doesn't cut it."""
        long_blocker = "x" * 400
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "stalled", "reasons": [long_blocker]},
        )
        out = _joined(hook)
        assert "\u2026" not in out
        assert long_blocker in out


class TestCapHitState:
    @pytest.mark.asyncio
    async def test_verdict_is_unconfirmed_never_failed(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "cap_hit", "cap": 8, "continuations": 8},
        )
        out = _joined(hook)
        assert "Goal unconfirmed" in out
        assert "\u26a0" in out
        assert "Goal not met" not in out
        assert "Goal met" not in out

    @pytest.mark.asyncio
    async def test_cap_number_folded_into_header(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "cap_hit", "cap": 8, "continuations": 8},
        )
        out = _joined(hook)
        assert "hit the 8-turn cap" in out

    @pytest.mark.asyncio
    async def test_summary_shown_with_still_open_prefix(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "cap": 8,
                "continuations": 8,
                "reason": "still working",
                "summary": ("changelog entry not written; tests and build passed"),
            },
        )
        out = _joined(hook)
        assert "still open: changelog entry not written; tests and build passed" in out
        # reason/summary overlap -- only the preferred one (summary) shows
        assert "still working" not in out

    @pytest.mark.asyncio
    async def test_falls_back_to_reason_when_no_summary(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "cap": 8,
                "reason": "still working",
                "summary": None,
            },
        )
        out = _joined(hook)
        assert "still open: still working" in out

    @pytest.mark.asyncio
    async def test_always_includes_rerun_hint(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress", {"state": "cap_hit", "cap": 8}
        )
        out = _joined(hook)
        assert "rerun with a higher cap to finish" in out

    @pytest.mark.asyncio
    async def test_no_cap_hit_reasoning_leaks_hint_to_other_states(self, hook):
        """The rerun hint is correct in exactly cap_hit -- never on stalled."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "stalled", "reasons": ["blocked"]},
        )
        out = _joined(hook)
        assert "rerun with a higher cap" not in out

    @pytest.mark.asyncio
    async def test_no_cap_number_never_shown_on_other_states(self, hook):
        """(cap: N) appears in no state except cap_hit."""
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "stalled", "cap": 20, "reasons": ["blocked"]},
        )
        out = _joined(hook)
        assert "cap" not in out.lower()


class TestCancelledState:
    @pytest.mark.asyncio
    async def test_bare_cancelled_no_reason(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "cancelled", "reason": None, "summary": None},
        )
        out = _joined(hook)
        assert "Goal unconfirmed" in out
        assert "cancelled" in out.lower()
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1  # header only, no empty prose line

    @pytest.mark.asyncio
    async def test_cancelled_with_reason(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "cancelled", "reason": "user pressed ctrl-c"},
        )
        out = _joined(hook)
        assert "user pressed ctrl-c" in out


class TestErrorState:
    @pytest.mark.asyncio
    async def test_error_reads_as_unconfirmed(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "error", "reason": "rate limit from provider after 3 retries"},
        )
        out = _joined(hook)
        assert "Goal unconfirmed" in out
        assert "evaluator failed" in out
        assert "rate limit from provider after 3 retries" in out

    @pytest.mark.asyncio
    async def test_error_falls_back_to_summary(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "error", "reason": None, "summary": "evaluator crashed"},
        )
        out = _joined(hook)
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
        assert "Goal met" in out

    @pytest.mark.asyncio
    async def test_completely_empty_payload_does_not_raise(self, hook):
        for state in ("achieved", "cap_hit", "cancelled", "error", "stalled"):
            await hook.on_goal_progress("orchestrator:goal_progress", {"state": state})

    @pytest.mark.asyncio
    async def test_unrecognized_state_falls_back_to_raw_payload(self, hook):
        await hook.on_goal_progress(
            "orchestrator:goal_progress", {"state": "mystery", "foo": "bar"}
        )
        out = _joined(hook)
        assert "goal progress" in out
        assert "mystery" in out


class TestNoRichLayoutPrimitives:
    """Structural guard: none of the banned width-dependent primitives may
    appear anywhere in the rendered output, for any terminal state."""

    PAYLOADS: ClassVar[dict[str, dict[str, object]]] = {
        "achieved": {"state": "achieved", "continuations": 2},
        "cap_hit": {
            "state": "cap_hit",
            "cap": 8,
            "continuations": 8,
            "summary": "changelog entry not written; tests and build passed",
        },
        "cancelled": {"state": "cancelled"},
        "error": {
            "state": "error",
            "reason": "rate limit from provider after 3 retries",
        },
        "stalled": {
            "state": "stalled",
            "continuations": 4,
            "reasons": ["same blocker"] * 4,
        },
    }

    @pytest.mark.asyncio
    async def test_no_divider_characters(self, hook):
        for payload in self.PAYLOADS.values():
            await hook.on_goal_progress("orchestrator:goal_progress", dict(payload))
        out = _joined(hook)
        assert "\u2500" * 3 not in out  # no rule/divider run of dashes
        assert "\u258c" not in out  # no rail gutter glyph


class TestWidthAgnosticRendering:
    """The actual regression guard for this whole change: every terminal
    state's structural lines (header + prose) must render byte-identically
    at 40, 80, and 200 columns. Rendering is done with ``soft_wrap=True``
    specifically so Rich never reflows based on console width -- this test
    proves that holds for every terminal state.
    """

    PAYLOADS: ClassVar[dict[str, dict[str, object]]] = {
        "achieved_bare": {"state": "achieved", "continuations": 0},
        "achieved_with_count": {"state": "achieved", "continuations": 2},
        "stalled_same_blocker": {
            "state": "stalled",
            "continuations": 4,
            "reasons": [
                "no 16-digit code can be derived from anything given",
            ]
            * 4,
        },
        "stalled_different_blockers": {
            "state": "stalled",
            "continuations": 4,
            "reasons": ["blocked A", "blocked B", "blocked C", "blocked D"],
        },
        "cap_hit": {
            "state": "cap_hit",
            "cap": 8,
            "continuations": 8,
            "summary": "changelog entry not written; tests and build passed",
        },
        "cancelled_bare": {"state": "cancelled"},
        "error": {
            "state": "error",
            "reason": "rate limit from provider after 3 retries",
        },
        "continuing": {
            "state": "continuing",
            "turn": 2,
            "cap": 20,
            "reason": "not done yet",
        },
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,payload", list(PAYLOADS.items()))
    async def test_identical_across_widths(self, monkeypatch, name, payload):
        rendered = {}
        for width in WIDTHS:
            h, buffer = _make_hook(monkeypatch, width)
            await h.on_goal_progress("orchestrator:goal_progress", dict(payload))
            rendered[width] = buffer.getvalue()

        baseline = rendered[WIDTHS[0]]
        for width in WIDTHS[1:]:
            assert rendered[width] == baseline, (
                f"{name!r} rendered differently at width={width} vs "
                f"width={WIDTHS[0]}:\n"
                f"--- width={WIDTHS[0]} ---\n{baseline!r}\n"
                f"--- width={width} ---\n{rendered[width]!r}"
            )

    @pytest.mark.asyncio
    async def test_long_prose_line_is_not_wrapped_by_rich_at_narrow_width(
        self, monkeypatch
    ):
        """A prose line longer than the console width must still come back
        as a single unbroken line (no embedded newline) -- proof that Rich
        isn't hard-wrapping it into the buffer. The terminal, not this
        hook, is responsible for any visual reflow."""
        long_summary = (
            "this is a deliberately long summary sentence that comfortably "
            "exceeds forty columns and must not be hard-wrapped by rich"
        )
        h, buffer = _make_hook(monkeypatch, width=40)
        await h.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "cap": 8,
                "continuations": 8,
                "summary": long_summary,
            },
        )
        out = buffer.getvalue()
        matching_lines = [line for line in out.splitlines() if "still open" in line]
        assert len(matching_lines) == 1
        assert long_summary in matching_lines[0]

    @pytest.mark.asyncio
    async def test_very_long_prose_survives_verbatim_no_truncation(self, monkeypatch):
        """There is no character budget: a 300+ char prose string comes back
        completely intact, with no ellipsis and nothing cut -- the terminal
        wraps it, this hook never truncates it."""
        very_long_reason = (
            "the evaluator determined the goal was not fully satisfied because "
            "several acceptance criteria remained unaddressed, including the "
            "requirement to update the changelog, the requirement to add "
            "regression tests covering the new edge cases, and the requirement "
            "to update the public documentation describing the changed behavior"
        )
        assert len(very_long_reason) > 300
        h, buffer = _make_hook(monkeypatch, width=80)
        await h.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "cancelled", "reason": very_long_reason},
        )
        out = buffer.getvalue()
        assert very_long_reason in out
        assert "\u2026" not in out


class TestClipForDisplay:
    """Unit-level coverage of `_clip_for_display` in isolation -- exact,
    hardcoded expected output, independent of any event rendering."""

    def test_text_under_cap_is_unchanged(self):
        text = "short and under the cap"
        assert _clip_for_display(text) == text

    def test_text_over_cap_clips_at_word_boundary(self):
        # 130 chars, well over the default 120-char cap, with clean word
        # boundaries throughout so the expected clip point is exact.
        text = (
            "the evaluator keeps reporting the exact same blocker every "
            "single turn and no new progress has been made toward the "
            "condition at all whatsoever"
        )
        assert len(text) > _SUMMARY_DISPLAY_MAX_CHARS
        result = _clip_for_display(text)
        assert len(result) <= _SUMMARY_DISPLAY_MAX_CHARS
        # Clipped at the last whole word within the cap -- never a partial
        # word, and never the raw text[:120] slice verbatim.
        assert text.startswith(result)
        assert not result.endswith(" ")
        cutoff = text[:_SUMMARY_DISPLAY_MAX_CHARS]
        assert result == cutoff[: cutoff.rfind(" ")]

    def test_custom_max_chars_honored(self):
        text = "one two three four five six seven eight nine ten"
        result = _clip_for_display(text, max_chars=10)
        assert len(result) <= 10
        assert result == "one two"


class TestSummaryClippedForDisplayOnly:
    """Display-time clipping of the `summary` field -- storage/emission is
    the orchestrator's concern (amplifier-module-loop-streaming stores and
    emits the model's full summary text unclipped); this hook clips only
    what it prints, so a one-line render is guaranteed regardless of how
    long the upstream stored/graph-recorded text is. `reason` is never
    clipped -- see TestWidthAgnosticRendering.
    test_very_long_prose_survives_verbatim_no_truncation for that guarantee.
    """

    @pytest.mark.asyncio
    async def test_cap_hit_overlong_summary_is_clipped_in_still_open_line(self, hook):
        overlong_summary = (
            "the evaluator determined the goal was not fully satisfied "
            "because several acceptance criteria remained unaddressed "
            "including the changelog entry and the regression tests"
        )
        assert len(overlong_summary) > _SUMMARY_DISPLAY_MAX_CHARS
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "cap": 8,
                "continuations": 8,
                "summary": overlong_summary,
            },
        )
        out = _joined(hook)
        assert overlong_summary not in out
        assert _clip_for_display(overlong_summary) in out

    @pytest.mark.asyncio
    async def test_cap_hit_overlong_reason_fallback_is_not_clipped(self, hook):
        """When there's no summary, cap_hit falls back to `reason` --
        which, unlike `summary`, is never clipped even if it happens to be
        long."""
        overlong_reason = (
            "the evaluator determined the goal was not fully satisfied "
            "because several acceptance criteria remained unaddressed "
            "including the changelog entry and the regression tests"
        )
        assert len(overlong_reason) > _SUMMARY_DISPLAY_MAX_CHARS
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {
                "state": "cap_hit",
                "cap": 8,
                "reason": overlong_reason,
                "summary": None,
            },
        )
        out = _joined(hook)
        assert overlong_reason in out

    @pytest.mark.asyncio
    async def test_error_overlong_summary_fallback_is_clipped(self, hook):
        """cancelled/error prefer `reason`; when absent, the `summary`
        fallback is clipped just like cap_hit's."""
        overlong_summary = (
            "the evaluator determined the goal was not fully satisfied "
            "because several acceptance criteria remained unaddressed "
            "including the changelog entry and the regression tests"
        )
        assert len(overlong_summary) > _SUMMARY_DISPLAY_MAX_CHARS
        await hook.on_goal_progress(
            "orchestrator:goal_progress",
            {"state": "error", "reason": None, "summary": overlong_summary},
        )
        out = _joined(hook)
        assert overlong_summary not in out
        assert _clip_for_display(overlong_summary) in out
