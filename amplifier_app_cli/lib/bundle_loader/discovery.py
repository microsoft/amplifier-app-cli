"""App-layer bundle discovery implementing filesystem search paths.

This module implements BundleDiscoveryProtocol with CLI-specific search paths,
following the same pattern as profile/agent discovery.

Per KERNEL_PHILOSOPHY: Search paths are APP LAYER POLICY.
Per MODULAR_DESIGN_PHILOSOPHY: Bundles are just content - mechanism is generic.

Packaged bundles (foundation, design-intelligence, recipes, etc.) are discovered
via a general mechanism that finds bundles co-located with Python packages.
This is app-layer policy - the foundation library knows nothing about specific bundles.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_collections import CollectionResolver

logger = logging.getLogger(__name__)

# App-layer policy: mapping bundle names → Python packages that contain them
# Convention: bundle root is the parent directory of the Python package
PACKAGED_BUNDLES: dict[str, str] = {
    "foundation": "amplifier_foundation",
    # Future bundles follow the same pattern:
    # "design-intelligence": "amplifier_collection_design_intelligence",
    # "recipes": "amplifier_collection_recipes",
}


class AppBundleDiscovery:
    """CLI-specific bundle discovery with filesystem search paths.

    Implements BundleDiscoveryProtocol with search order (highest precedence first):
    1. Project bundles (.amplifier/bundles/)
    2. User bundles (~/.amplifier/bundles/)
    3. Collection bundles (via CollectionResolver)
    4. Bundled bundles (package data/bundles/)

    Bundle resolution:
    - "name" → looks for name/, name.yaml, name.md in search paths
    - "parent/child" → looks for parent/child/, etc.
    """

    def __init__(
        self,
        search_paths: list[Path] | None = None,
        collection_resolver: CollectionResolver | None = None,
    ) -> None:
        """Initialize discovery with search paths.

        Args:
            search_paths: Explicit search paths (default: CLI standard paths).
            collection_resolver: Resolver for collection bundles.
        """
        self._search_paths = search_paths or self._default_search_paths()
        self._collection_resolver = collection_resolver
        self._registry: dict[str, str] = {}  # Manual name → URI registrations

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
        1. Manual registry (from register())
        2. Packaged bundles (foundation, design-intelligence, etc.)
        3. Filesystem search paths
        4. Collections (if resolver provided)

        Args:
            name: Bundle name (e.g., "foundation", "design-intelligence").

        Returns:
            file:// URI for the bundle, or None if not found.
        """
        # Check manual registry first
        if name in self._registry:
            return self._registry[name]

        # Check packaged bundles (app-layer policy defines which bundles come from packages)
        if name in PACKAGED_BUNDLES:
            uri = self._find_packaged_bundle(PACKAGED_BUNDLES[name])
            if uri:
                logger.debug(f"Found packaged bundle '{name}' at {uri}")
                return uri

        # Search filesystem paths
        for base_path in self._search_paths:
            uri = self._find_in_path(base_path, name)
            if uri:
                logger.debug(f"Found bundle '{name}' at {uri}")
                return uri

        # Try collections if resolver available
        if self._collection_resolver:
            uri = self._find_in_collections(name)
            if uri:
                logger.debug(f"Found bundle '{name}' in collection at {uri}")
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
            file:// URI if found, None otherwise.
        """
        # Handle nested names (e.g., "foundation/providers/anthropic")
        name_path = Path(name)
        target_dir = base_path / name_path

        # Check directory bundle formats
        if target_dir.is_dir():
            bundle_md = target_dir / "bundle.md"
            if bundle_md.exists():
                return f"file://{bundle_md.resolve()}"

            bundle_yaml = target_dir / "bundle.yaml"
            if bundle_yaml.exists():
                return f"file://{bundle_yaml.resolve()}"

        # Check single file formats
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
        self._registry[name] = uri
        logger.debug(f"Registered bundle '{name}' → {uri}")

    def list_bundles(self) -> list[str]:
        """List all discoverable bundle names.

        Returns:
            List of bundle names found in all search paths.
        """
        bundles: set[str] = set()

        # Foundation is always available
        bundles.add("foundation")

        # Add registered bundles
        bundles.update(self._registry.keys())

        # Scan filesystem paths
        for base_path in self._search_paths:
            bundles.update(self._scan_path_for_bundles(base_path))

        # Scan collections
        if self._collection_resolver:
            bundles.update(self._scan_collections_for_bundles())

        return sorted(bundles)

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
