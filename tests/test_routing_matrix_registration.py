"""Regression tests for routing-matrix bundle registration and lazy-fetch.

Two related issues on a clean install:

1. `amplifier-bundle-routing-matrix` was not registered in WELL_KNOWN_BUNDLES,
   so `amplifier update` never fetched it. The `amplifier routing list`
   command's "Run `amplifier update` to fetch the routing-matrix bundle"
   hint message was therefore lying to the user.

2. `_discover_matrix_files()` was a pure filesystem scanner — no fetch logic.
   On a clean install it returned [] silently because the cache directory
   didn't exist yet.

These tests pin the behaviour: the bundle is registered, and the lazy-fetch
path is wired up so `_discover_matrix_files()` populates the cache on its
first call.
"""

from __future__ import annotations

from unittest.mock import patch

from amplifier_app_cli.lib.bundle_loader.discovery import WELL_KNOWN_BUNDLES


# ---------------------------------------------------------------------------
# Bug 3(a) — WELL_KNOWN_BUNDLES registration
# ---------------------------------------------------------------------------


def test_routing_matrix_in_well_known_bundles():
    """`amplifier update` relies on WELL_KNOWN_BUNDLES to know what to fetch.
    Without this entry, `amplifier update` silently ignored routing-matrix
    even though the CLI shipped commands that depend on it.
    """
    assert "routing-matrix" in WELL_KNOWN_BUNDLES


def test_routing_matrix_registration_has_required_fields():
    """The entry must follow the same shape as other content-only bundles
    (notify, modes) so the bundle-loader infrastructure treats it uniformly.
    """
    entry = WELL_KNOWN_BUNDLES["routing-matrix"]

    assert entry["package"] == ""  # Bundle-only, no Python package
    assert isinstance(entry["remote"], str)
    assert "amplifier-bundle-routing-matrix" in entry["remote"]
    assert entry["remote"].startswith("git+https://")
    # Consumed by routing CLI / hooks-routing module — not a user-selectable bundle
    assert entry["show_in_list"] is False


# ---------------------------------------------------------------------------
# Bug 3(b) — lazy-fetch wiring
# ---------------------------------------------------------------------------


def test_discover_matrix_files_triggers_fetch_on_empty_cache(tmp_path):
    """When the bundle cache is empty, `_discover_matrix_files()` must call
    `_ensure_routing_bundle_cached()` exactly once before giving up.

    We don't exercise the real git clone here — we just assert that the
    function actually tries to populate the cache instead of silently
    returning [].
    """
    from amplifier_app_cli.commands import routing

    fake_home = tmp_path  # No .amplifier/cache directory under tmp_path

    with (
        patch.object(routing.Path, "home", return_value=fake_home),
        patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
    ):
        result = routing._discover_matrix_files()

    mock_fetch.assert_called_once()
    assert result == []  # Fetch was a no-op (mocked), so still nothing to find


def test_discover_matrix_files_skips_fetch_when_cache_exists(tmp_path):
    """When the bundle is already cached, `_discover_matrix_files()` must NOT
    trigger a fresh fetch. This preserves idempotency and keeps repeated
    `amplifier routing list` calls fast.
    """
    from amplifier_app_cli.commands import routing

    # Simulate a populated cache
    cache_dir = (
        tmp_path / ".amplifier" / "cache" / "amplifier-bundle-routing-matrix-abc"
    )
    routing_dir = cache_dir / "routing"
    routing_dir.mkdir(parents=True)
    (routing_dir / "anthropic.yaml").write_text("name: anthropic\nroles: {}\n")

    with (
        patch.object(routing.Path, "home", return_value=tmp_path),
        patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
    ):
        result = routing._discover_matrix_files()

    mock_fetch.assert_not_called()
    assert len(result) == 1
    assert result[0].name == "anthropic.yaml"


def test_ensure_routing_bundle_cached_swallows_errors(capsys):
    """A failed fetch (network down, git missing, corporate firewall) must
    NOT crash the CLI — the user gets a visible yellow warning via console,
    not a stack trace. This is the exact UX promise COE asked for.
    """
    from amplifier_app_cli.commands import routing

    class _BoomResolver:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def resolve(self) -> None:
            raise RuntimeError("simulated network failure")

    with patch(
        "amplifier_app_cli.lib.bundle_loader.resolvers.FoundationGitSource",
        _BoomResolver,
    ):
        # Must not raise
        routing._ensure_routing_bundle_cached()

    # User MUST see the failure — silent-block + silent-fail is the
    # anti-pattern this test is guarding against.
    captured = capsys.readouterr()
    assert "simulated network failure" in captured.out
