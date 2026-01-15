"""App-layer bundle discovery implementing filesystem search paths.

This module implements CLI-specific bundle discovery with search paths.

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

from amplifier_foundation import BundleRegistry

# LEGACY: Legacy collection resolver removed

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
#   - show_in_list: Whether to show in default `bundle list` output (default: True)
#
# Local package is checked first for performance (editable installs).
# Remote URL is used as fallback, ensuring bundles ALWAYS resolve.
WELL_KNOWN_BUNDLES: dict[str, dict[str, str | bool]] = {
    "foundation": {
        "package": "amplifier_foundation",
        "remote": "git+https://github.com/microsoft/amplifier-foundation@main",
        "show_in_list": True,
    },
    "recipes": {
        "package": "",  # No Python package - bundle-only
        "remote": "git+https://github.com/microsoft/amplifier-bundle-recipes@main",
        "show_in_list": False,  # Included by foundation, not standalone
    },
    "design-intelligence": {
        "package": "",  # No Python package - bundle-only
        "remote": "git+https://github.com/microsoft/amplifier-bundle-design-intelligence@main",
        "show_in_list": False,  # Included by foundation, not standalone
    },
    # Experimental delegation-only bundle (subdirectory of foundation)
    "exp-delegation": {
        "package": "",  # Experimental bundle in foundation/experiments/
        "remote": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/delegation-only",
        "show_in_list": True,
    },
    # Amplifier ecosystem development bundle - multi-repo workflows, shadow environments
    "amplifier-dev": {
        "package": "",  # Bundle in foundation/bundles/
        "remote": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/amplifier-dev.yaml",
        "show_in_list": True,
    },
    # Notification hooks - loaded dynamically via config.notifications settings
    # but registered here so `amplifier update` can track it
    "notify": {
        "package": "",  # No Python package - bundle with embedded modules
        "remote": "git+https://github.com/microsoft/amplifier-bundle-notify@main",
        "show_in_list": False,  # Loaded via settings, not standalone
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
    5. Bundled bundles (package data/bundles/)

    Bundle resolution:
    - "name" → looks for name/, name.yaml, name.md in search paths
    - "parent/child" → looks for parent/child/, etc.
    """

    def __init__(
        self,
        search_paths: list[Path] | None = None,
        registry: BundleRegistry | None = None,
    ) -> None:
        """Initialize discovery with search paths.

        Args:
            search_paths: Explicit search paths (default: CLI standard paths).
            registry: Optional BundleRegistry (creates default if not provided).
        """
        self._search_paths = search_paths or self._default_search_paths()
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

    def list_bundles(self, show_all: bool = False) -> list[str]:
        """List discoverable bundle names for user selection.

        By default, shows only bundles that are:
        - Well-known bundles with show_in_list=True
        - Explicitly requested by user (via bundle use/add)
        - Found in filesystem search paths

        With show_all=True, shows ALL discovered bundles including:
        - Dependencies loaded transitively
        - Well-known bundles with show_in_list=False
        - Sub-bundles (behaviors, providers, etc.)

        Args:
            show_all: If True, show all bundles including dependencies and sub-bundles.

        Returns:
            List of bundle names found in all search paths.
        """
        if show_all:
            return self._list_all_bundles()
        return self._list_user_bundles()

    def _list_user_bundles(self) -> list[str]:
        """List bundles intended for user selection (filtered view)."""
        bundles: set[str] = set()

        # Add well-known bundles that should be shown
        for name, info in WELL_KNOWN_BUNDLES.items():
            if info.get("show_in_list", True):
                bundles.add(name)

        # Add explicitly requested bundles from persisted registry
        explicitly_requested = self._get_explicitly_requested_bundles()
        bundles.update(explicitly_requested)

        # Scan filesystem paths (user's local bundles)
        for base_path in self._search_paths:
            bundles.update(self._scan_path_for_bundles(base_path))

        # Add user-added bundles from user registry
        from amplifier_app_cli.lib.bundle_loader import user_registry

        user_bundles = user_registry.load_user_registry()
        bundles.update(user_bundles.keys())

        return sorted(bundles)

    def _list_all_bundles(self) -> list[str]:
        """List ALL bundles including dependencies and sub-bundles."""
        bundles: set[str] = set()

        # Add ALL registered bundles (includes well-known bundles registered on init)
        bundles.update(self._registry.list_registered())

        # Scan filesystem paths
        for base_path in self._search_paths:
            bundles.update(self._scan_path_for_bundles(base_path))

        # Read ALL from persisted registry (includes dependencies and sub-bundles)
        bundles.update(self._read_all_from_registry())

        return sorted(bundles)

    def _get_explicitly_requested_bundles(self) -> set[str]:
        """Get bundles that were explicitly requested by the user."""
        import json

        registry_path = Path.home() / ".amplifier" / "registry.json"
        if not registry_path.exists():
            return set()

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)

            requested: set[str] = set()
            for name, bundle_data in data.get("bundles", {}).items():
                if bundle_data.get("explicitly_requested", False):
                    requested.add(name)

            return requested
        except Exception as e:
            logger.debug(f"Could not read persisted registry: {e}")
            return set()

    def _read_all_from_registry(self) -> list[str]:
        """Read ALL bundle names from persisted registry (no filtering)."""
        import json

        registry_path = Path.home() / ".amplifier" / "registry.json"
        if not registry_path.exists():
            return []

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)
            return list(data.get("bundles", {}).keys())
        except Exception as e:
            logger.debug(f"Could not read persisted registry: {e}")
            return []

    def list_cached_root_bundles(self) -> list[str]:
        """List all cached ROOT bundles for update checking.

        Returns bundles that are:
        - is_root=True (not sub-bundles like behaviors)
        - NOT subdirectory bundles (#subdirectory= in URI) since those share
          a repo with their parent and updating the parent updates them too

        This is used by `amplifier update` to show locally cached bundles
        that can be independently updated.

        Returns:
            List of root bundle names that are cached locally.
        """
        import json

        registry_path = Path.home() / ".amplifier" / "registry.json"
        if not registry_path.exists():
            return []

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)

            root_bundles: list[str] = []
            for name, bundle_data in data.get("bundles", {}).items():
                # Skip sub-bundles (behaviors, providers, etc.)
                if not bundle_data.get("is_root", True):
                    continue

                # Skip subdirectory bundles - they share a repo with their parent
                # and updating the parent updates them too
                uri = bundle_data.get("uri", "")
                if "#subdirectory=" in uri:
                    continue

                root_bundles.append(name)

            return sorted(root_bundles)
        except Exception as e:
            logger.debug(f"Could not read persisted registry: {e}")
            return []

    def get_bundle_categories(self) -> dict[str, list[dict[str, str]]]:
        """Get all bundles categorized by type for detailed display.

        Returns:
            Dict with categories: well_known, user_added, dependencies, sub_bundles
            Each category contains list of {name, uri, ...} dicts.
        """
        import json

        categories: dict[str, list[dict[str, str]]] = {
            "well_known": [],
            "user_added": [],
            "dependencies": [],
            "sub_bundles": [],
        }

        # Well-known bundles
        for name, info in WELL_KNOWN_BUNDLES.items():
            uri = self._find_packaged_bundle(info.get("package", "")) or info["remote"]
            categories["well_known"].append(
                {
                    "name": name,
                    "uri": uri,
                    "show_in_list": str(info.get("show_in_list", True)),
                }
            )

        # User-added bundles
        from amplifier_app_cli.lib.bundle_loader import user_registry

        user_bundles = user_registry.load_user_registry()
        for name, info in user_bundles.items():
            categories["user_added"].append(
                {
                    "name": name,
                    "uri": info.get("uri", ""),
                }
            )

        # Read persisted registry for dependencies and sub-bundles
        registry_path = Path.home() / ".amplifier" / "registry.json"
        if registry_path.exists():
            try:
                with open(registry_path, encoding="utf-8") as f:
                    data = json.load(f)

                well_known_names = set(WELL_KNOWN_BUNDLES.keys())
                user_added_names = set(user_bundles.keys())

                for name, bundle_data in data.get("bundles", {}).items():
                    # Skip if already categorized
                    if name in well_known_names or name in user_added_names:
                        continue

                    entry = {
                        "name": name,
                        "uri": bundle_data.get("uri", ""),
                    }

                    if not bundle_data.get("is_root", True):
                        # Sub-bundle (behavior, provider, etc.)
                        entry["root"] = bundle_data.get("root_name", "")
                        categories["sub_bundles"].append(entry)
                    elif not bundle_data.get("explicitly_requested", False):
                        # Dependency (loaded transitively)
                        included_by = bundle_data.get("included_by", [])
                        entry["included_by"] = (
                            ", ".join(included_by) if included_by else ""
                        )
                        categories["dependencies"].append(entry)

            except Exception as e:
                logger.debug(f"Could not read persisted registry: {e}")

        return categories

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
                if bundle_data.get("is_root", True):
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

