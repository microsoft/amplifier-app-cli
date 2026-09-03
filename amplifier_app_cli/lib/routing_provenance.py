"""Routing-matrix provenance: which matrix file wins, and what it shadows.

``amplifier routing list`` used to show a user matrix and a same-named bundle
matrix as peers.  At load time only one of them is ever read: hooks-routing's
``mount()`` searches ``[*custom_routing_dirs, bundle routing/]`` and takes the
first hit, so a same-named file in ``~/.amplifier/routing/`` silently makes the
shipped bundle matrix dead.  Every matrix change shipped in the bundle is inert
on such a host and nothing in the CLI said so.

**The precedence rule is not re-implemented here.**  It lives in exactly one
place -- ``resolve_matrix_source()`` in the routing-matrix bundle's
``amplifier_module_hooks_routing/matrix_loader.py`` (routing-matrix PR #52).
This module's entire job is to reach that function from a CLI process that
never starts a session, and to degrade visibly-by-omission when it cannot.

Why a dynamic load rather than an ``import``:
    hooks-routing is a *bundle module*.  It is not a distribution app-cli
    depends on, it is not on ``sys.path``, and ``amplifier routing list`` never
    mounts a bundle -- so the ``model_role_resolver`` capability that publishes
    ``matrix_path`` / ``matrix_source`` / ``shadowed_paths`` at session start
    does not exist in this process either.  The bundle *is* on disk, in the
    same cache directory ``_discover_matrix_files()`` already globs, so
    ``matrix_loader.py`` is loaded from there by file path.

When the cached bundle predates PR #52 (no ``resolve_matrix_source``), or the
load fails for any reason, :func:`resolve_matrix_origins` returns ``{}`` and
the listing is byte-identical to what it was before.  We never guess the
precedence rule ourselves -- a wrong shadowing marker is worse than none.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cache directory name prefix for the routing-matrix bundle, as written by the
# bundle loader (see lib/bundle_loader/discovery.py WELL_KNOWN_BUNDLES).
BUNDLE_CACHE_PREFIX = "amplifier-bundle-routing-matrix"

# Where hooks-routing's loader lives, relative to the bundle root.
_MATRIX_LOADER_RELPATH = (
    Path("modules")
    / "hooks-routing"
    / "amplifier_module_hooks_routing"
    / "matrix_loader.py"
)

# Loaded-module cache keyed by the resolved matrix_loader.py path, so a listing
# that resolves many matrix names pays the file load exactly once.
_loader_cache: dict[str, Any] = {}


def _key(path: Path) -> Path:
    """Best-effort canonical form for identity comparison."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - exotic filesystem states
        return path


def is_bundle_routing_dir(routing_dir: Path) -> bool:
    """True when *routing_dir* is a ``routing/`` dir inside a routing-matrix bundle.

    Two signals, either sufficient: the cache-directory naming convention, or
    the presence of the hooks-routing module beside it (which covers a bundle
    checked out somewhere other than the cache, e.g. a dev worktree).
    """
    parent = routing_dir.parent
    if parent.name.startswith(BUNDLE_CACHE_PREFIX):
        return True
    return (parent / _MATRIX_LOADER_RELPATH).exists()


def classify_routing_dirs(
    matrix_files: Sequence[Path],
) -> tuple[list[Path], list[Path]]:
    """Split the dirs *matrix_files* came from into ``(custom_dirs, bundle_dirs)``.

    This classifies *where a directory lives*; it does not decide precedence --
    that stays entirely inside ``resolve_matrix_source``, which also owns the
    aliasing rules (a "custom" dir that really is the bundle dir is labelled
    ``bundle``, and a file reached twice is counted once).

    Order is first-seen, deduplicated by resolved path.
    """
    custom_dirs: list[Path] = []
    bundle_dirs: list[Path] = []
    seen: set[Path] = set()

    for file_path in matrix_files:
        parent = file_path.parent
        parent_key = _key(parent)
        if parent_key in seen:
            continue
        seen.add(parent_key)
        if is_bundle_routing_dir(parent):
            bundle_dirs.append(parent)
        else:
            custom_dirs.append(parent)

    return custom_dirs, bundle_dirs


def load_resolve_matrix_source(
    bundle_dirs: Sequence[Path],
) -> Callable[..., Any] | None:
    """Load hooks-routing's ``resolve_matrix_source`` from a cached bundle.

    Returns ``None`` -- never a fallback implementation -- when no cached
    routing-matrix bundle carries the function (e.g. a bundle older than
    routing-matrix PR #52), or when the module cannot be loaded.
    """
    for bundle_dir in bundle_dirs:
        loader_path = bundle_dir.parent / _MATRIX_LOADER_RELPATH
        if not loader_path.exists():
            continue

        cache_key = str(_key(loader_path))
        if cache_key in _loader_cache:
            module = _loader_cache[cache_key]
        else:
            module = _load_module(loader_path)
            _loader_cache[cache_key] = module

        if module is None:
            continue

        fn = getattr(module, "resolve_matrix_source", None)
        if callable(fn):
            return fn

        logger.debug(
            "routing-matrix bundle at %s has no resolve_matrix_source "
            "(bundle predates PR #52); shadowing will not be marked",
            bundle_dir.parent,
        )

    return None


