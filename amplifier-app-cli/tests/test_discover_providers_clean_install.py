"""Tests for _discover_providers_from_sources() clean-install fix.

On a clean install none of the provider SDK dependencies (anthropic, openai,
google-generativeai, …) are present, so the initial import always fails.  The
fix calls ensure_provider_installed() for git-sourced providers when the import
fails, then retries get_provider_info().  If the install also fails the provider
still appears in the picker via _get_provider_display_name() fallback.
"""

from unittest.mock import MagicMock, patch


from amplifier_app_cli.provider_manager import ProviderManager
from amplifier_app_cli.provider_sources import DEFAULT_PROVIDER_SOURCES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> ProviderManager:
    """Return a ProviderManager with a default (no-op) config."""
    return ProviderManager()


def _failing_source(error: Exception | None = None) -> MagicMock:
    """Return a mock source whose resolve() raises, simulating a missing install."""
    mock = MagicMock()
    mock.resolve.side_effect = error or ImportError("No module named 'anthropic'")
    return mock


# ---------------------------------------------------------------------------
# Core fix: install on demand for git sources
# ---------------------------------------------------------------------------


class TestGitProviderInstalledOnDemand:
    """ensure_provider_installed() is called when a git provider can't be imported."""

    def test_provider_appears_after_successful_install(self):
        """Import fails → install succeeds → get_provider_info retry → full info registered."""
        manager = _make_manager()

        provider_info = {
            "display_name": "Anthropic",
            "description": "Anthropic Claude provider",
        }

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={
                    "provider-anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main"
                },
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=[
                    (
                        "provider-anthropic",
                        "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
                    )
                ],
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=True,
            ) as mock_ensure,
            patch(
                "amplifier_app_cli.provider_manager.get_provider_info",
                return_value=provider_info,
            ),
        ):
            providers = manager._discover_providers_from_sources()

        assert "provider-anthropic" in providers
        module_id, display_name, description = providers["provider-anthropic"]
        assert module_id == "provider-anthropic"
        assert display_name == "Anthropic"
        assert description == "Anthropic Claude provider"
        mock_ensure.assert_called_once_with(
            "provider-anthropic", config_manager=manager.config
        )

    def test_ensure_provider_installed_called_with_correct_module_id(self):
        """Verify module_id passed to ensure_provider_installed is the full prefixed ID."""
        manager = _make_manager()

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={
                    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main"
                },
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=[
                    (
                        "provider-openai",
                        "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
                    )
                ],
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=True,
            ) as mock_ensure,
            patch(
                "amplifier_app_cli.provider_manager.get_provider_info",
                return_value={
                    "display_name": "OpenAI",
                    "description": "OpenAI provider",
                },
            ),
        ):
            manager._discover_providers_from_sources()

        call_args = mock_ensure.call_args
        assert call_args[0][0] == "provider-openai"
        assert call_args[1]["config_manager"] is manager.config


# ---------------------------------------------------------------------------
# Fallback: provider still appears when install fails
# ---------------------------------------------------------------------------


class TestGitProviderFallbackOnInstallFailure:
    """Provider appears with display-name fallback when ensure_provider_installed() fails."""

    def test_provider_appears_with_display_name_when_install_returns_false(self):
        """Install returns False → provider still appears using _get_provider_display_name()."""
        manager = _make_manager()

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={
                    "provider-openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main"
                },
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=[
                    (
                        "provider-openai",
                        "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
                    )
                ],
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=False,
            ) as mock_ensure,
        ):
            providers = manager._discover_providers_from_sources()

        # Provider must still appear — not silently vanish
        assert "provider-openai" in providers
        module_id, display_name, description = providers["provider-openai"]
        assert module_id == "provider-openai"
        assert display_name == "OpenAI"
        mock_ensure.assert_called_once()

    def test_provider_display_names_are_correct_for_all_fallbacks(self):
        """When all installs fail, each provider gets its proper display name from the dict."""
        expected_names = {
            "provider-anthropic": "Anthropic",
            "provider-openai": "OpenAI",
            "provider-azure-openai": "Azure OpenAI",
            "provider-gemini": "Google Gemini",
            "provider-ollama": "Ollama",
            "provider-github-copilot": "GitHub Copilot",
            "provider-vllm": "vLLM",
        }
        manager = _make_manager()
        ordered = [(mid, uri) for mid, uri in expected_names.items()]

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={mid: "git+https://..." for mid in expected_names},
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=ordered,
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=False,
            ),
        ):
            providers = manager._discover_providers_from_sources()

        for module_id, expected_display in expected_names.items():
            assert module_id in providers, f"Provider {module_id} missing from list"
            assert providers[module_id][1] == expected_display, (
                f"{module_id}: expected '{expected_display}', "
                f"got '{providers[module_id][1]}'"
            )


