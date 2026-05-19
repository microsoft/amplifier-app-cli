"""Tests for the root-bundle (* prefix) marker in /config show behaviors.

Tests that:
- render_behaviors_section() prefixes explicitly_requested=True items with "*"
- _render_compact() prefixes explicitly_requested=True items with "*"
- Items with explicitly_requested=False (default) receive no prefix
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock


sys.path.insert(0, os.path.dirname(__file__))

from amplifier_app_cli.ui.dashboard_renderer import DashboardRenderer
from amplifier_app_cli.ui.item_renderer import ItemRenderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console() -> tuple[MagicMock, list[str]]:
    """Create a mock Rich console that captures printed lines."""
    lines: list[str] = []
    console = MagicMock()
    console.print.side_effect = lambda *args, **kwargs: lines.append(
        str(args[0]) if args else ""
    )
    return console, lines


def _make_behavior_item(
    name: str,
    enabled: bool = True,
    explicitly_requested: bool = False,
) -> dict:
    """Build a minimal behavior item dict compatible with both renderers."""
    return {
        "name": name,
        "enabled": enabled,
        "explicitly_requested": explicitly_requested,
        "config_summary": {
            "tools": ["tool:bash"],
            "context": [],
            "hooks": [],
            "providers": [],
            "agents": [],
        },
    }


# ---------------------------------------------------------------------------
# DashboardRenderer.render_behaviors_section tests
# ---------------------------------------------------------------------------


class TestRenderBehaviorsSectionMarker:
    """Tests for the '*' prefix in render_behaviors_section()."""

    def test_explicitly_requested_bundle_gets_star_prefix(self) -> None:
        """A behavior with explicitly_requested=True is rendered with '* ' prefix."""
        console, lines = _make_console()
        dr = DashboardRenderer(console)

        items = [_make_behavior_item("amplifier-dev", explicitly_requested=True)]
        dr.render_behaviors_section(items)

        # Find the behavior line (has [on] marker)
        behavior_lines = [ln for ln in lines if "[on]" in ln or "on]" in ln]
        assert behavior_lines, f"No '[on]' line found in output: {lines}"
        behavior_line = behavior_lines[0]

        assert "* amplifier-dev" in behavior_line, (
            f"Expected '* amplifier-dev' in line but got: {behavior_line!r}"
        )

    def test_transitive_bundle_has_no_star_prefix(self) -> None:
        """A behavior with explicitly_requested=False is rendered without '* ' prefix."""
        console, lines = _make_console()
        dr = DashboardRenderer(console)

        items = [
            _make_behavior_item("behavior-apply-patch", explicitly_requested=False)
        ]
        dr.render_behaviors_section(items)

        behavior_lines = [ln for ln in lines if "[on]" in ln or "on]" in ln]
        assert behavior_lines, f"No '[on]' line found in output: {lines}"
        behavior_line = behavior_lines[0]

        assert "* " not in behavior_line, (
            f"Did not expect '* ' in line but got: {behavior_line!r}"
        )
        assert "behavior-apply-patch" in behavior_line

    def test_mixed_items_only_explicit_gets_star(self) -> None:
        """Only explicitly_requested=True items get the '*' prefix; others don't."""
        console, lines = _make_console()
        dr = DashboardRenderer(console)

        items = [
            _make_behavior_item("amplifier-dev", explicitly_requested=True),
            _make_behavior_item("foundation", explicitly_requested=False),
            _make_behavior_item("behavior-apply-patch", explicitly_requested=False),
        ]
        dr.render_behaviors_section(items)

        behavior_lines = [ln for ln in lines if "[on]" in ln or "on]" in ln]
        assert len(behavior_lines) == 3, (
            f"Expected 3 behavior lines, got: {behavior_lines}"
        )

        starred = [ln for ln in behavior_lines if "* " in ln]
        unstarred = [ln for ln in behavior_lines if "* " not in ln]

        assert len(starred) == 1, f"Expected exactly 1 starred item, got: {starred}"
        assert "amplifier-dev" in starred[0]
        assert len(unstarred) == 2

    def test_disabled_explicitly_requested_bundle_gets_star(self) -> None:
        """A disabled but explicitly_requested bundle also gets the '*' prefix."""
        console, lines = _make_console()
        dr = DashboardRenderer(console)

        items = [
            _make_behavior_item(
                "amplifier-dev", enabled=False, explicitly_requested=True
            )
        ]
        dr.render_behaviors_section(items)

        behavior_lines = [ln for ln in lines if "[off]" in ln or "off]" in ln]
        assert behavior_lines, f"No '[off]' line found: {lines}"
        assert "* amplifier-dev" in behavior_lines[0]


# ---------------------------------------------------------------------------
# ItemRenderer._render_compact tests (for behaviors category)
# ---------------------------------------------------------------------------


class TestItemRendererCompactMarker:
    """Tests for the '*' prefix in ItemRenderer._render_compact()."""

    def test_compact_explicitly_requested_gets_star(self) -> None:
        """_render_compact() adds '* ' prefix for explicitly_requested=True items."""
        console, lines = _make_console()
        ir = ItemRenderer(console)

        items = [_make_behavior_item("amplifier-dev", explicitly_requested=True)]
        ir._render_compact(items, section_title=None, trailing_newline=False)

        content_lines = [ln for ln in lines if ln.strip()]
        assert content_lines, f"No output lines: {lines}"
        # Should contain "* amplifier-dev" in the item line
        assert any("* amplifier-dev" in ln for ln in content_lines), (
            f"Expected '* amplifier-dev' in compact output but got: {content_lines}"
        )

    def test_compact_transitive_no_star(self) -> None:
        """_render_compact() does NOT add '*' prefix for explicitly_requested=False."""
        console, lines = _make_console()
        ir = ItemRenderer(console)

        items = [_make_behavior_item("foundation", explicitly_requested=False)]
        ir._render_compact(items, section_title=None, trailing_newline=False)

        content_lines = [ln for ln in lines if ln.strip() and "foundation" in ln]
        assert content_lines, f"No 'foundation' lines found: {lines}"
        assert not any("* foundation" in ln for ln in content_lines), (
            f"Did not expect '* foundation' in compact output but got: {content_lines}"
        )
        assert any("foundation" in ln for ln in content_lines)

    def test_compact_non_behavior_items_no_star(self) -> None:
        """Non-behavior items (tools, hooks) never get the '*' prefix."""
        console, lines = _make_console()
        ir = ItemRenderer(console)

        # A tool item with explicitly_requested defaulting to False
        tool_item = {
            "name": "bash",
            "enabled": True,
            "module_id": "tool-bash",
            "source_uri": None,
            "config_summary": {},
            "origins": [],
            "include_paths": [],
            "explicitly_requested": False,
        }
        ir._render_compact([tool_item], section_title=None, trailing_newline=False)

        content_lines = [ln for ln in lines if "bash" in ln]
        assert content_lines
        assert not any("* bash" in ln for ln in content_lines)
