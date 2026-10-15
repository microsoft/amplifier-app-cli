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
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Mapping

from .file_uri import file_uri_path_value

if TYPE_CHECKING:
    from amplifier_foundation import Bundle, BundleRegistry
    from amplifier_foundation.registry import _NamespaceSource
    from amplifier_foundation.sources.protocol import ResolvedSource, SourceStatus

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


def local_source_path(uri: str) -> Path | None:
    """Decode explicit file URIs once; don't mistake registry aliases for paths."""
    if uri.lower().startswith("file:"):
        native = file_uri_path_value(uri, windows=os.name == "nt")
        return Path(native) if native is not None else None
    if Path(uri).is_absolute() or uri.startswith(("./", "../", ".\\", "..\\")):
        return Path(uri)
    return None


def _cached_source_for(
    uri: str, cache_dir: Path, registry: BundleRegistry
) -> ResolvedSource | None:
    """Reconstruct Foundation's ResolvedSource from existing files, never resolve."""
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.file import FileSourceHandler
    from amplifier_foundation.sources.git import GitSourceHandler
    from amplifier_foundation.sources.protocol import ResolvedSource

    parsed = parse_uri(uri)
    handler = GitSourceHandler()
    if handler.can_handle(parsed):
        source_root = handler._get_cache_path(parsed, cache_dir)
        if source_root.resolve() != Path(os.path.abspath(source_root)):
            return None  # A sibling checkout is not this source's cache.
        path = source_root / parsed.subpath if parsed.subpath else source_root
        path = registry._find_resource_path(path)
    else:
        path = local_source_path(uri)
        if path is None:
            return None
        base = path.resolve()
        subpath = parsed.subpath if uri.lower().startswith("file:") else ""
        path = base / subpath if subpath else base
        path = registry._find_resource_path(path)
        if path is None:
            return None
        source_root = (
            base
            if subpath
            else FileSourceHandler()._find_source_root(path, cache_dir)
        )
    if path is not None and path.is_dir():
        # Validate the actual file the loader would choose, not only its parent.
        path = registry._find_nearest_bundle_file(path, path)
    if path is None or not path.resolve().is_relative_to(source_root.resolve()):
        return None
    return ResolvedSource(active_path=path, source_root=source_root)


async def _cached_namespace_sources(
    uri: str, resolved: ResolvedSource, bundle: Bundle, registry: BundleRegistry
) -> dict[str, _NamespaceSource]:
    """Bind containing-root then self exactly as Foundation does, without loading includes."""
    from amplifier_foundation.registry import _NamespaceSource

    path = resolved.active_path
    source_root = resolved.source_root.resolve()
    search_start = path.parent.parent if path.is_file() else path.parent
    containing = None
    if search_start.resolve().is_relative_to(source_root):
        nearest = registry._find_nearest_bundle_file(search_start, source_root)
        if nearest is not None:
            if not nearest.resolve().is_relative_to(source_root):
                raise ValueError("Escaped containing manifest")
            containing = await registry._load_from_path(nearest)
    bindings = {}
    for owner in (containing, bundle):
        if owner is not None and owner.name and owner.base_path:
            base = (owner.base_path / (owner.namespace_root or ".")).resolve()
            if not base.is_relative_to(source_root):
                raise ValueError("Escaped namespace root")
            bindings[owner.name] = _NamespaceSource(uri, base, source_root)
    return bindings


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
            resolved_source = _cached_source_for(uri, cache_dir, registry)
            if resolved_source is None:
                failed(root_label)
                yield root_label, label, uri, None
                continue
            path = resolved_source.active_path
            bundle = await registry._load_from_path(path)
            namespace_sources = await _cached_namespace_sources(
                uri, resolved_source, bundle, registry
            )
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
                # Foreign namespaces may have no local_path (or a stale one).
                # Rebuild their selected cached context rather than guessing a
                # checkout-relative path or modifying shared registrations.
                bindings = dict(namespace_sources)
                if ":" in spec and "://" not in spec:
                    namespace = spec.split(":", 1)[0]
                    if namespace not in bindings:
                        foreign_uri = registry.find(namespace)
                        if foreign_uri:
                            foreign = _cached_source_for(foreign_uri, cache_dir, registry)
                            if foreign is None:
                                raise ValueError("Missing cached namespace manifest")
                            owner = await registry._load_from_path(foreign.active_path)
                            foreign_bindings = await _cached_namespace_sources(
                                foreign_uri, foreign, owner, registry
                            )
                            if owner.name in foreign_bindings:
                                bindings[namespace] = foreign_bindings[owner.name]
                resolved = registry._resolve_include_source(
                    spec, namespace_sources=bindings
                )
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
    declaring_uri: str = ""
    embedded_in: str | None = None


def _embedded_owner(
    uri: str, declaring_uri: str, cache_dir: Path, registry: BundleRegistry
) -> str | None:
    """Prove local code and its declaring manifest belong to one exact Git cache."""
    from amplifier_foundation.paths.resolution import parse_uri
    from amplifier_foundation.sources.git import GitSourceHandler

    path = local_source_path(uri)
    if path is None or not _is_git_uri(declaring_uri):
        return None
    try:
        root = GitSourceHandler()._get_cache_path(parse_uri(declaring_uri), cache_dir)
        manifest = _cached_source_for(declaring_uri, cache_dir, registry)
        if manifest is None or not root.is_dir() or not path.is_dir():
            return None
        # Do not hide symlinked or escaping paths as repository-owned content.
        canonical = path.resolve(strict=True)
        if canonical != Path(os.path.abspath(path)):
            return None
        if not canonical.is_relative_to(root.resolve()):
            return None
        if not manifest.active_path.resolve().is_relative_to(root.resolve()):
            return None
        if not os.access(path, os.R_OK | os.X_OK):
            return None
    except (OSError, ValueError):
        return None
    return strip_uri_fragment(declaring_uri)


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
    async for root, parent, declaring_uri, bundle in _iter_cached_bundles(
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
            embedded_in = (
                None
                if module_id in overrides
                else _embedded_owner(uri, declaring_uri, cache_dir, registry)
            )
            entry = found.get(uri)
            if entry is None:
                entry = DeclaredModuleSource(
                    module_id,
                    bundle.name or parent,
                    root,
                    declaring_uri=declaring_uri,
                    embedded_in=embedded_in,
                )
            elif entry.embedded_in != embedded_in:
                entry = replace(entry, embedded_in=None)
            found[uri] = entry
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
    "local_source_path",
    "refresh_transitive_source",
    "strip_uri_fragment",
    "transitive_statuses_for",
]
