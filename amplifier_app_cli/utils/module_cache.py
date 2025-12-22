"""Module cache utilities - single source of truth for cache operations.

Philosophy: DRY consolidation of cache scanning, clearing, and updating.
All module cache operations should go through this module.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CachedModuleInfo:
    """Information about a cached module."""

    module_id: str
    module_type: str  # tool, hook, provider, orchestrator, context, agent
    ref: str
    sha: str
    url: str
    is_mutable: bool
    cached_at: str
    cache_path: Path


def get_cache_dir() -> Path:
    """Get the module cache directory path.

    Uses ~/.amplifier/cache/ as the consolidated cache directory for all module
    and bundle caching. This unified path ensures update checks find modules
    regardless of whether they were installed via bundles or the legacy profile system.
    """
    return Path.home() / ".amplifier" / "cache"


def _infer_module_type(module_id: str) -> str:
    """Infer module type from ID prefix."""
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


def _extract_module_id(url: str) -> str:
    """Extract module ID from repository URL.

    Example: https://github.com/microsoft/amplifier-module-tool-filesystem.git
           → tool-filesystem
    """
    repo_name = url.rstrip("/").split("/")[-1]
    # Remove .git suffix properly (not with rstrip which removes any char)
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    # Extract module ID from repo name
    if repo_name.startswith("amplifier-module-"):
        return repo_name[len("amplifier-module-") :]
    return repo_name


def scan_cached_modules(type_filter: str = "all") -> list[CachedModuleInfo]:
    """Scan and return info for all cached modules.

    Single source of truth for cache scanning.
    Used by: module list, module check-updates, source_status.py

    Supports both cache formats:
    - Foundation format: {module-name}-{hash}/.amplifier_cache_meta.json
    - Legacy format: {hash}/{ref}/.amplifier_cache_metadata.json

    Args:
        type_filter: Filter by module type ("all", "tool", "hook", "provider", etc.)

    Returns:
        List of CachedModuleInfo sorted by module_id
    """
    cache_dir = get_cache_dir()

    if not cache_dir.exists():
        return []

    modules: list[CachedModuleInfo] = []
    seen_ids: set[str] = set()  # Avoid duplicates if same module in both formats

    for cache_entry in cache_dir.iterdir():
        if not cache_entry.is_dir():
            continue

        # Try foundation format first: {module-name}-{hash}/.amplifier_cache_meta.json
        foundation_meta = cache_entry / ".amplifier_cache_meta.json"
        if foundation_meta.exists():
            try:
                metadata = json.loads(foundation_meta.read_text(encoding="utf-8"))
                # Foundation format uses git_url and commit
                url = metadata.get("git_url", "")
                sha = metadata.get("commit", "")

                module_id = _extract_module_id(url)
                if module_id in seen_ids:
                    continue
                seen_ids.add(module_id)

                module_type = _infer_module_type(module_id)

                if type_filter != "all" and type_filter != module_type:
                    continue

                # Foundation format doesn't track is_mutable, assume True for branches
                ref = metadata.get("ref", "main")
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
                    )
                )
            except Exception as e:
                logger.debug(f"Could not read foundation metadata from {foundation_meta}: {e}")
            continue

        # Try legacy format: {hash}/{ref}/.amplifier_cache_metadata.json
        for ref_dir in cache_entry.iterdir():
            if not ref_dir.is_dir():
                continue

            legacy_meta = ref_dir / ".amplifier_cache_metadata.json"
            if not legacy_meta.exists():
                continue

            try:
                metadata = json.loads(legacy_meta.read_text(encoding="utf-8"))
                url = metadata.get("url", "")

                module_id = _extract_module_id(url)
                if module_id in seen_ids:
                    continue
                seen_ids.add(module_id)

                module_type = _infer_module_type(module_id)

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
                        cache_path=ref_dir,
                    )
                )
            except Exception as e:
                logger.debug(f"Could not read legacy metadata from {legacy_meta}: {e}")
                continue

    # Sort by module_id for consistent output
    modules.sort(key=lambda m: m.module_id)
    return modules


def _is_immutable_ref(ref: str) -> bool:
    """Check if ref is immutable (SHA or version tag)."""
    import re

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

    for cache_hash in cache_dir.iterdir():
        if not cache_hash.is_dir():
            continue

        for ref_dir in cache_hash.iterdir():
            if not ref_dir.is_dir():
                continue

            metadata_file = ref_dir / ".amplifier_cache_metadata.json"
            if not metadata_file.exists():
                # No metadata - just delete
                try:
                    shutil.rmtree(ref_dir)
                    cleared += 1
                except Exception as e:
                    logger.warning(f"Could not clear {ref_dir}: {e}")
                continue

            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                url = metadata.get("url", "")
                cached_module_id = _extract_module_id(url)

                # Filter by module_id if specified
                if module_id and cached_module_id != module_id:
                    continue

                # Skip immutable refs if mutable_only is set
                if mutable_only and not metadata.get("is_mutable", True):
                    skipped += 1
                    continue

                # Report progress
                if progress_callback:
                    progress_callback(cached_module_id, "clearing")

                # Delete cache directory
                shutil.rmtree(ref_dir)
                cleared += 1

                logger.debug(f"Cleared cache for {cached_module_id}@{metadata.get('ref', 'unknown')}")

            except Exception as e:
                logger.warning(f"Could not clear {ref_dir}: {e}")
                continue

    return cleared, skipped


def update_module(
    url: str,
    ref: str,
    progress_callback: Callable[[str, str], None] | None = None,
) -> Path:
    """Clear cache and immediately re-download a module.

    Single source of truth for update (clear + re-download).
    Uses GitSource from amplifier-module-resolution.

    Args:
        url: Git repository URL
        ref: Git ref (branch, tag, or SHA)
        progress_callback: Optional callback(module_id, status) for progress

    Returns:
        Path to the newly downloaded module
    """
    from amplifier_app_cli.lib.legacy import GitSource

    module_id = _extract_module_id(url)

    # Report progress: clearing
    if progress_callback:
        progress_callback(module_id, "clearing")

    # Clear existing cache for this module
    clear_module_cache(module_id=module_id)

    # Report progress: downloading
    if progress_callback:
        progress_callback(module_id, "downloading")

    # Re-download using GitSource
    git_source = GitSource(url=url, ref=ref)
    result_path = git_source.resolve()

    logger.debug(f"Updated {module_id}@{ref} to {result_path}")

    return result_path
