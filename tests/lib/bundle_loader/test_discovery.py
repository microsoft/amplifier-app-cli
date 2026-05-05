"""Tests for AppBundleDiscovery.list_cached_root_bundles().

Validates the Step 4 subdirectory filter which uses base-URI comparison:

- A sub-bundle (URI contains #subdirectory=) is filtered when another root
  bundle in the list points to the same underlying git repo (same base URI).
- A sub-bundle is KEPT when its underlying repo is NOT tracked by any other
  root bundle — it is the only update-checkable entry for that repo.

The old exemption was "user-added bundles are NEVER filtered."
The new rule is "filter only when the root repo IS already tracked."
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
        """A sub-bundle is filtered when its root repo IS tracked by another root bundle.

        Under the new URI-comparison logic, filtering is triggered by the presence of
        a non-subdirectory root bundle pointing to the same underlying repo — not by
        whether the bundle was user-added.  Here ``some-bundle`` (clean URI) tracks
        the repo, so ``nested-behavior`` (#subdirectory=) is redundant and filtered.
        """
        _write_registry(
            tmp_path,
            {
                "nested-behavior": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main#subdirectory=behaviors/nested.yaml",
                    "is_root": True,
                },
                "some-bundle": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main",
                    "is_root": True,
                },
            },
        )

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"nested-behavior", "some-bundle"}, set()),
            ),
            patch(
                "amplifier_app_cli.lib.bundle_loader.discovery.WELL_KNOWN_BUNDLES",
                {},
            ),
            patch("amplifier_app_cli.lib.settings.AppSettings") as MockSettings,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            # User did NOT explicitly add these bundles
            MockSettings.return_value.get_added_bundles.return_value = {}
            result = discovery.list_cached_root_bundles()

        assert "nested-behavior" not in result, (
            "nested-behavior has #subdirectory= and its root repo is tracked by "
            "some-bundle, so it must be filtered out as redundant"
        )
        assert "some-bundle" in result, (
            "some-bundle is the root-repo entry and must remain"
        )

    def test_multiple_bundles_mixed_filtering(self, tmp_path: Path):
        """Correct bundles survive and are filtered in a realistic mixed scenario.

        - env-all: user-added, #subdirectory=, root NOT tracked → INCLUDED (only update target)
        - digital-twin-universe-behavior: user-added, #subdirectory=, root NOT tracked → INCLUDED
        - auto-nested: auto-discovered, #subdirectory=, root IS tracked → FILTERED
        - some-bundle: root entry for auto-nested's repo → INCLUDED
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
                # Root bundle for auto-nested's repo — this is what triggers the filter
                "some-bundle": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main",
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
                        "some-bundle",
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

        assert "env-all" in result, (
            "env-all root repo is not tracked by any other bundle → kept"
        )
        assert "digital-twin-universe-behavior" in result, (
            "digital-twin-universe-behavior root repo not tracked → kept"
        )
        assert "parallax-discovery" in result, (
            "parallax-discovery is a clean-URI root bundle → kept"
        )
        assert "some-bundle" in result, (
            "some-bundle is the root-repo entry → kept"
        )
        assert "auto-nested" not in result, (
            "auto-nested #subdirectory= bundle filtered because some-bundle tracks the same repo"
        )

    def test_settings_read_failure_still_filters_sub_bundle_when_root_tracked(
        self, tmp_path: Path
    ):
        """Sub-bundle filtering still works via URI comparison even when settings.yaml is unreadable.

        When AppSettings raises, added_bundles falls back to {}.  The filter still
        fires because ``some-bundle`` (clean URI) is in the registry and its normalised
        base URI matches the base of ``auto-nested`` (#subdirectory=).  This confirms
        the filter is driven by URI comparison, not by the user-added list.
        """
        _write_registry(
            tmp_path,
            {
                "auto-nested": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main#subdirectory=behaviors/auto.yaml",
                    "is_root": True,
                },
                "some-bundle": {
                    "uri": "git+https://github.com/microsoft/some-bundle@main",
                    "is_root": True,
                },
            },
        )

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"auto-nested", "some-bundle"}, set()),
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
            "auto-nested must be filtered: some-bundle tracks the same repo "
            "and this check uses only registry URIs — settings failure is irrelevant"
        )
        assert "some-bundle" in result, "some-bundle is the root entry and must remain"

    # -----------------------------------------------------------------------
    # New tests covering the base-URI comparison logic (spec cases a, b, c)
    # -----------------------------------------------------------------------

    def test_sub_bundle_filtered_when_root_repo_tracked(self, tmp_path: Path):
        """(spec case a) Sub-bundle filtered when the root repo is already tracked.

        Mirrors the real pair that triggered the bug report:
        amplifier-tester (clean URI) + amplifier-tester-behavior (#subdirectory=).
        Both point to the same github repo.  The behavior bundle must vanish from
        the update list because updating amplifier-tester covers it.
        """
        _write_registry(
            tmp_path,
            {
                "amplifier-tester": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main",
                    "is_root": True,
                },
                "amplifier-tester-behavior": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main#subdirectory=behaviors/amplifier-tester.yaml",
                    "is_root": True,
                },
            },
        )

        added_bundles = {
            "amplifier-tester-behavior": "git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main#subdirectory=behaviors/amplifier-tester.yaml",
        }

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"amplifier-tester", "amplifier-tester-behavior"}, set()),
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

        assert "amplifier-tester" in result, (
            "amplifier-tester is the root entry and must be kept"
        )
        assert "amplifier-tester-behavior" not in result, (
            "amplifier-tester-behavior is redundant: amplifier-tester tracks the same repo"
        )

    def test_sub_bundle_kept_when_root_repo_not_tracked(self, tmp_path: Path):
        """(spec case b) Sub-bundle kept when no non-subdirectory root tracks its repo.

        When a user added ONLY a behavior bundle (and not the root bundle), the
        sub-bundle must appear in the update list — it is the only update target
        for that repo.  Filtering it would silently break `amplifier update`.
        """
        _write_registry(
            tmp_path,
            {
                "reality-check-behavior": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-reality-check@main#subdirectory=behaviors/reality-check.yaml",
                    "is_root": True,
                },
                # Note: no "reality-check" root bundle — only the behavior was added
            },
        )

        added_bundles = {
            "reality-check-behavior": "git+https://github.com/microsoft/amplifier-bundle-reality-check@main#subdirectory=behaviors/reality-check.yaml",
        }

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=({"reality-check-behavior"}, set()),
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

        assert "reality-check-behavior" in result, (
            "reality-check-behavior must be kept: no root bundle tracks amplifier-bundle-reality-check, "
            "so this IS the update target for that repo"
        )

    def test_multiple_sub_bundles_same_repo_all_filtered_when_root_tracked(
        self, tmp_path: Path
    ):
        """(spec case c) Multiple sub-bundles of the same repo are ALL filtered when root tracked.

        Mirrors the env-all / behavior-env-all / execution-environments scenario:
        two different behavior bundles both point to amplifier-bundle-execution-environments.
        The root ``execution-environments`` bundle is also tracked, so both sub-bundles
        are redundant and must be removed.
        """
        _write_registry(
            tmp_path,
            {
                "execution-environments": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main",
                    "is_root": True,
                },
                "env-all": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
                    "is_root": True,
                },
                "behavior-env-all": {
                    "uri": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
                    "is_root": True,
                },
            },
        )

        added_bundles = {
            "env-all": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
            "behavior-env-all": "git+https://github.com/microsoft/amplifier-bundle-execution-environments@main#subdirectory=behaviors/env-all.yaml",
        }

        discovery = _make_discovery()

        with (
            patch.object(
                discovery,
                "_get_root_and_nested_bundles",
                return_value=(
                    {"execution-environments", "env-all", "behavior-env-all"},
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

        assert "execution-environments" in result, (
            "execution-environments is the root entry and must be kept"
        )
        assert "env-all" not in result, (
            "env-all is redundant: execution-environments tracks the same repo"
        )
        assert "behavior-env-all" not in result, (
            "behavior-env-all is redundant: execution-environments tracks the same repo"
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
