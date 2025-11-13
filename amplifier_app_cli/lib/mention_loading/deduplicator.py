"""Content deduplication for loaded files."""

import hashlib
from pathlib import Path

from .models import ContextFile


class ContentDeduplicator:
    """Deduplicates file content by hash, tracking all source paths."""

    def __init__(self) -> None:
        """Initialize deduplicator with empty state."""
        self._content_by_hash: dict[str, str] = {}
        self._paths_by_hash: dict[str, list[Path]] = {}

    def add_file(self, path: Path, content: str) -> None:
        """Add a file to the deduplicator.

        Args:
            path: Source path of the file
            content: File content
        """
        content_hash = self._hash_content(content)

        if content_hash not in self._content_by_hash:
            self._content_by_hash[content_hash] = content
            self._paths_by_hash[content_hash] = []

        if path not in self._paths_by_hash[content_hash]:
            self._paths_by_hash[content_hash].append(path)

    def get_unique_files(self) -> list[ContextFile]:
        """Get deduplicated files with all source paths.

        Returns:
            List of ContextFile objects, one per unique content
        """
        return [
            ContextFile(
                content=content,
                paths=self._paths_by_hash[content_hash],
                hash=content_hash,
            )
            for content_hash, content in self._content_by_hash.items()
        ]

    def get_known_hashes(self) -> set[str]:
        """Return hashes currently tracked by the deduplicator."""
        return set(self._content_by_hash.keys())

    @staticmethod
    def _hash_content(content: str) -> str:
        """Compute SHA-256 hash of content.

        Args:
            content: Text content to hash

        Returns:
            Hex digest of SHA-256 hash
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
