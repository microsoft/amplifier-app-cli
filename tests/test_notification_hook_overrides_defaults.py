"""Regression test for the hooks-notify clean-install failure.

On a clean install, `AppSettings.get_notification_hook_overrides()` must return
an empty list. Before this fix, it returned `[{"module": "hooks-notify", ...}]`
because the `desktop.enabled` default was True — which caused
`_apply_hook_overrides()` to append a hook reference for a module that was
never composed into the bundle, producing:

    Module 'hooks-notify' not found in prepared bundle.

The companion function `_build_notification_behaviors()` already defaulted to
False. Both functions must agree that the user has to opt in explicitly.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


def _make_settings(tmp_path: Path) -> AppSettings:
    """AppSettings with isolated, empty settings paths (clean-install shape)."""
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def test_clean_install_returns_no_notification_hook_overrides(tmp_path):
    """On a clean install with no settings files, no hooks-notify override is
    injected. This is the exact scenario that broke with PR #167.
    """
    settings = _make_settings(tmp_path)

    overrides = settings.get_notification_hook_overrides()

    assert overrides == [], (
        f"Expected no overrides on clean install, got {overrides!r}. "
        "A desktop-notifications override injected here would make the CLI "
        "try to load 'hooks-notify' even though the bundle never composed it."
    )


def test_explicit_enable_produces_override(tmp_path):
    """When the user explicitly opts in, the override IS produced so the
    behavior composition path (which also checks this key) can install the
    module. Both sides must agree on the same source of truth.
    """
    settings = _make_settings(tmp_path)
    settings.set_notification_config("desktop", {"enabled": True})

    overrides = settings.get_notification_hook_overrides()

    assert len(overrides) == 1
    assert overrides[0]["module"] == "hooks-notify"
    assert overrides[0]["config"]["enabled"] is True


def test_explicit_disable_returns_no_override(tmp_path):
    """Explicit `enabled: False` also produces no override."""
    settings = _make_settings(tmp_path)
    settings.set_notification_config("desktop", {"enabled": False})

    overrides = settings.get_notification_hook_overrides()

    assert overrides == []


def test_null_desktop_section_returns_no_override(tmp_path):
    """`notifications.desktop: null` in YAML is a real edge case — the guard
    added alongside the default flip must handle it without AttributeError.

    No public setter can inject None here, so we have to reach into the
    private scope writer for this specific test.
    """
    settings = _make_settings(tmp_path)
    scope = settings._read_scope("global")
    scope.setdefault("config", {})["notifications"] = {"desktop": None}
    settings._write_scope("global", scope)

    overrides = settings.get_notification_hook_overrides()

    assert overrides == []
