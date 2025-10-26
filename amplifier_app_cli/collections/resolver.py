"""
Collection resolver - Resolve collection names to paths.

CRITICAL (KERNEL_PHILOSOPHY): This is APP LAYER POLICY, not kernel mechanism.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES (search order is policy)
- Different apps could resolve collections differently
- Kernel doesn't know about collections

Per AGENTS.md: Ruthless simplicity - direct filesystem checks, no caching complexity.
"""

from pathlib import Path

from .utils import get_collection_search_paths


class CollectionResolver:
    """
    Resolve collection names to installation paths (APP LAYER POLICY).

    This class implements POLICY for how collection names map to filesystem paths.
    The kernel doesn't know about this - it's an app-layer decision.

    Different applications could:
    - Use different search orders
    - Add custom search locations
    - Implement different precedence rules

    Philosophy:
    - Simple, direct filesystem checks
    - No caching (YAGNI - optimize if needed later)
    - Clear error messages
    """

    def __init__(self):
        """Initialize resolver with app-layer search paths."""
        self.search_paths = get_collection_search_paths()

    def resolve(self, collection_name: str) -> Path | None:
        """
        Resolve collection name to installation path.

        Searches in precedence order (highest first):
        1. Project collections (.amplifier/collections/)
        2. User collections (~/.amplifier/collections/)
        3. Bundled collections (package data/collections/)

        Args:
            collection_name: Name of collection (e.g., "foundation")

        Returns:
            Path to collection directory if found, None otherwise

        Example:
            >>> resolver = CollectionResolver()
            >>> path = resolver.resolve("foundation")
            >>> # Returns ~/.amplifier/collections/foundation or bundled path
        """
        # Search in reverse order (highest precedence first)
        for search_path in reversed(self.search_paths):
            candidate = search_path / collection_name

            # Check if directory exists and has pyproject.toml
            if candidate.exists() and candidate.is_dir() and (candidate / "pyproject.toml").exists():
                return candidate.resolve()

        return None

    def list_collections(self) -> list[tuple[str, Path]]:
        """
        List all available collections with their paths.

        Returns list of (name, path) tuples. Higher precedence collections
        override lower precedence (e.g., user overrides bundled).

        Returns:
            List of (collection_name, collection_path) tuples

        Example:
            >>> resolver = CollectionResolver()
            >>> collections = resolver.list_collections()
            >>> for name, path in collections:
            ...     print(f"{name}: {path}")
            foundation: ~/.amplifier/collections/foundation
            developer-expertise: <bundled>/data/collections/developer-expertise
        """
        collections = {}

        # Iterate in precedence order (lowest to highest)
        # Higher precedence overwrites lower in dictionary
        for search_path in self.search_paths:
            if not search_path.exists():
                continue

            for collection_dir in search_path.iterdir():
                if not collection_dir.is_dir():
                    continue

                # Valid collection must have pyproject.toml
                if (collection_dir / "pyproject.toml").exists():
                    # Higher precedence overwrites
                    collections[collection_dir.name] = collection_dir

        return list(collections.items())
