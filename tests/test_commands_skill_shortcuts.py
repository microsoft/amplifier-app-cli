"""Tests for CommandProcessor COMMANDS + SKILL_SHORTCUTS additions.

Tests cover:
1. /skills and /skill entries in COMMANDS dict
2. SKILL_SHORTCUTS class variable
3. _populate_skill_shortcuts() method reads from skills_discovery
4. __init__() calls _populate_skill_shortcuts()
"""

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helper - build a minimal CommandProcessor without a real session
# ---------------------------------------------------------------------------


def _make_command_processor(skills_discovery=None, mode_shortcuts=None):
    """Create a CommandProcessor with mocked session for unit testing."""
    from amplifier_app_cli.main import CommandProcessor

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


# ---------------------------------------------------------------------------
# 1. /skills and /skill appear in COMMANDS dict
# ---------------------------------------------------------------------------


class TestSkillCommandsExist:
    """Tests that /skills and /skill are in the COMMANDS dict."""

    def test_skills_command_in_commands(self):
        """/skills should be in COMMANDS dict."""
        from amplifier_app_cli.main import CommandProcessor

        assert "/skills" in CommandProcessor.COMMANDS

    def test_skill_command_in_commands(self):
        """/skill should be in COMMANDS dict."""
        from amplifier_app_cli.main import CommandProcessor

        assert "/skill" in CommandProcessor.COMMANDS

    def test_skills_command_action(self):
        """/skills should have action='list_skills'."""
        from amplifier_app_cli.main import CommandProcessor

        assert CommandProcessor.COMMANDS["/skills"]["action"] == "list_skills"

    def test_skill_command_action(self):
        """/skill should have action='load_skill'."""
        from amplifier_app_cli.main import CommandProcessor

        assert CommandProcessor.COMMANDS["/skill"]["action"] == "load_skill"

    def test_skills_command_description(self):
        """/skills should have a description."""
        from amplifier_app_cli.main import CommandProcessor

        assert CommandProcessor.COMMANDS["/skills"]["description"]

    def test_skill_command_description(self):
        """/skill should have a description."""
        from amplifier_app_cli.main import CommandProcessor

        assert CommandProcessor.COMMANDS["/skill"]["description"]

    def test_skills_command_description_mentions_list(self):
        """/skills description should mention listing skills."""
        from amplifier_app_cli.main import CommandProcessor

        desc = CommandProcessor.COMMANDS["/skills"]["description"].lower()
        assert "list" in desc and "skill" in desc

    def test_skill_command_description_mentions_load(self):
        """/skill description should mention loading skill."""
        from amplifier_app_cli.main import CommandProcessor

        desc = CommandProcessor.COMMANDS["/skill"]["description"].lower()
        assert "load" in desc and "skill" in desc

    def test_skills_command_is_after_fork(self):
        """/skills and /skill should appear after /fork in COMMANDS dict."""
        from amplifier_app_cli.main import CommandProcessor

        keys = list(CommandProcessor.COMMANDS.keys())
        fork_idx = keys.index("/fork")
        skills_idx = keys.index("/skills")
        skill_idx = keys.index("/skill")

        assert skills_idx > fork_idx
        assert skill_idx > fork_idx


# ---------------------------------------------------------------------------
# 2. SKILL_SHORTCUTS class variable exists
# ---------------------------------------------------------------------------


class TestSkillShortcutsClassVariable:
    """Tests that SKILL_SHORTCUTS class variable exists."""

    def test_skill_shortcuts_class_variable_exists(self):
        """SKILL_SHORTCUTS class variable should exist on CommandProcessor."""
        from amplifier_app_cli.main import CommandProcessor

        assert hasattr(CommandProcessor, "SKILL_SHORTCUTS")

    def test_skill_shortcuts_is_dict(self):
        """SKILL_SHORTCUTS should be a dict."""
        from amplifier_app_cli.main import CommandProcessor

        assert isinstance(CommandProcessor.SKILL_SHORTCUTS, dict)

    def test_skill_shortcuts_initially_empty(self):
        """SKILL_SHORTCUTS should start as empty dict."""
        from amplifier_app_cli.main import CommandProcessor

        # Reset it in case previous tests have populated it
        CommandProcessor.SKILL_SHORTCUTS = {}
        assert CommandProcessor.SKILL_SHORTCUTS == {}


# ---------------------------------------------------------------------------
# 3. _populate_skill_shortcuts() reads from session_state['skills_discovery']
# ---------------------------------------------------------------------------


class TestPopulateSkillShortcuts:
    """Tests for _populate_skill_shortcuts() method."""

    def test_populate_skill_shortcuts_method_exists(self):
        """_populate_skill_shortcuts() method should exist."""
        from amplifier_app_cli.main import CommandProcessor

        assert hasattr(CommandProcessor, "_populate_skill_shortcuts")
        assert callable(CommandProcessor._populate_skill_shortcuts)

    def test_populate_skill_shortcuts_no_discovery(self):
        """With no skills_discovery in session_state, should not raise."""
        cp = _make_command_processor()  # no skills_discovery
        # Should not raise
        cp._populate_skill_shortcuts()

    def test_populate_skill_shortcuts_reads_discovery(self):
        """With skills_discovery in session_state, should call get_shortcuts()."""
        mock_discovery = MagicMock()
        mock_discovery.get_shortcuts.return_value = {"simplify": {"name": "simplify"}}

        _make_command_processor(skills_discovery=mock_discovery)
        # SKILL_SHORTCUTS should have been populated during __init__
        mock_discovery.get_shortcuts.assert_called()

    def test_populate_skill_shortcuts_updates_class_shortcuts(self):
        """Should update SKILL_SHORTCUTS with shortcuts from discovery."""
        from amplifier_app_cli.main import CommandProcessor

        # Reset SKILL_SHORTCUTS
        CommandProcessor.SKILL_SHORTCUTS = {}

        mock_discovery = MagicMock()
        shortcuts = {"simplify": {"name": "simplify", "description": "Simplify code"}}
        mock_discovery.get_shortcuts.return_value = shortcuts

        _make_command_processor(skills_discovery=mock_discovery)

        assert "simplify" in CommandProcessor.SKILL_SHORTCUTS

    def test_populate_skill_shortcuts_no_get_shortcuts_attribute(self):
        """If discovery has no get_shortcuts(), should not raise."""

        class SimpleDiscovery:
            pass

        _make_command_processor(skills_discovery=SimpleDiscovery())
        # Should not raise


