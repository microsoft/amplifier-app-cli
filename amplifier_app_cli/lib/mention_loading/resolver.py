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
    4. @bundle:path - DEPRECATED: Resolves to amplifier_app_cli/data/context/{path}
    5. @~/path - Resolves to user home directory
    6. @path - Resolves relative to CWD or relative_to parameter

    Missing files are skipped gracefully (returns None).

    Per KERNEL_PHILOSOPHY: Resolution order and search paths are APP LAYER POLICY.
    """

    def __init__(
        self,
        bundled_data_dir: Path | None = None,
        project_context_dir: Path | None = None,
        user_context_dir: Path | None = None,
        relative_to: Path | None = None,
    ):
        """Initialize resolver with search paths and collection resolver.

        Args:
            bundled_data_dir: Path to bundled data directory (default: package data/)
            project_context_dir: Path to project context (default: .amplifier/context/)
            user_context_dir: Path to user context (default: ~/.amplifier/context/)
            relative_to: Base path for resolving relative mentions (./file)
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

        # NEW: Collection resolver (APP LAYER POLICY)
        from ...collections import CollectionResolver
        self.collection_resolver = CollectionResolver()

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
        2. @user:path - Shortcut to ~/.amplifier/{path}
        3. @project:path - Shortcut to .amplifier/{path}
        4. @bundle:path - DEPRECATED: Resolves to amplifier_app_cli/data/context/{path}
        5. @~/path - User home directory
        6. @path - Relative to CWD or relative_to

        Args:
            mention: @mention string with prefix

        Returns:
            Absolute Path if file exists, None if not found (graceful skip)
        """
        # BACKWARD COMPAT: @bundle: (DEPRECATED but check first to avoid collision)
        if mention.startswith("@bundle:"):
            path_str = mention[8:]  # Remove '@bundle:'

            # Security: Prevent path traversal
            if ".." in path_str:
                logger.warning(f"Path traversal attempt blocked: {mention}")
                return None

            bundle_path = self.bundled_data_dir / "context" / path_str

            if bundle_path.exists() and bundle_path.is_file():
                return bundle_path.resolve()

            # Graceful skip - bundled file missing (shouldn't happen but handle it)
            logger.debug(f"Bundled context file not found: {bundle_path}")
            return None

        # NEW PATTERN: Collection references (@collection:path)
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
                if user_path.exists() and user_path.is_file():
                    return user_path.resolve()
                logger.debug(f"User shortcut file not found: {user_path}")
                return None

            if prefix == "project":
                project_path = Path.cwd() / ".amplifier" / path
                if project_path.exists() and project_path.is_file():
                    return project_path.resolve()
                logger.debug(f"Project shortcut file not found: {project_path}")
                return None

            # Otherwise: Collection reference
            collection_path = self.collection_resolver.resolve(prefix)
            if collection_path:
                resource_path = collection_path / path
                if resource_path.exists() and resource_path.is_file():
                    return resource_path.resolve()
                logger.debug(f"Collection resource not found: {resource_path}")
                return None

            # Collection not found
            logger.debug(f"Collection '{prefix}' not found")
            return None

        # EXISTING PATTERN: @~/ - user home directory ONLY
        if mention.startswith("@~/"):
            path_str = mention[3:]  # Remove '@~/'
            home_path = Path.home() / path_str

            if home_path.exists() and home_path.is_file():
                return home_path.resolve()

            # Graceful skip - user file doesn't exist (expected - optional files)
            logger.debug(f"User home file not found: {home_path}")
            return None

        # Type 3: Regular @ - CWD or relative_to (EXISTING LOGIC - keep it)
        path_str = mention.lstrip("@")

        # Handle old ./ and ../ syntax (keep backward compat)
        if path_str.startswith("./") or path_str.startswith("../"):
            return self._resolve_relative(path_str)

        # If relative_to set (agent/profile loading), try that first
        if self.relative_to:
            candidate = self.relative_to / path_str
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()

        # Try CWD (for user prompts)
        candidate = Path.cwd() / path_str
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

        # Not found - graceful skip
        logger.debug(f"Project file not found: {path_str} (tried relative_to and CWD)")
        return None

    def _resolve_relative(self, path: str) -> Path | None:
        """Resolve relative path mention."""
        if self.relative_to is None:
            return None

        resolved = (self.relative_to / path).resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
        return None
