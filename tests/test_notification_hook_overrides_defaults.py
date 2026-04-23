"""Regression tests for notification enablement — single source of truth.

The notification plumbing has two consumers that must agree on "is this
enabled?": ``AppSettings.get_notification_hook_overrides()`` (emits the hook
override injected at runtime) and ``_build_notification_behaviors()`` (composes
behavior bundles before prepare()). Historically they held independent defaults,
which drifted in PR #167 and caused:

    Module 'hooks-notify' not found in prepared bundle.

The structural fix introduces ``NotificationFlags`` as the single source of
truth: both consumers derive from ``AppSettings.get_notification_flags()``
so disagreement is impossible by construction. Tests in this file pin that
contract from two directions — behavior of each consumer on various configs,
and agreement between the two on the same config.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_app_cli.lib.settings import (
    AppSettings,
    NotificationFlags,
    SettingsPaths,
)
from amplifier_app_cli.runtime.config import _build_notification_behaviors


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
    must handle it without AttributeError.

    No public setter can inject None here, so we have to reach into the
    private scope writer for this specific test.
    """
    settings = _make_settings(tmp_path)
    scope = settings._read_scope("global")
    scope.setdefault("config", {})["notifications"] = {"desktop": None}
    settings._write_scope("global", scope)

    overrides = settings.get_notification_hook_overrides()

    assert overrides == []


# ---------------------------------------------------------------------------
# NotificationFlags — single source of truth
# ---------------------------------------------------------------------------


def test_get_notification_flags_defaults_to_all_false(tmp_path):
    """On a clean install with no settings files, both flags resolve False.
    This is the sanity test that prevents a default-drift regression.
    """
    settings = _make_settings(tmp_path)

    flags = settings.get_notification_flags()

    assert flags == NotificationFlags(desktop_enabled=False, push_enabled=False)


def test_get_notification_flags_desktop_only(tmp_path):
    """Explicit desktop opt-in flips only the desktop flag."""
    settings = _make_settings(tmp_path)
    settings.set_notification_config("desktop", {"enabled": True})

    flags = settings.get_notification_flags()

    assert flags.desktop_enabled is True
    assert flags.push_enabled is False


def test_get_notification_flags_push_and_ntfy_are_aliases(tmp_path):
    """push.enabled OR ntfy.enabled produces push_enabled=True. They are
    aliases at the enablement level — users configure under either key.
    """
    # Only ntfy enabled → push_enabled is True
    settings = _make_settings(tmp_path)
    settings.set_notification_config("ntfy", {"enabled": True})
    assert settings.get_notification_flags().push_enabled is True

    # Only push enabled → push_enabled is True
    settings = _make_settings(tmp_path)
    scope = settings._read_scope("global")
    scope.setdefault("config", {})["notifications"] = {"push": {"enabled": True}}
    settings._write_scope("global", scope)
    assert settings.get_notification_flags().push_enabled is True


def test_get_notification_flags_robust_to_non_dict_subsections(tmp_path):
    """notifications.desktop: "garbage" (or None, or list) must not crash
    get_notification_flags — the isinstance guards short-circuit cleanly.
    """
    settings = _make_settings(tmp_path)
    scope = settings._read_scope("global")
    scope.setdefault("config", {})["notifications"] = {
        "desktop": "garbage",
        "push": None,
        "ntfy": ["unexpected"],
    }
    settings._write_scope("global", scope)

    flags = settings.get_notification_flags()

    assert flags == NotificationFlags(desktop_enabled=False, push_enabled=False)


# ---------------------------------------------------------------------------
# Consumer agreement — the two consumers must not drift
# ---------------------------------------------------------------------------


def test_consumers_agree_on_clean_install(tmp_path):
    """Both consumers — the behavior-composition path and the hook-override
    path — return empty on a clean install. This is the regression test that
    pins the fix for the original hooks-notify-not-found failure.
    """
    settings = _make_settings(tmp_path)

    flags = settings.get_notification_flags()
    behaviors = _build_notification_behaviors(flags)
    overrides = settings.get_notification_hook_overrides()

    assert behaviors == []
    assert overrides == []


def test_consumers_agree_on_desktop_enabled(tmp_path):
    """When desktop is enabled, both consumers fire: the behavior bundle
    gets composed AND the hook override gets injected. If either side
    silently no-op'd, the `hooks-notify` module would fail to load.
    """
    settings = _make_settings(tmp_path)
    settings.set_notification_config("desktop", {"enabled": True})

    flags = settings.get_notification_flags()
    behaviors = _build_notification_behaviors(flags)
    overrides = settings.get_notification_hook_overrides()

    # Behavior path composes the root bundle + the desktop behavior.
    assert any("amplifier-bundle-notify" in b for b in behaviors)
    assert any("desktop-notifications" in b for b in behaviors)
    # Hook-override path emits exactly the hooks-notify override.
    assert [o["module"] for o in overrides] == ["hooks-notify"]


def test_push_only_enabled_produces_both_behavior_and_override(tmp_path):
    """Regression: push.enabled=True + ntfy.enabled=False previously produced
    a behavior bundle (from _build_notification_behaviors) but NO hook
    override (because the old get_notification_hook_overrides relied on a
    dict merge where ntfy won). After the refactor both consumers read
    flags.push_enabled and wire up correctly.
    """
    settings = _make_settings(tmp_path)
    scope = settings._read_scope("global")
    scope.setdefault("config", {})["notifications"] = {
        "push": {"enabled": True},
        "ntfy": {"enabled": False},
    }
    settings._write_scope("global", scope)

    flags = settings.get_notification_flags()
    behaviors = _build_notification_behaviors(flags)
    overrides = settings.get_notification_hook_overrides()

    # Behavior path composes the push-notifications behavior.
    assert any("push-notifications" in b for b in behaviors)
    # Hook-override path also emits the hooks-notify-push override — THIS
    # is what was previously missing when ntfy.enabled won a dict merge.
    assert [o["module"] for o in overrides] == ["hooks-notify-push"]


def test_consumers_agree_when_both_desktop_and_push_enabled(tmp_path):
    """Full belt-and-suspenders: both flags on → both behaviors composed,
    both hook overrides emitted. No dropped plumbing on either side.
    """
    settings = _make_settings(tmp_path)
    settings.set_notification_config("desktop", {"enabled": True})
    settings.set_notification_config("ntfy", {"enabled": True})

    flags = settings.get_notification_flags()
    behaviors = _build_notification_behaviors(flags)
    overrides = settings.get_notification_hook_overrides()

    # Behavior path composes root + both per-notification behaviors.
    assert any("desktop-notifications" in b for b in behaviors)
    assert any("push-notifications" in b for b in behaviors)
    # Hook-override path emits both overrides.
    assert {o["module"] for o in overrides} == {"hooks-notify", "hooks-notify-push"}


def test_build_notification_behaviors_noop_when_nothing_enabled():
    """The behavior-composition helper returns an empty list when the
    NotificationFlags value is all-False, regardless of how it was produced.
    This pins the contract of the new signature.
    """
    flags = NotificationFlags(desktop_enabled=False, push_enabled=False)
    assert _build_notification_behaviors(flags) == []