# ---------------------------------------------------------------------------
# 4. __init__() calls _populate_skill_shortcuts()
# ---------------------------------------------------------------------------


class TestInitCallsPopulateSkillShortcuts:
    """Tests that __init__() calls _populate_skill_shortcuts()."""

    def test_init_calls_populate_skill_shortcuts(self):
        """__init__() should call _populate_skill_shortcuts()."""
        from amplifier_app_cli.main import CommandProcessor

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.session_state = {"active_mode": None}

        # Track if _populate_skill_shortcuts was called
        call_tracker = []

        original_method = CommandProcessor._populate_skill_shortcuts

        def tracking_method(self):
            call_tracker.append(True)
            return original_method(self)

        CommandProcessor._populate_skill_shortcuts = tracking_method
        try:
            CommandProcessor(mock_session, "test-bundle")
            assert len(call_tracker) > 0, "_populate_skill_shortcuts was not called"
        finally:
            CommandProcessor._populate_skill_shortcuts = original_method

    def test_init_calls_both_populate_methods(self):
        """__init__() should call both _populate_mode_shortcuts() and _populate_skill_shortcuts()."""
        from amplifier_app_cli.main import CommandProcessor

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.session_state = {"active_mode": None}

        mode_call_tracker = []
        skill_call_tracker = []

        original_mode_method = CommandProcessor._populate_mode_shortcuts
        original_skill_method = CommandProcessor._populate_skill_shortcuts

        def tracking_mode_method(self):
            mode_call_tracker.append(True)
            return original_mode_method(self)

        def tracking_skill_method(self):
            skill_call_tracker.append(True)
            return original_skill_method(self)

        CommandProcessor._populate_mode_shortcuts = tracking_mode_method
        CommandProcessor._populate_skill_shortcuts = tracking_skill_method
        try:
            CommandProcessor(mock_session, "test-bundle")
            assert len(mode_call_tracker) > 0, "_populate_mode_shortcuts was not called"
            assert len(skill_call_tracker) > 0, "_populate_skill_shortcuts was not called"
        finally:
            CommandProcessor._populate_mode_shortcuts = original_mode_method
            CommandProcessor._populate_skill_shortcuts = original_skill_method


# ---------------------------------------------------------------------------
# 5. Pattern parity with MODE_SHORTCUTS/_populate_mode_shortcuts
# ---------------------------------------------------------------------------


class TestPatternParity:
    """Tests that SKILL_SHORTCUTS/populate follows the same pattern as MODE_SHORTCUTS."""

    def test_skill_shortcuts_comes_after_mode_shortcuts_in_source(self):
        """SKILL_SHORTCUTS should appear after MODE_SHORTCUTS in source code."""
        import inspect

        from amplifier_app_cli.main import CommandProcessor

        source = inspect.getsource(CommandProcessor)

        mode_shortcuts_pos = source.find("MODE_SHORTCUTS")
        skill_shortcuts_pos = source.find("SKILL_SHORTCUTS")

        assert mode_shortcuts_pos >= 0, "MODE_SHORTCUTS not found in class source"
        assert skill_shortcuts_pos >= 0, "SKILL_SHORTCUTS not found in class source"
        assert skill_shortcuts_pos > mode_shortcuts_pos, (
            "SKILL_SHORTCUTS should come after MODE_SHORTCUTS"
        )

    def test_populate_skill_shortcuts_comes_after_populate_mode_shortcuts(self):
        """_populate_skill_shortcuts method should appear after _populate_mode_shortcuts."""
        import inspect

        from amplifier_app_cli.main import CommandProcessor

        source = inspect.getsource(CommandProcessor)

        populate_mode_pos = source.find("_populate_mode_shortcuts")
        populate_skill_pos = source.find("_populate_skill_shortcuts")

        assert populate_mode_pos >= 0, "_populate_mode_shortcuts not found"
        assert populate_skill_pos >= 0, "_populate_skill_shortcuts not found"
        assert populate_skill_pos > populate_mode_pos, (
            "_populate_skill_shortcuts should come after _populate_mode_shortcuts"
        )

    def test_process_input_skills_command(self):
        """/skills command should be processed correctly."""
        cp = _make_command_processor()
        action, _data = cp.process_input("/skills")
        assert action == "list_skills"

    def test_process_input_skill_command(self):
        """/skill command with args should be processed correctly."""
        cp = _make_command_processor()
        action, data = cp.process_input("/skill simplify")
        assert action == "load_skill"
        assert data["args"] == "simplify"
