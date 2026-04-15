"""Tests for mode slash command trailing prompt feature.

Verifies that text after a mode slash command (e.g., /brainstorm my great idea)
is captured and queued for execution as the first prompt in that mode, rather
than being silently discarded.
"""

from unittest.mock import MagicMock



# ---------------------------------------------------------------------------
# Helpers – build a minimal CommandProcessor without a real session
# ---------------------------------------------------------------------------


def _make_command_processor(active_mode=None, mode_shortcuts=None):
    """Create a CommandProcessor with mocked session for unit testing."""
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()
    mock_session.coordinator.session_state = {
        "active_mode": active_mode,
        "mode_discovery": MagicMock(),
        "mode_hooks": MagicMock(),
    }

    # Mock mode_discovery to return shortcuts
    if mode_shortcuts is None:
        mode_shortcuts = {"brainstorm": "brainstorm", "plan": "plan"}
    mock_session.coordinator.session_state[
        "mode_discovery"
    ].get_shortcuts.return_value = mode_shortcuts

    # Mock mode_discovery.find() to return a mode definition for known modes
    def mock_find(name):
        mock_mode = MagicMock()
        mock_mode.name = name
        mock_mode.description = f"Test {name} mode"
        mock_mode.shortcut = name
        return mock_mode

    mock_session.coordinator.session_state[
        "mode_discovery"
    ].find.side_effect = mock_find
    mock_session.coordinator.session_state["mode_discovery"].list_modes.return_value = [
        ("brainstorm", "Design refinement"),
        ("plan", "Implementation planning"),
    ]

    cp = CommandProcessor(mock_session, "test-bundle")
    return cp


# ===========================================================================
# _split_mode_trailing – the helper that parses /mode arguments
# ===========================================================================


class TestSplitModeTrailing:
    """Unit tests for CommandProcessor._split_mode_trailing."""

    def setup_method(self):
        self.cp = _make_command_processor()

    # --- Basic cases ---

    def test_mode_name_only(self):
        assert self.cp._split_mode_trailing("brainstorm") == ("brainstorm", None)

    def test_mode_name_explicit_on(self):
        assert self.cp._split_mode_trailing("brainstorm on") == ("brainstorm on", None)

    def test_mode_name_explicit_off(self):
        assert self.cp._split_mode_trailing("brainstorm off") == (
            "brainstorm off",
            None,
        )

    def test_empty_string(self):
        assert self.cp._split_mode_trailing("") == ("", None)

    def test_whitespace_only(self):
        assert self.cp._split_mode_trailing("   ") == ("   ", None)

    # --- Trailing prompt cases ---

    def test_trailing_prompt_basic(self):
        mode_args, trailing = self.cp._split_mode_trailing("brainstorm my great idea")
        assert mode_args == "brainstorm on"
        assert trailing == "my great idea"

    def test_trailing_prompt_long(self):
        mode_args, trailing = self.cp._split_mode_trailing(
            "brainstorm design a new caching layer for the API"
        )
        assert mode_args == "brainstorm on"
        assert trailing == "design a new caching layer for the API"

    # --- CRITICAL: partial "on"/"off" must NOT be consumed ---

    def test_on_that_note_not_consumed(self):
        """'/mode brainstorm on that note, do X' must keep 'on' in the prompt."""
        mode_args, trailing = self.cp._split_mode_trailing(
            "brainstorm on that note, let's do X"
        )
        assert mode_args == "brainstorm on"
        assert trailing == "on that note, let's do X"

    def test_on_second_thought_not_consumed(self):
        mode_args, trailing = self.cp._split_mode_trailing(
            "brainstorm on second thought, let's try Y"
        )
        assert mode_args == "brainstorm on"
        assert trailing == "on second thought, let's try Y"

    def test_off_topic_not_consumed(self):
        """'/mode brainstorm off topic but important' must keep 'off' in prompt."""
        mode_args, trailing = self.cp._split_mode_trailing(
            "brainstorm off topic but important"
        )
        assert mode_args == "brainstorm on"
        assert trailing == "off topic but important"

    # --- Deactivation ---

    def test_bare_off(self):
        assert self.cp._split_mode_trailing("off") == ("off", None)

    def test_off_with_trailing_not_treated_as_deactivation(self):
        """'/mode off my idea' — 'off' followed by text is not a bare deactivation."""
        mode_args, trailing = self.cp._split_mode_trailing("off my idea")
        # "off" is first_word, "my idea" is rest — but rest is non-empty
        # so it should NOT be treated as deactivation
        assert mode_args == "off on"
        assert trailing == "my idea"

    # --- Case insensitivity ---

    def test_on_case_insensitive(self):
        assert self.cp._split_mode_trailing("brainstorm ON") == ("brainstorm ON", None)

    def test_off_case_insensitive(self):
        assert self.cp._split_mode_trailing("brainstorm OFF") == (
            "brainstorm OFF",
            None,
        )


