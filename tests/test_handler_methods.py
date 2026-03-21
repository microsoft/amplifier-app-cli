"""Tests for _list_skills, _load_skill handler methods and REPL loop integration.

Tests cover:
1. handle_command() dispatches list_skills and load_skill actions
2. _list_skills() returns formatted skill list with shortcuts section
3. _list_skills() returns error when skills_discovery not available
4. _load_skill() validates empty skill_name (returns usage)
5. _load_skill() returns error for unknown skill with available list
6. _load_skill() constructs synthetic prompt without args
7. _load_skill() constructs synthetic prompt with args
8. REPL loop executes skill load prompts through session.execute()
9. Error messages are printed as cyan text, not executed
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from amplifier_app_cli.main import CommandProcessor


# ---------------------------------------------------------------------------
# Helper - build a minimal CommandProcessor without a real session
# ---------------------------------------------------------------------------


def _make_command_processor(skills_discovery=None):
    """Create a CommandProcessor with mocked session for unit testing."""
    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": None,
    }
    mock_session.coordinator.get_capability.return_value = None

    if skills_discovery is not None:
        original_get_capability = mock_session.coordinator.get_capability
        def _get_capability(key):
            if key == "skills_discovery":
                return skills_discovery
            return original_get_capability(key)
        mock_session.coordinator.get_capability = _get_capability

    cp = CommandProcessor(mock_session, "test-bundle")
    return cp


def _make_mock_discovery(skills=None, shortcuts=None):
    """Create a mock skills discovery object."""
    mock_discovery = MagicMock()

    if skills is None:
        skills = [
            ("simplify", "Simplify complex code"),
            ("refactor", "Refactor code structure"),
        ]

    if shortcuts is None:
        shortcuts = {s[0]: {"name": s[0], "description": s[1]} for s in skills}

    mock_discovery.list_skills.return_value = skills
    mock_discovery.get_shortcuts.return_value = shortcuts

    return mock_discovery


# ---------------------------------------------------------------------------
# 1. handle_command() dispatches list_skills action
# ---------------------------------------------------------------------------


class TestHandleCommandListSkillsDispatch:
    """Tests that handle_command() dispatches list_skills action."""

    @pytest.mark.asyncio
    async def test_handle_command_dispatches_list_skills(self):
        """handle_command() should dispatch list_skills action to _list_skills()."""
        cp = _make_command_processor()
        result = await cp.handle_command("list_skills", {})
        # Should not be "Unhandled action"
        assert not result.startswith("Unhandled action:")

    @pytest.mark.asyncio
    async def test_handle_command_list_skills_calls_list_skills_method(self):
        """handle_command() with list_skills should call self._list_skills()."""
        cp = _make_command_processor()
        cp._list_skills = AsyncMock(return_value="Skills list result")
        result = await cp.handle_command("list_skills", {})
        cp._list_skills.assert_called_once()
        assert result == "Skills list result"

    @pytest.mark.asyncio
    async def test_handle_command_list_skills_before_unknown_command(self):
        """handle_command() should dispatch list_skills BEFORE the unknown_command check."""
        cp = _make_command_processor()
        result = await cp.handle_command("list_skills", {"command": "/skills"})
        # Should NOT return "Unhandled action" or treat it as unknown
        assert "Unhandled action" not in result
        assert "Unknown command" not in result


# ---------------------------------------------------------------------------
# 2. handle_command() dispatches load_skill action
# ---------------------------------------------------------------------------


class TestHandleCommandLoadSkillDispatch:
    """Tests that handle_command() dispatches load_skill action."""

    @pytest.mark.asyncio
    async def test_handle_command_dispatches_load_skill(self):
        """handle_command() should dispatch load_skill action to _load_skill()."""
        cp = _make_command_processor()
        result = await cp.handle_command(
            "load_skill", {"skill_name": "simplify", "arguments": ""}
        )
        # Should not be "Unhandled action"
        assert not result.startswith("Unhandled action:")

    @pytest.mark.asyncio
    async def test_handle_command_load_skill_calls_load_skill_method(self):
        """handle_command() with load_skill should call self._load_skill(skill_name, arguments)."""
        cp = _make_command_processor()
        cp._load_skill = AsyncMock(return_value=(True, "Skill prompt"))
        result = await cp.handle_command(
            "load_skill", {"skill_name": "simplify", "arguments": "focus on memory"}
        )
        cp._load_skill.assert_called_once_with("simplify", "focus on memory")
        assert result == "Skill prompt"

    @pytest.mark.asyncio
    async def test_handle_command_load_skill_passes_skill_name_and_arguments(self):
        """handle_command() should pass skill_name and arguments to _load_skill()."""
        cp = _make_command_processor()
        cp._load_skill = AsyncMock(return_value=(True, "Prompt result"))
        await cp.handle_command(
            "load_skill",
            {"skill_name": "refactor", "arguments": "please clean this up"},
        )
        cp._load_skill.assert_called_once_with("refactor", "please clean this up")

    @pytest.mark.asyncio
    async def test_handle_command_load_skill_before_unknown_command(self):
        """handle_command() should dispatch load_skill BEFORE the unknown_command check."""
        cp = _make_command_processor()
        # Empty skill_name - should return usage, not "Unhandled action"
        result = await cp.handle_command(
            "load_skill", {"skill_name": "", "arguments": ""}
        )
        assert "Unhandled action" not in result
        assert "Unknown command" not in result


# ---------------------------------------------------------------------------
# 3. _list_skills() returns formatted skill list with shortcuts section
# ---------------------------------------------------------------------------


class TestListSkillsFormatting:
    """Tests for _list_skills() formatting."""

    @pytest.mark.asyncio
    async def test_list_skills_returns_string(self):
        """_list_skills() should return a string."""
        mock_discovery = _make_mock_discovery()
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_list_skills_includes_skill_names(self):
        """_list_skills() should include skill names in output."""
        mock_discovery = _make_mock_discovery(
            skills=[("simplify", "Simplify complex code")]
        )
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        assert "simplify" in result

    @pytest.mark.asyncio
    async def test_list_skills_includes_skill_descriptions(self):
        """_list_skills() should include skill descriptions in output."""
        mock_discovery = _make_mock_discovery(
            skills=[("simplify", "Simplify complex code")]
        )
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        assert "Simplify complex code" in result

    @pytest.mark.asyncio
    async def test_list_skills_uses_20_char_name_padding(self):
        """_list_skills() should use 20-char padding for name column."""
        mock_discovery = _make_mock_discovery(
            skills=[("simplify", "Simplify complex code")]
        )
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        # Check that the name is padded to 20 chars (left-justified)
        # "simplify            " is 20 chars followed by description
        assert "simplify            " in result or f"{'simplify':<20}" in result

    @pytest.mark.asyncio
    async def test_list_skills_includes_shortcuts_section(self):
        """_list_skills() should include a shortcuts section."""
        mock_discovery = _make_mock_discovery(
            skills=[("simplify", "Simplify code")],
            shortcuts={
                "simplify": {"name": "simplify", "description": "Simplify code"}
            },
        )
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        # Should have a shortcuts section
        assert "shortcut" in result.lower() or "/simplify" in result

    @pytest.mark.asyncio
    async def test_list_skills_shortcuts_show_slash_prefix(self):
        """_list_skills() shortcuts section should show /name for each shortcut."""
        mock_discovery = _make_mock_discovery(
            skills=[("simplify", "Simplify code")],
            shortcuts={
                "simplify": {"name": "simplify", "description": "Simplify code"}
            },
        )
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        assert "/simplify" in result

    @pytest.mark.asyncio
    async def test_list_skills_includes_footer(self):
        """_list_skills() should include 'Use /skill <name> to load a skill.' footer."""
        mock_discovery = _make_mock_discovery()
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        assert "Use /skill" in result
        assert "to load a skill" in result

    @pytest.mark.asyncio
    async def test_list_skills_lists_multiple_skills(self):
        """_list_skills() should list all skills."""
        mock_discovery = _make_mock_discovery(
            skills=[("simplify", "Simplify code"), ("refactor", "Refactor code")]
        )
        cp = _make_command_processor(skills_discovery=mock_discovery)
        result = await cp._list_skills()
        assert "simplify" in result
        assert "refactor" in result


# ---------------------------------------------------------------------------
# 4. _list_skills() returns error when skills_discovery not available
# ---------------------------------------------------------------------------


class TestListSkillsNoDiscovery:
    """Tests that _list_skills() handles missing discovery gracefully."""

    @pytest.mark.asyncio
    async def test_list_skills_no_discovery_returns_error(self):
        """_list_skills() should return error message when skills_discovery not in session_state."""
        cp = _make_command_processor()  # No skills_discovery
        result = await cp._list_skills()
        # Should indicate skills system not available
        assert (
            "Skills system" in result
            or "not available" in result
            or "skill" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_list_skills_no_discovery_returns_string(self):
        """_list_skills() should return a string even when discovery is not available."""
        cp = _make_command_processor()  # No skills_discovery
        result = await cp._list_skills()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 5. _load_skill() validates empty skill_name
# ---------------------------------------------------------------------------


class TestLoadSkillValidation:
    """Tests for _load_skill() input validation."""

    @pytest.mark.asyncio
    async def test_load_skill_empty_name_returns_usage(self):
        """_load_skill() with empty skill_name should return usage message."""
        cp = _make_command_processor()
        is_prompt, text = await cp._load_skill("", "")
        assert is_prompt is False
        assert "Usage:" in text

    @pytest.mark.asyncio
    async def test_load_skill_empty_name_returns_string(self):
        """_load_skill() with empty skill_name should return a string."""
        cp = _make_command_processor()
        is_prompt, text = await cp._load_skill("", "")
        assert is_prompt is False
        assert isinstance(text, str)

    @pytest.mark.asyncio
    async def test_load_skill_empty_name_usage_mentions_skill(self):
        """_load_skill() usage message should mention /skill."""
        cp = _make_command_processor()
        is_prompt, text = await cp._load_skill("", "")
        assert is_prompt is False
        assert "/skill" in text


# ---------------------------------------------------------------------------
# 6. _load_skill() returns error for unknown skill
# ---------------------------------------------------------------------------


class TestLoadSkillUnknownSkill:
    """Tests that _load_skill() handles unknown skills."""

    @pytest.mark.asyncio
    async def test_load_skill_unknown_returns_error(self):
        """_load_skill() with unknown skill should return 'Unknown skill:' error."""
        mock_discovery = _make_mock_discovery(skills=[("simplify", "Simplify code")])
        mock_discovery.find.return_value = None  # Skill not found

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("nonexistent", "")
        assert is_prompt is False
        assert "Unknown skill:" in text

    @pytest.mark.asyncio
    async def test_load_skill_unknown_includes_available_list(self):
        """_load_skill() with unknown skill should include available skills list."""
        mock_discovery = _make_mock_discovery(skills=[("simplify", "Simplify code")])
        mock_discovery.find.return_value = None  # Skill not found

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("nonexistent", "")
        # Should mention available skills
        assert is_prompt is False
        assert "simplify" in text

    @pytest.mark.asyncio
    async def test_load_skill_unknown_no_discovery_returns_error(self):
        """_load_skill() with no discovery should return an appropriate error."""
        cp = _make_command_processor()  # No skills_discovery
        is_prompt, text = await cp._load_skill("simplify", "")
        # Should be some kind of error (Skills system not available)
        assert is_prompt is False
        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# 7. _load_skill() constructs synthetic prompt without args
# ---------------------------------------------------------------------------


class TestLoadSkillPromptConstruction:
    """Tests for _load_skill() synthetic prompt construction."""

    @pytest.mark.asyncio
    async def test_load_skill_constructs_prompt_without_args(self):
        """_load_skill() should construct 'Use the load_skill tool...' prompt when no args."""
        mock_discovery = _make_mock_discovery()
        mock_discovery.find.return_value = {
            "name": "simplify",
            "description": "Simplify code",
        }

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("simplify", "")
        assert is_prompt is True
        assert 'Use the load_skill tool to load the skill "simplify".' in text

    @pytest.mark.asyncio
    async def test_load_skill_prompt_without_args_exact_format(self):
        """_load_skill() without args should match exact format spec."""
        mock_discovery = _make_mock_discovery()
        mock_discovery.find.return_value = {
            "name": "simplify",
            "description": "Simplify code",
        }

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("simplify", "")
        assert is_prompt is True
        # Exact format: 'Use the load_skill tool to load the skill "<name>".'
        assert text == 'Use the load_skill tool to load the skill "simplify".'


# ---------------------------------------------------------------------------
# 8. _load_skill() constructs synthetic prompt with args
# ---------------------------------------------------------------------------


class TestLoadSkillPromptWithArgs:
    """Tests for _load_skill() prompt construction with arguments."""

    @pytest.mark.asyncio
    async def test_load_skill_constructs_prompt_with_args(self):
        """_load_skill() should include args in prompt when provided."""
        mock_discovery = _make_mock_discovery()
        mock_discovery.find.return_value = {
            "name": "simplify",
            "description": "Simplify code",
        }

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("simplify", "focus on memory usage")
        assert is_prompt is True
        assert "focus on memory usage" in text

    @pytest.mark.asyncio
    async def test_load_skill_prompt_with_args_exact_format(self):
        """_load_skill() with args should match exact format spec."""
        mock_discovery = _make_mock_discovery()
        mock_discovery.find.return_value = {
            "name": "simplify",
            "description": "Simplify code",
        }

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("simplify", "focus on memory usage")
        assert is_prompt is True
        # Exact format: 'Use the load_skill tool to load the skill "<name>". Additional context from the user: <args>'
        expected = 'Use the load_skill tool to load the skill "simplify". Additional context from the user: focus on memory usage'
        assert text == expected

    @pytest.mark.asyncio
    async def test_load_skill_with_different_args(self):
        """_load_skill() should include different args correctly."""
        mock_discovery = _make_mock_discovery()
        mock_discovery.find.return_value = {
            "name": "refactor",
            "description": "Refactor code",
        }

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("refactor", "please clean this up")
        assert is_prompt is True
        expected = 'Use the load_skill tool to load the skill "refactor". Additional context from the user: please clean this up'
        assert text == expected

    @pytest.mark.asyncio
    async def test_load_skill_with_empty_string_args_uses_no_args_format(self):
        """_load_skill() with empty string arguments should use the no-args format."""
        mock_discovery = _make_mock_discovery()
        mock_discovery.find.return_value = {
            "name": "simplify",
            "description": "Simplify code",
        }

        cp = _make_command_processor(skills_discovery=mock_discovery)
        is_prompt, text = await cp._load_skill("simplify", "")
        assert is_prompt is True
        # Empty args -> no-args format
        assert "Additional context" not in text
        assert text == 'Use the load_skill tool to load the skill "simplify".'
