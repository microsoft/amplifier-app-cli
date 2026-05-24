"""Test credential refresh during sub-session resume.

When session metadata is persisted, API keys are redacted to "[REDACTED]"
(security fix).  At resume time, live credentials must be re-derived from
user settings so provider calls succeed.
"""

from __future__ import annotations

from unittest.mock import patch

from amplifier_app_cli.runtime.config import (
    _apply_provider_overrides,
    _map_id_to_instance_id,
    expand_env_vars,
)


def _make_redacted_config() -> dict:
    """Config as it would be loaded from disk after redaction."""
    return {
        "providers": [
            {
                "module": "provider-anthropic",
                "config": {
                    "api_key": "[REDACTED]",
                    "model": "claude-sonnet-4-20250514",
                },
            },
            {
                "module": "provider-openai",
                "config": {"api_key": "[REDACTED]", "model": "gpt-4o"},
            },
        ],
        "tools": [{"module": "tool-bash"}],
    }


def _make_live_overrides() -> list[dict]:
    """User settings overrides with live credentials."""
    return [
        {
            "module": "provider-anthropic",
            "config": {"api_key": "sk-ant-live-key-123"},
        },
        {
            "module": "provider-openai",
            "config": {"api_key": "sk-openai-live-key-456"},
        },
    ]


def test_credential_refresh_restores_api_keys():
    """Redacted api_key values are replaced with live credentials."""
    redacted = _make_redacted_config()
    live_overrides = _make_live_overrides()

    refreshed = _apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = _map_id_to_instance_id(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})

    anthropic = next(
        p for p in result["providers"] if p["module"] == "provider-anthropic"
    )
    openai = next(p for p in result["providers"] if p["module"] == "provider-openai")

    assert anthropic["config"]["api_key"] == "sk-ant-live-key-123"
    assert openai["config"]["api_key"] == "sk-openai-live-key-456"


def test_credential_refresh_preserves_non_credential_config():
    """Agent-specific provider config (model, routing) survives the refresh."""
    redacted = _make_redacted_config()
    live_overrides = _make_live_overrides()

    refreshed = _apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = _map_id_to_instance_id(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})

    anthropic = next(
        p for p in result["providers"] if p["module"] == "provider-anthropic"
    )
    openai = next(p for p in result["providers"] if p["module"] == "provider-openai")

    # Model selections from the original spawn config must survive
    assert anthropic["config"]["model"] == "claude-sonnet-4-20250514"
    assert openai["config"]["model"] == "gpt-4o"

    # Non-provider config must be untouched
    assert result["tools"] == [{"module": "tool-bash"}]


def test_credential_refresh_handles_env_var_overrides():
    """Settings with ${VAR} references are expanded to real values."""
    redacted = _make_redacted_config()
    env_overrides = [
        {
            "module": "provider-anthropic",
            "config": {"api_key": "${TEST_ANTHROPIC_KEY}"},
        },
    ]

    with patch.dict("os.environ", {"TEST_ANTHROPIC_KEY": "sk-from-env-789"}):
        refreshed = _apply_provider_overrides(redacted["providers"], env_overrides)
        refreshed = _map_id_to_instance_id(refreshed)
        result = expand_env_vars({**redacted, "providers": refreshed})

    anthropic = next(
        p for p in result["providers"] if p["module"] == "provider-anthropic"
    )
    assert anthropic["config"]["api_key"] == "sk-from-env-789"


def test_credential_refresh_with_id_mapping():
    """Providers with 'id' field get 'instance_id' mapped for kernel compat."""
    redacted = {
        "providers": [
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"api_key": "[REDACTED]"},
            },
        ],
    }
    live_overrides = [
        {
            "module": "provider-anthropic",
            "id": "anthropic-sonnet",
            "config": {"api_key": "sk-live-key"},
        },
    ]

    refreshed = _apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = _map_id_to_instance_id(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})

    provider = result["providers"][0]
    assert provider["config"]["api_key"] == "sk-live-key"
    assert provider["instance_id"] == "anthropic-sonnet"


def test_credential_refresh_no_overrides_is_noop():
    """When no user overrides exist, config passes through unchanged."""
    redacted = _make_redacted_config()
    empty_overrides: list[dict] = []

    refreshed = _apply_provider_overrides(redacted["providers"], empty_overrides)
    refreshed = _map_id_to_instance_id(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})

    # Keys remain redacted — no overrides to restore from
    anthropic = next(
        p for p in result["providers"] if p["module"] == "provider-anthropic"
    )
    assert anthropic["config"]["api_key"] == "[REDACTED]"
