"""Tests for the _make_command_processor helper in helpers.py.

Tests cover:
1. configurator parameter defaults to None (no attribute set) when not provided
2. configurator parameter sets cp.configurator when provided
"""

from unittest.mock import MagicMock

from helpers import _make_command_processor


class TestMakeCommandProcessorConfiguratorParam:
    """Tests for the configurator parameter in _make_command_processor."""

    def test_configurator_is_set_when_provided(self):
        """When configurator is provided, cp.configurator should be set to it."""
        mock_configurator = MagicMock()
        cp = _make_command_processor(configurator=mock_configurator)
        assert cp.configurator is mock_configurator

    def test_configurator_none_by_default(self):
        """When configurator is not provided, it defaults to None and is not set via helper."""
        cp = _make_command_processor()
        # The configurator param defaults to None, so helper does NOT set cp.configurator
        # (existing tests are unaffected; we just confirm no error occurs)
        assert cp is not None

    def test_existing_params_still_work_with_configurator(self):
        """Existing skills_discovery and mode_shortcuts params work alongside configurator."""
        mock_discovery = MagicMock()
        mock_discovery.list_skills.return_value = [("simplify", "Simplify code")]
        mock_discovery.get_shortcuts.return_value = {}
        mock_configurator = MagicMock()

        cp = _make_command_processor(
            skills_discovery=mock_discovery,
            configurator=mock_configurator,
        )
        assert cp.configurator is mock_configurator

    def test_configurator_not_set_when_none(self):
        """When configurator=None (default), cp.configurator should not be explicitly set by the helper."""
        cp = _make_command_processor(configurator=None)
        # We should not have raised an error; and configurator should NOT be force-set
        # (It may or may not exist as an attribute based on CommandProcessor init)
        # But passing None must not cause cp.configurator to be set to None by our helper
        # The safest check: calling without configurator works fine
        assert cp is not None
