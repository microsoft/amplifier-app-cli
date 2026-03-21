"""Tests for process_input() skill shortcut handling.

Tests cover:
1. /shortcut routes to ('load_skill', {'skill_name': shortcut_name, ...})
2. /shortcut with args includes arguments in the response
3. /skill <name> [args] parses skill_name and arguments
4. Unknown commands still return 'unknown_command'
5. Mode shortcuts still work as before
"""

import pytest
from unittest.mock import MagicMock

from amplifier_app_cli.main import CommandProcessor


# ---------------------------------------------------------------------------
# Fixture - reset class-level SKILL_SHORTCUTS between tests to prevent
# state leaking from one test into another via the shared class dict.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_skill_shortcuts():
    """Clear SKILL_SHORTCUTS before and after every test in this module."""
    CommandProcessor.SKILL_SHORTCUTS.clear()
    yield
    CommandProcessor.SKILL_SHORTCUTS.clear()


# ---------------------------------------------------------------------------
# Helper - build a minimal CommandProcessor without a real session
# ---------------------------------------------------------------------------


def _make_command_processor(skills_discovery=None, mode_shortcuts=None):
    """Create a CommandProcessor with mocked session for unit testing."""
    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": None,
    }
    mock_session.coordinator.get_capability.return_value = None

    if mode_shortcuts is not None:
        mock_mode_discovery = MagicMock()
        mock_mode_discovery.get_shortcuts.return_value = mode_shortcuts
        mock_session.coordinator.session_state["mode_discovery"] = mock_mode_discovery

    if skills_discovery is not None:
        original_get_capability = mock_session.coordinator.get_capability
        def _get_capability(key):
            if key == "skills_discovery":
                return skills_discovery
            return original_get_capability(key)
        mock_session.coordinator.get_capability = _get_capability

    cp = CommandProcessor(mock_session, "test-bundle")
    return cp


def _make_cp_with_skill_shortcut(shortcut_name="simplify"):
    """Create a CommandProcessor with a specific skill shortcut populated."""
    mock_discovery = MagicMock()
    mock_discovery.get_shortcuts.return_value = {
        shortcut_name: {"name": shortcut_name, "description": f"{shortcut_name} skill"}
    }
    cp = _make_command_processor(skills_discovery=mock_discovery)
    # Ensure the shortcut is in SKILL_SHORTCUTS
    assert shortcut_name in CommandProcessor.SKILL_SHORTCUTS
    return cp


# ---------------------------------------------------------------------------
# 1. Skill shortcut routing: /simplify -> ('load_skill', {'skill_name': 'simplify', ...})
# ---------------------------------------------------------------------------


