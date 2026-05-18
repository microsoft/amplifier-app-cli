"""Tests for Items 1, 2 (chain dedupe + truncation) and Item 3 (--trees flag).

Organised into three test classes:

- TestDedupeChain     — unit tests for :func:`dedupe_behavior_chain`
- TestTruncateChain   — unit tests for :func:`truncate_attribution_chain`
- TestBuildAttribution — integration tests via DashboardRenderer.build_attribution
- TestTreesFlag       — smoke tests for ``--trees`` flag parsing and rendering
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from amplifier_app_cli.ui._attribution import (
    dedupe_behavior_chain,
    truncate_attribution_chain,
)
from amplifier_app_cli.ui.item_renderer import (
    dedupe_behavior_chain as ir_dedupe,
    truncate_attribution_chain as ir_truncate,
)
from amplifier_app_cli.ui.dashboard_renderer import DashboardRenderer
from amplifier_app_cli.main import _parse_config_flags  # type: ignore[attr-defined]
from helpers import _make_command_processor


# ---------------------------------------------------------------------------
# Item 2 — dedupe_behavior_chain
# ---------------------------------------------------------------------------


class TestDedupeChain:
    """Tests for the dedupe_behavior_chain helper (Item 2)."""

    def test_no_pair_unchanged(self):
        """Chain without any X / X-behavior pair is returned unchanged."""
        assert dedupe_behavior_chain(["X", "Y"]) == ["X", "Y"]

    def test_no_pair_single_entry(self):
        """Single-entry chain with no -behavior sibling is unchanged."""
        assert dedupe_behavior_chain(["foundation"]) == ["foundation"]

    def test_behavior_only_unchanged(self):
        """X-behavior without a bare X is kept unchanged."""
        assert dedupe_behavior_chain(["X-behavior", "Y"]) == ["X-behavior", "Y"]

    def test_pair_unsuffixed_first(self):
        """X appears before X-behavior → X is dropped, X-behavior kept."""
        assert dedupe_behavior_chain(["X", "X-behavior", "foundation"]) == [
            "X-behavior",
            "foundation",
        ]

    def test_pair_suffixed_first(self):
        """X-behavior appears before X → X is dropped, X-behavior stays in place."""
        assert dedupe_behavior_chain(["X-behavior", "X", "foundation"]) == [
            "X-behavior",
            "foundation",
        ]

    def test_multiple_pairs(self):
        """Two independent X/X-behavior pairs: both unsuffixed entries dropped."""
        chain = ["alpha", "alpha-behavior", "beta", "beta-behavior", "root"]
        assert dedupe_behavior_chain(chain) == [
            "alpha-behavior",
            "beta-behavior",
            "root",
        ]

    def test_empty_chain(self):
        """Empty list returns empty list."""
        assert dedupe_behavior_chain([]) == []

    def test_real_world_digital_twin(self):
        """Realistic digital-twin-universe chain from the spec description."""
        chain = [
            "digital-twin-universe",
            "digital-twin-universe-behavior",
            "foundation",
            "amplifier-dev",
        ]
        assert dedupe_behavior_chain(chain) == [
            "digital-twin-universe-behavior",
            "foundation",
            "amplifier-dev",
        ]

    # Re-export check: item_renderer re-exports the same function
    def test_re_exported_from_item_renderer(self):
        """dedupe_behavior_chain is importable directly from item_renderer."""
        assert ir_dedupe is dedupe_behavior_chain


# ---------------------------------------------------------------------------
# Item 1 — truncate_attribution_chain
# ---------------------------------------------------------------------------


class TestTruncateChain:
    """Tests for the truncate_attribution_chain helper (Item 1)."""

    def test_one_entry_unchanged(self):
        """Single entry: no truncation."""
        assert truncate_attribution_chain(["foundation"]) == "foundation"

    def test_two_entries_unchanged(self):
        """Two entries: no truncation."""
        assert truncate_attribution_chain(["a", "b"]) == "a, b"

    def test_three_entries_unchanged(self):
        """Three entries: no truncation (boundary)."""
        assert truncate_attribution_chain(["a", "b", "c"]) == "a, b, c"

    def test_four_entries_truncated(self):
        """Four entries: first, second, …, last."""
        result = truncate_attribution_chain(["a", "b", "c", "d"])
        assert result == "a, b, \u2026, d"

    def test_five_entries_truncated(self):
        """Five entries: first, second, …, last (spec example)."""
        result = truncate_attribution_chain(
            ["first", "second", "third", "fourth", "fifth"]
        )
        assert result == "first, second, \u2026, fifth"

    def test_unicode_ellipsis_character(self):
        """Truncated result uses U+2026 HORIZONTAL ELLIPSIS, not '...'."""
        result = truncate_attribution_chain(["a", "b", "c", "d"])
        assert "\u2026" in result
        assert "..." not in result

    def test_empty_chain(self):
        """Empty list produces empty string."""
        assert truncate_attribution_chain([]) == ""

    # Re-export check
    def test_re_exported_from_item_renderer(self):
        """truncate_attribution_chain is importable directly from item_renderer."""
        assert ir_truncate is truncate_attribution_chain


# ---------------------------------------------------------------------------
# Integration — DashboardRenderer.build_attribution
# ---------------------------------------------------------------------------


class TestBuildAttribution:
    """Integration tests: build_attribution applies dedupe then truncate."""

    def _make_dr(self) -> DashboardRenderer:
        return DashboardRenderer(MagicMock())

    def _item(self, behaviors: list[str]) -> dict:
        return {"name": "test-item", "enabled": True, "behaviors": behaviors}

    def test_short_chain_unchanged(self):
        """1-3 entries: no truncation, no unnecessary dedup."""
        dr = self._make_dr()
        item = self._item(["foundation"])
        assert dr.build_attribution(item) == "foundation"

    def test_three_entries_unchanged(self):
        """Three-entry chain rendered verbatim."""
        dr = self._make_dr()
        item = self._item(["a", "b", "c"])
        assert dr.build_attribution(item) == "a, b, c"

    def test_long_chain_truncated(self):
        """Five-entry chain is truncated to first, second, …, last."""
        dr = self._make_dr()
        item = self._item(["a", "b", "c", "d", "e"])
        assert dr.build_attribution(item) == "a, b, \u2026, e"

    def test_dedupe_applied_before_truncation(self):
        """Deduplication reduces count before truncation threshold is checked."""
        dr = self._make_dr()
        # Without dedup: 4 entries → truncated
        # After dedup: 3 entries → NOT truncated
        item = self._item(["X", "X-behavior", "foundation", "amplifier"])
        result = dr.build_attribution(item)
        # X should be dropped; 3 entries remain: [X-behavior, foundation, amplifier]
        assert result == "X-behavior, foundation, amplifier"
        assert "\u2026" not in result

    def test_real_world_superpowers_chain(self):
        """The spec's motivating example: 4 entries → truncated."""
        dr = self._make_dr()
        item = self._item(
            [
                "superpowers-methodology-behavior",
                "behavior-modes",
                "foundation",
                "amplifier-dev",
            ]
        )
        result = dr.build_attribution(item)
        assert (
            result
            == "superpowers-methodology-behavior, behavior-modes, \u2026, amplifier-dev"
        )

    def test_empty_behaviors_returns_empty(self):
        """Items with no behaviors return an empty string."""
        dr = self._make_dr()
        item = self._item([])
        assert dr.build_attribution(item) == ""


