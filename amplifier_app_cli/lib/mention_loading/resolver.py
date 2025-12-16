"""Path resolution for @mentions with search path support."""

import importlib.resources
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class MentionResolver:
    """Resolves @mentions to file paths with explicit prefix handling.

    Mention types supported (APP LAYER POLICY per KERNEL_PHILOSOPHY):
    1. @collection:path - Resolves to collection resources (e.g., @foundation:context/file.md)
    2. @user:path - Shortcut to ~/.amplifier/{path}
    3. @project:path - Shortcut to .amplifier/{path}
    4. @~/path - Resolves to user home directory
    5. @path - Resolves relative to CWD or relative_to parameter

    Missing files are skipped gracefully (returns None).

    Per KERNEL_PHILOSOPHY: Resolution order and search paths are APP LAYER POLICY.
    """

    def __init__(
        self,
        bundled_data_dir: Path | None = None,
        project_context_dir: Path | None = None,
        user_context_dir: Path | None = None,
        relative_to: Path | None = None,
        bundle_mappings: dict[str, Path] | None = None,
    ):
        """Initialize resolver with search paths and collection resolver.

        Args:
            bundled_data_dir: Path to bundled data directory (default: package data/)
            project_context_dir: Path to project context (default: .amplifier/context/)
            user_context_dir: Path to user context (default: ~/.amplifier/context/)
            relative_to: Base path for resolving relative mentions (./file)
            bundle_mappings: Optional dict mapping bundle namespace to base_path.
                Enables @namespace:path mentions to resolve from the bundle's
                base_path. Supports multiple namespaces from composed bundles
                (e.g., foundation + recipes both resolvable after composition).
        """
        if bundled_data_dir is None:
            bundled_data_dir = self._get_bundled_data_dir()

        if project_context_dir is None:
            project_context_dir = Path.cwd() / ".amplifier" / "context"

        if user_context_dir is None:
            user_context_dir = Path.home() / ".amplifier" / "context"

        self.bundled_data_dir = bundled_data_dir
        self.project_context_dir = project_context_dir
        self.user_context_dir = user_context_dir
        self.relative_to = relative_to
        self.bundle_mappings = bundle_mappings or {}

        # Collection resolver with source override support (APP LAYER POLICY)
        from ...paths import create_collection_resolver

        self.collection_resolver = create_collection_resolver()

    def _get_bundled_data_dir(self) -> Path:
        """Get path to bundled data directory."""
        try:
            if hasattr(importlib.resources, "files"):
                data_path = importlib.resources.files("amplifier_app_cli") / "data"
                # Convert to Path - works for both Path-like and Traversable
                return Path(str(data_path))
        except (ImportError, AttributeError, TypeError):
            pass

        pkg_path = Path(__file__).parent.parent.parent / "data"
        if pkg_path.exists():
            return pkg_path

        return Path.cwd() / "data"

    def resolve(self, mention: str) -> Path | None:
        """Resolve @mention to file path.

        Mention types (APP LAYER POLICY):
        1. @collection:path - Collection resources (e.g., @foundation:context/file.md)
           - Tries collection_path / path first (package subdirectory)
           - Falls back to collection_path.parent / path (hybrid packaging)
        2. @user:path - Shortcut to ~/.amplifier/{path}
        3. @project:path - Shortcut to .amplifier/{path}
        4. @~/path - User home directory
        5. @path - Relative to CWD or relative_to

        Hybrid packaging: Collections installed via pip/uv create nested structure
        where resources (docs/, agents/) are at collection root, while package
        subdirectory contains pyproject.toml for metadata. Resolver uses parent
        fallback to find resources at root when not found in package subdir.

        Args:
            mention: @mention string with prefix

        Returns:
            Absolute Path if file exists, None if not found (graceful skip)
        """
        # Collection references (@collection:path)
        # Also handles shortcuts (@user:path, @project:path)
        if ":" in mention[1:] and not mention.startswith("@~/"):
            prefix, path = mention[1:].split(":", 1)

            # Security: Prevent path traversal in path component
            if ".." in path:
                logger.warning(f"Path traversal attempt blocked: {mention}")
                return None

            # Handle shortcuts first
            if prefix == "user":
                user_path = Path.home() / ".amplifier" / path
                # Support both files AND directories
                if user_path.exists():
                    return user_path.resolve()
                logger.debug(f"User shortcut path not found: {user_path}")
                return None

            if prefix == "project":
                project_path = Path.cwd() / ".amplifier" / path
                # Support both files AND directories
                if project_path.exists():
                    return project_path.resolve()
                logger.debug(f"Project shortcut path not found: {project_path}")
                return None

            # Bundle mappings: If prefix matches a known bundle namespace, resolve from
            # that bundle's base_path. Supports composed bundles (foundation + recipes).
            # This allows @foundation:context/... and @recipes:examples/... to resolve.
            if prefix in self.bundle_mappings:
                bundle_base_path = self.bundle_mappings[prefix]
                resource_path = bundle_base_path / path
                if resource_path.exists():
                    logger.debug(f"Bundle resource found: {resource_path}")
                    return resource_path.resolve()
                logger.debug(f"Bundle resource not found: {resource_path}")
                return None

            # Otherwise: Collection reference
            collection_path = self.collection_resolver.resolve(prefix)
            if collection_path:
                resource_path = collection_path / path

                # Try at collection path first (package subdirectory)
                # Support both files AND directories
                if resource_path.exists():
                    return resource_path.resolve()

                # Hybrid packaging fallback: If collection_path has pyproject.toml,
                # try parent directory (resources may be at collection root, not package subdir).
                # Mirrors discovery.py pattern for resource discovery.
                if (collection_path / "pyproject.toml").exists():
                    parent_resource_path = collection_path.parent / path
                    # Support both files AND directories
                    if parent_resource_path.exists():
                        logger.debug(f"Collection resource found at parent: {parent_resource_path}")
                        return parent_resource_path.resolve()

                logger.debug(f"Collection resource not found: {resource_path}")
                return None

            # Collection not found
            logger.debug(f"Collection '{prefix}' not found")
            return None

        # EXISTING PATTERN: @~/ - user home directory
        # Support both files AND directories
        if mention.startswith("@~/"):
            path_str = mention[3:]  # Remove '@~/'
            home_path = Path.home() / path_str

            # Allow both files and directories
            if home_path.exists():
                return home_path.resolve()

            # Graceful skip - path doesn't exist (expected - optional files)
            logger.debug(f"User home path not found: {home_path}")
            return None

        # Type 3: Regular @ - CWD or relative_to
        path_str = mention.lstrip("@")

        # Handle relative path syntax
        if path_str.startswith("./") or path_str.startswith("../"):
            return self._resolve_relative(path_str)

        # If relative_to set (agent/profile loading), try that first
        # Support both files AND directories
        if self.relative_to:
            candidate = self.relative_to / path_str
            if candidate.exists():
                return candidate.resolve()

        # Try CWD (for user prompts)
        # Support both files AND directories
        candidate = Path.cwd() / path_str
        if candidate.exists():
            return candidate.resolve()

        # Not found - graceful skip
        logger.debug(f"Project path not found: {path_str} (tried relative_to and CWD)")
        return None

    def _resolve_relative(self, path: str) -> Path | None:
        """Resolve relative path mention."""
        if self.relative_to is None:
            return None

        resolved = (self.relative_to / path).resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
        return None
