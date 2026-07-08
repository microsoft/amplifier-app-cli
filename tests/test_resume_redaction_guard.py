"""Tests for the fail-loud redaction guard used during sub-session resume.

At resume time, secret-bearing config fields that were redacted to the
sentinel "[REDACTED]" before persistence must be re-hydrated from live
settings.  Anything the refresh pass could NOT restore is detected by
_find_redacted_values so it is logged loudly instead of being silently
mounted (which would surface downstream as a misleading 401).

The guard scans the ENTIRE merged config, not just hooks: a provider entry
with no matching live override keeps its redacted key, tools are not
re-hydrated on resume, and any of these can also appear agent-scoped under
agents[*].  These tests pin that whole-config coverage.
"""

from __future__ import annotations

from amplifier_app_cli.session_spawner import (
    _REDACTION_SENTINEL,
    _find_redacted_values,
)


def test_clean_config_reports_nothing():
    config = {
        "providers": [
            {"module": "provider-anthropic", "config": {"api_key": "sk-live"}}
        ],
        "tools": [{"module": "tool-bash"}],
        "hooks": [{"module": "hook-x", "config": {"token": "live-token"}}],
    }
    assert _find_redacted_values(config) == []


def test_redacted_provider_is_detected():
    """A provider with no live override keeps the sentinel -- the original gap."""
    config = {
        "providers": [
            {
                "module": "provider-anthropic",
                "config": {"api_key": _REDACTION_SENTINEL},
            },
        ],
    }
    found = _find_redacted_values(config)
    assert found == [".providers[0].config.api_key"]


def test_redacted_hook_is_detected():
    config = {
        "hooks": [
            {
                "module": "hook-webhook",
                "config": {"destinations": [{"api_key": _REDACTION_SENTINEL}]},
            },
        ],
    }
    found = _find_redacted_values(config)
    assert found == [".hooks[0].config.destinations[0].api_key"]


def test_redacted_tool_is_detected():
    """Tools are not re-hydrated on resume; the guard must still surface them."""
    config = {
        "tools": [
            {"module": "tool-remote", "config": {"token": _REDACTION_SENTINEL}},
        ],
    }
    found = _find_redacted_values(config)
    assert found == [".tools[0].config.token"]


def test_agent_scoped_secret_is_detected():
    """Secrets nested under agents[*] were invisible to the old hooks-only scan."""
    config = {
        "agents": {
            "researcher": {
                "providers": [
                    {
                        "module": "provider-openai",
                        "config": {"api_key": _REDACTION_SENTINEL},
                    },
                ],
            },
        },
    }
    found = _find_redacted_values(config)
    assert found == [".agents.researcher.providers[0].config.api_key"]


def test_multiple_redactions_across_sections():
    config = {
        "providers": [
            {"module": "provider-anthropic", "config": {"api_key": _REDACTION_SENTINEL}}
        ],
        "hooks": [{"module": "hook-x", "config": {"token": _REDACTION_SENTINEL}}],
    }
    found = _find_redacted_values(config)
    assert set(found) == {
        ".providers[0].config.api_key",
        ".hooks[0].config.token",
    }
