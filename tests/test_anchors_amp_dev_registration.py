"""Visible built-in roots, canonical nested URIs, and retained internal/legacy aliases."""

from amplifier_app_cli.lib.bundle_loader.discovery import (
    DEFAULT_BUNDLE,
    WELL_KNOWN_BUNDLES,
)

EXPECTED_REMOTE = (
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=bundles/anchors-amp-dev/bundle.md"
)


def test_anchors_amp_dev_is_registered():
    assert "anchors-amp-dev" in WELL_KNOWN_BUNDLES
    entry = WELL_KNOWN_BUNDLES["anchors-amp-dev"]
    assert entry["remote"] == EXPECTED_REMOTE
    assert entry["package"] == ""  # bundle-only, no Python package
    assert entry["show_in_list"] is True


def test_only_anchors_roots_are_visible_and_amp_dev_is_default():
    assert DEFAULT_BUNDLE == "anchors-amp-dev"
    assert {
        name for name, info in WELL_KNOWN_BUNDLES.items() if info["show_in_list"]
    } == {"anchors", "anchors-amp-dev"}
    assert WELL_KNOWN_BUNDLES["anchors"]["remote"] == (
        "git+https://github.com/microsoft/amplifier-foundation@main"
        "#subdirectory=bundles/anchors/bundle.md"
    )


def test_internal_and_legacy_aliases_still_registered_but_hidden():
    for name in (
        "foundation",
        "amplifier-dev",
        "exp-delegation",
        "recipes",
        "design-intelligence",
        "notify",
        "modes",
        "routing-matrix",
    ):
        assert WELL_KNOWN_BUNDLES[name]["remote"]
        assert WELL_KNOWN_BUNDLES[name]["show_in_list"] is False
