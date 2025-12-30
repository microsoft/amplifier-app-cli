"""App-layer bundle discovery implementing filesystem search paths.

This module implements CLI-specific bundle discovery with search paths,
following the same pattern as profile/agent discovery.

Per KERNEL_PHILOSOPHY: Search paths are APP LAYER POLICY.
Per MODULAR_DESIGN_PHILOSOPHY: Bundles are just content - mechanism is generic.

Uses BundleRegistry from amplifier-foundation for central bundle management,
adding CLI-specific policy (search paths, well-known bundles).

Packaged bundles (foundation, design-intelligence, recipes, etc.) are discovered
via a general mechanism that finds bundles co-located with Python packages.
This is app-layer policy - the foundation library knows nothing about specific bundles.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_foundation import BundleRegistry

if TYPE_CHECKING:
    from amplifier_app_cli.lib.legacy import CollectionResolver

logger = logging.getLogger(__name__)

# ===========================================================================
# WELL-KNOWN BUNDLES (APP-LAYER POLICY)
# ===========================================================================
# Following the foundation-first pattern (see AGENTS.md "Foundation-First
# Development Strategy"), these are bundles the CLI knows about by default.
#
# Each entry maps bundle name → info dict with:
#   - package: Python package name (for local editable install check)
#   - remote: Git URL (fallback when package not installed)
#
# Local package is checked first for performance (editable installs).
# Remote URL is used as fallback, ensuring bundles ALWAYS resolve.
WELL_KNOWN_BUNDLES: dict[str, dict[str, str]] = {
    "foundation": {
        "package": "amplifier_foundation",
        "remote": "git+https://github.com/microsoft/amplifier-foundation@main",
    },
    "recipes": {
        "package": "",  # No Python package - bundle-only
        "remote": "git+https://github.com/microsoft/amplifier-bundle-recipes@main",
    },
    "design-intelligence": {
        "package": "",  # No Python package - bundle-only
        "remote": "git+https://github.com/microsoft/amplifier-bundle-design-intelligence@main",
    },
    # TODO: Revisit this - experimental bundles should ideally be discoverable
    # via a general mechanism (e.g., foundation:experiments/delegation-only syntax)
    # rather than hardcoded here. For now, adding as well-known for easy access.
    "exp-delegation": {
        "package": "",  # Experimental bundle in foundation/experiments/
        "remote": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/delegation-only",
    },
}


class AppBundleDiscovery:
    """CLI-specific bundle discovery with filesystem search paths.

    Uses BundleRegistry for central bundle management while adding
    CLI-specific policy (search paths, well-known bundles).

    Search order (highest precedence first):
    1. Manual registrations (via register())
    2. Well-known bundles (foundation, etc. - local package → remote fallback)
    3. Project bundles (.amplifier/bundles/)
    4. User bundles (~/.amplifier/bundles/)
    5. Collection bundles (via CollectionResolver)
    6. Bundled bundles (package data/bundles/)

    Bundle resolution:
    - "name" → looks for name/, name.yaml, name.md in search paths
    - "parent/child" → looks for parent/child/, etc.
    """

    def __init__(
        self,
        search_paths: list[Path] | None = None,
        collection_resolver: CollectionResolver | None = None,
        registry: BundleRegistry | None = None,
    ) -> None:
        """Initialize discovery with search paths.

        Args:
            search_paths: Explicit search paths (default: CLI standard paths).
            collection_resolver: Resolver for collection bundles.
            registry: Optional BundleRegistry (creates default if not provided).
        """
        self._search_paths = search_paths or self._default_search_paths()
        self._collection_resolver = collection_resolver
        self._registry = registry or BundleRegistry()

        # Register well-known bundles first (defaults)
        self._register_well_known_bundles()

        # Load user-added bundles second (can override well-known bundles)
        self._load_user_registry()

    def _load_user_registry(self) -> None:
        """Load user-added bundles from the registry file.

        User bundles have higher priority than well-known bundles,
        allowing users to override or shadow built-in bundles.
        """
        from amplifier_app_cli.lib.bundle_loader import user_registry

        bundles = user_registry.load_user_registry()
        for name, info in bundles.items():
            uri = info.get("uri")
            if uri:
                self._registry.register({name: uri})
                logger.debug(f"Loaded user bundle '{name}' → {uri}")

    def _register_well_known_bundles(self) -> None:
        """Register well-known bundles with the registry.

        For each well-known bundle, resolves the URI (local package → remote fallback)
        and registers it with the BundleRegistry.
        """
        for name, bundle_info in WELL_KNOWN_BUNDLES.items():
            # Try local package first (faster for editable installs)
            uri = self._find_packaged_bundle(bundle_info["package"])
            if not uri:
                # Fallback to remote URI (always works)
                uri = bundle_info["remote"]
            self._registry.register({name: uri})
            logger.debug(f"Registered well-known bundle '{name}' → {uri}")

    @property
    def registry(self) -> BundleRegistry:
        """Get the underlying BundleRegistry for loading bundles."""
        return self._registry

    def _default_search_paths(self) -> list[Path]:
        """Get default CLI search paths for bundles.

        Returns:
            List of paths to search, highest precedence first.
        """
        package_dir = Path(__file__).parent.parent.parent
        bundled = package_dir / "data" / "bundles"

        paths = []

        # Project (highest precedence)
        project_bundles = Path.cwd() / ".amplifier" / "bundles"
        if project_bundles.exists():
            paths.append(project_bundles)

        # User
        user_bundles = Path.home() / ".amplifier" / "bundles"
        if user_bundles.exists():
            paths.append(user_bundles)

        # Bundled (lowest)
        if bundled.exists():
            paths.append(bundled)

        return paths

    def find(self, name: str) -> str | None:
        """Find a bundle URI by name.

        Search order:
        1. BundleRegistry (includes well-known bundles registered on init)
        2. Filesystem search paths
        3. Collections (if resolver provided)

        Args:
            name: Bundle name (e.g., "foundation", "design-intelligence").

        Returns:
            URI for the bundle, or None if not found.
        """
        # Check registry first (includes well-known bundles)
        uri = self._registry.find(name)
        if uri:
            return uri

        # Search filesystem paths
        for base_path in self._search_paths:
            uri = self._find_in_path(base_path, name)
            if uri:
                logger.debug(f"Found bundle '{name}' at {uri}")
                # Register for future lookups
                self._registry.register({name: uri})
                return uri

        # Try collections if resolver available
        if self._collection_resolver:
            uri = self._find_in_collections(name)
            if uri:
                logger.debug(f"Found bundle '{name}' in collection at {uri}")
                # Register for future lookups
                self._registry.register({name: uri})
                return uri

        logger.debug(f"Bundle '{name}' not found in any search path")
        return None

    def _find_packaged_bundle(self, package_name: str) -> str | None:
        """Find a bundle co-located with a Python package.

        Convention: Bundle root is the parent directory of the Python package.
        This works for editable installs where the package lives in:
            repo-root/package_name/__init__.py
        And the bundle.md is at:
            repo-root/bundle.md

        Args:
            package_name: Python package name (e.g., "amplifier_foundation").

        Returns:
            file:// URI for the bundle, or None if not found.
        """
        try:
            pkg = importlib.import_module(package_name)
            if pkg.__file__ is None:
                return None

            pkg_dir = Path(pkg.__file__).parent
            bundle_root = pkg_dir.parent  # Go up from package/ to repo root

            # Check for bundle definition file
            if (bundle_root / "bundle.md").exists():
                return f"file://{bundle_root.resolve()}"
            if (bundle_root / "bundle.yaml").exists():
                return f"file://{bundle_root.resolve()}"

        except ImportError:
            logger.debug(f"Package '{package_name}' not installed")
        except Exception as e:
            logger.debug(f"Error finding packaged bundle '{package_name}': {e}")

        return None

    def _find_in_path(self, base_path: Path, name: str) -> str | None:
        """Search for bundle in a single base path.

        Looks for (in order):
        1. base_path/name/bundle.md (directory bundle with markdown)
        2. base_path/name/bundle.yaml (directory bundle with YAML)
        3. base_path/name.yaml (single file YAML bundle)
        4. base_path/name.md (single file markdown bundle)

        Args:
            base_path: Base directory to search.
            name: Bundle name (may contain / for nested paths).

        Returns:
            file:// URI pointing to the bundle directory (for directory bundles)
            or the bundle file (for single-file bundles). None if not found.
        """
        # Handle nested names (e.g., "foundation/providers/anthropic")
        name_path = Path(name)
        target_dir = base_path / name_path

        # Check directory bundle formats - return directory URI for consistency
        # with _find_packaged_bundle() which also returns directory URIs
        if target_dir.is_dir():
            bundle_md = target_dir / "bundle.md"
            if bundle_md.exists():
                return f"file://{target_dir.resolve()}"

            bundle_yaml = target_dir / "bundle.yaml"
            if bundle_yaml.exists():
                return f"file://{target_dir.resolve()}"

        # Check single file formats - return file URI (no directory exists)
        yaml_file = base_path / f"{name}.yaml"
        if yaml_file.exists():
            return f"file://{yaml_file.resolve()}"

        md_file = base_path / f"{name}.md"
        if md_file.exists():
            return f"file://{md_file.resolve()}"

        return None

    def _find_in_collections(self, name: str) -> str | None:
        """Search for bundle in installed collections.

        Args:
            name: Bundle name.

        Returns:
            file:// URI if found, None otherwise.
        """
        if not self._collection_resolver:
            return None

        for _metadata_name, collection_path in self._collection_resolver.list_collections():
            # Check if collection has bundles
            bundles_dir = collection_path / "bundles"
            if bundles_dir.exists():
                uri = self._find_in_path(bundles_dir, name)
                if uri:
                    return uri

            # Also check collection root for direct bundle files
            uri = self._find_in_path(collection_path, name)
            if uri:
                return uri

        return None

    def register(self, name: str, uri: str) -> None:
        """Register a bundle name to URI mapping.

        Manual registrations take precedence over filesystem search.

        Args:
            name: Bundle name.
            uri: URI for the bundle.
        """
        self._registry.register({name: uri})
        logger.debug(f"Registered bundle '{name}' → {uri}")

    def list_bundles(self) -> list[str]:
        """List all discoverable ROOT bundle names.

        Only returns root bundles (not sub-bundles like behaviors, providers).
        Sub-bundles are tracked in the registry but filtered out here since
        they're part of their root bundle's git repository.

        Returns:
            List of root bundle names found in all search paths.
        """
        bundles: set[str] = set()

        # Add registered bundles (includes well-known bundles registered on init)
        bundles.update(self._registry.list_registered())

        # Scan filesystem paths
        for base_path in self._search_paths:
            bundles.update(self._scan_path_for_bundles(base_path))

        # Scan collections
        if self._collection_resolver:
            bundles.update(self._scan_collections_for_bundles())

        # Read from persisted registry (includes bundles loaded via includes)
        bundles.update(self._read_persisted_registry())

        # Filter to only root bundles using the persisted registry as authority
        # The persisted registry tracks is_root for all bundles loaded by foundation
        root_bundles, sub_bundles = self._get_root_and_sub_bundles()
        
        # Remove any sub-bundles from our discovered set
        bundles -= sub_bundles
        
        # Also add any root bundles we might have missed (from persisted registry)
        bundles.update(root_bundles)

        return sorted(bundles)

    def _get_root_and_sub_bundles(self) -> tuple[set[str], set[str]]:
        """Get sets of root bundles and sub-bundles from persisted registry.

        Uses foundation's persisted registry as the authority for which bundles
        are roots vs sub-bundles.

        Returns:
            Tuple of (root_bundle_names, sub_bundle_names)
        """
        import json

        registry_path = Path.home() / ".amplifier" / "registry.json"
        if not registry_path.exists():
            return set(), set()

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)

            root_bundles: set[str] = set()
            sub_bundles: set[str] = set()

            for name, bundle_data in data.get("bundles", {}).items():
                if bundle_data.get("is_root", True):  # Default True for backwards compat
                    root_bundles.add(name)
                else:
                    sub_bundles.add(name)

            logger.debug(
                f"Registry has {len(root_bundles)} root bundles, {len(sub_bundles)} sub-bundles"
            )
            return root_bundles, sub_bundles
        except Exception as e:
            logger.debug(f"Could not read persisted registry: {e}")
            return set(), set()

    def _read_persisted_registry(self) -> list[str]:
        """Read root bundle names from foundation's persisted registry.

        This discovers bundles that were loaded during previous sessions.
        Only returns ROOT bundles (not sub-bundles like behaviors/providers).
        Sub-bundles are tracked but filtered out since they're part of their
        root bundle's git repository.

        Returns:
            List of root bundle names from persisted registry.
        """
        root_bundles, _ = self._get_root_and_sub_bundles()
        return list(root_bundles)

    def _scan_path_for_bundles(self, base_path: Path) -> list[str]:
        """Scan a path for bundle names.

        Args:
            base_path: Directory to scan.

        Returns:
            List of bundle names found.
        """
        bundles = []

        if not base_path.exists():
            return bundles

        for item in base_path.iterdir():
            if item.is_dir():
                # Directory bundle if it has bundle.md or bundle.yaml
                if (item / "bundle.md").exists() or (item / "bundle.yaml").exists():
                    bundles.append(item.name)
            elif item.suffix in (".yaml", ".yml", ".md"):
                # Single file bundle
                bundles.append(item.stem)

        return bundles

    def _scan_collections_for_bundles(self) -> list[str]:
        """Scan collections for bundle names.

        Returns:
            List of bundle names found in collections.
        """
        bundles = []

        if not self._collection_resolver:
            return bundles

        for _metadata_name, collection_path in self._collection_resolver.list_collections():
            bundles_dir = collection_path / "bundles"
            if bundles_dir.exists():
                bundles.extend(self._scan_path_for_bundles(bundles_dir))

        return bundles
