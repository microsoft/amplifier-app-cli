"""Collection lock file management - APP LAYER POLICY.

Tracks installed collections with git commit SHAs for reproducibility.

Per KERNEL_PHILOSOPHY:
- "Could two teams want different behavior?" → YES (lock format is policy)
- This is APP LAYER - kernel doesn't know about collections

Per IMPLEMENTATION_PHILOSOPHY:
- Ruthless simplicity: Simple JSON file, no complex format
- YAGNI: Just track what's needed now (name, source, commit, path)
"""

import json
import logging
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CollectionLockEntry:
    """Entry in collections lock file (APP LAYER DATA)."""

    name: str
    source: str
    commit: str | None
    path: str
    installed_at: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CollectionLockEntry":
        """Create from dictionary."""
        return cls(**data)


class CollectionLock:
    """
    Collections lock file manager (APP LAYER POLICY).

    Manages .amplifier/collections.lock file that tracks installed collections.

    Lock format (JSON):
    {
      "version": "1.0",
      "collections": {
        "foundation": {
          "name": "foundation",
          "source": "git+https://github.com/org/foundation@main",
          "commit": "abc123...",
          "path": "~/.amplifier/collections/foundation",
          "installed_at": "2025-10-26T12:00:00Z"
        }
      }
    }
    """

    VERSION = "1.0"

    def __init__(self, lock_path: Path | None = None):
        """Initialize lock manager.

        Args:
            lock_path: Path to lock file (default: ~/.amplifier/collections.lock)
        """
        if lock_path is None:
            lock_path = Path.home() / ".amplifier" / "collections.lock"

        self.lock_path = lock_path
        self._data: dict[str, CollectionLockEntry] = {}
        self._load()

    def _load(self) -> None:
        """Load lock file if it exists."""
        if not self.lock_path.exists():
            self._data = {}
            return

        try:
            with open(self.lock_path) as f:
                data = json.load(f)

            # Validate version
            if data.get("version") != self.VERSION:
                logger.warning(f"Lock file version mismatch: expected {self.VERSION}, got {data.get('version')}")

            # Load collections
            collections = data.get("collections", {})
            self._data = {name: CollectionLockEntry.from_dict(entry) for name, entry in collections.items()}

            logger.debug(f"Loaded {len(self._data)} collections from lock file")

        except Exception as e:
            logger.error(f"Failed to load lock file: {e}")
            self._data = {}

    def _save(self) -> None:
        """Save lock file."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self.VERSION,
            "collections": {name: entry.to_dict() for name, entry in self._data.items()},
        }

        try:
            with open(self.lock_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved lock file with {len(self._data)} collections")
        except Exception as e:
            logger.error(f"Failed to save lock file: {e}")

    def add(
        self,
        name: str,
        source: str,
        commit: str | None,
        path: Path,
    ) -> None:
        """
        Add or update collection in lock file.

        Args:
            name: Collection name
            source: Git source URI
            commit: Git commit SHA (None if not git)
            path: Installation path
        """
        entry = CollectionLockEntry(
            name=name,
            source=source,
            commit=commit,
            path=str(path),
            installed_at=datetime.now(UTC).isoformat(),
        )

        self._data[name] = entry
        self._save()

        logger.debug(f"Added {name} to lock file")

    def remove(self, name: str) -> None:
        """
        Remove collection from lock file.

        Args:
            name: Collection name
        """
        if name in self._data:
            del self._data[name]
            self._save()
            logger.debug(f"Removed {name} from lock file")

    def get(self, name: str) -> CollectionLockEntry | None:
        """
        Get lock entry for collection.

        Args:
            name: Collection name

        Returns:
            Lock entry or None if not found
        """
        return self._data.get(name)

    def list_installed(self) -> list[CollectionLockEntry]:
        """
        List all installed collections.

        Returns:
            List of lock entries
        """
        return list(self._data.values())

    def is_installed(self, name: str) -> bool:
        """
        Check if collection is in lock file.

        Args:
            name: Collection name

        Returns:
            True if collection is tracked
        """
        return name in self._data
