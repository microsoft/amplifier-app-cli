"""Tests for AppSettings.get_routing_config_with_source().

Covers the "source" half of bundle-declared routing matrix precedence: the
highest-precedence settings scope file that set routing.matrix, used by
runtime/config.py to attribute "who set the active matrix" and by
`amplifier routing show` to display a Source: line.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


def _make_settings(tmp_path: Path) -> AppSettings:
    """Create AppSettings with isolated paths for testing."""
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


def test_source_is_none_when_unset(tmp_path):
    """No scope sets routing.matrix -> source is None, config is empty."""
    settings = _make_settings(tmp_path)

    config, source = settings.get_routing_config_with_source()

    assert config == {}
    assert source is None


def test_source_reports_highest_precedence_scope(tmp_path):
    """global -> project -> local; local (highest precedence) wins as source."""
    settings = _make_settings(tmp_path)
    _write_yaml(settings.paths.global_settings, {"routing": {"matrix": "openai"}})
    _write_yaml(settings.paths.project_settings, {"routing": {"matrix": "gemini"}})
    _write_yaml(settings.paths.local_settings, {"routing": {"matrix": "anthropic"}})

    config, source = settings.get_routing_config_with_source()

    assert config["matrix"] == "anthropic"
    assert source == str(settings.paths.local_settings)


def test_source_ignores_scopes_setting_only_overrides(tmp_path):
    """A scope that sets routing.overrides (no matrix) must not become the
    reported source -- only a scope that actually sets `matrix` counts."""
    settings = _make_settings(tmp_path)
    _write_yaml(settings.paths.global_settings, {"routing": {"matrix": "openai"}})
    # Local scope only overrides per-role behavior, doesn't set a matrix.
    _write_yaml(
        settings.paths.local_settings,
        {"routing": {"overrides": {"coding": "quality"}}},
    )

    config, source = settings.get_routing_config_with_source()

    # Merged config still carries the local overrides (deep-merged as before).
    assert config["matrix"] == "openai"
    assert config["overrides"] == {"coding": "quality"}
    # But the reported *source* of the matrix selection is the global file,
    # since local never set routing.matrix itself.
    assert source == str(settings.paths.global_settings)


def test_get_routing_config_thin_wrapper_matches_source_variant(tmp_path):
    """get_routing_config() must keep returning exactly the merged config
    half of get_routing_config_with_source() -- no behavior change for
    existing callers."""
    settings = _make_settings(tmp_path)
    _write_yaml(settings.paths.project_settings, {"routing": {"matrix": "balanced"}})

    config_only = settings.get_routing_config()
    config_with_source, _source = settings.get_routing_config_with_source()

    assert config_only == config_with_source
