"""Walk a bundle's include graph to find sources the update path would miss.

Why this exists
---------------
``amplifier update`` and ``amplifier bundle update`` enumerate update targets
from *registered* sources: registry roots (``registry.json`` entries with
``is_root: true``) plus app bundles from settings.  A bundle that enters the
active closure only through another bundle's ``includes:`` list is not in
either place, so its cache was never checked -- and both commands printed a
green "All sources up to date" over a cache that had silently fallen behind
its ``@<branch>`` upstream.

That gap is not cosmetic.  Foundation's ``GitSourceHandler.resolve()``
(``sources/git.py:697-709``) returns an existing cache verbatim whenever it
passes the integrity check: no TTL, no fetch, no refresh, ever.  A cached
``@main`` include is therefore frozen at whatever commit it was first cloned
at until something explicitly calls ``GitSourceHandler.update()``.  The update
commands are the only thing that does -- so a source they do not enumerate is
a source that never updates.

What it does
------------
Walks the include graph from a set of seed URIs, cycle-safe, and returns every
transitively-included git source that is not already a direct update target.

Resolution is *reused*, never reimplemented: the loader's own
``BundleRegistry._parse_include`` / ``_resolve_include_source`` /
``_load_from_path`` do the parsing, so ``@namespace:path``, ``git+...`` and
``#subdirectory=`` forms resolve exactly the way a real session resolves them.
A second copy of those rules here would drift from the loader and reintroduce
the same class of silent miss.

The walk is read-only.  It reads caches that already exist (via the handler's
own ``_get_cache_path``) and never resolves a missing one, because resolving
downloads -- and a download would make a deleted cache look "up to date", the
exact side effect ``_check_all_bundle_status`` was written to avoid.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Mapping

if TYPE_CHECKING:
    from amplifier_foundation import Bundle, BundleRegistry
    from amplifier_foundation.sources.protocol import SourceStatus

logger = logging.getLogger(__name__)


def strip_uri_fragment(uri: str) -> str:
    """Drop a URI's ``#fragment``, keeping the ``@ref`` intact.

    The subdirectory fragment names a file *inside* a repo; the repo at a ref
    is the update target.  ``@main`` and ``@v2`` of the same repo are two
    different targets, so the ref must survive.
    """
    return uri.split("#", 1)[0]


@dataclass(frozen=True)
class TransitiveSource:
    """A git source reachable only through another bundle's ``includes:``."""

    uri: str
    """Fragment-stripped git URI -- the update target's identity."""

    parent: str
    """Display name of the bundle whose ``includes:`` reaches it."""

    root: str
    """Display name of the registered source the walk started from.

    Distinct from *parent* on purpose.  ``parent`` is the honest immediate
    includer (``digital-twin-universe-behavior``); ``root`` is the row the
    user actually has registered (``foundation``), which is the only thing a
    table can group under.  Collapsing the two would either lie about who
    includes what or scatter rows under names that have no row of their own.
    """

    via: str
    """The full resolved include URI, fragment and all (for diagnostics)."""


@dataclass
class TransitiveStatus:
    """A transitive source's update status, with the parent that pulls it in."""

    source: TransitiveSource
    status: "SourceStatus"

    @property
    def uri(self) -> str:
        return self.source.uri

    @property
    def parent(self) -> str:
        return self.source.parent

    @property
    def root(self) -> str:
        return self.source.root

    @property
    def is_pinned(self) -> bool:
        """True when the ref is a commit SHA or version tag.

        Reuses foundation's own ``SourceStatus.is_pinned`` rather than
        re-deriving "what counts as pinned" here.
        """
        return self.status.is_pinned

    @property
    def has_update(self) -> bool:
        return self.status.has_update is True


def _local_path_for(uri: str, cache_dir: Path) -> Path | None:
    """Best-available on-disk path for *uri*, WITHOUT touching the network.

    Returns None when the source is not a readable local/cached thing --
    which is the correct answer for a source whose cache has been deleted.
    Resolving it would clone, and a clone here would report a missing cache
    as healthy.
    """
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    try:
        parsed = parse_uri(uri)
    except Exception:  # noqa: BLE001 - an unparseable URI is simply not walkable
        return None

    git_handler = GitSourceHandler()
    if git_handler.can_handle(parsed):
        cache_path = git_handler._get_cache_path(parsed, cache_dir)
        if not cache_path.exists():
            return None
        active = cache_path / parsed.subpath if parsed.subpath else cache_path
        return active if active.exists() else None

    if parsed.is_file:
        path = Path(parsed.path)
        if parsed.subpath:
            path = path / parsed.subpath
        return path if path.exists() else None

    return None


