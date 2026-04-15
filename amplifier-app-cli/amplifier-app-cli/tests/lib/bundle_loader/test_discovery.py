"""Tests for AppBundleDiscovery.list_cached_root_bundles().

Focuses on the subdirectory-filter exemption for user-added bundles:
- Bundles added via `amplifier bundle add ...#subdirectory=...` must survive
  both Step 3 (the former skip) and Step 4 (the registry URI filter).
- Auto-discovered nested bundles (not in bundle.added) must still be filtered
  out when their registry URI contains #subdirectory=.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discovery() -> AppBundleDiscovery:
    """Return an AppBundleDiscovery instance, bypassing costly __init__ side-effects."""
    mock_registry = MagicMock()
    mock_registry.list_bundles.return_value = []

    with (
        patch.object(AppBundleDiscovery, "_register_well_known_bundles"),
        patch.object(AppBundleDiscovery, "_load_user_registry"),
    ):
        discovery = AppBundleDiscovery(search_paths=[], registry=mock_registry)
    return discovery


def _write_registry(tmp_path: Path, bundles: dict) -> None:
    """Write a minimal registry.json under tmp_path/.amplifier/."""
    amplifier_dir = tmp_path / ".amplifier"
    amplifier_dir.mkdir(parents=True, exist_ok=True)
    (amplifier_dir / "registry.json").write_text(json.dumps({"bundles": bundles}))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListCachedRootBundlesSubdirectoryFilter:
    """User-added subdirectory bundles must not be filtered out."""

    def test_user_added_subdirectory_bundle_is_included(self, tmp_path: Path):
        """A bundle in bundle.added whose URI has #subdirectory= must appear in the result.

        Regression test for the bug where Step 3 skipped subdirectory URIs
        and Step 4 filtered them out again, causing `amplifier update` to
        silently drop bundles added via `amplifier bundle add ...#subdirectory=...`.
        """
        _write_registry(
            tmp_path,
            {
                "env-all": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
                    "is_root": True,
                }
            },
        )

        # User explicitly added this bundle
        added_bundles = {
            "env-all": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
        }

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"env-all"}, set()),
            ),
            patch(
                "amplifier_app_cli.lib.bundle_loader.discovery.WELL_KNOWN_BUNDLES",
                {},
            ),
            patch("amplifier_app_cli.lib.settings.AppSettings") as MockSettings,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            MockSettings.return_value.get_added_bundles.return_value = added_bundles
            result = discovery.list_cached_root_bundles()

        assert "env-all" in result, (
            "env-all was added via `bundle add` with a #subdirectory= URI "
            "and must survive the subdirectory filter in list_cached_root_bundles()"
        )

    def test_non_user_added_subdirectory_bundle_is_filtered_out(self, tmp_path: Path):
        """A bundle NOT in bundle.added whose registry URI has #subdirectory= must be filtered.

        This preserves the original behaviour: auto-discovered nested bundles
        share a repo with their parent; updating the parent is sufficient.
        """
        _write_registry(
            tmp_path,
            {
                "nested-behavior": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main#subdirectory=behaviors/nested.yaml",
                    "is_root": True,
                }
            },
        )

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"nested-behavior"}, set()),
            ),
            patch(
                "amplifier_app_cli.lib.bundle_loader.discovery.WELL_KNOWN_BUNDLES",
                {},
            ),
            patch("amplifier_app_cli.lib.settings.AppSettings") as MockSettings,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            # User did NOT explicitly add this bundle
            MockSettings.return_value.get_added_bundles.return_value = {}
            result = discovery.list_cached_root_bundles()

        assert "nested-behavior" not in result, (
            "nested-behavior is not user-added and its registry URI has "
            "#subdirectory=, so it must be filtered out (parent repo update covers it)"
        )

    def test_multiple_bundles_mixed_filtering(self, tmp_path: Path):
        """Correct bundles survive and are filtered in a realistic mixed scenario.

        - env-all: user-added, #subdirectory= in registry → INCLUDED
        - digital-twin-universe-behavior: user-added, #subdirectory= in registry → INCLUDED
        - auto-nested: auto-discovered, #subdirectory= in registry → FILTERED
        - parallax-discovery: user-added, clean URI → INCLUDED
        """
        _write_registry(
            tmp_path,
            {
                "env-all": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
                    "is_root": True,
                },
                "digital-twin-universe-behavior": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main#subdirectory=behaviors/digital-twin-universe.yaml",
                    "is_root": True,
                },
                "auto-nested": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main#subdirectory=behaviors/auto.yaml",
                    "is_root": True,
                },
                "parallax-discovery": {
                    "uri": "git+https://github.com/bkrabach/amplifier-bundle-parallax-discovery@main",
                    "is_root": True,
                },
            },
        )

        added_bundles = {
            "env-all": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
            "digital-twin-universe-behavior": "git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main#subdirectory=behaviors/digital-twin-universe.yaml",
            "parallax-discovery": "git+https://github.com/bkrabach/amplifier-bundle-parallax-discovery@main",
        }

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=(
                    {
                        "env-all",
                        "digital-twin-universe-behavior",
                        "auto-nested",
                        "parallax-discovery",
                    },
                    set(),
                ),
            ),
            patch(
                "amplifier_app_cli.lib.bundle_loader.discovery.WELL_KNOWN_BUNDLES",
                {},
            ),
            patch("amplifier_app_cli.lib.settings.AppSettings") as MockSettings,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            MockSettings.return_value.get_added_bundles.return_value = added_bundles
            result = discovery.list_cached_root_bundles()

        assert "env-all" in result, "user-added subdirectory bundle must be included"
        assert "digital-twin-universe-behavior" in result, (
            "user-added subdirectory bundle must be included"
        )
        assert "parallax-discovery" in result, (
            "user-added clean-URI bundle must be included"
        )
        assert "auto-nested" not in result, (
            "auto-discovered subdirectory bundle must be filtered out"
        )

    def test_settings_read_failure_still_filters_non_user_bundles(self, tmp_path: Path):
        """If settings.yaml can't be read, auto-discovered subdirectory bundles are still filtered.

        When AppSettings raises, added_bundles stays empty ({}), so no bundles
        are protected and the normal subdirectory filter applies to everything.
        """
        _write_registry(
            tmp_path,
            {
                "auto-nested": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main#subdirectory=behaviors/auto.yaml",
                    "is_root": True,
                },
            },
        )

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"auto-nested"}, set()),
            ),
            patch(
                "amplifier_app_cli.lib.bundle_loader.discovery.WELL_KNOWN_BUNDLES",
                {},
            ),
            patch(
                "amplifier_app_cli.lib.settings.AppSettings",
                side_effect=RuntimeError("settings unreadable"),
            ),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = discovery.list_cached_root_bundles()

        assert "auto-nested" not in result, (
            "when settings can't be read, auto-discovered subdirectory bundles "
            "must still be filtered (no bundles are protected)"
        )

    def test_result_is_sorted(self, tmp_path: Path):
        """list_cached_root_bundles() returns a sorted list."""
        _write_registry(tmp_path, {})

        added_bundles = {
            "zebra-bundle": "git+https://github.com/example/zebra@main",
            "alpha-bundle": "git+https://github.com/example/alpha@main",
            "middle-bundle": "git+https://github.com/example/middle@main",
        }

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=(set(), set()),
            ),
            patch(
                "amplifier_app_cli.lib.bundle_loader.discovery.WELL_KNOWN_BUNDLES",
                {},
            ),
            patch("amplifier_app_cli.lib.settings.AppSettings") as MockSettings,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            MockSettings.return_value.get_added_bundles.return_value = added_bundles
            result = discovery.list_cached_root_bundles()

        assert result == sorted(result), "result must be in sorted order"
        assert set(result) == set(added_bundles.keys()), (
            "all user-added bundles must appear"
        )
