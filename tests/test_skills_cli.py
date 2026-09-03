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

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from amplifier_app_cli.runtime import config
from helpers import _make_command_processor


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


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

    def test_packaged_amplifier_config_discovery_and_invocation(
        self, monkeypatch, tmp_path
    ):
        """Discover and invoke amplifier-config from only the packaged skills dir."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "isolated-home"))
        packaged_dir = Path(config.__file__).parent.parent / "data" / "skills"

        shortcuts = {}
        for skill_file in packaged_dir.glob("*/SKILL.md"):
            _, frontmatter, _ = skill_file.read_text().split("---", 2)
            metadata = yaml.safe_load(frontmatter)
            if metadata.get("user-invocable") is True:
                shortcuts[metadata["name"]] = {
                    "name": metadata["name"],
                    "description": metadata["description"],
                    "context": metadata.get("context"),
                }

        assert "amplifier-config" in shortcuts
        discovery = MagicMock()
        discovery.get_shortcuts.return_value = shortcuts
        processor = _make_command_processor(skills_discovery=discovery)

        action, data = processor.process_input(
            "/amplifier-config make coding agents use the coding provider"
        )

        assert action == "load_skill"
        assert data == {
            "skill_name": "amplifier-config",
            "arguments": "make coding agents use the coding provider",
            "command": "/amplifier-config",
        }


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

    def test_format_help_uses_skill_shortcuts_not_live_capability(self):
        """_format_help() should read from self.SKILL_SHORTCUTS, not get_capability().

        When SKILL_SHORTCUTS is populated but get_capability('skills_discovery')
        returns None, the 'Skill Commands:' section must still appear.
        This ensures _format_help() and process_input() use the same cached data.
        """
        # Create processor with no skills_discovery (get_capability returns None)
        cp = _make_command_processor()  # No skills_discovery

        # Manually inject into SKILL_SHORTCUTS (simulates cache already populated)
        cp.SKILL_SHORTCUTS["simplify"] = {
            "name": "simplify",
            "description": "Simplify complex code",
        }

        help_text = cp._format_help()

        # Must show skill from SKILL_SHORTCUTS even though get_capability returns None
        assert "Skill Commands:" in help_text
        assert "/simplify" in help_text
