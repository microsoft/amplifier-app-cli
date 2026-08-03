"""Tests for the /goal command: unlimited-by-default turn cap (deliberate --
see docs/decisions/ADR-0005-goal-unlimited-by-default.md), the --max-turns
opt-in cap, state shape, and status display (docs/GOAL_COMMAND.md).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.main import (
    _GOAL_MAX_TURNS_FLAG,
    _parse_goal_max_turns,
)
from helpers import _make_command_processor

_MODULE = "amplifier_app_cli.main"

# === _parse_goal_max_turns ===


class TestParseGoalMaxTurns:
    def test_no_flag_defaults_to_unlimited(self):
        cap, condition = _parse_goal_max_turns("do the thing")
        assert cap is None
        assert condition == "do the thing"

    def test_explicit_positive_value(self):
        cap, condition = _parse_goal_max_turns(f"{_GOAL_MAX_TURNS_FLAG} 5 do the thing")
        assert cap == 5
        assert condition == "do the thing"

    def test_explicit_zero_means_unlimited(self):
        cap, condition = _parse_goal_max_turns(f"{_GOAL_MAX_TURNS_FLAG} 0 do the thing")
        assert cap is None
        assert condition == "do the thing"

    def test_negative_value_raises(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            _parse_goal_max_turns(f"{_GOAL_MAX_TURNS_FLAG} -1 do the thing")

    def test_non_integer_value_raises(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            _parse_goal_max_turns(f"{_GOAL_MAX_TURNS_FLAG} abc do the thing")

    def test_missing_value_raises(self):
        with pytest.raises(ValueError):
            _parse_goal_max_turns(_GOAL_MAX_TURNS_FLAG)

    def test_no_args_defaults_to_unlimited_with_empty_condition(self):
        cap, condition = _parse_goal_max_turns("")
        assert cap is None
        assert condition == ""


# === CommandProcessor._handle_goal (interactive) ===


class TestHandleGoalSet:
    @pytest.mark.asyncio
    async def test_default_is_unlimited(self):
        cp = _make_command_processor()
        result = await cp._handle_goal("do the thing")
        goal = cp.session.coordinator.session_state["goal"]
        assert goal["cap"] is None
        assert goal["condition"] == "do the thing"
        assert "unlimited turns" in result

    @pytest.mark.asyncio
    async def test_set_confirmation_does_not_echo_condition(self):
        """The user just typed the condition -- printing it back is pure
        duplication. The confirmation should surface only what they
        couldn't already see: whether a turn cap is in effect."""
        cp = _make_command_processor()
        result = await cp._handle_goal("a very long condition the user already typed")
        assert "a very long condition the user already typed" not in result
        assert "Goal set" in result

    @pytest.mark.asyncio
    async def test_custom_cap_applied(self):
        cp = _make_command_processor()
        result = await cp._handle_goal(f"{_GOAL_MAX_TURNS_FLAG} 3 do the thing")
        goal = cp.session.coordinator.session_state["goal"]
        assert goal["cap"] == 3
        assert "max 3 turns" in result
        assert "do the thing" not in result

    @pytest.mark.asyncio
    async def test_explicit_zero_is_equivalent_to_default_unlimited(self):
        cp = _make_command_processor()
        result = await cp._handle_goal(f"{_GOAL_MAX_TURNS_FLAG} 0 do the thing")
        goal = cp.session.coordinator.session_state["goal"]
        assert goal["cap"] is None
        assert "unlimited turns" in result

    @pytest.mark.asyncio
    async def test_new_state_fields_initialized(self):
        cp = _make_command_processor()
        await cp._handle_goal("do the thing")
        goal = cp.session.coordinator.session_state["goal"]
        assert goal["turns_used"] == 0
        assert goal["last_reason"] is None
        assert goal["reasons"] == []
        assert goal["continuations"] == 0
        assert goal["no_tool_turns"] == 0
        assert goal["escalated"] is False

    @pytest.mark.asyncio
    async def test_malformed_max_turns_does_not_set_goal(self):
        cp = _make_command_processor()
        result = await cp._handle_goal(f"{_GOAL_MAX_TURNS_FLAG} abc do the thing")
        assert cp.session.coordinator.session_state.get("goal") is None
        assert "Goal not set" in result


