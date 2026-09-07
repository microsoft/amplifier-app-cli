"""The routing hook is composed on EVERY session this CLI starts.

Routing is app-level policy: this CLI ships `amplifier routing show/use/create/
manage` and `amplifier init`'s routing panel, so the hook those commands
describe must be in every session -- whatever base bundle the user picked --
or the UX lies.

Measured 2026-09-07 on the DEFAULT bundle (`anchors`, which did not include
routing-matrix): `routing show` printed "balanced ... <- active" for every role
while the session's `delegate:agent_spawned` carried `provider_preferences:
null` and the explorer ran on the parent's model. Every default user was in
that state.

`_build_routing_behaviors` is the fix, shaped exactly like
`_build_skills_behaviors` / `_build_wayfinder_behaviors`. These tests pin its
three properties: always a behavior file (never the bundle root), derived from
WELL_KNOWN_BUNDLES (one home for the URI), and swappable through the existing
`sources.bundles` override so a user who prefers a fork composes exactly one
routing hook -- theirs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from amplifier_app_cli.lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.runtime.config import (
    ROUTING_BEHAVIOR_SUBDIRECTORY,
    _build_routing_behaviors,
    _routing_hook_source,
)

ROUTING_ROOT = str(WELL_KNOWN_BUNDLES["routing-matrix"]["remote"])


def _settings(tmp_path: Path, body: dict | None = None) -> AppSettings:
    paths = SettingsPaths(
        global_settings=tmp_path / "global" / "settings.yaml",
        project_settings=tmp_path / "project" / "settings.yaml",
        local_settings=tmp_path / "local" / "settings.local.yaml",
    )
    if body is not None:
        paths.global_settings.parent.mkdir(parents=True, exist_ok=True)
        paths.global_settings.write_text(yaml.safe_dump(body), encoding="utf-8")
    return AppSettings(paths=paths)


def test_default_is_the_well_known_bundles_behavior_file(tmp_path: Path) -> None:
    """No override -> the well-known routing-matrix bundle, behavior file only."""
    assert _build_routing_behaviors(_settings(tmp_path)) == [
        f"{ROUTING_ROOT}#subdirectory={ROUTING_BEHAVIOR_SUBDIRECTORY}"
    ]


def test_never_returns_the_bare_bundle_root(tmp_path: Path) -> None:
    """Composing a root bundle.md pulls in its includes and can clobber the
    user's system prompt -- the same hazard test_notify_behaviors_not_root
    pins for notifications. A behavior YAML carries no body."""
    for uri in _build_routing_behaviors(_settings(tmp_path)):
        assert uri != ROUTING_ROOT
        assert "#subdirectory=behaviors/" in uri
        assert uri.endswith(".yaml")


def test_uri_shares_one_home_with_the_hook_source_and_the_routing_commands() -> None:
    """WELL_KNOWN_BUNDLES['routing-matrix'] is the ONLY place that knows where
    the bundle lives. `_routing_hook_source` (the post-hoc hook injection for
    `routing.matrix` settings) and `amplifier routing` read the same entry; if
    this builder ever hard-coded a URL the three could drift apart."""
    assert _routing_hook_source().startswith(ROUTING_ROOT + "#")
    assert _build_routing_behaviors(_settings(Path("/nonexistent")))[0].startswith(
        ROUTING_ROOT + "#"
    )


def test_sources_bundles_override_swaps_the_implementation(tmp_path: Path) -> None:
    """A user who prefers a fork composes THEIR routing bundle, not ours.

    Uses the existing `amplifier source add` vocabulary (`sources.bundles`),
    keyed by the bundle name -- no new concept. The override is a bundle ROOT;
    the behavior subdirectory is appended exactly as it is for the default, so
    the fork must ship the same `behaviors/routing.yaml` shape.
    """
    fork = "git+https://github.com/someone/amplifier-bundle-routing-matrix@my-branch"
    settings = _settings(tmp_path, {"sources": {"bundles": {"routing-matrix": fork}}})
    assert _build_routing_behaviors(settings) == [
        f"{fork}#subdirectory={ROUTING_BEHAVIOR_SUBDIRECTORY}"
    ]


def test_override_means_exactly_one_routing_behavior_is_composed(
    tmp_path: Path,
) -> None:
    """Swap, not add: with an override the well-known URI must NOT also appear,
    or the user would mount two hooks-routing and the compose order would
    silently decide which one wins."""
    fork = "file:///home/someone/routing-fork"
    settings = _settings(tmp_path, {"sources": {"bundles": {"routing-matrix": fork}}})
    behaviors = _build_routing_behaviors(settings)
    assert len(behaviors) == 1
    assert not any(uri.startswith(ROUTING_ROOT) for uri in behaviors)


def test_unrelated_bundle_overrides_do_not_touch_routing(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        {"sources": {"bundles": {"amplifier-bundle-superpowers": "/local/sp"}}},
    )
    assert _build_routing_behaviors(settings)[0].startswith(ROUTING_ROOT + "#")


def test_routing_is_in_the_always_composed_list_in_source() -> None:
    """The builder exists to be CALLED from the compose site. Pin that it is,
    next to its siblings, so a refactor cannot leave it defined-but-unused --
    which would reproduce the exact silent failure it fixes."""
    src = (
        Path(__file__).resolve().parents[1]
        / "amplifier_app_cli"
        / "runtime"
        / "config.py"
    ).read_text(encoding="utf-8")
    assert "compose_behaviors.extend(_build_routing_behaviors(app_settings))" in src
    # ...and before app bundles are appended, so a user's app-bundle fork
    # composes LATER and wins by module id.
    assert src.index("compose_behaviors.extend(_build_routing_behaviors(") < src.index(
        "compose_behaviors = compose_behaviors + app_bundles"
    )