# ---------------------------------------------------------------------------
# All 7 well-known providers appear on clean install (integration-style)
# ---------------------------------------------------------------------------


class TestAllSevenProvidersOnCleanInstall:
    """All 7 DEFAULT_PROVIDER_SOURCES appear in the list regardless of install outcome."""

    def test_all_providers_appear_when_all_installs_fail(self):
        """Worst case: every install fails — all 7 providers still appear via fallback."""
        manager = _make_manager()
        ordered = list(DEFAULT_PROVIDER_SOURCES.items())

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value=DEFAULT_PROVIDER_SOURCES,
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=ordered,
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=False,
            ),
        ):
            providers = manager._discover_providers_from_sources()

        for module_id in DEFAULT_PROVIDER_SOURCES:
            assert module_id in providers, (
                f"Provider {module_id} missing — should appear even when install fails"
            )

    def test_all_providers_appear_when_all_installs_succeed(self):
        """Happy path: every install succeeds — all 7 providers appear with full info."""
        manager = _make_manager()
        ordered = list(DEFAULT_PROVIDER_SOURCES.items())

        def fake_get_info(module_id: str) -> dict:
            name = module_id.replace("provider-", "")
            return {
                "display_name": name.title(),
                "description": f"Desc for {module_id}",
            }

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value=DEFAULT_PROVIDER_SOURCES,
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=ordered,
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.provider_manager.get_provider_info",
                side_effect=fake_get_info,
            ),
        ):
            providers = manager._discover_providers_from_sources()

        assert len(providers) == len(DEFAULT_PROVIDER_SOURCES)
        for module_id in DEFAULT_PROVIDER_SOURCES:
            assert module_id in providers


# ---------------------------------------------------------------------------
# Post-install info retry edge cases
# ---------------------------------------------------------------------------


class TestPostInstallInfoRetry:
    """get_provider_info() is retried after a successful install."""

    def test_display_name_fallback_when_info_still_none_after_install(self):
        """Install succeeds but get_provider_info still returns None → display name used."""
        manager = _make_manager()

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={
                    "provider-gemini": "git+https://github.com/microsoft/amplifier-module-provider-gemini@main"
                },
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=[
                    (
                        "provider-gemini",
                        "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
                    )
                ],
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.provider_manager.get_provider_info",
                return_value=None,  # Still None even after successful install
            ),
        ):
            providers = manager._discover_providers_from_sources()

        assert "provider-gemini" in providers
        _, display_name, _ = providers["provider-gemini"]
        assert display_name == "Google Gemini"

    def test_get_provider_info_called_after_ensure_installed_succeeds(self):
        """get_provider_info() is called exactly once in the retry path after install."""
        manager = _make_manager()

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={
                    "provider-ollama": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main"
                },
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=[
                    (
                        "provider-ollama",
                        "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
                    )
                ],
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
                return_value=True,
            ),
            patch(
                "amplifier_app_cli.provider_manager.get_provider_info",
                return_value={
                    "display_name": "Ollama",
                    "description": "Ollama provider",
                },
            ) as mock_get_info,
        ):
            providers = manager._discover_providers_from_sources()

        # get_provider_info should be called once (in the retry, not the initial try
        # since source.resolve() raised before reaching it)
        mock_get_info.assert_called_once_with("provider-ollama")
        assert "provider-ollama" in providers


# ---------------------------------------------------------------------------
# Ensure local source path is unchanged
# ---------------------------------------------------------------------------


class TestLocalSourcePathUnchanged:
    """ensure_provider_installed() is NOT called for local-path providers."""

    def test_ensure_not_called_for_local_source(self):
        """Local source still goes through the existing subprocess.run install path."""
        manager = _make_manager()

        with (
            patch(
                "amplifier_app_cli.provider_manager.get_effective_provider_sources",
                return_value={"provider-local": "./local/provider"},
            ),
            patch(
                "amplifier_app_cli.provider_manager._get_ordered_providers",
                return_value=[("provider-local", "./local/provider")],
            ),
            patch(
                "amplifier_app_cli.provider_manager.source_from_uri",
                return_value=_failing_source(),
            ),
            patch(
                "amplifier_app_cli.provider_manager.ensure_provider_installed",
            ) as mock_ensure,
            patch(
                "amplifier_app_cli.provider_manager.subprocess.run",
                return_value=MagicMock(returncode=1, stderr="local install failed"),
            ),
        ):
            manager._discover_providers_from_sources()

        # The new git-source code path must NOT fire for local providers
        mock_ensure.assert_not_called()
