"""Tests for GitHub Copilot well-known provider registration."""


class TestGitHubCopilotRegistration:
    """Verify GitHub Copilot is registered in all well-known provider dicts."""

    def test_registered_in_provider_sources(self):
        """GitHub Copilot should be in DEFAULT_PROVIDER_SOURCES."""
        from amplifier_app_cli.provider_sources import DEFAULT_PROVIDER_SOURCES

        assert "provider-github-copilot" in DEFAULT_PROVIDER_SOURCES
        assert (
            "amplifier-module-provider-github-copilot"
            in DEFAULT_PROVIDER_SOURCES["provider-github-copilot"]
        )

    def test_registered_in_env_detect(self):
        """GitHub Copilot should be in PROVIDER_CREDENTIAL_VARS."""
        from amplifier_app_cli.provider_env_detect import PROVIDER_CREDENTIAL_VARS

        assert "provider-github-copilot" in PROVIDER_CREDENTIAL_VARS
        assert "GITHUB_TOKEN" in PROVIDER_CREDENTIAL_VARS["provider-github-copilot"]

    def test_registered_in_display_names(self):
        """GitHub Copilot should be in _PROVIDER_DISPLAY_NAMES."""
        from amplifier_app_cli.provider_manager import _PROVIDER_DISPLAY_NAMES

        assert "github-copilot" in _PROVIDER_DISPLAY_NAMES
        assert _PROVIDER_DISPLAY_NAMES["github-copilot"] == "GitHub Copilot"

    def test_copilot_before_ollama_in_env_detect(self):
        """Copilot should have higher priority than Ollama in detection order."""
        from amplifier_app_cli.provider_env_detect import PROVIDER_CREDENTIAL_VARS

        keys = list(PROVIDER_CREDENTIAL_VARS.keys())
        copilot_idx = keys.index("provider-github-copilot")
        ollama_idx = keys.index("provider-ollama")
        assert copilot_idx < ollama_idx, (
            "Copilot should appear before Ollama in PROVIDER_CREDENTIAL_VARS"
        )
