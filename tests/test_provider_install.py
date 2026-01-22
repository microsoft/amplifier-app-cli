"""Tests for the provider install command."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_app_cli.commands.provider import provider


class TestProviderInstall:
    """Tests for the `amplifier provider install` command."""

    @pytest.fixture
    def runner(self):
        """Create a Click test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_sources(self):
        """Mock the known provider sources."""
        return {
            "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
            "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
            "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
        }

    def test_install_all_providers_default(self, runner, mock_sources):
        """Running without arguments should install all known providers."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.install_known_providers",
                return_value=[
                    "provider-anthropic",
                    "provider-openai",
                    "provider-ollama",
                ],
            ) as mock_install,
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
        ):
            result = runner.invoke(provider, ["install"])

            assert result.exit_code == 0
            assert "Installing all known providers" in result.output
            assert "Installed 3 provider(s)" in result.output
            mock_install.assert_called_once()

    def test_install_specific_provider(self, runner, mock_sources):
        """Should install only the specified provider."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed",
                return_value=True,
            ) as mock_install,
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[]),
        ):
            result = runner.invoke(provider, ["install", "anthropic"])

            assert result.exit_code == 0
            assert "Provider installation complete" in result.output
            mock_install.assert_called_once()
            # Verify the module_id was normalized correctly
            call_args = mock_install.call_args
            assert call_args[0][0] == "provider-anthropic"

    def test_install_multiple_providers(self, runner, mock_sources):
        """Should install multiple specified providers."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed",
                return_value=True,
            ) as mock_install,
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[]),
        ):
            result = runner.invoke(provider, ["install", "anthropic", "openai"])

            assert result.exit_code == 0
            assert mock_install.call_count == 2

    def test_install_unknown_provider_fails(self, runner, mock_sources):
        """Should fail when an unknown provider is specified."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
        ):
            result = runner.invoke(provider, ["install", "unknown-provider"])

            assert result.exit_code == 1
            assert "Unknown provider" in result.output
            assert "Known providers:" in result.output

    def test_install_quiet_mode_no_output_on_success(self, runner, mock_sources):
        """Quiet mode should suppress output on success."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.install_known_providers",
                return_value=["provider-anthropic"],
            ),
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
        ):
            result = runner.invoke(provider, ["install", "-q"])

            assert result.exit_code == 0
            # Output should be empty or minimal in quiet mode
            assert "Installing" not in result.output
            assert "Installed" not in result.output

    def test_install_quiet_mode_exits_on_failure(self, runner, mock_sources):
        """Quiet mode should exit with error code on failure."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed",
                return_value=False,
            ),
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[]),
        ):
            result = runner.invoke(provider, ["install", "-q", "anthropic"])

            assert result.exit_code == 1

    def test_install_already_installed_skips(self, runner, mock_sources):
        """Should skip already installed providers unless --force is used."""
        # Create a mock entry point that indicates the provider is installed
        mock_ep = MagicMock()
        mock_ep.name = "provider-anthropic"

        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed"
            ) as mock_install,
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[mock_ep]),
        ):
            result = runner.invoke(provider, ["install", "anthropic"])

            assert result.exit_code == 0
            assert "already installed" in result.output
            # Should not have called install since already installed
            mock_install.assert_not_called()

    def test_install_force_reinstalls(self, runner, mock_sources):
        """--force should reinstall even if already installed."""
        mock_ep = MagicMock()
        mock_ep.name = "provider-anthropic"

        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed",
                return_value=True,
            ) as mock_install,
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[mock_ep]),
        ):
            result = runner.invoke(provider, ["install", "--force", "anthropic"])

            assert result.exit_code == 0
            # Should have called install despite being already installed
            mock_install.assert_called_once()

    def test_install_handles_provider_prefix(self, runner, mock_sources):
        """Should handle both 'anthropic' and 'provider-anthropic' inputs."""
        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed",
                return_value=True,
            ) as mock_install,
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[]),
        ):
            # Test with full prefix
            result = runner.invoke(provider, ["install", "provider-anthropic"])

            assert result.exit_code == 0
            call_args = mock_install.call_args
            assert call_args[0][0] == "provider-anthropic"

    def test_install_partial_failure_reports_and_exits(self, runner, mock_sources):
        """Should report failures and exit with error code."""
        # First call succeeds, second fails
        install_results = [True, False]

        def mock_install_side_effect(*args, **kwargs):
            return install_results.pop(0)

        with (
            patch(
                "amplifier_app_cli.commands.provider.get_effective_provider_sources",
                return_value=mock_sources,
            ),
            patch(
                "amplifier_app_cli.commands.provider.ensure_provider_installed",
                side_effect=mock_install_side_effect,
            ),
            patch("amplifier_app_cli.commands.provider.create_config_manager"),
            patch("importlib.metadata.entry_points", return_value=[]),
        ):
            result = runner.invoke(provider, ["install", "anthropic", "openai"])

            assert result.exit_code == 1
            assert "Failed to install" in result.output


class TestProviderInstallHelp:
    """Tests for provider install command help and documentation."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_install_appears_in_provider_help(self, runner):
        """The install subcommand should appear in provider --help."""
        result = runner.invoke(provider, ["--help"])
        assert "install" in result.output

    def test_install_help_shows_examples(self, runner):
        """The install command help should show usage examples."""
        result = runner.invoke(provider, ["install", "--help"])
        assert result.exit_code == 0
        assert "amplifier provider install" in result.output
        assert "--quiet" in result.output or "-q" in result.output
        assert "--force" in result.output
