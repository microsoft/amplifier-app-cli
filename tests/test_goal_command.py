"""Tests for the /goal command: unlimited-by-default turn cap (deliberate --
see docs/decisions/ADR-0005-goal-unlimited-by-default.md), the --max-turns
opt-in cap, state shape, and status display (docs/GOAL_COMMAND.md).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.main import (
    _GOAL_MAX_TURNS_FLAG,
    _parse_goal_max_turns,
)
from helpers import _make_command_processor

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
    async def test_custom_cap_applied(self):
        cp = _make_command_processor()
        result = await cp._handle_goal(f"{_GOAL_MAX_TURNS_FLAG} 3 do the thing")
        goal = cp.session.coordinator.session_state["goal"]
        assert goal["cap"] == 3
        assert "max 3 turns" in result

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
