"""Focused tests for resolve_tui_startup_preference().

Covers the config.tui.startup_mode / startup_permission validation used to
seed a brand-new interactive session's mode + trust posture (ADR-0005:
a configured ``startup_permission`` IS the explicit user action the ADR
requires -- see ``runtime/interactive_resources.py`` for how the caller
latches ``_trust_explicitly_set`` before applying it).
"""

from __future__ import annotations

import logging

import pytest

from amplifier_app_cli.runtime.interactive_resource_setup import TuiStartupPreference
from amplifier_app_cli.runtime.interactive_resource_setup import (
    resolve_tui_startup_preference,
)
from amplifier_app_cli.ui.mode_profiles import ModeProfileRegistry

_VALID_MODES = ModeProfileRegistry().names
_VALID_PERMISSIONS = ("chat", "build", "plan", "auto", "bypass")


def test_no_settings_yields_no_preference() -> None:
    """Default behavior with no config.tui section: unchanged (chat/chat)."""
    preference = resolve_tui_startup_preference(
        {}, valid_modes=_VALID_MODES, valid_permissions=_VALID_PERMISSIONS
    )
    assert preference == TuiStartupPreference()


def test_valid_mode_and_permission_are_applied() -> None:
    preference = resolve_tui_startup_preference(
        {"startup_mode": "auto", "startup_permission": "bypass"},
        valid_modes=_VALID_MODES,
        valid_permissions=_VALID_PERMISSIONS,
    )
    assert preference == TuiStartupPreference(mode="auto", permission="bypass")


def test_only_mode_configured_leaves_permission_unset() -> None:
    preference = resolve_tui_startup_preference(
        {"startup_mode": "build"},
        valid_modes=_VALID_MODES,
        valid_permissions=_VALID_PERMISSIONS,
    )
    assert preference == TuiStartupPreference(mode="build", permission=None)


def test_only_permission_configured_leaves_mode_unset() -> None:
    preference = resolve_tui_startup_preference(
        {"startup_permission": "bypass"},
        valid_modes=_VALID_MODES,
        valid_permissions=_VALID_PERMISSIONS,
    )
    assert preference == TuiStartupPreference(mode=None, permission="bypass")


def test_invalid_mode_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        preference = resolve_tui_startup_preference(
            {"startup_mode": "nope"},
            valid_modes=_VALID_MODES,
            valid_permissions=_VALID_PERMISSIONS,
        )
    assert preference.mode is None
    assert "startup_mode" in caplog.text
    assert "nope" in caplog.text


def test_invalid_permission_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        preference = resolve_tui_startup_preference(
            {"startup_permission": "godmode"},
            valid_modes=_VALID_MODES,
            valid_permissions=_VALID_PERMISSIONS,
        )
    assert preference.permission is None
    assert "startup_permission" in caplog.text
    assert "godmode" in caplog.text


def test_non_string_values_are_dropped_safely() -> None:
    preference = resolve_tui_startup_preference(
        {"startup_mode": 123, "startup_permission": True},
        valid_modes=_VALID_MODES,
        valid_permissions=_VALID_PERMISSIONS,
    )
    assert preference == TuiStartupPreference()


def test_default_valid_permissions_cover_all_built_in_presets() -> None:
    """Without an explicit valid_permissions override, all five built-in
    trust presets (chat/build/plan/auto/bypass) must validate -- this is
    the exact set ADR-0005's amendment cycles through via ctrl-p."""
    for name in ("chat", "build", "plan", "auto", "bypass"):
        preference = resolve_tui_startup_preference(
            {"startup_permission": name}, valid_modes=_VALID_MODES
        )
        assert preference.permission == name
