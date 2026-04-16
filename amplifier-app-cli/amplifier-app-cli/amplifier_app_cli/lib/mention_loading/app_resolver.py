"""App-layer mention resolver that extends foundation's BaseMentionResolver.

This module demonstrates the proper pattern for extending foundation mechanisms:
- Foundation provides the mechanism (bundle namespace resolution)
- App provides policy (shortcuts, resolution order)

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

    relative_to: Path | None

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
    3. Relative paths: @path (CWD)

    Shortcut prefixes (app-layer policy):
    - @user:path → ~/.amplifier/{path}
    - @project:path → .amplifier/{path}
    - @~/path → ~/{path}

    Bundle namespaces (@namespace:path like @recipes:examples/...) are delegated
    to the foundation resolver, which understands bundle composition and can
    resolve paths across all composed bundles.
    """

    def __init__(
        self,
        foundation_resolver: BaseMentionResolver
        | MentionResolverProtocol
        | None = None,
        bundle_mappings: dict[str, Path] | None = None,
    ):
        """Initialize app mention resolver.

        Args:
            foundation_resolver: Foundation's BaseMentionResolver for bundle namespaces.
                In bundle mode, this should be the resolver registered by
                PreparedBundle.create_session() which has all composed bundle
                namespaces (e.g., foundation, recipes).
            bundle_mappings: Optional dict mapping bundle namespace to base_path.
                Enables @namespace:path mentions to resolve from the bundle's
                base_path. Supports multiple namespaces from composed bundles.
        """
        self.foundation_resolver = foundation_resolver
        self._bundle_mappings = bundle_mappings or {}
        self.relative_to: Path | None = None  # For context-relative path resolution

    def resolve(self, mention: str) -> Path | None:
        """Resolve @mention to file path.

        Resolution order (app-layer policy):
        1. App shortcuts: @user:, @project:, @~/
        2. Bundle namespaces: @namespace:path (via foundation)
        3. Bundle resources: @bundle:path
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
        # This ensures bundle namespaces work correctly
        if self.foundation_resolver and ":" in mention[1:]:
            result = self.foundation_resolver.resolve(mention)
            if result:
                logger.debug(f"Resolved via foundation: {mention} -> {result}")
                return result

        # === BUNDLE MAPPINGS ===
        # If no foundation resolver, try bundle_mappings dict directly
        # This supports @namespace:path where bundles are composed
        if self._bundle_mappings and ":" in mention[1:]:
            result = self._resolve_bundle_mapping(mention)
            if result:
                logger.debug(f"Resolved via bundle mapping: {mention} -> {result}")
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

    def _resolve_bundle_mapping(self, mention: str) -> Path | None:
        """Resolve @namespace:path via bundle_mappings dict.

        Used when foundation_resolver is not available.
        """
        # Extract prefix and path
        prefix, path = mention[1:].split(":", 1)

        # Skip app shortcuts (already handled)
        if prefix in ("user", "project"):
            return None

        if prefix not in self._bundle_mappings:
            return None

        bundle_base_path = self._bundle_mappings[prefix]

        # Strip leading "/" to prevent path from becoming absolute
        # (Python's Path("/base") / "/" = Path("/") which is wrong)
        path = path.lstrip("/")
        if not path:
            # Empty path means bundle root
            if bundle_base_path.exists():
                return bundle_base_path.resolve()
            return None

        resource_path = bundle_base_path / path
        if resource_path.exists():
            return resource_path.resolve()

        logger.debug(f"Bundle resource not found: {resource_path}")
        return None

    def _resolve_relative(self, mention: str) -> Path | None:
        """Resolve @path → base_path/path.

        Uses self.relative_to if set, otherwise falls back to CWD.
        """
        path = mention[1:]  # Remove "@"
        if not path:
            return None

        # Use relative_to if set, otherwise CWD
        base_path = self.relative_to if self.relative_to else Path.cwd()

        # Handle explicit relative paths
        if path.startswith("./") or path.startswith("../"):
            resolved_path = (base_path / path).resolve()
        else:
            resolved_path = base_path / path

        if resolved_path.exists():
            logger.debug(f"Relative path resolved: {mention} -> {resolved_path}")
            return resolved_path.resolve()

        logger.debug(f"Relative path not found: {resolved_path}")
        return None
