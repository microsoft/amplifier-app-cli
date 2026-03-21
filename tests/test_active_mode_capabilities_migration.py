"""Tests for task-12: active_mode reads/writes via capabilities, not session_state.

Verifies that CommandProcessor uses get_capability("modes.active_mode") for reads
and register_capability("modes.active_mode", ...) for writes, with no session_state
references for active_mode.
"""

from unittest.mock import MagicMock


def _make_command_processor_capabilities_only(active_mode=None):
    """Create a CommandProcessor with capabilities but NO session_state active_mode.

    This helper intentionally omits session_state['active_mode'] so tests fail
    if the code still reads from session_state instead of get_capability.
    """
    from amplifier_app_cli.main import CommandProcessor

    mock_session = MagicMock()
    mock_session.coordinator = MagicMock()
    # session_state does NOT contain 'active_mode' — code must use get_capability
    mock_session.coordinator.session_state = {}

    mode_discovery_mock = MagicMock()
    mode_hooks_mock = MagicMock()
    capabilities = {
        "modes.discovery": mode_discovery_mock,
        "modes.hooks": mode_hooks_mock,
        "modes.active_mode": active_mode,
    }
    mock_session.coordinator.get_capability.side_effect = lambda key: capabilities.get(
        key
    )

    # register_capability updates the capabilities dict
    def mock_register(key, value):
        capabilities[key] = value

    mock_session.coordinator.register_capability.side_effect = mock_register

    # Mock mode_discovery
    mode_shortcuts = {"brainstorm": "brainstorm", "plan": "plan"}
    mode_discovery_mock.get_shortcuts.return_value = mode_shortcuts

    def mock_find(name):
        mock_mode = MagicMock()
        mock_mode.name = name
        mock_mode.description = f"Test {name} mode"
        mock_mode.shortcut = name
        return mock_mode

    mode_discovery_mock.find.side_effect = mock_find
    mode_discovery_mock.list_modes.return_value = [
        ("brainstorm", "Design refinement"),
        ("plan", "Implementation planning"),
    ]

    cp = CommandProcessor(mock_session, "test-bundle")
    return cp, mock_session.coordinator, capabilities


class TestActiveModeReadFromCapabilities:
    """Verify process_input reads active_mode from capabilities, not session_state."""

    def test_process_input_reads_active_mode_from_capability(self):
        """process_input returns active_mode from get_capability, not session_state."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode="brainstorm"
        )

        action, data = cp.process_input("hello world")

        assert action == "prompt"
        assert data["active_mode"] == "brainstorm", (
            f"Expected 'brainstorm' from capabilities, got {data['active_mode']!r}. "
            "Code may still be reading from session_state."
        )

    def test_process_input_active_mode_none_when_capability_is_none(self):
        """process_input returns None when modes.active_mode capability is None."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode=None
        )

        action, data = cp.process_input("hello world")

        assert action == "prompt"
        assert data["active_mode"] is None

    def test_process_input_active_mode_reflects_updated_capability(self):
        """process_input reflects a live update to the modes.active_mode capability."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode=None
        )

        # Simulate another piece of code setting the capability
        capabilities["modes.active_mode"] = "plan"

        action, data = cp.process_input("do something")

        assert data["active_mode"] == "plan", (
            f"Expected 'plan' after capability update, got {data['active_mode']!r}"
        )


class TestActiveModeWriteToCapabilities:
    """Verify _handle_mode writes active_mode via register_capability."""

    def test_handle_mode_on_calls_register_capability(self):
        """'/mode brainstorm on' calls register_capability('modes.active_mode', 'brainstorm')."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode=None
        )

        import asyncio

        asyncio.run(cp._handle_mode("brainstorm on"))

        coordinator.register_capability.assert_called_with("modes.active_mode", "brainstorm")

    def test_handle_mode_off_calls_register_capability_none(self):
        """'/mode off' calls register_capability('modes.active_mode', None)."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode="brainstorm"
        )

        import asyncio

        asyncio.run(cp._handle_mode("off"))

        coordinator.register_capability.assert_called_with("modes.active_mode", None)

    def test_handle_mode_toggle_off_calls_register_capability_none(self):
        """Toggling active mode off calls register_capability('modes.active_mode', None)."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode="brainstorm"
        )

        import asyncio

        asyncio.run(cp._handle_mode("brainstorm"))

        coordinator.register_capability.assert_called_with("modes.active_mode", None)

    def test_handle_mode_toggle_on_calls_register_capability(self):
        """Toggling inactive mode on calls register_capability('modes.active_mode', name)."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode=None
        )

        import asyncio

        asyncio.run(cp._handle_mode("plan"))

        coordinator.register_capability.assert_called_with("modes.active_mode", "plan")

    def test_handle_mode_explicit_off_calls_register_capability_none(self):
        """'/mode brainstorm off' calls register_capability('modes.active_mode', None)."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode="brainstorm"
        )

        import asyncio

        asyncio.run(cp._handle_mode("brainstorm off"))

        coordinator.register_capability.assert_called_with("modes.active_mode", None)


