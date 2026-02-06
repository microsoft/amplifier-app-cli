"""Tests for bundle remove command - app bundle cleanup (issue #40).

Verifies that `amplifier bundle remove <name>` (without --app) also
removes the bundle from bundle.app in settings.yaml, preventing
"ghost" bundles whose hooks continue to fire after removal.
"""

import pytest
from unittest.mock import MagicMock, patch
from click.testing import CliRunner

from amplifier_app_cli.commands.bundle import bundle


@pytest.fixture
def mock_app_settings():
    """Create a mock AppSettings with controllable bundle state."""
    mock = MagicMock()

    # Default: bundle exists in both added and app
    added_bundles = {
        "team-tracking": "git+https://github.com/org/team-tracking@main",
        "my-custom-bundle": "git+https://github.com/org/my-custom-bundle@main",
    }
    app_bundles = [
        "git+https://github.com/org/team-tracking@main",
    ]

    mock.get_added_bundles.return_value = added_bundles.copy()
    mock.get_app_bundles.return_value = app_bundles.copy()
    mock.remove_added_bundle.return_value = True
    mock.remove_app_bundle.return_value = True

    return mock


@pytest.fixture
def runner():
    return CliRunner()


class TestBundleRemoveAlsoRemovesAppRegistration:
    """Issue #40: bundle remove should also remove app registration."""

    def test_remove_bundle_that_is_also_app_bundle(self, runner, mock_app_settings):
        """Removing a bundle that was added with --app should clean up both locations."""
        with (
            patch(
                "amplifier_app_cli.commands.bundle.AppSettings",
                return_value=mock_app_settings,
            ),
            patch(
                "amplifier_app_cli.commands.bundle.create_bundle_registry",
            ) as mock_registry_factory,
        ):
            mock_registry = MagicMock()
            mock_registry.unregister.return_value = True
            mock_registry_factory.return_value = mock_registry

            result = runner.invoke(bundle, ["remove", "team-tracking"])

            assert result.exit_code == 0

            # Must remove from bundle.added
            mock_app_settings.remove_added_bundle.assert_called_once_with(
                "team-tracking"
            )

            # Must ALSO remove from bundle.app (the fix for issue #40)
            mock_app_settings.remove_app_bundle.assert_called_once_with(
                "git+https://github.com/org/team-tracking@main"
            )

            assert "Removed bundle" in result.output
            assert "app bundles" in result.output

    def test_remove_bundle_not_in_app_bundles(self, runner, mock_app_settings):
        """Removing a bundle that is NOT an app bundle should not touch bundle.app."""
        mock_app_settings.get_app_bundles.return_value = []

        with (
            patch(
                "amplifier_app_cli.commands.bundle.AppSettings",
                return_value=mock_app_settings,
            ),
            patch(
                "amplifier_app_cli.commands.bundle.create_bundle_registry",
            ) as mock_registry_factory,
        ):
            mock_registry = MagicMock()
            mock_registry.unregister.return_value = True
            mock_registry_factory.return_value = mock_registry

            result = runner.invoke(bundle, ["remove", "my-custom-bundle"])

            assert result.exit_code == 0

            # Should remove from bundle.added
            mock_app_settings.remove_added_bundle.assert_called_once_with(
                "my-custom-bundle"
            )

            # Should NOT call remove_app_bundle (not an app bundle)
            mock_app_settings.remove_app_bundle.assert_not_called()

    def test_remove_bundle_fallback_name_match_in_app_bundles(
        self, runner, mock_app_settings
    ):
        """If bundle not in added but name matches app URI, still remove from app."""
        # Bundle not in added (already removed or never was there)
        mock_app_settings.get_added_bundles.return_value = {}
        mock_app_settings.remove_added_bundle.return_value = False
        mock_app_settings.get_app_bundles.return_value = [
            "git+https://github.com/org/team-tracking@main",
        ]

        with (
            patch(
                "amplifier_app_cli.commands.bundle.AppSettings",
                return_value=mock_app_settings,
            ),
            patch(
                "amplifier_app_cli.commands.bundle.create_bundle_registry",
            ) as mock_registry_factory,
        ):
            mock_registry = MagicMock()
            mock_registry.unregister.return_value = False
            mock_registry_factory.return_value = mock_registry

            result = runner.invoke(bundle, ["remove", "team-tracking"])

            assert result.exit_code == 0

            # Should still remove from bundle.app via name matching
            mock_app_settings.remove_app_bundle.assert_called_once_with(
                "git+https://github.com/org/team-tracking@main"
            )

    def test_remove_well_known_bundle_rejected(self, runner, mock_app_settings):
        """Well-known bundles (like 'foundation') cannot be removed."""
        with patch(
            "amplifier_app_cli.commands.bundle.AppSettings",
            return_value=mock_app_settings,
        ):
            result = runner.invoke(bundle, ["remove", "foundation"])

            assert result.exit_code != 0
            assert "Cannot remove well-known bundle" in result.output


class TestBundleRemoveWithAppFlag:
    """Verify the --app flag path still works correctly."""

    def test_remove_app_bundle_by_uri(self, runner, mock_app_settings):
        """--app flag with exact URI should remove from both locations."""
        uri = "git+https://github.com/org/team-tracking@main"

        with patch(
            "amplifier_app_cli.commands.bundle.AppSettings",
            return_value=mock_app_settings,
        ):
            result = runner.invoke(bundle, ["remove", uri, "--app"])

            assert result.exit_code == 0
            mock_app_settings.remove_app_bundle.assert_called_once_with(uri)
            mock_app_settings.remove_added_bundle.assert_called_once_with(uri)

    def test_remove_app_bundle_by_name(self, runner, mock_app_settings):
        """--app flag with name should find matching URI and remove."""
        with patch(
            "amplifier_app_cli.commands.bundle.AppSettings",
            return_value=mock_app_settings,
        ):
            result = runner.invoke(bundle, ["remove", "team-tracking", "--app"])

            assert result.exit_code == 0
            mock_app_settings.remove_app_bundle.assert_called_once_with(
                "git+https://github.com/org/team-tracking@main"
            )