def _is_git_uri(uri: str) -> bool:
    return uri.startswith("git+") or uri.startswith("git://")


async def _iter_cached_bundles(
    roots: Mapping[str, str],
    *,
    registry: BundleRegistry,
    cache_dir: Path,
    max_depth: int = 16,
    failures: dict[str, str] | None = None,
) -> AsyncIterator[tuple[str, str, str, Bundle | None]]:
    """Yield root, immediate parent, full URI and cached manifest (if readable).

    Both collectors use this bounded, read-only walk. A missing manifest still
    yields its URI so include status checking retains its existing behavior.
    Failure text deliberately contains neither configured URIs nor exceptions.
    """
    from amplifier_foundation.paths.resolution import parse_uri

    def failed(root: str) -> None:
        if failures is not None:
            failures[root] = "Incomplete cached module discovery"
        logger.debug("Could not completely read cached bundle declarations")

    # Visited on the FULL uri (fragment included): two behaviors in one repo
    # are different files with different includes, so collapsing them on the
    # repo URI would skip half the graph.  This set is also what makes a
    # cycle (B includes C includes B) terminate.
    visited: set[str] = set()

    queue: list[tuple[str, str, str, int]] = []
    for label, uri in roots.items():
        if uri in visited:
            continue
        visited.add(uri)
        queue.append((label, label, uri, 0))

    while queue:
        root_label, label, uri, depth = queue.pop(0)
        if depth >= max_depth:
            failed(root_label)
            yield root_label, label, uri, None
            continue

        try:
            path = _local_path_for(uri, cache_dir)
            if path is None:
                failed(root_label)
                yield root_label, label, uri, None
                continue
            bundle = await registry._load_from_path(path)
        except Exception:  # noqa: BLE001 - an unreadable bundle is not fatal
            failed(root_label)
            yield root_label, label, uri, None
            continue

        yield root_label, label, uri, bundle
        parent_label = bundle.name or label

        for raw_include in bundle.includes or []:
            try:
                spec = registry._parse_include(raw_include)
                if not spec:
                    failed(root_label)
                    continue
                resolved = registry._resolve_include_source(spec)
                if not resolved:
                    failed(root_label)
                    continue
                # A plain name is a registry alias, not a filesystem source.
                if (
                    "://" not in resolved
                    and not resolved.startswith("git+")
                    and not parse_uri(resolved).is_file
                ):
                    resolved = registry.find(resolved)
                    if not resolved:
                        failed(root_label)
                        continue
            except Exception:  # noqa: BLE001
                failed(root_label)
                continue

            if resolved in visited:
                continue
            visited.add(resolved)
            queue.append((root_label, parent_label, resolved, depth + 1))


async def collect_transitive_git_sources(
    roots: Mapping[str, str],
    *,
    registry: "BundleRegistry",
    cache_dir: Path,
    known_uris: set[str] | None = None,
    max_depth: int = 16,
) -> dict[str, TransitiveSource]:
    """Return fragment-stripped included git sources, excluding direct targets.

    Visit full URIs (including fragments) and reuse registry include resolution.
    Missing caches are never downloaded; their include URIs are still returned.
    """
    known = set(known_uris or set())
    root_uris = set(roots.values())
    found: dict[str, TransitiveSource] = {}
    async for root, parent, uri, _ in _iter_cached_bundles(
        roots, registry=registry, cache_dir=cache_dir, max_depth=max_depth
    ):
        if uri in root_uris or not _is_git_uri(uri):
            continue
        key = strip_uri_fragment(uri)
        if key not in known and key not in found:
            found[key] = TransitiveSource(uri=key, parent=parent, root=root, via=uri)
    return found


@dataclass(frozen=True)
class DeclaredModuleSource:
    """First declaration plus replaced URIs, so legacy caches honor overrides."""

    module_id: str
    declared_by: str
    root: str
    overridden_sources: tuple[str, ...] = ()


