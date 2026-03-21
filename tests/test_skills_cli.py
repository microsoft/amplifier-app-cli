"""Tests for skill CLI integration: process_input shortcuts and _format_help() skills section.

Tests cover:
1. TestProcessInputSkillShortcuts (5 tests):
   - skill shortcut recognized (/simplify → load_skill)
   - with arguments (/simplify focus on memory → arguments captured)
   - /skill command parses name (/skill simplify → skill_name='simplify')
   - /skills command (/skills → list_skills action)
   - unknown still works (/foobar → unknown_command)

2. TestFormatHelpSkillsSection (3 tests):
   - help includes skill commands section with /simplify /batch /debug
   - help without skills has no section
   - help includes /skills and /skill base commands
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
# Helper factories
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


def _make_skills_discovery():
    """Create a mock skills discovery with 4 skills and 3 shortcuts.

    Skills: batch, debug, python-testing, simplify
    Shortcuts: simplify, batch, debug
    """
    mock_discovery = MagicMock()

    mock_discovery.list_skills.return_value = [
        ("batch", "Batch processing skill"),
        ("debug", "Debug code issues"),
        ("python-testing", "Python testing skill"),
        ("simplify", "Simplify complex code"),
    ]

    mock_discovery.get_shortcuts.return_value = {
        "simplify": {"name": "simplify", "description": "Simplify complex code"},
        "batch": {"name": "batch", "description": "Batch processing skill"},
        "debug": {"name": "debug", "description": "Debug code issues"},
    }

    return mock_discovery


# ===========================================================================
# TestProcessInputSkillShortcuts
# ===========================================================================


class TestProcessInputSkillShortcuts:
    """Tests that process_input handles skill shortcuts correctly."""

    def setup_method(self):
        self.skills_discovery = _make_skills_discovery()
        self.cp = _make_command_processor(skills_discovery=self.skills_discovery)

    def test_skill_shortcut_recognized(self):
        """/simplify should be recognized as a skill shortcut → load_skill action."""
        action, _data = self.cp.process_input("/simplify")
        assert action == "load_skill"

    def test_skill_shortcut_with_arguments(self):
        """/simplify focus on memory should capture 'focus on memory' as arguments."""
        action, data = self.cp.process_input("/simplify focus on memory")
        assert action == "load_skill"
        assert data["arguments"] == "focus on memory"
        assert data["skill_name"] == "simplify"

    def test_skill_command_parses_name(self):
        """/skill simplify should parse skill_name='simplify' and empty arguments."""
        action, data = self.cp.process_input("/skill simplify")
        assert action == "load_skill"
        assert data["skill_name"] == "simplify"
        assert data["arguments"] == ""

    def test_skills_command(self):
        """/skills should route to list_skills action."""
        action, _data = self.cp.process_input("/skills")
        assert action == "list_skills"

    def test_unknown_command_still_works(self):
        """/foobar (not a skill shortcut) should return unknown_command action."""
        action, data = self.cp.process_input("/foobar")
        assert action == "unknown_command"
        assert data["command"] == "/foobar"


# ===========================================================================
# TestFormatHelpSkillsSection
# ===========================================================================


class TestFormatHelpSkillsSection:
    """Tests that _format_help() includes a Skill Commands section."""

    def test_help_includes_skill_commands_section(self):
        """When skills are available, help should include 'Skill Commands:' section
        listing /simplify, /batch, /debug shortcuts."""
        skills_discovery = _make_skills_discovery()
        cp = _make_command_processor(skills_discovery=skills_discovery)

        help_text = cp._format_help()

        assert "Skill Commands:" in help_text
        assert "/simplify" in help_text
        assert "/batch" in help_text
        assert "/debug" in help_text

    def test_help_without_skills_has_no_section(self):
        """When no skills_discovery is available, help should NOT include
        'Skill Commands:' section."""
        cp = _make_command_processor()  # No skills_discovery

        help_text = cp._format_help()

        assert "Skill Commands:" not in help_text

    def test_help_includes_skill_base_commands(self):
        """Help should always include /skills and /skill base commands."""
        cp = _make_command_processor()  # No skills_discovery needed for base commands

        help_text = cp._format_help()

        assert "/skills" in help_text
        assert "/skill" in help_text
