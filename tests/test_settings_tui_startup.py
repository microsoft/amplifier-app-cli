"""Tests for AppSettings.get_tui_startup_config() (config.tui.*).

Mirrors the pattern in test_notification_hook_overrides_defaults.py:
isolated SettingsPaths under tmp_path so no test touches a real
~/.amplifier/settings.yaml.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_app_cli.lib.settings import AppSettings
from amplifier_app_cli.lib.settings import SettingsPaths


def _make_settings(tmp_path: Path) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def test_clean_install_returns_empty_dict(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    assert settings.get_tui_startup_config() == {}


def test_reads_config_tui_section(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    settings._write_scope(
        "global",
        {"config": {"tui": {"startup_mode": "auto", "startup_permission": "bypass"}}},
    )

    assert settings.get_tui_startup_config() == {
        "startup_mode": "auto",
        "startup_permission": "bypass",
    }


def test_project_scope_overrides_global_scope(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    settings._write_scope("global", {"config": {"tui": {"startup_mode": "auto"}}})
    settings._write_scope("project", {"config": {"tui": {"startup_mode": "build"}}})

    assert settings.get_tui_startup_config()["startup_mode"] == "build"


def test_non_dict_tui_section_is_ignored(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    settings._write_scope("global", {"config": {"tui": "not-a-dict"}})

    assert settings.get_tui_startup_config() == {}