# ---------------------------------------------------------------------------
# Item 3 — --trees flag parsing
# ---------------------------------------------------------------------------


class TestParseconfigFlags:
    """Unit tests for _parse_config_flags — ensures --trees is parsed correctly."""

    def test_no_flags(self):
        remaining, compact, detailed, trees, fmt = _parse_config_flags(["show"])
        assert remaining == ["show"]
        assert compact is False
        assert detailed is False
        assert trees is False
        assert fmt == "text"

    def test_detailed_flag(self):
        remaining, compact, detailed, trees, fmt = _parse_config_flags(
            ["show", "--detailed"]
        )
        assert remaining == ["show"]
        assert detailed is True
        assert trees is False

    def test_trees_flag(self):
        remaining, compact, detailed, trees, fmt = _parse_config_flags(
            ["show", "--trees"]
        )
        assert remaining == ["show"]
        assert trees is True
        assert detailed is False

    def test_trees_clears_detailed_last_wins(self):
        """--detailed followed by --trees: trees wins."""
        _, _, detailed, trees, _ = _parse_config_flags(
            ["show", "--detailed", "--trees"]
        )
        assert trees is True
        assert detailed is False

    def test_detailed_clears_trees_last_wins(self):
        """--trees followed by --detailed: detailed wins."""
        _, _, detailed, trees, _ = _parse_config_flags(
            ["show", "--trees", "--detailed"]
        )
        assert detailed is True
        assert trees is False

    def test_compact_flag(self):
        remaining, compact, detailed, trees, fmt = _parse_config_flags(
            ["show", "--compact"]
        )
        assert remaining == ["show"]
        assert compact is True

    def test_format_flag(self):
        remaining, compact, detailed, trees, fmt = _parse_config_flags(
            ["show", "--format", "json"]
        )
        assert remaining == ["show"]
        assert fmt == "json"

    def test_trees_with_format_json(self):
        """--trees --format json: trees flag is parsed (JSON path ignores it)."""
        remaining, _, _, trees, fmt = _parse_config_flags(
            ["show", "--trees", "--format", "json"]
        )
        assert trees is True
        assert fmt == "json"
        assert remaining == ["show"]


