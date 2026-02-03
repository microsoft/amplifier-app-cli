"""Directory exclusion filter for hybrid storage sync control.

Provides glob-pattern based filtering to determine which directories
should have sessions stored locally-only (not synced to cloud).

Example patterns:
- "~/work/private/**" - All subdirectories of private folder
- "*/sensitive-*" - Any directory containing "sensitive-"
- "**/confidential/**" - Any path containing "confidential"
- "**/test-projects/**" - Test project directories
"""

from __future__ import annotations

import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    # Type alias matching amplifier-session-storage's SyncFilter
    SyncFilter = Callable[[str, dict[str, Any]], bool]

logger = logging.getLogger(__name__)


class DirectoryExclusionFilter:
    """Filter that excludes sessions based on working directory patterns.

    Used to determine whether sessions started in certain directories
    should remain local-only (not synced to cloud storage).

    Contract:
    - Inputs: exclusion_patterns (list[str]), current working directory
    - Outputs: SyncFilter function for HybridBlockStorage
    - Side effects: None (pure function after initialization)

    Example usage:
        filter = DirectoryExclusionFilter(["~/work/private/**", "**/test-*"])
        if filter.should_exclude(Path.cwd()):
            # Use local-only storage
            pass
        else:
            # Use hybrid storage with sync filter
            sync_filter = filter.create_sync_filter(Path.cwd())
    """

    def __init__(self, exclusion_patterns: list[str]) -> None:
        """Initialize with exclusion patterns.

        Args:
            exclusion_patterns: List of glob patterns. Supports ~ expansion.
                               Patterns are matched against absolute paths.
        """
        self.patterns = self._normalize_patterns(exclusion_patterns)
        if self.patterns:
            logger.debug(
                f"DirectoryExclusionFilter initialized with {len(self.patterns)} patterns"
            )

    def _normalize_patterns(self, patterns: list[str]) -> list[str]:
        """Expand ~ and normalize patterns.

        Args:
            patterns: Raw patterns from configuration

        Returns:
            Normalized patterns with ~ expanded to home directory
        """
        normalized = []
        for pattern in patterns:
            # Expand ~ to home directory
            if pattern.startswith("~"):
                pattern = str(Path.home()) + pattern[1:]
            # Normalize path separators for cross-platform
            pattern = pattern.replace("\\", "/")
            normalized.append(pattern)
        return normalized

    def should_exclude(self, directory: Path) -> bool:
        """Check if directory matches any exclusion pattern.

        Args:
            directory: Directory path to check

        Returns:
            True if directory matches any exclusion pattern and should
            be excluded from cloud sync (local-only storage).
        """
        if not self.patterns:
            return False

        # Resolve to absolute path and normalize separators
        dir_str = str(directory.resolve()).replace("\\", "/")

        for pattern in self.patterns:
            # Direct match
            if fnmatch(dir_str, pattern):
                logger.debug(
                    f"Directory {dir_str} matches exclusion pattern: {pattern}"
                )
                return True

            # Also check if any parent matches (for ** patterns)
            for parent in directory.resolve().parents:
                parent_str = str(parent).replace("\\", "/")
                if fnmatch(parent_str, pattern):
                    logger.debug(
                        f"Parent {parent_str} matches exclusion pattern: {pattern}"
                    )
                    return True

        return False

    def create_sync_filter(self, working_dir: Path) -> "SyncFilter":
        """Create a SyncFilter function for HybridBlockStorage.

        The returned function determines whether a session should be
        synced to cloud storage based on its metadata.

        Args:
            working_dir: The working directory when the session was started

        Returns:
            A function that takes (session_id, metadata) and returns True
            if the session SHOULD be synced (False = local-only).
        """
        # Pre-compute exclusion for the working directory
        is_excluded = self.should_exclude(working_dir)

        def sync_filter(session_id: str, metadata: dict[str, Any]) -> bool:
            """Determine if session should be synced to cloud.

            Args:
                session_id: Session identifier
                metadata: Session metadata dict, may contain 'working_directory'

            Returns:
                True if session should be synced, False for local-only.
            """
            # Check metadata for working_directory if available
            # (sessions may record their original working directory)
            session_dir = metadata.get("working_directory")
            if session_dir:
                try:
                    return not self.should_exclude(Path(session_dir))
                except Exception:
                    pass  # Fall back to initialization-time decision

            # Fall back to initialization-time decision
            return not is_excluded

        return sync_filter

    def __repr__(self) -> str:
        return f"DirectoryExclusionFilter(patterns={self.patterns})"
