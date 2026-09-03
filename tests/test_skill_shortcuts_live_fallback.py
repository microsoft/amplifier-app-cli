"""Tests for defense-in-depth stale-SKILL_SHORTCUTS handling.

SKILL_SHORTCUTS is populated once, additively, at CommandProcessor.__init__
time (see _populate_skill_shortcuts). Skills sourced from @namespace:skills
packs resolve LAZILY -- on first provider:request -- which happens AFTER
that startup snapshot. These tests simulate that timing: the discovery
capability's get_shortcuts() return value changes AFTER the CommandProcessor
is constructed (as it would once the lazy source resolves), and verify that:

1. Dispatch (process_input) falls back to a live re-check of the discovery
   capability on a cache miss, so a skill that only became visible after
   __init__ is still dispatched correctly.
2. A genuinely unknown command (never present in discovery, live or not)
   still returns 'unknown_command'.
3. /help ("_format_help") reflects a skill that appeared after __init__,
   because it refreshes the cache before rendering.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.main import CommandProcessor
from helpers import _make_command_processor


@pytest.fixture(autouse=True)
def reset_skill_shortcuts():
    """Clear SKILL_SHORTCUTS before and after every test in this module.

    SKILL_SHORTCUTS is a CLASS-level dict shared across CommandProcessor
    instances -- reset it so state never leaks between tests.
    """
    CommandProcessor.SKILL_SHORTCUTS.clear()
    yield
    CommandProcessor.SKILL_SHORTCUTS.clear()


def _make_lazy_discovery(initial_shortcuts: dict) -> MagicMock:
    """A skills_discovery mock whose get_shortcuts() result can be mutated
    later, simulating a skill resolving lazily after CommandProcessor
    construction.
    """
    mock_discovery = MagicMock()
    mock_discovery.get_shortcuts.return_value = dict(initial_shortcuts)
    return mock_discovery


class TestDispatchFallsBackToLiveDiscovery:
    """A skill that appears in discovery AFTER __init__ must still dispatch."""

    def test_skill_added_after_init_still_dispatches(self):
        """A skill unresolved at __init__ time resolves via live fallback."""
        # At construction time, discovery has no shortcuts yet (lazy source
        # not yet resolved).
        mock_discovery = _make_lazy_discovery({})
        cp = _make_command_processor(skills_discovery=mock_discovery)

        # Sanity: the shortcut is genuinely absent from the startup snapshot.
        assert "wayfinder-pack" not in CommandProcessor.SKILL_SHORTCUTS

        # Simulate lazy resolution: the skill now exists in the live
        # capability (as it would after a first provider:request).
        mock_discovery.get_shortcuts.return_value = {
            "wayfinder-pack": {
                "name": "wayfinder-pack",
                "description": "Wayfinder pack skill",
            }
        }

        action, data = cp.process_input("/wayfinder-pack")

        assert action == "load_skill"
        assert data["skill_name"] == "wayfinder-pack"
        assert data["command"] == "/wayfinder-pack"

    def test_skill_added_after_init_with_arguments(self):
        """Arguments after a late-resolving skill shortcut are preserved."""
        mock_discovery = _make_lazy_discovery({})
        cp = _make_command_processor(skills_discovery=mock_discovery)

        mock_discovery.get_shortcuts.return_value = {
            "seam-test": {"name": "seam-test", "description": "Seam test skill"}
        }

        action, data = cp.process_input("/seam-test check the seams")

        assert action == "load_skill"
        assert data["skill_name"] == "seam-test"
        assert data["arguments"] == "check the seams"

    def test_alias_shortcut_resolves_to_canonical_name_after_refresh(self):
        """A late-resolving alias entry still maps to its canonical name."""
        mock_discovery = _make_lazy_discovery({})
        cp = _make_command_processor(skills_discovery=mock_discovery)

        mock_discovery.get_shortcuts.return_value = {
            "dc": {"name": "design-council", "description": "Design council"}
        }

        action, data = cp.process_input("/dc")

        assert action == "load_skill"
        assert data["skill_name"] == "design-council"

    def test_fallback_refreshes_class_level_cache(self):
        """A successful live fallback should also update SKILL_SHORTCUTS
        (additive), so subsequent lookups don't need to refresh again."""
        mock_discovery = _make_lazy_discovery({})
        cp = _make_command_processor(skills_discovery=mock_discovery)

        mock_discovery.get_shortcuts.return_value = {
            "simplify": {"name": "simplify"}
        }

        cp.process_input("/simplify")

        assert "simplify" in CommandProcessor.SKILL_SHORTCUTS


class TestUnknownCommandStillUnknownAfterFallback:
    """A command absent from discovery, live or cached, is still unknown."""

    def test_genuinely_unknown_command_returns_unknown_command(self):
        """A command never present in discovery must still be unknown_command."""
        mock_discovery = _make_lazy_discovery({"simplify": {"name": "simplify"}})
        cp = _make_command_processor(skills_discovery=mock_discovery)

        action, data = cp.process_input("/totally-not-a-real-skill")

        assert action == "unknown_command"
        assert data["command"] == "/totally-not-a-real-skill"

    def test_no_discovery_capability_fails_soft(self):
        """With no skills_discovery capability at all, fallback must not
        raise and must still return unknown_command (fail-soft parity with
        the pre-existing miss path)."""
        cp = _make_command_processor()  # no skills_discovery

        action, data = cp.process_input("/notaskill")

        assert action == "unknown_command"
        assert data["command"] == "/notaskill"

    def test_discovery_without_get_shortcuts_fails_soft(self):
        """A discovery object lacking get_shortcuts() must not raise during
        the live-fallback refresh."""

        class SimpleDiscovery:
            pass

        cp = _make_command_processor(skills_discovery=SimpleDiscovery())

        action, _data = cp.process_input("/notaskill")

        assert action == "unknown_command"


class TestHelpDisplayFreshness:
    """/help ("_format_help") must reflect skills that resolved after __init__."""

    def test_help_shows_skill_added_after_init(self):
        """A skill shortcut that only appears after construction should show
        up in the /help output, because _format_help refreshes the cache
        before rendering."""
        mock_discovery = _make_lazy_discovery({})
        cp = _make_command_processor(skills_discovery=mock_discovery)

        mock_discovery.get_shortcuts.return_value = {
            "wayfinder-pack": {
                "name": "wayfinder-pack",
                "description": "Wayfinder pack skill",
            }
        }

        help_text = cp._format_help()

        assert "/wayfinder-pack" in help_text
        assert "Wayfinder pack skill" in help_text