class TestListModesReadsFromCapabilities:
    """Verify _list_modes reads current_mode from capabilities."""

    def test_list_modes_marks_active_from_capability(self):
        """_list_modes marks the active mode using get_capability, not session_state."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode="brainstorm"
        )

        import asyncio

        result = asyncio.run(cp._list_modes())

        # Should show brainstorm as active (with * marker)
        assert "brainstorm" in result
        assert "*" in result, (
            f"Expected * marker for active mode 'brainstorm', got:\n{result}"
        )

    def test_list_modes_no_marker_when_no_active_mode(self):
        """_list_modes shows no active marker when modes.active_mode is None."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode=None
        )

        import asyncio

        result = asyncio.run(cp._list_modes())

        assert "*" not in result, (
            f"Expected no * marker when no active mode, got:\n{result}"
        )


class TestGetConfigDisplayReadsFromCapabilities:
    """Verify _get_config_display reads active_mode from capabilities, not session_state."""

    def test_get_config_display_calls_get_capability_for_active_mode(self):
        """_get_config_display calls get_capability('modes.active_mode') for active mode."""
        cp, coordinator, capabilities = _make_command_processor_capabilities_only(
            active_mode="plan"
        )

        import asyncio

        asyncio.run(cp._get_config_display())

        # Verify get_capability was called with "modes.active_mode"
        call_args_list = coordinator.get_capability.call_args_list
        capability_keys_called = [str(c) for c in call_args_list]
        assert any("modes.active_mode" in s for s in capability_keys_called), (
            f"Expected get_capability('modes.active_mode') to be called, got: {call_args_list}"
        )

    def test_get_config_display_does_not_use_session_state_active_mode(self):
        """_get_config_display should NOT read session_state for active_mode."""
        from amplifier_app_cli.main import CommandProcessor

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()
        mock_session.coordinator.session_state = {}  # No active_mode in session_state

        capabilities = {
            "modes.discovery": MagicMock(
                get_shortcuts=MagicMock(return_value={}),
            ),
            "modes.active_mode": "plan",
        }
        mock_session.coordinator.get_capability.side_effect = lambda key: capabilities.get(key)
        mock_session.coordinator.register_capability = MagicMock()
        mock_session.config = {}

        import asyncio

        cp = CommandProcessor(mock_session, "test-bundle")

        # If _get_config_display raises AttributeError accessing session_state['active_mode'],
        # it would still use session_state. We just verify it runs without error.
        # The change from session_state to get_capability is what matters here.
        asyncio.run(cp._get_config_display())

        # get_capability should have been called for modes.active_mode
        call_args_list = mock_session.coordinator.get_capability.call_args_list
        capability_keys_called = [str(c) for c in call_args_list]
        assert any("modes.active_mode" in s for s in capability_keys_called), (
            f"Expected get_capability('modes.active_mode') call, got: {call_args_list}"
        )


class TestNoSessionStateReferences:
    """Verify zero session_state references for active_mode in production code."""

    def test_process_input_does_not_access_session_state_active_mode(self):
        """process_input should NOT access session_state for active_mode."""
        from amplifier_app_cli.main import CommandProcessor

        mock_session = MagicMock()
        mock_session.coordinator = MagicMock()

        # Make session_state raise if accessed at 'active_mode' key
        ss_mock = MagicMock()
        ss_mock.__contains__ = MagicMock(return_value=False)

        def fail_on_active_mode_get(key, default=None):
            if key == "active_mode":
                raise AssertionError(
                    "process_input still reads from session_state['active_mode']! "
                    "Migrate to get_capability('modes.active_mode')."
                )
            return default

        ss_mock.get.side_effect = fail_on_active_mode_get
        mock_session.coordinator.session_state = ss_mock

        capabilities = {
            "modes.discovery": MagicMock(
                get_shortcuts=MagicMock(return_value={}),
            ),
            "modes.active_mode": "brainstorm",
        }
        mock_session.coordinator.get_capability.side_effect = lambda key: capabilities.get(
            key
        )
        mock_session.coordinator.register_capability = MagicMock()

        cp = CommandProcessor(mock_session, "test-bundle")
        action, data = cp.process_input("hello")

        assert data["active_mode"] == "brainstorm"
