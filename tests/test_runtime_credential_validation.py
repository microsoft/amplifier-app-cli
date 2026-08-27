"""Tests for the runtime credential-fallback hardening.

Covers `_validate_provider_credentials()` (amplifier_app_cli.runtime.config),
added alongside the reuse-or-separate multi-instance credential wizard
(docs/designs/provider-instance-credentials.md). Without this guard, an
unresolved *separate* credential placeholder expands to "" at runtime and
several provider modules treat that as "not configured", silently falling
back to their own canonical ambient env var -- routing the "separate"
instance through the WRONG account's key. This must fail loudly instead.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_app_cli.runtime.config import _validate_provider_credentials


def _provider_info(env_var: str = "OPENAI_API_KEY", required: bool = True) -> dict:
    return {
        "display_name": "OpenAI",
        "config_fields": [
            {
                "id": "api_key",
                "display_name": "API Key",
                "field_type": "secret",
                "prompt": "Enter your API key",
                "env_var": env_var,
                "required": required,
            }
        ],
    }


class TestValidateProviderCredentials:
    def test_unset_separate_binding_fails_loud(self, monkeypatch):
        """The core hardening case: a required secret field whose ${VAR}
        placeholder resolves to nothing must raise BEFORE session mount,
        instead of silently letting expand_env_vars turn it into "" (which
        would let the provider module fall back to its own ambient var --
        e.g. a *different* account's OPENAI_API_KEY)."""
        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-shared-account-key")

        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ), pytest.raises(ValueError, match="OPENAI_WORK_API_KEY"):
            _validate_provider_credentials(providers)

    def test_set_credential_does_not_raise(self, monkeypatch):
        """A required secret field whose env var IS set must pass silently."""
        monkeypatch.setenv("OPENAI_WORK_API_KEY", "sk-distinct-value")

        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ):
            _validate_provider_credentials(providers)  # must not raise

    def test_shared_binding_with_set_var_does_not_raise(self, monkeypatch):
        """Two instances sharing the SAME credential env var (the "reuse"
        path) must not raise as long as that shared var is set -- this is
        the normal, intended shared-credential configuration."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared-value")

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-opus",
                "config": {"api_key": "${ANTHROPIC_API_KEY}"},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"api_key": "${ANTHROPIC_API_KEY}"},
            },
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="ANTHROPIC_API_KEY"),
        ):
            _validate_provider_credentials(providers)  # must not raise

    def test_optional_keyless_secret_field_unset_does_not_raise(self, monkeypatch):
        """A secret field declared `required=False` (e.g. a local/keyless
        Chat Completions server) must be left alone even when unset --
        the fail-loud guard is scoped to REQUIRED credential fields only."""
        monkeypatch.delenv("LOCAL_SERVER_API_KEY", raising=False)

        providers = [
            {
                "module": "provider-chat-completions",
                "id": "local-server",
                "config": {"api_key": "${LOCAL_SERVER_API_KEY}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="LOCAL_SERVER_API_KEY", required=False),
        ):
            _validate_provider_credentials(providers)  # must not raise

    def test_missing_provider_metadata_skips_validation(self, monkeypatch):
        """When provider metadata can't be loaded (custom/removed provider,
        import error, etc.) validation is skipped for that entry rather
        than blocking session start -- consistent with how the rest of
        this module treats a missing get_provider_info() result."""
        monkeypatch.delenv("SOME_UNSET_VAR", raising=False)

        providers = [
            {
                "module": "provider-custom-thing",
                "id": "custom",
                "config": {"api_key": "${SOME_UNSET_VAR}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=None,
        ):
            _validate_provider_credentials(providers)  # must not raise

    def test_literal_value_is_not_validated(self, monkeypatch):
        """A literal (non-placeholder) config value is untouched by this
        guard -- it isn't an unresolved env var reference at all."""
        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "sk-literal-value-not-a-placeholder"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ):
            _validate_provider_credentials(providers)  # must not raise

    def test_inline_default_placeholder_is_not_validated(self, monkeypatch):
        """A placeholder carrying an inline default (${VAR:default}) already
        has its own "unset" handling via expand_env_vars -- this guard only
        concerns itself with bare ${VAR} placeholders."""
        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)

        providers = [
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY:not-needed}"},
            }
        ]

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
        ):
            _validate_provider_credentials(providers)  # must not raise

    def test_multiple_providers_identifies_the_failing_instance(self, monkeypatch):
        """With several configured providers, the error must name the
        instance and env var actually at fault, not a generic message."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fine")
        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)

        providers = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-opus",
                "config": {"api_key": "${ANTHROPIC_API_KEY}"},
            },
            {
                "module": "provider-openai",
                "id": "openai-work",
                "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
            },
        ]

        def _info(module_id: str):
            if module_id == "provider-anthropic":
                return _provider_info(env_var="ANTHROPIC_API_KEY")
            return _provider_info(env_var="OPENAI_WORK_API_KEY")

        with patch(
            "amplifier_app_cli.runtime.config.get_provider_info",
            side_effect=_info,
        ), pytest.raises(ValueError) as exc_info:
            _validate_provider_credentials(providers)

        assert "OPENAI_WORK_API_KEY" in str(exc_info.value)
        assert "openai-work" in str(exc_info.value)

    def test_non_dict_or_non_module_entries_are_skipped(self):
        """Malformed provider entries must not crash the guard."""
        providers = [
            "not-a-dict",
            {"config": {"api_key": "${SOMETHING}"}},  # no "module"
            {"module": 123, "config": {"api_key": "${SOMETHING}"}},  # bad module type
            {"module": "provider-x", "config": "not-a-dict"},  # bad config type
        ]
        _validate_provider_credentials(providers)  # must not raise


# ============================================================
# Integration: the guard actually runs inside resolve_bundle_config(),
# before expand_env_vars() would otherwise silently launder the unset
# placeholder into "".
# ============================================================


class TestValidateProviderCredentialsIntegration:
    @pytest.mark.asyncio
    async def test_resolve_bundle_config_raises_for_unset_separate_credential(
        self, monkeypatch
    ):
        from amplifier_app_cli.runtime.config import resolve_bundle_config

        monkeypatch.delenv("OPENAI_WORK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-shared-account-key")

        mount_plan = {
            "providers": [
                {
                    "module": "provider-openai",
                    "id": "openai-work",
                    "config": {"api_key": "${OPENAI_WORK_API_KEY}"},
                }
            ],
        }
        mock_prepared = MagicMock()
        mock_prepared.mount_plan = mount_plan
        mock_prepared.bundle.load_agent_metadata = MagicMock()

        settings = MagicMock()
        settings.get_config_overrides.return_value = {}
        settings.get_provider_overrides.return_value = []
        settings.get_tool_overrides.return_value = []
        settings.get_notification_hook_overrides.return_value = []
        settings.get_routing_config.return_value = None
        settings.get_source_overrides.return_value = {}
        settings.get_module_sources.return_value = {}
        settings.get_bundle_sources.return_value = {}

        with (
            patch(
                "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
                new_callable=AsyncMock,
                return_value=mock_prepared,
            ),
            patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
            patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
            patch(
                "amplifier_app_cli.runtime.config.get_provider_info",
                return_value=_provider_info(env_var="OPENAI_WORK_API_KEY"),
            ),pytest.raises(ValueError, match="OPENAI_WORK_API_KEY")
        ):
            await resolve_bundle_config(bundle_name="test", app_settings=settings)
