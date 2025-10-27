"""
Tests for collection lock file management.

Per IMPLEMENTATION_PHILOSOPHY: Test behavior, not implementation.
"""

from pathlib import Path

from amplifier_app_cli.collections.installer import is_collection_installed
from amplifier_app_cli.collections.lock import CollectionLock
from amplifier_app_cli.collections.lock import CollectionLockEntry


def test_lock_file_creation(tmp_path):
    """Test creating a new lock file."""
    lock_path = tmp_path / "collections.lock"

    lock = CollectionLock(lock_path=lock_path)

    # Should create empty lock
    assert len(lock.list_installed()) == 0
    assert not lock.is_installed("anything")


def test_add_collection_to_lock(tmp_path):
    """Test adding collection to lock file."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    # Add collection
    lock.add(
        name="foundation",
        source="git+https://example.com/foundation@main",
        commit="abc123",
        path=Path("/path/to/foundation"),
    )

    # Verify added
    assert lock.is_installed("foundation")

    entry = lock.get("foundation")
    assert entry is not None
    assert entry.name == "foundation"
    assert entry.source == "git+https://example.com/foundation@main"
    assert entry.commit == "abc123"
    assert entry.path == "/path/to/foundation"
    assert entry.installed_at  # Should have timestamp


def test_add_multiple_collections(tmp_path):
    """Test adding multiple collections."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    # Add multiple
    lock.add("foundation", "git+https://example.com/foundation@main", "abc123", Path("/path/1"))
    lock.add("dev-tools", "git+https://example.com/dev-tools@main", "def456", Path("/path/2"))

    # Verify both exist
    installed = lock.list_installed()
    assert len(installed) == 2

    names = {e.name for e in installed}
    assert names == {"foundation", "dev-tools"}


def test_update_existing_collection(tmp_path):
    """Test updating an existing collection entry."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    # Add initial
    lock.add("foundation", "git+https://example.com/foundation@main", "abc123", Path("/path/1"))

    # Get initial timestamp
    entry1 = lock.get("foundation")
    assert entry1 is not None
    timestamp1 = entry1.installed_at

    # Update (same name, different commit)
    lock.add("foundation", "git+https://example.com/foundation@main", "def456", Path("/path/1"))

    # Should update, not duplicate
    installed = lock.list_installed()
    assert len(installed) == 1

    entry2 = lock.get("foundation")
    assert entry2 is not None
    assert entry2.commit == "def456"  # Updated
    assert entry2.installed_at != timestamp1  # New timestamp


def test_remove_collection(tmp_path):
    """Test removing collection from lock file."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    # Add collections
    lock.add("foundation", "git+https://example.com/foundation@main", "abc123", Path("/path/1"))
    lock.add("dev-tools", "git+https://example.com/dev-tools@main", "def456", Path("/path/2"))

    # Remove one
    lock.remove("foundation")

    # Verify removed
    assert not lock.is_installed("foundation")
    assert lock.is_installed("dev-tools")

    installed = lock.list_installed()
    assert len(installed) == 1
    assert installed[0].name == "dev-tools"


def test_remove_nonexistent_collection(tmp_path):
    """Test removing collection that doesn't exist is safe."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    # Remove non-existent (should not error)
    lock.remove("nonexistent")

    # Lock should still be empty
    assert len(lock.list_installed()) == 0


def test_lock_file_persistence(tmp_path):
    """Test lock file persists across instances."""
    lock_path = tmp_path / "collections.lock"

    # Add with first instance
    lock1 = CollectionLock(lock_path=lock_path)
    lock1.add("foundation", "git+https://example.com/foundation@main", "abc123", Path("/path/1"))

    # Create new instance (should load from file)
    lock2 = CollectionLock(lock_path=lock_path)

    # Should have the collection
    assert lock2.is_installed("foundation")

    entry = lock2.get("foundation")
    assert entry is not None
    assert entry.name == "foundation"
    assert entry.commit == "abc123"


def test_lock_file_format(tmp_path):
    """Test lock file has correct JSON format."""
    import json

    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    lock.add("foundation", "git+https://example.com/foundation@main", "abc123", Path("/path/1"))

    # Read file directly
    with open(lock_path) as f:
        data = json.load(f)

    # Verify structure
    assert "version" in data
    assert data["version"] == "1.0"

    assert "collections" in data
    assert "foundation" in data["collections"]

    foundation = data["collections"]["foundation"]
    assert foundation["name"] == "foundation"
    assert foundation["source"] == "git+https://example.com/foundation@main"
    assert foundation["commit"] == "abc123"
    assert foundation["path"] == "/path/1"
    assert "installed_at" in foundation


def test_lock_file_version_mismatch(tmp_path, caplog):
    """Test handling version mismatch in lock file."""
    import json

    lock_path = tmp_path / "collections.lock"

    # Create lock file with wrong version
    with open(lock_path, "w") as f:
        json.dump(
            {
                "version": "99.0",  # Future version
                "collections": {
                    "test": {
                        "name": "test",
                        "source": "git+https://example.com/test@main",
                        "commit": "abc123",
                        "path": "/path/1",
                        "installed_at": "2025-10-26T12:00:00Z",
                    }
                },
            },
            f,
        )

    # Load (should warn but not fail)
    lock = CollectionLock(lock_path=lock_path)

    # Should still load the collections
    assert lock.is_installed("test")


def test_lock_entry_serialization():
    """Test CollectionLockEntry serialization."""
    entry = CollectionLockEntry(
        name="test",
        source="git+https://example.com/test@main",
        commit="abc123",
        path="/path/to/test",
        installed_at="2025-10-26T12:00:00Z",
    )

    # to_dict
    data = entry.to_dict()
    assert data["name"] == "test"
    assert data["source"] == "git+https://example.com/test@main"
    assert data["commit"] == "abc123"

    # from_dict
    entry2 = CollectionLockEntry.from_dict(data)
    assert entry2.name == entry.name
    assert entry2.source == entry.source
    assert entry2.commit == entry.commit


def test_list_installed_empty(tmp_path):
    """Test listing installed collections when none exist."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    installed = lock.list_installed()
    assert installed == []


def test_list_installed_multiple(tmp_path):
    """Test listing multiple installed collections."""
    lock_path = tmp_path / "collections.lock"
    lock = CollectionLock(lock_path=lock_path)

    # Add multiple
    lock.add("foundation", "git+https://example.com/foundation@main", "abc123", Path("/path/1"))
    lock.add("dev-tools", "git+https://example.com/dev-tools@main", "def456", Path("/path/2"))
    lock.add("security", "git+https://example.com/security@main", "ghi789", Path("/path/3"))

    installed = lock.list_installed()
    assert len(installed) == 3

    names = {e.name for e in installed}
    assert names == {"foundation", "dev-tools", "security"}


def test_is_collection_installed_function(tmp_path):
    """Test is_collection_installed() function."""
    target_dir = tmp_path / "collections"

    # Not installed
    assert not is_collection_installed("test", target_dir=target_dir)

    # Create collection
    collection_path = target_dir / "test"
    collection_path.mkdir(parents=True)
    (collection_path / "pyproject.toml").write_text('[project]\nname = "test"\nversion = "1.0.0"\n')

    # Now installed
    assert is_collection_installed("test", target_dir=target_dir)