async def collect_declared_module_sources(
    roots: Mapping[str, str],
    *,
    registry: BundleRegistry,
    cache_dir: Path,
    overrides: Mapping[str, str] | None = None,
) -> tuple[dict[str, DeclaredModuleSource], dict[str, str]]:
    """Return exact effective URI -> declaration metadata, and root failures.

    Inspect the same five module locations as Foundation's source collector.
    Module sources are declarations, never bundle manifests to walk. First
    provenance wins when several module IDs share the same effective source.
    """
    found: dict[str, DeclaredModuleSource] = {}
    failures: dict[str, str] = {}
    overrides = overrides or {}
    async for root, parent, _, bundle in _iter_cached_bundles(
        roots, registry=registry, cache_dir=cache_dir, failures=failures
    ):
        if bundle is None:
            continue
        session = bundle.session or {}
        declarations = [
            session.get("orchestrator"),
            session.get("context"),
            *(bundle.providers or []),
            *(bundle.tools or []),
            *(bundle.hooks or []),
        ]
        for declaration in declarations:
            if not isinstance(declaration, dict):
                continue
            module_id = declaration.get("module")
            if not isinstance(module_id, str):
                continue
            uri = overrides.get(module_id, declaration.get("source"))
            if not isinstance(uri, str) or not uri:
                continue
            entry = found.setdefault(
                uri, DeclaredModuleSource(module_id, bundle.name or parent, root)
            )
            original = declaration.get("source")
            if (
                isinstance(original, str)
                and original != uri
                and original not in entry.overridden_sources
            ):
                found[uri] = replace(
                    entry, overridden_sources=(*entry.overridden_sources, original)
                )
    return found, failures


async def check_transitive_sources(
    sources: Mapping[str, TransitiveSource],
    *,
    cache_dir: Path,
) -> dict[str, TransitiveStatus]:
    """Status-check each transitive source the same way a direct source is checked.

    Uses foundation's ``GitSourceHandler.get_status`` -- the identical call
    ``_check_all_bundle_status`` makes for a registered bundle -- so a
    transitive row carries the same cached/remote commit truth, and the same
    pinned handling, as a direct one.
    """
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler
    from amplifier_foundation.sources.protocol import SourceStatus

    git_handler = GitSourceHandler()
    results: dict[str, TransitiveStatus] = {}

    for key, source in sources.items():
        try:
            parsed = parse_uri(source.uri)
            if not git_handler.can_handle(parsed):
                continue
            status = await git_handler.get_status(parsed, cache_dir)
        except Exception:  # noqa: BLE001 - one bad source must not sink the report
            logger.debug("Transitive source status check failed")
            status = SourceStatus(
                source_uri=source.uri,
                is_cached=False,
                has_update=None,
                error="status check failed",
            )
        results[key] = TransitiveStatus(source=source, status=status)

    return results


async def transitive_statuses_for(
    roots: Mapping[str, str],
    *,
    registry: "BundleRegistry",
    cache_dir: Path,
    known_uris: set[str] | None = None,
) -> dict[str, TransitiveStatus]:
    """Collect + status-check in one call: the shape both update commands want."""
    found = await collect_transitive_git_sources(
        roots, registry=registry, cache_dir=cache_dir, known_uris=known_uris
    )
    return await check_transitive_sources(found, cache_dir=cache_dir)


async def refresh_transitive_source(uri: str, *, cache_dir: Path) -> None:
    """Re-clone *uri*'s cache -- the same mechanism a direct source refresh uses.

    ``GitSourceHandler.update()`` removes the cache and re-resolves, which is
    exactly what ``update_bundle`` ends up doing for a bundle's own source.
    Calling it directly skips composing a bundle whose content we do not need.
    """
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    git_handler = GitSourceHandler()
    parsed = parse_uri(uri)
    await git_handler.update(parsed, cache_dir)


__all__ = [
    "DeclaredModuleSource",
    "TransitiveSource",
    "TransitiveStatus",
    "check_transitive_sources",
    "collect_declared_module_sources",
    "collect_transitive_git_sources",
    "refresh_transitive_source",
    "strip_uri_fragment",
    "transitive_statuses_for",
]
