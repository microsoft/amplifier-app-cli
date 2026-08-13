"""Tests for bundle-declared routing matrix defaults.

A bundle may declare a default routing matrix (a top-level ``routing:``
section in its frontmatter, exposed as ``Bundle.routing`` by
amplifier-foundation). This is consumed by ``runtime/config.py`` as the
WEAKEST source in the routing precedence chain:

    built-in default < bundle-declared routing.matrix
    < user ~/.amplifier/settings.yaml < project .amplifier/settings.yaml
    < project .amplifier/settings.local.yaml

These tests exercise that precedence through the real
``resolve_bundle_config()`` function. ``load_and_prepare_bundle()`` itself is
mocked out (it has its own dedicated tests in
``tests/lib/bundle_loader/test_prepare.py`` covering the
``on_bundle_loaded``/``required_behaviors`` plumbing) -- the mock's
``side_effect`` simulates the ONE thing these tests care about: invoking the
``on_bundle_loaded`` callback with a stub ``Bundle``, exactly as the real
``load_and_prepare_bundle()`` does right after loading it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.lib.settings import AppSettings, NotificationFlags, SettingsPaths
from amplifier_app_cli.runtime.config import resolve_bundle_config

ROUTING_URI = (
    "git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main"
    "#subdirectory=behaviors/routing.yaml"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_app_settings(
    *,
    routing_config: dict[str, Any] | None = None,
    routing_source: str | None = None,
    **kwargs: Any,
) -> MagicMock:
    """Build a mock AppSettings with controlled routing (and other) config."""
    settings = MagicMock()
    settings.get_config_overrides.return_value = kwargs.get("config_overrides", {})
    settings.get_provider_overrides.return_value = kwargs.get("provider_overrides", [])
    settings.get_tool_overrides.return_value = kwargs.get("tool_overrides", [])
    settings.get_notification_hook_overrides.return_value = kwargs.get(
        "hook_overrides", []
    )
    routing_config = routing_config or {}
    settings.get_routing_config.return_value = routing_config
    settings.get_routing_config_with_source.return_value = (
        routing_config,
        routing_source,
    )
    settings.get_notification_flags.return_value = NotificationFlags(
        desktop_enabled=False,
        push_enabled=False,
    )
    settings.get_app_bundles.return_value = []
    settings.get_source_overrides.return_value = {}
    settings.get_module_sources.return_value = {}
    settings.get_bundle_sources.return_value = {}
    return settings


def _real_app_settings(tmp_path: Path) -> AppSettings:
    """Build a REAL AppSettings backed by tmp_path scope files.

    Needed for tests that must attribute a warning to a genuine settings
    scope file path (e.g. .amplifier/settings.local.yaml).
    """
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    return AppSettings(paths=paths)


def _write_yaml(path: Path, data: dict) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


def _fake_prepare(mount_plan: dict[str, Any], bundle_stub: Any | None):
    """Build an AsyncMock-compatible side_effect for load_and_prepare_bundle.

    Simulates the ONE behavior these tests need from the real function:
    invoking ``on_bundle_loaded(bundle_stub)`` right after "loading" the
    bundle. Everything else (required_behaviors bookkeeping inside
    prepare.py, actual git/network I/O) is out of scope here -- see
    tests/lib/bundle_loader/test_prepare.py for that.

    Returns (side_effect_coroutine_fn, captured) where captured accumulates
    {"kwargs": ..., "callback_result": ...} after the call.
    """
    mock_prepared = MagicMock()
    mock_prepared.mount_plan = mount_plan
    mock_prepared.bundle.load_agent_metadata = MagicMock()

    captured: dict[str, Any] = {}

    async def _prepare(*_args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        on_loaded = kwargs.get("on_bundle_loaded")
        if on_loaded is not None and bundle_stub is not None:
            captured["callback_result"] = on_loaded(bundle_stub)
        return mock_prepared

    return _prepare, captured


def _printed_text(console_mock: MagicMock) -> str:
    """Flatten all console.print() call args into one searchable string."""
    parts = []
    for call in console_mock.print.call_args_list:
        if call.args:
            parts.append(str(call.args[0]))
    return "\n".join(parts)


async def _run(
    settings: AppSettings,
    bundle_stub: Any | None,
    mount_plan: dict[str, Any] | None = None,
    known_matrices: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], MagicMock]:
    """Run resolve_bundle_config() with the fake prepare + patched collaborators.

    Returns (result_config, captured, console_mock).
    """
    prepare_fn, captured = _fake_prepare(mount_plan or {"hooks": []}, bundle_stub)
    console = MagicMock()

    with (
        patch(
            "amplifier_app_cli.lib.bundle_loader.prepare.load_and_prepare_bundle",
            AsyncMock(side_effect=prepare_fn),
        ),
        patch("amplifier_app_cli.paths.get_bundle_search_paths", return_value=[]),
        patch("amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery"),
        patch(
            "amplifier_app_cli.runtime.config.known_matrix_names",
            return_value=known_matrices if known_matrices is not None else set(),
        ),
    ):
        result, _ = await resolve_bundle_config(
            bundle_name="test", app_settings=settings, console=console
        )

    return result, captured, console


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bundle_without_routing_behaves_identically_to_today():
    """MOST IMPORTANT: a bundle with no routing default -> byte-identical
    to pre-change behavior. No routing behavior composed, no hooks-routing
    entry injected, no observability output."""
    settings = _make_app_settings()  # no user routing config
    bundle_stub = SimpleNamespace(name="test-bundle", routing={})

    result, captured, console = await _run(settings, bundle_stub)

    compose_behaviors = captured["kwargs"]["compose_behaviors"]
    assert all(
        "amplifier-bundle-routing-matrix" not in uri for uri in compose_behaviors
    )
    assert captured["kwargs"]["required_behaviors"] is None
    assert captured["callback_result"] == []
    assert result["hooks"] == []
    assert _printed_text(console) == "" or "Routing matrix" not in _printed_text(
        console
    )


@pytest.mark.asyncio
async def test_bundle_routing_used_when_no_user_setting():
    """No user setting anywhere -> the bundle's declared matrix wins as the
    default, and the callback requests the canonical routing behavior."""
    settings = _make_app_settings()  # user_routing == {}
    bundle_stub = SimpleNamespace(name="my-bundle", routing={"matrix": "quality"})

    result, captured, console = await _run(
        settings, bundle_stub, known_matrices={"quality", "balanced"}
    )

    assert captured["callback_result"] == [ROUTING_URI]
    routing_entries = [h for h in result["hooks"] if h.get("module") == "hooks-routing"]
    assert len(routing_entries) == 1
    assert routing_entries[0]["config"]["default_matrix"] == "quality"

    printed = _printed_text(console)
    assert "quality" in printed
    assert "default from bundle 'my-bundle'" in printed


@pytest.mark.asyncio
async def test_user_settings_matrix_beats_bundle_routing():
    """A user setting (any scope) always wins over the bundle default."""
    settings = _make_app_settings(
        routing_config={"matrix": "anthropic"},
        routing_source="/home/u/.amplifier/settings.yaml",
    )
    bundle_stub = SimpleNamespace(name="my-bundle", routing={"matrix": "openai"})

    result, captured, console = await _run(
        settings, bundle_stub, known_matrices={"anthropic", "openai"}
    )

    # User settings already trigger composition/require independent of the
    # bundle -- the callback must not add a second, redundant behavior entry
    # (dedup is prepare.py's job; here we assert the callback's own decision).
    assert captured["callback_result"] == []

    routing_entries = [h for h in result["hooks"] if h.get("module") == "hooks-routing"]
    assert len(routing_entries) == 1
    assert routing_entries[0]["config"]["default_matrix"] == "anthropic"

    printed = _printed_text(console)
    assert "anthropic" in printed
    assert "/home/u/.amplifier/settings.yaml" in printed
    assert "overrides bundle 'my-bundle' default 'openai'" in printed
    assert "amplifier routing use openai" in printed


@pytest.mark.asyncio
async def test_project_local_settings_matrix_beats_bundle_routing(tmp_path: Path):
    """Project-local settings.local.yaml (highest precedence scope) beats a
    bundle default, and the warning names that exact file path."""
    settings = _real_app_settings(tmp_path)
    _write_yaml(settings.paths.local_settings, {"routing": {"matrix": "anthropic"}})
    bundle_stub = SimpleNamespace(name="my-bundle", routing={"matrix": "openai"})

    result, _captured, console = await _run(
        settings, bundle_stub, known_matrices={"anthropic", "openai"}
    )

    routing_entries = [h for h in result["hooks"] if h.get("module") == "hooks-routing"]
    assert routing_entries[0]["config"]["default_matrix"] == "anthropic"

    printed = _printed_text(console)
    assert str(settings.paths.local_settings) in printed
    assert "overrides bundle 'my-bundle' default 'openai'" in printed


@pytest.mark.asyncio
async def test_bundle_overrides_merge_under_user_overrides():
    """Shallow merge at the top level: user_routing keys win key-by-key over
    bundle_routing -- NOT a deep per-role merge of `overrides`."""
    settings = _make_app_settings(
        routing_config={"overrides": {"docs": "bar"}},
    )
    bundle_stub = SimpleNamespace(
        name="my-bundle",
        routing={"matrix": "quality", "overrides": {"coding": "foo"}},
    )

    result, _captured, _console = await _run(
        settings, bundle_stub, known_matrices={"quality"}
    )

    routing_entries = [h for h in result["hooks"] if h.get("module") == "hooks-routing"]
    cfg = routing_entries[0]["config"]
    # matrix: only the bundle set it -> bundle value survives
    assert cfg["default_matrix"] == "quality"
    # overrides: user_routing HAS the "overrides" key -> it wins wholesale,
    # bundle's {"coding": "foo"} is NOT deep-merged in.
    assert cfg["overrides"] == {"docs": "bar"}


@pytest.mark.asyncio
async def test_unknown_bundle_matrix_is_dropped_and_warns():
    """Bundle's matrix isn't a known/installed matrix -> dropped, warns,
    session is not bricked (falls back to no routing)."""
    settings = _make_app_settings()  # no user matrix
    bundle_stub = SimpleNamespace(name="my-bundle", routing={"matrix": "foo"})

    result, captured, console = await _run(
        settings, bundle_stub, known_matrices={"balanced", "quality"}
    )

    # Matrix key dropped -> nothing left to contribute -> no behavior, no hook.
    assert captured["callback_result"] == []
    assert result["hooks"] == []

    printed = _printed_text(console)
    assert "Bundle 'my-bundle' requests routing matrix 'foo'" in printed
    assert "not installed" in printed
    assert "~/.amplifier/routing" in printed
    assert "amplifier-bundle-routing-matrix-*/routing" in printed
    assert "Falling back to: no routing" in printed


@pytest.mark.asyncio
async def test_unknown_bundle_matrix_does_not_disable_user_matrix():
    """Unknown-matrix validation applies ONLY when the bundle's matrix is
    about to win. When the user has already set a matrix, the bundle's
    (invalid) matrix is never checked, and the user's matrix keeps working."""
    settings = _make_app_settings(
        routing_config={"matrix": "anthropic"},
        routing_source="/home/u/.amplifier/settings.yaml",
    )
    bundle_stub = SimpleNamespace(name="my-bundle", routing={"matrix": "foo"})

    # known_matrices deliberately does NOT include "foo" -- if the unknown
    # check ran anyway, it still must not affect the user's own matrix.
    result, _captured, console = await _run(
        settings, bundle_stub, known_matrices={"anthropic"}
    )

    routing_entries = [h for h in result["hooks"] if h.get("module") == "hooks-routing"]
    assert routing_entries[0]["config"]["default_matrix"] == "anthropic"

    printed = _printed_text(console)
    assert "not installed" not in printed  # unknown-matrix check never ran


