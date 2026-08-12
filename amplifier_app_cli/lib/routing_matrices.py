"""Routing matrix file discovery.

Single source of truth for locating routing matrix YAML files on disk.
Shared by:
  - ``commands/routing.py`` (``amplifier routing list/show/use/...``) which
    may lazily fetch the routing-matrix bundle on first use.
  - ``runtime/config.py`` (session preparation), which validates a
    bundle-declared ``routing.matrix`` name and must NEVER touch the
    network on this hot path -- see ``known_matrix_names()``.

Extracted from ``commands/routing.py``'s ``_discover_matrix_files()`` so
both call sites share one filesystem-scanning implementation instead of
risking the "listable but not loadable" bug this module's sibling,
``get_custom_routing_dir()`` (in ``lib/settings.py``), already guards
against for the custom-matrix directory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from .bundle_loader.discovery import WELL_KNOWN_BUNDLES
from .settings import get_custom_routing_dir

console = Console()
logger = logging.getLogger(__name__)

# Single source of truth for the routing-matrix bundle URL lives in
# WELL_KNOWN_BUNDLES (bundle_loader/discovery.py).
_ROUTING_BUNDLE_URI = str(WELL_KNOWN_BUNDLES["routing-matrix"]["remote"])


def _ensure_routing_bundle_cached() -> None:
    """Fetch the routing-matrix bundle into the cache if not yet present.

    Called lazily from ``discover_matrix_files(fetch=True)`` so `amplifier
    routing list` works on a clean install without requiring the user to
    run `amplifier update` first. FoundationGitSource.resolve() is a sync
    wrapper that is safe to call from a synchronous CLI command (it spawns
    a ThreadPoolExecutor internally if an event loop is already running).

    Failures are reported both to the debug log and visibly to the user so
    that a silent blocking clone followed by a silent empty list can never
    happen.
    """
    from .bundle_loader.resolvers import FoundationGitSource

    try:
        FoundationGitSource(_ROUTING_BUNDLE_URI).resolve()
    except Exception as e:
        logger.warning("Could not fetch routing-matrix bundle: %s", e)
        console.print(f"[yellow]Could not fetch routing-matrix bundle: {e}[/yellow]")


def discover_matrix_files(fetch: bool = False) -> list[Path]:
    """Discover available routing matrix YAML files.

    Looks in:
    1. ~/.amplifier/cache/amplifier-bundle-routing-matrix-*/routing/*.yaml (bundle)
    2. ~/.amplifier/routing/*.yaml (custom user matrices)

    Args:
        fetch: When True and the routing-matrix bundle is not yet cached,
            lazily fetch it (network I/O; prints progress/failure feedback
            via the console). This is what makes `amplifier routing list`
            work out of the box on a clean install. When False (the
            default), this function NEVER touches the network -- callers
            on a session-start hot path (see ``known_matrix_names()``)
            must not silently block on git I/O just to validate a name.
    """
    home = Path.home()
    files: list[Path] = []

    # Bundle cache matrices (lazy-fetch on first use, only when fetch=True)
    cache_base = home / ".amplifier" / "cache"
    bundle_dirs = (
        list(cache_base.glob("amplifier-bundle-routing-matrix-*"))
        if cache_base.exists()
        else []
    )
    if not bundle_dirs and fetch:
        # First run on a clean install -- fetch the bundle with visible
        # feedback. This can take 5-30s on a slow network so we must NOT
        # block silently.
        console.print("[dim]Fetching routing-matrix bundle...[/dim]")
        _ensure_routing_bundle_cached()
        bundle_dirs = (
            list(cache_base.glob("amplifier-bundle-routing-matrix-*"))
            if cache_base.exists()
            else []
        )

    for bundle_dir in bundle_dirs:
        routing_dir = bundle_dir / "routing"
        if routing_dir.is_dir():
            files.extend(routing_dir.glob("*.yaml"))

    # Custom user matrices (single source of truth: get_custom_routing_dir())
    custom_dir = get_custom_routing_dir()
    if custom_dir.is_dir():
        files.extend(custom_dir.glob("*.yaml"))

    return sorted(files)


def known_matrix_names() -> set[str]:
    """Return the set of matrix names discoverable on disk, WITHOUT fetching.

    Used at session-start (``runtime/config.py``) to validate a
    bundle-declared ``routing.matrix`` name before letting it win as the
    effective default. Never touches the network -- if the routing-matrix
    bundle isn't cached yet (clean install, no prior `amplifier routing
    list`/`update`), this simply returns an empty set. Callers treat an
    empty set as "nothing to validate against" and skip the check, rather
    than treating "unknown on an uncached install" the same as "genuinely
    unknown name".
    """
    names: set[str] = set()
    for path in discover_matrix_files(fetch=False):
        try:
            with open(path, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}
        except Exception:
            continue
        name = data.get("name")
        if name:
            names.add(str(name))
    return names


__all__ = [
    "discover_matrix_files",
    "known_matrix_names",
]