class TestHandleGoalStatus:
    @pytest.mark.asyncio
    async def test_no_goal_active(self):
        cp = _make_command_processor()
        result = await cp._handle_goal("")
        assert result == "No goal active. Usage: /goal <condition>"

    @pytest.mark.asyncio
    async def test_status_shows_continuations_and_cap(self):
        cp = _make_command_processor()
        cp.session.coordinator.session_state["goal"] = {
            "condition": "ship the feature",
            "turns_used": 4,
            "last_reason": "tests still failing",
            "cap": 20,
            "reasons": ["first reason", "second reason", "tests still failing"],
            "continuations": 3,
            "no_tool_turns": 0,
            "escalated": False,
        }
        result = await cp._handle_goal("")
        assert "ship the feature" in result
        assert "4/20" in result
        assert "Continuations (sent back to assistant): 3" in result
        assert "tests still failing" in result
        assert "Recent reasons:" in result
        assert "first reason" in result

    @pytest.mark.asyncio
    async def test_status_single_reason_no_history_section(self):
        cp = _make_command_processor()
        cp.session.coordinator.session_state["goal"] = {
            "condition": "ship the feature",
            "turns_used": 1,
            "last_reason": "not yet",
            "cap": 20,
            "reasons": ["not yet"],
            "continuations": 1,
            "no_tool_turns": 0,
            "escalated": False,
        }
        result = await cp._handle_goal("")
        assert "Recent reasons:" not in result

    @pytest.mark.asyncio
    async def test_clear_goal(self):
        cp = _make_command_processor()
        cp.session.coordinator.session_state["goal"] = {
            "condition": "ship the feature",
            "turns_used": 1,
            "last_reason": None,
            "cap": 20,
            "reasons": [],
            "continuations": 0,
            "no_tool_turns": 0,
            "escalated": False,
        }
        result = await cp._handle_goal("clear")
        assert cp.session.coordinator.session_state["goal"] is None
        assert "Goal cleared: ship the feature" in result


# === @mention expansion (docs/GOAL_COMMAND.md -- snapshot semantics) ===
#
# Bug 1: @mention expansion in execute_single() used to run BEFORE /goal
# detection, so a /goal prompt containing an @mention no longer started
# with "/goal " once expanded (expansion prepends a <context_file ...>
# block ahead of the text) -- the goal was silently never set.
#
# Bug 2: the stored condition was never expanded, so the evaluator saw the
# literal "@file" token forever instead of the file's content. Fix:
# expand once, at set time (snapshot semantics) -- both in the interactive
# _handle_goal() and in execute_single()'s headless /goal handling.


async def _fake_expand_mentions(session, text: str) -> str:
    """Stand-in for process_runtime_mentions().

    Mirrors the real function's shape (see its docstring at
    amplifier_app_cli.main.process_runtime_mentions): prepends a
    <context_file> block ahead of the original text for any @mention it
    recognizes, and returns the text unchanged otherwise.
    """
    if "@note.md" in text:
        return (
            '<context_file paths="note.md">The number is 42.</context_file>\n\n' + text
        )
    return text


def _make_headless_session() -> MagicMock:
    """Minimal mock AmplifierSession for headless execute_single() tests."""
    session = MagicMock()
    session.session_id = "test-session"
    session.execute = AsyncMock(return_value="done")
    session.coordinator = MagicMock()
    session.coordinator.session_state = {}
    session.coordinator.get = MagicMock(return_value=None)
    session.coordinator.cancellation = MagicMock()
    session.coordinator.cancellation.is_cancelled = False
    return session


def _make_headless_initialized(session: MagicMock) -> MagicMock:
    """Minimal mock InitializedSession for headless execute_single() tests."""
    initialized = MagicMock()
    initialized.session = session
    initialized.session_id = "test-session"
    initialized.cleanup = AsyncMock()
    return initialized


