"""Regression coverage for the staged runtime configuration boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from amplifier_app_cli.runtime import config
from amplifier_app_cli.runtime import config_behaviors
from amplifier_app_cli.runtime import config_merge
from amplifier_app_cli.runtime import config_policies
from amplifier_app_cli.runtime import config_providers


def test_runtime_config_preserves_legacy_helper_imports() -> None:
    """Callers can keep importing established helpers from the facade."""
    assert config.deep_merge is config_merge.deep_merge
    assert config.expand_env_vars is config_merge.expand_env_vars
    assert config._merge_module_lists is config_merge._merge_module_lists
    assert config.apply_provider_overrides is config_providers.apply_provider_overrides
    assert config._apply_hook_overrides is config_policies._apply_hook_overrides
    assert config._apply_tool_overrides is config_policies._apply_tool_overrides
    assert (
        config._build_notification_behaviors
        is config_behaviors._build_notification_behaviors
    )


@pytest.mark.asyncio
async def test_resolver_uses_monkeypatchable_facade_bundle_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_resolve_bundle_config(
        bundle_name: str,
        app_settings: Any,
        console: Any = None,
        *,
        session_id: str | None = None,
        project_slug: str | None = None,
    ) -> tuple[dict[str, Any], object]:
        del app_settings, console, session_id, project_slug
        calls.append(bundle_name)
        return {"bundle": bundle_name}, object()

    monkeypatch.setattr(config, "resolve_bundle_config", fake_resolve_bundle_config)

    resolved, _prepared = await config.resolve_config_async(
        bundle_name=None,
        app_settings=object(),  # type: ignore[arg-type]
    )

    assert resolved == {"bundle": "anchors"}
    assert calls == ["anchors"]


def test_runtime_config_stages_remain_focused() -> None:
    runtime_dir = Path(config.__file__).parent
    for name in (
        "config.py",
        "config_behaviors.py",
        "config_merge.py",
        "config_policies.py",
        "config_providers.py",
    ):
        line_count = len((runtime_dir / name).read_text().splitlines())
        assert line_count < 500, f"{name} grew to {line_count} lines"