@pytest.mark.asyncio
async def test_composed_bundles_overlay_matrix_wins():
    """foundation deep-merges routing: across composed includes before
    app-cli ever sees Bundle.routing -- app-cli just takes whatever the
    final composed value is at face value."""
    settings = _make_app_settings()  # no user matrix
    # Simulates the state AFTER foundation has already composed multiple
    # bundles' routing: sections -- the overlay bundle's matrix is what
    # survives onto the final Bundle.routing.
    bundle_stub = SimpleNamespace(name="overlay-bundle", routing={"matrix": "overlay"})

    result, _captured, _console = await _run(
        settings, bundle_stub, known_matrices={"overlay", "base"}
    )

    routing_entries = [h for h in result["hooks"] if h.get("module") == "hooks-routing"]
    assert routing_entries[0]["config"]["default_matrix"] == "overlay"


@pytest.mark.asyncio
async def test_old_foundation_without_routing_attr_does_not_crash():
    """Forward-compat: a Bundle stub with NO `routing` attribute at all
    (the CURRENTLY installed amplifier-foundation) must not crash -- the
    getattr(bundle, "routing", {}) default path is what makes this work."""
    settings = _make_app_settings()

    class _OldBundleStub:
        def __init__(self, name: str) -> None:
            self.name = name
            # Deliberately no `.routing` attribute.

    bundle_stub = _OldBundleStub("old-bundle")

    result, captured, _console = await _run(settings, bundle_stub)

    assert captured["callback_result"] == []
    assert result["hooks"] == []