class TestSkillShortcutRouting:
    """Tests that skill shortcuts route to load_skill action."""

    def test_skill_shortcut_returns_load_skill_action(self):
        """/simplify should return action 'load_skill'."""
        cp = _make_cp_with_skill_shortcut("simplify")
        action, _data = cp.process_input("/simplify")
        assert action == "load_skill"

    def test_skill_shortcut_includes_skill_name(self):
        """/simplify should include skill_name='simplify' in data."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify")
        assert data["skill_name"] == "simplify"

    def test_skill_shortcut_includes_command(self):
        """/simplify should include the command in data."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify")
        assert data["command"] == "/simplify"

    def test_skill_shortcut_no_args_has_empty_arguments(self):
        """/simplify with no args should have arguments=''."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify")
        assert data["arguments"] == ""

    def test_skill_shortcut_returns_tuple(self):
        """/simplify should return a tuple (action, data)."""
        cp = _make_cp_with_skill_shortcut("simplify")
        result = cp.process_input("/simplify")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_different_skill_shortcut_name(self):
        """/refactor skill shortcut should return skill_name='refactor'."""
        cp = _make_cp_with_skill_shortcut("refactor")
        action, data = cp.process_input("/refactor")
        assert action == "load_skill"
        assert data["skill_name"] == "refactor"


# ---------------------------------------------------------------------------
# 2. Skill shortcut with arguments: /simplify focus on memory
# ---------------------------------------------------------------------------


class TestSkillShortcutWithArguments:
    """Tests that skill shortcuts include arguments in data."""

    def test_skill_shortcut_with_args_returns_load_skill(self):
        """/simplify focus on memory should return 'load_skill' action."""
        cp = _make_cp_with_skill_shortcut("simplify")
        action, _data = cp.process_input("/simplify focus on memory")
        assert action == "load_skill"

    def test_skill_shortcut_with_args_includes_arguments(self):
        """/simplify focus on memory should include arguments='focus on memory'."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify focus on memory")
        assert data["arguments"] == "focus on memory"

    def test_skill_shortcut_with_args_includes_skill_name(self):
        """/simplify focus on memory should include skill_name='simplify'."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify focus on memory")
        assert data["skill_name"] == "simplify"

    def test_skill_shortcut_with_args_includes_command(self):
        """/simplify focus on memory should include command='/simplify'."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify focus on memory")
        assert data["command"] == "/simplify"

    def test_skill_shortcut_with_multi_word_args(self):
        """/simplify make this code cleaner and faster should capture full args."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify make this code cleaner and faster")
        assert data["arguments"] == "make this code cleaner and faster"

    def test_skill_shortcut_args_are_stripped(self):
        """/simplify  extra spaces  should strip leading/trailing whitespace."""
        cp = _make_cp_with_skill_shortcut("simplify")
        _action, data = cp.process_input("/simplify  extra spaces  ")
        # args.strip() should produce clean arguments
        assert data["arguments"] == "extra spaces"


# ---------------------------------------------------------------------------
# 3. /skill <name> [args] command parsing
# ---------------------------------------------------------------------------


class TestSkillCommandParsing:
    """Tests that /skill command parses skill_name and arguments."""

    def test_skill_command_with_name_only(self):
        """/skill simplify should have skill_name='simplify' and arguments=''."""
        cp = _make_command_processor()
        action, data = cp.process_input("/skill simplify")
        assert action == "load_skill"
        assert data["skill_name"] == "simplify"
        assert data["arguments"] == ""

    def test_skill_command_with_name_and_args(self):
        """/skill simplify focus on memory should parse correctly."""
        cp = _make_command_processor()
        action, data = cp.process_input("/skill simplify focus on memory")
        assert action == "load_skill"
        assert data["skill_name"] == "simplify"
        assert data["arguments"] == "focus on memory"

    def test_skill_command_preserves_full_args_string(self):
        """/skill simplify x y z should have arguments='x y z'."""
        cp = _make_command_processor()
        _action, data = cp.process_input("/skill simplify x y z")
        assert data["arguments"] == "x y z"

    def test_skill_command_extracts_skill_name_as_first_word(self):
        """/skill command extracts first word as skill_name."""
        cp = _make_command_processor()
        _action, data = cp.process_input("/skill refactor please clean this up")
        assert data["skill_name"] == "refactor"
        assert data["arguments"] == "please clean this up"

    def test_skill_command_no_skill_name_empty_args(self):
        """/skill with no name should gracefully handle empty skill_name."""
        cp = _make_command_processor()
        action, data = cp.process_input("/skill")
        assert action == "load_skill"
        assert data.get("skill_name", "") == ""
        assert data.get("arguments", "") == ""


# ---------------------------------------------------------------------------
# 4. Unknown commands still return 'unknown_command'
# ---------------------------------------------------------------------------


class TestUnknownCommandStillWorks:
    """Tests that unknown commands still return 'unknown_command'."""

    def test_unknown_command_returns_unknown_command(self):
        """/foobar should return 'unknown_command' action."""
        cp = _make_command_processor()
        action, _data = cp.process_input("/foobar")
        assert action == "unknown_command"

    def test_unknown_command_includes_command_in_data(self):
        """/foobar should include 'command' in data."""
        cp = _make_command_processor()
        _action, data = cp.process_input("/foobar")
        assert data["command"] == "/foobar"

    def test_non_skill_shortcut_is_unknown(self):
        """A command that is not in SKILL_SHORTCUTS and not in COMMANDS should be unknown."""
        # Ensure 'notaskill' is not in SKILL_SHORTCUTS (reset_skill_shortcuts clears it, but be explicit)
        CommandProcessor.SKILL_SHORTCUTS.pop("notaskill", None)
        cp = _make_command_processor()
        action, _data = cp.process_input("/notaskill")
        assert action == "unknown_command"

    def test_skill_not_in_shortcuts_is_unknown_command(self):
        """A shortcut not registered in SKILL_SHORTCUTS remains unknown."""
        cp = _make_command_processor()  # No skills_discovery
        # With no skill shortcuts populated, this should be unknown
        action, _data = cp.process_input("/someunregisteredskill")
        assert action == "unknown_command"


# ---------------------------------------------------------------------------
# 5. Mode shortcuts still work as before
# ---------------------------------------------------------------------------


class TestModeShortcutsStillWork:
    """Tests that mode shortcuts are not affected by skill shortcut changes."""

    def test_mode_shortcut_still_returns_handle_mode(self):
        """/plan (mode shortcut) should still return 'handle_mode' action."""
        cp = _make_command_processor(mode_shortcuts={"plan": "Plan-focused mode"})
        action, _data = cp.process_input("/plan")
        assert action == "handle_mode"

    def test_mode_shortcut_with_trailing_text_still_works(self):
        """/plan do something should still set trailing_prompt."""
        cp = _make_command_processor(mode_shortcuts={"plan": "Plan-focused mode"})
        action, data = cp.process_input("/plan do something important")
        assert action == "handle_mode"
        assert data.get("trailing_prompt") == "do something important"

    def test_mode_shortcut_on_off_still_works(self):
        """/plan on should still work as mode control."""
        cp = _make_command_processor(mode_shortcuts={"plan": "Plan-focused mode"})
        action, data = cp.process_input("/plan on")
        assert action == "handle_mode"
        assert "on" in data["args"]

    def test_skill_shortcut_does_not_interfere_with_mode_shortcut(self):
        """A skill shortcut and mode shortcut with different names should coexist."""
        # Setup skill shortcut
        mock_skill_discovery = MagicMock()
        mock_skill_discovery.get_shortcuts.return_value = {
            "simplify": {"name": "simplify"}
        }

        # Setup mode shortcut
        mock_mode_discovery = MagicMock()
        mock_mode_discovery.get_shortcuts.return_value = {"plan": "Plan mode"}

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.session_state = {
            "active_mode": None,
            "mode_discovery": mock_mode_discovery,
        }
        original_get_capability = mock_session.coordinator.get_capability
        def _get_capability(key):
            if key == "skills_discovery":
                return mock_skill_discovery
            return original_get_capability(key)
        mock_session.coordinator.get_capability = _get_capability

        cp = CommandProcessor(mock_session, "test-bundle")

        # Mode shortcut still works
        action_mode, _ = cp.process_input("/plan")
        assert action_mode == "handle_mode"

        # Skill shortcut still works
        action_skill, data_skill = cp.process_input("/simplify")
        assert action_skill == "load_skill"
        assert data_skill["skill_name"] == "simplify"
