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

import pytest
import yaml

from amplifier_app_cli.runtime import config
from amplifier_app_cli.ui.completion import (
    SlashCompletionEngine,
    build_completion_snapshot,
)
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


@pytest.fixture
def packaged_doctor():
    skill_file = (
        Path(config.__file__).parent.parent / "data" / "skills" / "doctor" / "SKILL.md"
    )
    _, frontmatter, body = skill_file.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(frontmatter), body


@pytest.fixture
def doctor_discovery(packaged_doctor):
    """Mock only discovery metadata; this is not a real SkillsDiscovery test."""
    metadata, _ = packaged_doctor
    name = metadata["name"]
    entry = {
        "name": name,
        "description": metadata["description"],
        "context": metadata.get("context"),
    }
    discovery = MagicMock()
    discovery.get_shortcuts.return_value = (
        {name: entry} if metadata.get("user-invocable") is True else {}
    )
    discovery.find.side_effect = lambda selected: entry if selected == name else None
    discovery.list_skills.return_value = [(name, metadata["description"])]
    discovery.get_completion_catalog.return_value = [
        {**entry, "argument_hint": metadata.get("argument-hint"), "arguments": []}
    ]
    return discovery


class TestPackagedDoctorSkill:
    """Packaged asset and real CLI routing/prompt construction, not model dispatch."""

    def test_manual_inline_metadata(self, packaged_doctor):
        metadata, _ = packaged_doctor
        assert metadata["name"] == "doctor"
        assert metadata["user-invocable"] is True
        assert metadata["disable-model-invocation"] is True
        assert 0 < len(metadata["description"]) <= 400
        assert metadata["argument-hint"] == "[symptom or diagnostic focus]"
        assert not {"context", "agent", "allowed-tools"} & metadata.keys()

    def test_request_and_existing_agent_workflow(self, packaged_doctor):
        _, body = packaged_doctor
        assert "$ARGUMENTS" in body
        assert "An empty request means passive CLI inspection." in body
        assert "Delegate to `app-cli:cli-doctor`." in body
        assert "loads `amplifier-config` when needed." in body
        assert (
            "If delegation or the agent is unavailable, report that limitation." in body
        )
        assert "Diagnosis does not authorize changes or live probes." in body
        assert (
            "Health not assessed; no repair verdict\nfrom passive collection." in body
        )
        assert "Do not add a health or repair verdict of your own." in body

    def test_existing_config_includes_packaged_directory(self, monkeypatch):
        monkeypatch.delenv("AMPLIFIER_SKILLS_DIR", raising=False)
        tools = [{"module": "tool-skills", "config": {"skills": ["configured-skills"]}}]
        result = config._ensure_default_skills_dirs(tools)
        paths = result[0]["config"]["skills"]
        packaged_dir = Path(config.__file__).parent.parent / "data" / "skills"
        assert paths[0] == "configured-skills"
        assert Path(paths[-1]) == packaged_dir
        assert (packaged_dir / "doctor" / "SKILL.md").is_file()
        assert tools[0]["config"]["skills"] == ["configured-skills"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("line", "arguments"),
        [
            ("/doctor", ""),
            (
                "/doctor inspect my provider configuration",
                "inspect my provider configuration",
            ),
            ("/skill doctor", ""),
            ("/skill doctor inspect settings", "inspect settings"),
            (
                '/doctor   inspect "provider" {scope} and $(literal)\nkeep  spacing  ',
                'inspect "provider" {scope} and $(literal)\nkeep  spacing',
            ),
        ],
    )
    async def test_routes_and_constructs_load_prompt(
        self, doctor_discovery, line, arguments
    ):
        processor = _make_command_processor(skills_discovery=doctor_discovery)
        action, data = processor.process_input(line)
        assert action == "load_skill"
        assert data["skill_name"] == "doctor"
        assert data["arguments"] == arguments
        assert data["command"] == line.split()[0]

        is_prompt, prompt = await processor._load_skill(
            data["skill_name"], data["arguments"]
        )
        doctor_discovery.find.assert_called_once_with("doctor")
        assert is_prompt is True
        if arguments:
            assert 'load_skill(skill_name="doctor", arguments=...)' in prompt
            assert prompt.endswith(f"The user's input is: {arguments}")
        else:
            assert prompt == 'Use the load_skill tool to load the skill "doctor".'

    def test_help_and_completion_use_metadata(self, doctor_discovery):
        processor = _make_command_processor(skills_discovery=doctor_discovery)
        assert "/doctor" in processor._format_help()
        engine = SlashCompletionEngine(build_completion_snapshot(processor))
        candidates = engine.complete("/doc")
        assert [candidate.value for candidate in candidates] == ["/doctor"]
        assert "[symptom or diagnostic focus]" in candidates[0].description
        assert [candidate.value for candidate in engine.complete("/skill doc")] == [
            "doctor"
        ]
        assert engine.complete("/doctor ") == []
        doctor_discovery.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_without_discovery_does_not_inherit_doctor(
        self, doctor_discovery
    ):
        _make_command_processor(skills_discovery=doctor_discovery)
        processor = _make_command_processor()
        assert processor.process_input("/doctor")[0] == "unknown_command"
        is_prompt, message = await processor._load_skill("doctor", "")
        assert is_prompt is False
        assert "Skills system not available." in message

    def test_builtin_and_mode_precedence_unchanged(self, doctor_discovery):
        processor = _make_command_processor(
            skills_discovery=doctor_discovery, mode_shortcuts={"doctor": "plan"}
        )
        assert processor.process_input("/help")[0] == "show_help"
        assert processor.process_input("/doctor") == (
            "handle_mode",
            {"args": "plan", "command": "/doctor"},
        )
        assert processor.process_input("/skill doctor")[1]["skill_name"] == "doctor"