def _load_module(loader_path: Path) -> Any | None:
    """Import ``matrix_loader.py`` by file path, under a synthetic module name.

    A synthetic top-level name is safe because ``matrix_loader`` has no
    module-level relative imports (its one ``from .resolver import ...`` is
    inside a function body).  Should that ever change, the load raises and we
    degrade to "no markers" rather than reporting a guess.

    The module is registered in ``sys.modules`` *before* execution because
    ``matrix_loader`` defines a ``@dataclass`` (``MatrixSource``), and
    ``dataclasses`` looks its own module up by name while building the class.
    Without the registration the decorator fails with an opaque
    ``'NoneType' object has no attribute '__dict__'``.
    """
    module_name = f"_amplifier_cli_routing_matrix_loader_{abs(hash(str(loader_path)))}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, loader_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Could not load routing matrix_loader from %s: %s", loader_path, e)
        return None


def _stems_in_order(matrix_files: Sequence[Path]) -> list[str]:
    """Every distinct file stem, in first-seen order."""
    stems: list[str] = []
    seen: set[str] = set()
    for file_path in matrix_files:
        if file_path.stem not in seen:
            seen.add(file_path.stem)
            stems.append(file_path.stem)
    return stems


def resolve_matrix_origins(matrix_files: Sequence[Path]) -> dict[str, Any]:
    """Map matrix file stem -> ``MatrixSource`` for every discovered matrix.

    The returned objects are hooks-routing's own ``MatrixSource`` dataclass:
    ``.path`` (the winner), ``.source`` (``"user"`` / ``"bundle"``),
    ``.shadowed`` (``(path, source)`` for every same-named file that lost),
    ``.is_shadowed``, and ``.to_dict()``.

    Returns ``{}`` when ``resolve_matrix_source`` is unreachable -- callers
    must treat an absent entry as "no provenance known", never as "unshadowed".
    """
    custom_dirs, bundle_dirs = classify_routing_dirs(matrix_files)
    resolve = load_resolve_matrix_source(bundle_dirs)
    if resolve is None or not bundle_dirs:
        return {}

    origins: dict[str, Any] = {}
    for stem in _stems_in_order(matrix_files):
        best: Any | None = None
        # A host normally has exactly one cached routing-matrix bundle. If it
        # has several, the CLI cannot know which one a session would mount, so
        # report the outcome that shows the most suppression -- the claim
        # "a same-named bundle matrix exists and loses" is true either way.
        for bundle_dir in bundle_dirs:
            try:
                origin = resolve(stem, custom_dirs, bundle_dir)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("resolve_matrix_source failed for %r: %s", stem, e)
                continue
            if best is None or len(origin.shadowed) > len(best.shadowed):
                best = origin
        if best is not None:
            origins[stem] = best

    return origins


def resolve_winning_paths(
    matrix_files: Sequence[Path],
    origins: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Map matrix file stem -> the file hooks-routing would actually load.

    This is the answer to "which file wins?", and it is deliberately NOT
    derived from the order ``_discover_matrix_files()`` happens to return.
    That order is ``sorted()``, and ``sorted()`` puts
    ``~/.amplifier/cache/...`` before ``~/.amplifier/routing/...`` only
    because ``"c" < "r"``.  Any rename of either directory silently flips the
    answer, with no error.

    Two sources, in order:

    1. **The loader's own function.**  ``origins`` (from
       :func:`resolve_matrix_origins`) carries hooks-routing's
       ``MatrixSource.path`` -- literally the value its ``mount()`` assigns to
       ``matrix_path`` and loads (routing-matrix ``__init__.py``: ``matrix_origin
       = resolve_matrix_source(...)`` then ``matrix_path = matrix_origin.path``).
       When present, that path is used verbatim.

    2. **Directory precedence, as a labelled fallback.**  When the cached
       bundle predates routing-matrix PR #52 there is no
       ``resolve_matrix_source`` to ask, yet the CLI must still put *some* file
       in each row.  It then picks the first candidate whose directory appears
       earliest in ``[*custom_dirs, *bundle_dirs]`` -- the same list
       hooks-routing builds as ``search_dirs = [*custom_routing_dirs,
       routing_dir]``.

    The distinction between this and the shadowing *marker* is deliberate.  A
    marker can be omitted when provenance is unknown (and is -- see
    :func:`resolve_matrix_origins`), because "no claim" is a truthful state.  A
    listing row cannot be omitted, so the fallback picks by the documented rule
    rather than by an alphabetical accident.

    Args:
        matrix_files: Every discovered matrix file.
        origins: Result of :func:`resolve_matrix_origins`, if already computed.
            Passing it avoids re-loading the bundle module.

    Returns:
        ``{stem: winning_path}``, one entry per distinct stem.
    """
    if origins is None:
        origins = resolve_matrix_origins(matrix_files)

    custom_dirs, bundle_dirs = classify_routing_dirs(matrix_files)
    dir_rank = {_key(d): i for i, d in enumerate([*custom_dirs, *bundle_dirs])}

    by_stem: dict[str, list[Path]] = {}
    for file_path in matrix_files:
        by_stem.setdefault(file_path.stem, []).append(file_path)

    winners: dict[str, Path] = {}
    for stem in _stems_in_order(matrix_files):
        candidates = by_stem[stem]

        origin = origins.get(stem)
        loader_path = getattr(origin, "path", None) if origin is not None else None
        if loader_path is not None:
            # The loader's own answer. Prefer the discovered Path object that
            # denotes the same file, so callers keep the path they globbed.
            loader_key = _key(Path(loader_path))
            match = next((c for c in candidates if _key(c) == loader_key), None)
            winners[stem] = match if match is not None else Path(loader_path)
            continue

        # Fallback: earliest directory in [*custom_dirs, *bundle_dirs].
        # Ties (same directory reached twice) keep discovery order.
        winners[stem] = min(
            candidates,
            key=lambda p: dir_rank.get(_key(p.parent), len(dir_rank)),
        )

    return winners