class TestHeadlessGoalMentionExpansion:
    """Headless /goal + @mention regressions (execute_single())."""

    @pytest.mark.asyncio
    async def test_goal_with_mention_sets_goal_state(self, tmp_path: Path):
        """Regression guard for Bug 1: a /goal prompt containing an
        @mention must still set session_state["goal"]. This fails against
        the pre-fix code, where @mention expansion ran before /goal
        detection and broke the startswith("/goal ") check."""
        from amplifier_app_cli.main import execute_single

        session = _make_headless_session()
        initialized = _make_headless_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=_fake_expand_mentions),
            ),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="/goal --max-turns 1 state the number found in @note.md",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        goal = session.coordinator.session_state.get("goal")
        assert goal is not None, (
            "Goal was not set -- /goal detection likely ran AFTER @mention "
            "expansion (Bug 1 regression)."
        )
        assert goal["cap"] == 1

    @pytest.mark.asyncio
    async def test_goal_condition_is_expanded_not_literal(self, tmp_path: Path):
        """Bug 2: the stored condition must contain the expanded file
        content, not just the literal "@note.md" mention token."""
        from amplifier_app_cli.main import execute_single

        session = _make_headless_session()
        initialized = _make_headless_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=_fake_expand_mentions),
            ),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            await execute_single(
                prompt="/goal state the number found in @note.md",
                config={},
                search_paths=[tmp_path],
                verbose=False,
                output_format="text",
                bundle_name="test-bundle",
            )

        goal = session.coordinator.session_state.get("goal")
        assert goal is not None
        assert "The number is 42." in goal["condition"]

    @pytest.mark.asyncio
    async def test_bad_max_turns_with_mention_still_fails_loud(self, tmp_path: Path):
        """A malformed --max-turns value must still exit loud (and set no
        goal) even when the condition also contains an @mention --
        --max-turns parsing happens before expansion and must not be
        masked by it."""
        from amplifier_app_cli.main import execute_single

        session = _make_headless_session()
        initialized = _make_headless_initialized(session)

        with (
            patch(
                f"{_MODULE}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MODULE}.SessionStore") as MockStore,
            patch(f"{_MODULE}.console"),
            patch(
                f"{_MODULE}.process_runtime_mentions",
                new=AsyncMock(side_effect=_fake_expand_mentions),
            ),
        ):
            store_instance = MockStore.return_value
            store_instance.get_metadata.return_value = {}
            store_instance.save.return_value = None

            with pytest.raises(SystemExit) as exc_info:
                await execute_single(
                    prompt=(
                        f"/goal {_GOAL_MAX_TURNS_FLAG} abc state the number "
                        "found in @note.md"
                    ),
                    config={},
                    search_paths=[tmp_path],
                    verbose=False,
                    output_format="text",
                    bundle_name="test-bundle",
                )

        assert exc_info.value.code == 1
        assert session.coordinator.session_state.get("goal") is None


class TestInteractiveHandleGoalMentionExpansion:
    """Interactive CommandProcessor._handle_goal() + @mention (Bug 2)."""

    @pytest.mark.asyncio
    async def test_handle_goal_with_mention_expands_condition(self):
        """The condition stored by the interactive /goal handler must be
        expanded, not the literal @mention token."""
        cp = _make_command_processor()

        with patch(
            f"{_MODULE}.process_runtime_mentions",
            new=AsyncMock(side_effect=_fake_expand_mentions),
        ):
            await cp._handle_goal("state the number found in @note.md")

        goal = cp.session.coordinator.session_state["goal"]
        assert "The number is 42." in goal["condition"]

    @pytest.mark.asyncio
    async def test_handle_goal_bad_max_turns_with_mention_not_set(self):
        """A malformed --max-turns value must still fail loud (no goal
        set) even when the condition contains an @mention."""
        cp = _make_command_processor()

        with patch(
            f"{_MODULE}.process_runtime_mentions",
            new=AsyncMock(side_effect=_fake_expand_mentions),
        ):
            result = await cp._handle_goal(
                f"{_GOAL_MAX_TURNS_FLAG} abc state the number found in @note.md"
            )

        assert cp.session.coordinator.session_state.get("goal") is None
        assert "Goal not set" in result
