"""Module cache utilities - single source of truth for cache operations.

Philosophy: DRY consolidation of cache scanning, clearing, and updating.
All module cache operations should go through this module.

Type Detection Philosophy:
- NEVER use naming conventions (amplifier-bundle-*, amplifier-module-*) to identify types
- Use authoritative structural markers instead:
  - Bundles: presence of bundle.md or bundle.yaml file
  - Modules: pyproject.toml with [project.entry-points."amplifier.modules"]
- Extract display names from the items' own definitions, not repo names
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

import yaml

logger = logging.getLogger(__name__)


# =============================================================================
# Structural Type Detection (Authoritative - NOT name-based)
# =============================================================================


def is_bundle(cache_path: Path) -> bool:
    """Check if cached entry is a bundle using authoritative structural marker.

    Per amplifier-foundation/paths/discovery.py, bundles are identified by
    the presence of bundle.md or bundle.yaml file.
    """
    return (cache_path / "bundle.md").exists() or (cache_path / "bundle.yaml").exists()


def get_bundle_name(cache_path: Path) -> str | None:
    """Extract bundle name from bundle.md or bundle.yaml frontmatter.

    Returns the bundle's own declared name, not the repo name.
    """
    # Try bundle.md first (YAML frontmatter between --- markers)
    bundle_md = cache_path / "bundle.md"
    if bundle_md.exists():
        try:
            content = bundle_md.read_text(encoding="utf-8")
            # Extract YAML frontmatter between --- markers
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    if isinstance(frontmatter, dict):
                        bundle_section = frontmatter.get("bundle", {})
                        if isinstance(bundle_section, dict) and "name" in bundle_section:
                            return bundle_section["name"]
        except Exception as e:
            logger.debug(f"Could not parse bundle.md frontmatter: {e}")

    # Try bundle.yaml
    bundle_yaml = cache_path / "bundle.yaml"
    if bundle_yaml.exists():
        try:
            data = yaml.safe_load(bundle_yaml.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                bundle_section = data.get("bundle", data)  # May be nested or flat
                if isinstance(bundle_section, dict) and "name" in bundle_section:
                    return bundle_section["name"]
        except Exception as e:
            logger.debug(f"Could not parse bundle.yaml: {e}")

    return None


def get_module_info_from_pyproject(cache_path: Path) -> tuple[str | None, str | None]:
    """Extract module name and type from pyproject.toml.

    Per amplifier-core/loader.py, modules are identified by:
    - Entry point group: amplifier.modules
    - The entry point key (e.g., "tool-bash") reveals both ID and type

    Returns:
        Tuple of (module_id, module_type) or (None, None) if not a module.
    """
    pyproject = cache_path / "pyproject.toml"
    if not pyproject.exists():
        return None, None

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        entry_points = data.get("project", {}).get("entry-points", {}).get("amplifier.modules", {})

        if not entry_points:
            return None, None

        # Get the first entry point key (e.g., "tool-bash", "provider-anthropic")
        module_id = next(iter(entry_points.keys()))

        # Infer type from the entry point key prefix
        prefix = module_id.split("-")[0] if "-" in module_id else module_id
        type_map = {
            "tool": "tool",
            "hooks": "hook",
            "provider": "provider",
            "loop": "orchestrator",
            "context": "context",
            "agent": "agent",
        }
        module_type = type_map.get(prefix, "module")

        return module_id, module_type

    except Exception as e:
        logger.debug(f"Could not parse pyproject.toml: {e}")
        return None, None


def get_package_name_from_pyproject(cache_path: Path) -> str | None:
    """Get the package name from pyproject.toml [project] name field."""
    pyproject = cache_path / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return data.get("project", {}).get("name")
    except Exception:
        return None


@dataclass
class CachedModuleInfo:
    """Information about a cached module or bundle.

    Note: Despite the name, this also tracks bundles. The module_type field
    distinguishes between them (module_type="bundle" for bundles).
    """

    module_id: str  # Entry point key for modules (e.g., "tool-bash"), bundle name for bundles
    module_type: str  # tool, hook, provider, orchestrator, context, agent, bundle
    ref: str
    sha: str
    url: str
    is_mutable: bool
    cached_at: str
    cache_path: Path
    display_name: str = ""  # User-friendly name from the item's own definition


def get_cache_dir() -> Path:
    """Get the module cache directory path.

    Uses ~/.amplifier/cache/ as the consolidated cache directory for all module
    and bundle caching. This unified path ensures update checks find modules
    regardless of whether they were installed via bundles.
    """
    return Path.home() / ".amplifier" / "cache"


def _infer_module_type_from_name(module_id: str) -> str:
    """FALLBACK: Infer module type from ID prefix.

    WARNING: This is a fallback only. Prefer structural detection via
    get_module_info_from_pyproject() which uses authoritative entry points.
    """
    if module_id.startswith("tool-"):
        return "tool"
    if module_id.startswith("hooks-"):
        return "hook"
    if module_id.startswith("provider-"):
        return "provider"
    if module_id.startswith("loop-"):
        return "orchestrator"
    if module_id.startswith("context-"):
        return "context"
    if module_id.startswith("agent-"):
        return "agent"
    return "unknown"


def _extract_repo_name(url: str) -> str:
    """Extract repository name from URL (for fallback identification only)."""
    repo_name = url.rstrip("/").split("/")[-1]
    # Remove .git suffix properly (not with rstrip which removes any char)
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    return repo_name


def scan_cached_modules(type_filter: str = "all") -> list[CachedModuleInfo]:
    """Scan and return info for all cached modules and bundles.

    Single source of truth for cache scanning.
    Used by: module list, module check-updates, source_status.py

    Uses RECURSIVE search to find all cached items, including those in
    subdirectories like ~/.amplifier/cache/modules/.

    Type detection uses STRUCTURAL markers (not naming conventions):
    - Bundles: presence of bundle.md or bundle.yaml
    - Modules: pyproject.toml with amplifier.modules entry points

    Display names come from the items' own definitions:
    - Bundles: bundle.name from bundle.md/bundle.yaml frontmatter
    - Modules: entry point key from pyproject.toml

    Args:
        type_filter: Filter by type ("all", "tool", "hook", "provider", "bundle", etc.)

    Returns:
        List of CachedModuleInfo sorted by module_id
    """
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        return []

    modules: list[CachedModuleInfo] = []
    seen_paths: set[Path] = set()  # Avoid duplicates

    # RECURSIVE search for all cache metadata files
    # This finds modules in subdirectories like cache/modules/
    for meta_file in cache_dir.rglob(".amplifier_cache_meta.json"):
        cache_entry = meta_file.parent

        # Avoid duplicates
        if cache_entry in seen_paths:
            continue
        seen_paths.add(cache_entry)

        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
            url = metadata.get("git_url", "")
            sha = metadata.get("commit", "")
            ref = metadata.get("ref", "main")

            # === STRUCTURAL TYPE DETECTION ===
            # Check for bundle first (has bundle.md or bundle.yaml)
            if is_bundle(cache_entry):
                module_type = "bundle"
                # Get bundle name from its own definition
                bundle_name = get_bundle_name(cache_entry)
                module_id = bundle_name if bundle_name else _extract_repo_name(url)
                display_name = bundle_name if bundle_name else module_id
            else:
                # Check for module (has pyproject.toml with entry points)
                entry_id, entry_type = get_module_info_from_pyproject(cache_entry)
                if entry_id:
                    module_id = entry_id
                    module_type = entry_type or "module"
                    display_name = entry_id
                else:
                    # Fallback to name-based detection
                    repo_name = _extract_repo_name(url)
                    if repo_name.startswith("amplifier-module-"):
                        module_id = repo_name[len("amplifier-module-") :]
                    else:
                        module_id = repo_name
                    module_type = _infer_module_type_from_name(module_id)
                    display_name = module_id

            # Apply type filter
            if type_filter != "all" and type_filter != module_type:
                continue

            is_mutable = not _is_immutable_ref(ref)

            modules.append(
                CachedModuleInfo(
                    module_id=module_id,
                    module_type=module_type,
                    ref=ref,
                    sha=sha[:8] if sha else "",
                    url=url,
                    is_mutable=is_mutable,
                    cached_at=metadata.get("cached_at", ""),
                    cache_path=cache_entry,
                    display_name=display_name,
                )
            )
        except Exception as e:
            logger.debug(f"Could not read metadata from {meta_file}: {e}")
            continue

    # Also check older format: {hash}/{ref}/.amplifier_cache_metadata.json
    for legacy_meta in cache_dir.rglob(".amplifier_cache_metadata.json"):
        cache_entry = legacy_meta.parent

        if cache_entry in seen_paths:
            continue
        seen_paths.add(cache_entry)

        try:
            metadata = json.loads(legacy_meta.read_text(encoding="utf-8"))
            url = metadata.get("url", "")

            # Use structural detection for older entries too
            if is_bundle(cache_entry):
                module_type = "bundle"
                bundle_name = get_bundle_name(cache_entry)
                module_id = bundle_name if bundle_name else _extract_repo_name(url)
                display_name = bundle_name if bundle_name else module_id
            else:
                entry_id, entry_type = get_module_info_from_pyproject(cache_entry)
                if entry_id:
                    module_id = entry_id
                    module_type = entry_type or "module"
                    display_name = entry_id
                else:
                    repo_name = _extract_repo_name(url)
                    if repo_name.startswith("amplifier-module-"):
                        module_id = repo_name[len("amplifier-module-") :]
                    else:
                        module_id = repo_name
                    module_type = _infer_module_type_from_name(module_id)
                    display_name = module_id

            if type_filter != "all" and type_filter != module_type:
                continue

            modules.append(
                CachedModuleInfo(
                    module_id=module_id,
                    module_type=module_type,
                    ref=metadata.get("ref", "unknown"),
                    sha=metadata.get("sha", "")[:8],
                    url=url,
                    is_mutable=metadata.get("is_mutable", True),
                    cached_at=metadata.get("cached_at", ""),
                    cache_path=cache_entry,
                    display_name=display_name,
                )
            )
        except Exception as e:
            logger.debug(f"Could not read metadata from {legacy_meta}: {e}")
            continue

    # Sort by display_name for user-friendly output
    modules.sort(key=lambda m: m.display_name)
    return modules


def _is_immutable_ref(ref: str) -> bool:
    """Check if ref is immutable (SHA or version tag)."""
    # Full or short SHA
    if re.match(r"^[0-9a-f]{7,40}$", ref):
        return True
    # Semantic version tags
    return bool(re.match(r"^v?\d+\.\d+", ref))


def find_cached_module(module_id: str) -> CachedModuleInfo | None:
    """Find a specific cached module by ID.

    Args:
        module_id: Module ID to find (e.g., "tool-filesystem")

    Returns:
        CachedModuleInfo if found, None otherwise
    """
    for module in scan_cached_modules():
        if module.module_id == module_id:
            return module
    return None


def clear_module_cache(
    module_id: str | None = None,
    mutable_only: bool = False,
    progress_callback: Callable[[str, str], None] | None = None,
) -> tuple[int, int]:
    """Clear module cache entries.

    Single source of truth for cache deletion.
    Used by: module update, execute_selective_module_update

    Args:
        module_id: Specific module to clear (None = all modules)
        mutable_only: Only clear mutable refs (branches, not tags/SHAs)
        progress_callback: Optional callback(module_id, status) for progress

    Returns:
        Tuple of (cleared_count, skipped_count)
    """
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        return 0, 0

    cleared = 0
    skipped = 0

    if module_id is None and not mutable_only:
        # Clear ALL cached modules - simple delete of all directories
        # Foundation will rebuild correctly on next use
        for entry in cache_dir.iterdir():
            if entry.is_dir():
                try:
                    if progress_callback:
                        progress_callback(entry.name, "clearing")
                    shutil.rmtree(entry)
                    cleared += 1
                except Exception as e:
                    logger.warning(f"Could not clear {entry}: {e}")
        return cleared, skipped

    # For specific module or mutable_only: use scan_cached_modules to find entries
    # This handles both current and older cache formats
    modules = scan_cached_modules()

    for module in modules:
        # Filter by module_id if specified
        if module_id and module.module_id != module_id:
            continue

        # Skip immutable refs if mutable_only is set
        if mutable_only and not module.is_mutable:
            skipped += 1
            continue

        # Report progress
        if progress_callback:
            progress_callback(module.module_id, "clearing")

        # Delete cache directory
        try:
            if module.cache_path.exists():
                shutil.rmtree(module.cache_path)
                cleared += 1
                logger.debug(f"Cleared cache for {module.module_id}@{module.ref}")
        except Exception as e:
            logger.warning(f"Could not clear {module.cache_path}: {e}")
            continue

    return cleared, skipped


async def update_module(
    url: str,
    ref: str,
    progress_callback: Callable[[str, str], None] | None = None,
) -> Path:
    """Clear cache and immediately re-download a module.

    Single source of truth for update (clear + re-download).
    Uses foundation's SimpleSourceResolver (proper git clone, not uv pip install).

    Args:
        url: Git repository URL
        ref: Git ref (branch, tag, or SHA)
        progress_callback: Optional callback(module_id, status) for progress

    Returns:
        Path to the newly downloaded module
    """
    from amplifier_foundation.sources import SimpleSourceResolver

    repo_name = _extract_repo_name(url)
    # Normalize to match how scan_cached_modules() derives module_id from
    # pyproject.toml entry points (e.g., "provider-anthropic" not
    # "amplifier-module-provider-anthropic")
    if repo_name.startswith("amplifier-module-"):
        module_id = repo_name[len("amplifier-module-"):]
    else:
        module_id = repo_name

    # Report progress: clearing
    if progress_callback:
        progress_callback(module_id, "clearing")

    # Clear existing cache for this module
    clear_module_cache(module_id=module_id)

    # Report progress: downloading
    if progress_callback:
        progress_callback(module_id, "downloading")

    # Build git URI in foundation format (git+url@ref)
    uri = f"git+{url}@{ref}"

    # Use foundation's resolver (creates proper .git directory via git clone)
    cache_dir = get_cache_dir()
    resolver = SimpleSourceResolver(cache_dir=cache_dir)
    result = await resolver.resolve(uri)

    logger.debug(f"Updated {module_id}@{ref} to {result.active_path}")

    return result.active_path
