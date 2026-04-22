"""Tests for OpenAI-Compatible (chat-completions) well-known provider registration.

Provider: amplifier-module-provider-chat-completions
  https://github.com/microsoft/amplifier-module-provider-chat-completions

Security-critical invariants are enforced in this file as regression guards.
Modifying them requires understanding the original security rationale — see
test docstrings before adjusting.
"""

from amplifier_app_cli.provider_env_detect import PROVIDER_CREDENTIAL_VARS
from amplifier_app_cli.provider_manager import _PROVIDER_DISPLAY_NAMES
from amplifier_app_cli.provider_sources import DEFAULT_PROVIDER_SOURCES
from amplifier_app_cli.provider_sources import PROVIDER_DEPENDENCIES


class TestChatCompletionsRegistration:
    """Verify Chat Completions is correctly registered in all well-known provider dicts."""

    def test_registered_in_provider_sources(self):
        """provider-chat-completions must be in DEFAULT_PROVIDER_SOURCES with the standalone repo URL.

        Invariant also enforced: the app-cli key here MUST match the entry-point
        name declared in the module's pyproject.toml:

            [project.entry-points."amplifier.modules"]
            provider-chat-completions = "amplifier_module_provider_chat_completions:mount"

        If this app-cli key and that entry-point name diverge, the provider installs
        successfully but never appears in `amplifier provider list` — silent failure
        in the picker. There is no cross-repo integration test today; this comment
        documents the contract.
        """
        assert "provider-chat-completions" in DEFAULT_PROVIDER_SOURCES
        url = DEFAULT_PROVIDER_SOURCES["provider-chat-completions"]
        assert (
            url
            == "git+https://github.com/microsoft/amplifier-module-provider-chat-completions@main"
        )
        # Regression guard: must NOT use the old bundle-subdirectory URL shape.
        # The provider was extracted to its own top-level repo; accidentally
        # reverting would break every CLI install.
        assert "#subdirectory=" not in url, (
            f"provider-chat-completions source URL must not use #subdirectory= "
            f"(the module is now standalone). Got: {url}"
        )
        assert "amplifier-bundle-chat-completions" not in url, (
            f"provider-chat-completions source URL must point at the standalone "
            f"amplifier-module-provider-chat-completions repo, not the deprecated "
            f"amplifier-bundle-chat-completions. Got: {url}"
        )

    def test_registered_in_display_names(self):
        """provider-chat-completions must have a display name entry.

        Note the key is WITHOUT the `provider-` prefix — `_get_provider_display_name()`
        in provider_manager.py:42 strips the prefix before the lookup.

        The chosen display name "OpenAI-Compatible" matches what the module itself
        reports via ChatCompletionsProvider.get_info().display_name, so users see
        the same string whether the provider is freshly installed or hits the
        fallback display-name path.
        """
        assert "chat-completions" in _PROVIDER_DISPLAY_NAMES
        assert _PROVIDER_DISPLAY_NAMES["chat-completions"] == "OpenAI-Compatible"

    def test_NOT_in_credential_vars_SECURITY(self):
        """SSRF/exfiltration regression guard: MUST NOT be in PROVIDER_CREDENTIAL_VARS.

        Why this test exists (SECURITY):
        --------------------------------
        `CHAT_COMPLETIONS_BASE_URL` is a user-supplied endpoint, not a cloud API key.
        Adding it to `PROVIDER_CREDENTIAL_VARS` would enable silent auto-configuration
        in non-TTY environments via `auto_init_from_env()` in commands/init.py — which
        would permanently write the env-var-resolved URL into `~/.amplifier/settings.yaml`
        on first startup. Attack surface:

          - Conversation content routed to an attacker-controlled server
          - SSRF to internal services (e.g. http://169.254.169.254/v1 for cloud metadata)
          - Leak of `CHAT_COMPLETIONS_API_KEY` via the `Authorization: Bearer` header
          - Prompt injection via attacker-controlled model responses
          - Persistent foothold — written to settings survives future CLI upgrades

        This provider is protected in TWO layers and both must remain:
          1. Absence from PROVIDER_CREDENTIAL_VARS (this test) — keeps
             auto_init_from_env() from picking it silently.
          2. mount() silent-skip in __init__.py:1148 — provider returns None if
             `base_url` is not explicitly configured, so a user who never
             configures it cannot accidentally use it.

        Precedent: `provider-vllm` is also absent from PROVIDER_CREDENTIAL_VARS for
        the same "user-configured endpoint, not a detectable cloud credential"
        reason.

        If this test starts failing, DO NOT just update the expected value. Re-read
        the security rationale above and the bundle repo's deprecation README, and
        confirm with a security reviewer before touching.
        """
        assert "provider-chat-completions" not in PROVIDER_CREDENTIAL_VARS, (
            "provider-chat-completions MUST NOT be in PROVIDER_CREDENTIAL_VARS — "
            "this is a security regression guard. See test docstring for the "
            "SSRF/exfiltration rationale before 'fixing' by adding it."
        )

        # Also guard the env-var name itself — someone could register
        # CHAT_COMPLETIONS_BASE_URL under a different provider key and achieve the
        # same auto-init effect. Per zen-architect COE review.
        for provider_id, env_vars in PROVIDER_CREDENTIAL_VARS.items():
            assert "CHAT_COMPLETIONS_BASE_URL" not in env_vars, (
                f"CHAT_COMPLETIONS_BASE_URL must not appear under any provider "
                f"(found under {provider_id}). See SSRF rationale above."
            )
            assert "CHAT_COMPLETIONS_API_KEY" not in env_vars, (
                f"CHAT_COMPLETIONS_API_KEY must not appear under any provider "
                f"(found under {provider_id}). See SSRF rationale above."
            )

    def test_no_provider_dependencies(self):
        """Chat Completions has no cross-provider inheritance; must not appear in PROVIDER_DEPENDENCIES.

        Unlike provider-azure-openai (which subclasses OpenAIProvider at runtime),
        provider-chat-completions is self-contained — its only external dependency
        is the `openai` Python SDK (for the OpenAI-compatible HTTP client).
        Listing it here would imply an install-order requirement that doesn't exist.
        """
        assert "provider-chat-completions" not in PROVIDER_DEPENDENCIES
