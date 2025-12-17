"""App-layer mention resolver that extends foundation's BaseMentionResolver.

This module demonstrates the proper pattern for extending foundation mechanisms:
- Foundation provides the mechanism (bundle namespace resolution)
- App provides policy (shortcuts, collection support, resolution order)

Per KERNEL_PHILOSOPHY: Foundation provides mechanism, app provides policy.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from amplifier_foundation.mentions.resolver import BaseMentionResolver

logger = logging.getLogger(__name__)


class MentionResolverProtocol(Protocol):
    """Protocol for mention resolvers."""

    def resolve(self, mention: str) -> Path | None:
        """Resolve @mention to file path."""
        ...


class AppMentionResolver:
    """App-layer extension of foundation's mention resolver.

    Adds app-specific shortcuts while delegating bundle namespaces to foundation.
    Per KERNEL_PHILOSOPHY: Foundation provides mechanism, app provides policy.

    Resolution order (app-layer policy):
    1. App shortcuts: @user:, @project:, @~/
    2. Bundle namespaces: @namespace:path (delegated to foundation)
    3. Collections: @collection:path (DEPRECATED, profile mode only)
    4. Relative paths: @path (CWD)

    Shortcut prefixes (app-layer policy):
    - @user:path → ~/.amplifier/{path}
    - @project:path → .amplifier/{path}
    - @~/path → ~/{path}

    Bundle namespaces (@namespace:path like @recipes:examples/...) are delegated
    to the foundation resolver, which understands bundle composition and can
    resolve paths across all composed bundles.

    Collection prefix is deprecated - only enabled in legacy profile mode.
    In bundle mode, bundle namespaces take precedence, preventing conflicts
    if a collection and bundle share the same name.
    """

    def __init__(
        self,
        foundation_resolver: BaseMentionResolver | MentionResolverProtocol | None = None,
        enable_collections: bool = False,
    ):
        """Initialize app mention resolver.

        Args:
            foundation_resolver: Foundation's BaseMentionResolver for bundle namespaces.
                In bundle mode, this should be the resolver registered by
                PreparedBundle.create_session() which has all composed bundle
                namespaces (e.g., foundation, recipes).
            enable_collections: Enable @collection:path resolution.
                DEPRECATED: Only set True for legacy profile mode.
                Bundle mode should use False to prevent naming conflicts.
        """
        self.foundation_resolver = foundation_resolver
        self._enable_collections = enable_collections
        self._collection_resolver = None

        if enable_collections:
            try:
                from ...paths import create_collection_resolver

                self._collection_resolver = create_collection_resolver()
            except ImportError:
                logger.debug("Collection resolver not available")

    def resolve(self, mention: str) -> Path | None:
        """Resolve @mention to file path.

        Resolution order (app-layer policy):
        1. App shortcuts: @user:, @project:, @~/
        2. Bundle namespaces: @namespace:path (via foundation)
        3. Collections: @collection:path (DEPRECATED, profile mode only)
        4. Relative paths: @path (CWD)

        Args:
            mention: @mention string with prefix

        Returns:
            Absolute Path if file exists, None if not found (graceful skip)
        """
        if not mention.startswith("@"):
            return None

        # Security: Prevent path traversal
        if ".." in mention:
            logger.warning(f"Path traversal attempt blocked: {mention}")
            return None

        # === APP SHORTCUTS (always available) ===
        if mention.startswith("@user:"):
            return self._resolve_user(mention)
        if mention.startswith("@project:"):
            return self._resolve_project(mention)
        if mention.startswith("@~/"):
            return self._resolve_home(mention)

        # === BUNDLE NAMESPACES (foundation mechanism) ===
        # Try foundation resolver first - handles @namespace:path for all composed bundles
        # This ensures bundle namespaces take precedence over collections (no conflicts)
        if self.foundation_resolver and ":" in mention[1:]:
            result = self.foundation_resolver.resolve(mention)
            if result:
                logger.debug(f"Resolved via foundation: {mention} -> {result}")
                return result

        # === COLLECTIONS (deprecated, profile mode only) ===
        # Only try collections if bundle resolution failed AND collections enabled
        if self._enable_collections and ":" in mention[1:]:
            result = self._resolve_collection(mention)
            if result:
                logger.debug(f"Resolved via collection (deprecated): {mention} -> {result}")
                return result

        # === RELATIVE PATHS ===
        return self._resolve_relative(mention)

    def _resolve_user(self, mention: str) -> Path | None:
        """Resolve @user:path → ~/.amplifier/{path}."""
        path = mention[6:]  # Remove "@user:"
        if not path:
            return None

        user_path = Path.home() / ".amplifier" / path
        if user_path.exists():
            logger.debug(f"User shortcut resolved: {mention} -> {user_path}")
            return user_path.resolve()

        logger.debug(f"User shortcut not found: {user_path}")
        return None

    def _resolve_project(self, mention: str) -> Path | None:
        """Resolve @project:path → .amplifier/{path}."""
        path = mention[9:]  # Remove "@project:"
        if not path:
            return None

        project_path = Path.cwd() / ".amplifier" / path
        if project_path.exists():
            logger.debug(f"Project shortcut resolved: {mention} -> {project_path}")
            return project_path.resolve()

        logger.debug(f"Project shortcut not found: {project_path}")
        return None

    def _resolve_home(self, mention: str) -> Path | None:
        """Resolve @~/path → ~/{path}."""
        path = mention[3:]  # Remove "@~/"
        if not path:
            return None

        home_path = Path.home() / path
        if home_path.exists():
            logger.debug(f"Home shortcut resolved: {mention} -> {home_path}")
            return home_path.resolve()

        logger.debug(f"Home shortcut not found: {home_path}")
        return None

    def _resolve_collection(self, mention: str) -> Path | None:
        """Resolve @collection:path (DEPRECATED).

        Only used in legacy profile mode. Bundle mode should not call this.
        """
        if not self._collection_resolver:
            return None

        # Extract prefix and path
        prefix, path = mention[1:].split(":", 1)

        # Skip app shortcuts (already handled)
        if prefix in ("user", "project"):
            return None

        collection_path = self._collection_resolver.resolve(prefix)
        if collection_path:
            resource_path = collection_path / path

            # Try at collection path first
            if resource_path.exists():
                return resource_path.resolve()

            # Hybrid packaging fallback: try parent directory
            if (collection_path / "pyproject.toml").exists():
                parent_resource_path = collection_path.parent / path
                if parent_resource_path.exists():
                    logger.debug(f"Collection resource found at parent: {parent_resource_path}")
                    return parent_resource_path.resolve()

            logger.debug(f"Collection resource not found: {resource_path}")
            return None

        logger.debug(f"Collection '{prefix}' not found")
        return None

    def _resolve_relative(self, mention: str) -> Path | None:
        """Resolve @path → CWD/path."""
        path = mention[1:]  # Remove "@"
        if not path:
            return None

        # Handle explicit relative paths
        if path.startswith("./") or path.startswith("../"):
            cwd_path = (Path.cwd() / path).resolve()
        else:
            cwd_path = Path.cwd() / path

        if cwd_path.exists():
            logger.debug(f"Relative path resolved: {mention} -> {cwd_path}")
            return cwd_path.resolve()

        logger.debug(f"Relative path not found: {cwd_path}")
        return None
