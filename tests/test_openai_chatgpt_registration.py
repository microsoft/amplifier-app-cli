"""Tests for OpenAI ChatGPT well-known (first-class) provider registration.

At f9a2e15 (see `git log -p -- amplifier_app_cli/provider_sources.py`),
provider-openai-chatgpt had never been added to DEFAULT_PROVIDER_SOURCES --
confirmed absent, and no commit anywhere in history ever added it. This
made `provider install openai-chatgpt` / `provider add openai-chatgpt`
work only by accident (e.g. a local source override), not as a first-class
well-known provider like anthropic/openai/gemini/etc.

Deliberately does NOT add a PROVIDER_DEPENDENCIES entry: the
openai-chatgpt module is standalone (verified) and doesn't extend another
provider's class the way azure-openai extends openai.
"""


class TestOpenAIChatGPTRegistration:
    """Verify OpenAI ChatGPT is registered as a well-known provider."""

    def test_registered_in_provider_sources(self):
        """OpenAI ChatGPT should be in DEFAULT_PROVIDER_SOURCES."""
        from amplifier_app_cli.provider_sources import DEFAULT_PROVIDER_SOURCES

        assert "provider-openai-chatgpt" in DEFAULT_PROVIDER_SOURCES
        assert (
            "amplifier-module-provider-openai-chatgpt"
            in DEFAULT_PROVIDER_SOURCES["provider-openai-chatgpt"]
        )

    def test_registered_in_display_names(self):
        """OpenAI ChatGPT should be in _PROVIDER_DISPLAY_NAMES."""
        from amplifier_app_cli.provider_manager import _PROVIDER_DISPLAY_NAMES

        assert "openai-chatgpt" in _PROVIDER_DISPLAY_NAMES
        assert _PROVIDER_DISPLAY_NAMES["openai-chatgpt"] == "OpenAI ChatGPT"

    def test_not_registered_as_a_runtime_dependency(self):
        """openai-chatgpt is standalone -- it must not appear in
        PROVIDER_DEPENDENCIES (that's reserved for providers that extend
        another provider's class at runtime, e.g. azure-openai -> openai)."""
        from amplifier_app_cli.provider_sources import PROVIDER_DEPENDENCIES

        assert "provider-openai-chatgpt" not in PROVIDER_DEPENDENCIES
        for dependent, deps in PROVIDER_DEPENDENCIES.items():
            assert "provider-openai-chatgpt" not in deps, (
                f"provider-openai-chatgpt should not be a dependency of {dependent}"
            )

    def test_effective_sources_include_openai_chatgpt_with_no_config_manager(self):
        """get_effective_provider_sources() with no config_manager should
        still surface the well-known default."""
        from amplifier_app_cli.provider_sources import get_effective_provider_sources

        sources = get_effective_provider_sources(None)
        assert "provider-openai-chatgpt" in sources
