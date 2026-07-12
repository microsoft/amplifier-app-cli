"""Regression tests: `anchors-amp-dev` is a registered, side-by-side bundle.

`anchors-amp-dev` is registered so it can be selected by name, but registration
must NOT change any default. It lives side by side with `amplifier-dev` and
`anchors`; no user's global or project default is affected.
"""
import importlib

from amplifier_app_cli.lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES

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


def test_registration_does_not_change_default():
    # The side-by-side guarantee: registering anchors-amp-dev must not flip the
    # no-active-bundle default away from `anchors`.
    tool_cmd = importlib.import_module("amplifier_app_cli.commands.tool")
    from unittest.mock import patch

    with patch.object(tool_cmd, "_get_active_bundle_name", return_value=None):
        use_bundle, bundle_name, _ = tool_cmd._should_use_bundle()
    assert use_bundle is True
    assert bundle_name == "anchors"


def test_amplifier_dev_still_registered_alongside():
    # anchors-amp-dev does not replace amplifier-dev; both coexist.
    assert "amplifier-dev" in WELL_KNOWN_BUNDLES
    assert "anchors-amp-dev" in WELL_KNOWN_BUNDLES