# ===========================================================================
# process_input – full input parsing including shortcut path
# ===========================================================================


class TestProcessInputModeShortcuts:
    """Test that process_input handles mode shortcuts with trailing text."""

    def setup_method(self):
        self.cp = _make_command_processor()

    # --- Shortcut with no args (existing toggle behavior) ---

    def test_shortcut_toggle(self):
        action, data = self.cp.process_input("/brainstorm")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm"
        assert "trailing_prompt" not in data

    # --- Shortcut with trailing text ---

    def test_shortcut_with_trailing_prompt(self):
        action, data = self.cp.process_input("/brainstorm my great idea")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert data["trailing_prompt"] == "my great idea"

    def test_shortcut_preserves_full_trailing_text(self):
        action, data = self.cp.process_input(
            "/brainstorm design a caching layer for the API"
        )
        assert action == "handle_mode"
        assert data["trailing_prompt"] == "design a caching layer for the API"

    # --- Shortcut with exact "on"/"off" ---

    def test_shortcut_explicit_on(self):
        action, data = self.cp.process_input("/brainstorm on")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert "trailing_prompt" not in data

    def test_shortcut_explicit_off(self):
        action, data = self.cp.process_input("/brainstorm off")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm off"
        assert "trailing_prompt" not in data

    # --- CRITICAL: shortcut with "on/off" as part of natural language ---

    def test_shortcut_on_that_note(self):
        """'/brainstorm on that note, do X' — 'on' is natural language, not control."""
        action, data = self.cp.process_input("/brainstorm on that note, let's do X")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert data["trailing_prompt"] == "on that note, let's do X"

    def test_shortcut_off_topic(self):
        """'/brainstorm off topic but important' — 'off' is natural language."""
        action, data = self.cp.process_input("/brainstorm off topic but important")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert data["trailing_prompt"] == "off topic but important"

    # --- /mode command path ---

    def test_mode_command_with_trailing(self):
        action, data = self.cp.process_input("/mode brainstorm my great idea")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert data["trailing_prompt"] == "my great idea"

    def test_mode_command_no_trailing(self):
        action, data = self.cp.process_input("/mode brainstorm")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm"
        assert "trailing_prompt" not in data

    def test_mode_command_explicit_on(self):
        action, data = self.cp.process_input("/mode brainstorm on")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert "trailing_prompt" not in data

    def test_mode_command_on_that_note(self):
        """'/mode brainstorm on that note' — full phrase preserved."""
        action, data = self.cp.process_input("/mode brainstorm on that note, do X")
        assert action == "handle_mode"
        assert data["args"] == "brainstorm on"
        assert data["trailing_prompt"] == "on that note, do X"

    # --- Regular prompts unaffected ---

    def test_regular_prompt_unchanged(self):
        action, data = self.cp.process_input("just a normal message")
        assert action == "prompt"
        assert data["text"] == "just a normal message"

    # --- Other commands unaffected ---

    def test_help_command_unchanged(self):
        action, data = self.cp.process_input("/help")
        assert action == "show_help"

    def test_modes_list_unchanged(self):
        action, data = self.cp.process_input("/modes")
        assert action == "list_modes"
