"""Shared test helper factories for amplifier-app-cli tests."""

from unittest.mock import MagicMock

from amplifier_app_cli.main import CommandProcessor


def _make_command_processor(
    skills_discovery=None, mode_shortcuts=None, configurator=None
):
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
    if configurator is not None:
        cp.configurator = configurator
    return cp
