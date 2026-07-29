"""Test credential refresh during sub-session resume.

When session metadata is persisted, API keys are redacted to "[REDACTED]"
(security fix).  At resume time, live credentials must be re-derived from
user settings so provider calls succeed.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock
from unittest.mock import patch

from amplifier_app_cli.runtime.config import (
    apply_provider_overrides,
    expand_env_vars,
    map_provider_ids_to_instance_ids,
)
from amplifier_app_cli.runtime.session_resume import _refresh_resume_credentials


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

    refreshed = apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = map_provider_ids_to_instance_ids(refreshed)
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

    refreshed = apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = map_provider_ids_to_instance_ids(refreshed)
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
        refreshed = apply_provider_overrides(redacted["providers"], env_overrides)
        refreshed = map_provider_ids_to_instance_ids(refreshed)
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

    refreshed = apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = map_provider_ids_to_instance_ids(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})

    provider = result["providers"][0]
    assert provider["config"]["api_key"] == "sk-live-key"
    assert provider["instance_id"] == "anthropic-sonnet"


def test_credential_refresh_no_overrides_is_noop():
    """When no user overrides exist, config passes through unchanged."""
    redacted = _make_redacted_config()
    empty_overrides: list[dict] = []

    refreshed = apply_provider_overrides(redacted["providers"], empty_overrides)
    refreshed = map_provider_ids_to_instance_ids(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})

    # Keys remain redacted — no overrides to restore from
    anthropic = next(
        p for p in result["providers"] if p["module"] == "provider-anthropic"
    )
    assert anthropic["config"]["api_key"] == "[REDACTED]"


def test_resume_refreshes_hook_credentials_and_warns_for_unrestored_secrets(
    monkeypatch, caplog
):
    """Resume applies both general and notification hook settings before init."""
    settings = MagicMock()
    settings.get_provider_overrides.return_value = []
    settings.get_config_overrides.return_value = {
        "hooks-context": {
            "destinations": [
                {
                    "url": "${TEST_CONTEXT_URL}",
                    "api_key": "live-context-key",
                }
            ]
        }
    }
    settings.get_notification_hook_overrides.return_value = [
        {
            "module": "hooks-notify",
            "config": {"api_key": "live-notify-key"},
        }
    ]
    monkeypatch.setattr(
        "amplifier_app_cli.runtime.session_resume.AppSettings",
        lambda: settings,
    )
    monkeypatch.setenv("TEST_CONTEXT_URL", "https://context.example.test")
    config = {
        "hooks": [
            {
                "module": "hooks-context",
                "config": {
                    "destinations": [{"api_key": "[REDACTED]"}],
                },
            },
            {
                "module": "hooks-notify",
                "config": {"api_key": "[REDACTED]"},
            },
        ],
        "tools": [
            {
                "module": "tool-remote",
                "config": {"token": "[REDACTED]"},
            }
        ],
    }

    with caplog.at_level(logging.WARNING):
        refreshed = _refresh_resume_credentials(config, session_id="child-123")

    context_config = refreshed["hooks"][0]["config"]
    assert context_config["destinations"] == [
        {
            "url": "https://context.example.test",
            "api_key": "live-context-key",
        }
    ]
    assert refreshed["hooks"][1]["config"]["api_key"] == "live-notify-key"
    assert refreshed["tools"][0]["config"]["token"] == "[REDACTED]"
    assert "child-123" in caplog.text
    assert ".tools[0].config.token" in caplog.text