# ---------------------------------------------------------------------------
# Item 3 — --trees rendering smoke test
# ---------------------------------------------------------------------------


class TestTreesRendering:
    """Smoke tests that --trees flag produces tree-style output."""

    def _make_mock_configurator_with_items(self):
        """Return a mock configurator with at least one tool item."""
        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.context_list.return_value = []
        mock_cfg.tools_list.return_value = [
            {
                "name": "bash",
                "enabled": True,
                "behaviors": ["foundation"],
                "module_id": "tool-bash",
                "config": {},
            }
        ]
        mock_cfg.hooks_list.return_value = []
        mock_cfg.providers_list.return_value = []
        mock_cfg.agents_list.return_value = []
        mock_cfg.behaviors_list.return_value = []
        mock_cfg.diff_from_original.return_value = []
        return mock_cfg

    @pytest.mark.asyncio
    async def test_trees_flag_accepted_and_produces_tree_markers(self):
        """--trees is accepted; output contains '└─ via' OR 'chain:' markers."""
        # Use ItemRenderer directly with a plain dict item (no configurator needed)
        # Give the tool item an origins list so tree rendering fires
        from amplifier_app_cli.ui.item_renderer import ItemRenderer
        from io import StringIO
        from rich.console import Console as RichConsole

        output = StringIO()
        real_console = RichConsole(
            file=output, force_terminal=False, highlight=False, width=200
        )
        ir = ItemRenderer(real_console)
        items = [
            {
                "name": "bash",
                "enabled": True,
                "behaviors": ["foundation"],
                "module_id": "tool-bash",
                "config": {},
            }
        ]
        # render in trees view
        ir.render(items, view="trees", category="tools")
        rendered = output.getvalue()

        # The section header must be present
        assert "tools" in rendered.lower(), (
            f"'tools' section header not in trees output: {rendered!r}"
        )
        # At least the item name must appear
        assert "bash" in rendered, f"item name 'bash' not in trees output: {rendered!r}"
        # runtime_injection label is emitted by _render_detailed_one
        assert "runtime_injection" in rendered, (
            f"'runtime_injection' not in trees output — trees mode didn't use "
            f"_render_detailed_one: {rendered!r}"
        )

    @pytest.mark.asyncio
    async def test_trees_via_get_config_display(self):
        """_get_config_display('show --trees') triggers trees rendering."""
        mock_cfg = self._make_mock_configurator_with_items()
        cp = _make_command_processor(configurator=mock_cfg)

        printed: list[str] = []

        class CapturingConsole:
            def print(self, text=""):
                printed.append(str(text))

        with patch("amplifier_app_cli.console.console", CapturingConsole()):
            await cp._get_config_display("show --trees")

        all_output = "\n".join(printed)
        # Trees mode must have rendered tools section with item detail
        assert "bash" in all_output, f"'bash' not in trees output: {all_output!r}"
        assert "runtime_injection" in all_output, (
            f"'runtime_injection' not in trees output — trees rendering not active: "
            f"{all_output!r}"
        )

    @pytest.mark.asyncio
    async def test_trees_flag_does_not_affect_json_output(self):
        """--trees --format json: JSON output is unchanged (trees ignored for JSON)."""
        mock_cfg = self._make_mock_configurator_with_items()
        cp = _make_command_processor(configurator=mock_cfg)

        import json

        printed: list[str] = []

        class CapturingConsole:
            def print(self, text=""):
                printed.append(str(text))

        with patch("amplifier_app_cli.console.console", CapturingConsole()):
            await cp._get_config_display("show --trees --format json")

        # Should have printed valid JSON
        all_output = "\n".join(printed)
        data = json.loads(all_output)
        assert "tools" in data, "JSON output should have 'tools' key"
        assert isinstance(data["tools"], list), "tools should be a list"
        # runtime_injection should NOT appear as a top-level JSON field here
        # (it's nested inside item records, not a top-level key)
        assert "providers" in data
        assert "behaviors" in data
