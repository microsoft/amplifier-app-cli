"""
Tests for collections.resolver module.

Test CollectionResolver resolution and listing.
"""

from pathlib import Path
from textwrap import dedent

import pytest

from amplifier_app_cli.collections.resolver import CollectionResolver


@pytest.fixture
def mock_collections(tmp_path: Path, monkeypatch):
    """
    Create mock collection directories for testing.

    Creates:
    - bundled/collections/foundation/
    - user/.amplifier/collections/foundation/  (overrides bundled)
    - user/.amplifier/collections/custom/
    - project/.amplifier/collections/custom/  (overrides user)
    """
    # Bundled collections
    bundled = tmp_path / "bundled" / "collections"
    bundled_foundation = bundled / "foundation"
    bundled_foundation.mkdir(parents=True)
    (bundled_foundation / "pyproject.toml").write_text(dedent("""
        [project]
        name = "foundation"
        version = "1.0.0"
    """))

    # User collections
    user = tmp_path / "user" / ".amplifier" / "collections"
    user_foundation = user / "foundation"
    user_foundation.mkdir(parents=True)
    (user_foundation / "pyproject.toml").write_text(dedent("""
        [project]
        name = "foundation"
        version = "2.0.0"
    """))

    user_custom = user / "custom"
    user_custom.mkdir(parents=True)
    (user_custom / "pyproject.toml").write_text(dedent("""
        [project]
        name = "custom"
        version = "1.0.0"
    """))

    # Project collections
    project = tmp_path / "project" / ".amplifier" / "collections"
    project_custom = project / "custom"
    project_custom.mkdir(parents=True)
    (project_custom / "pyproject.toml").write_text(dedent("""
        [project]
        name = "custom"
        version = "3.0.0"
    """))

    # Mock search paths
    def mock_get_search_paths():
        return [
            bundled,   # Lowest precedence
            user,      # Middle
            project,   # Highest precedence
        ]

    monkeypatch.setattr(
        "amplifier_app_cli.collections.resolver.get_collection_search_paths",
        mock_get_search_paths
    )

    return {
        "bundled": bundled,
        "user": user,
        "project": project,
    }


def test_resolve_finds_bundled(mock_collections):
    """Test resolving collection from bundled location."""
    resolver = CollectionResolver()

    # Foundation exists in both bundled and user, should find user (higher precedence)
    path = resolver.resolve("foundation")
    assert path is not None
    assert "user" in str(path)  # Should find user version, not bundled


def test_resolve_finds_user(mock_collections):
    """Test resolving collection from user location."""
    resolver = CollectionResolver()

    # Custom exists in both user and project, should find project (highest precedence)
    path = resolver.resolve("custom")
    assert path is not None
    assert "project" in str(path)  # Should find project version


def test_resolve_not_found(mock_collections):
    """Test resolving non-existent collection returns None."""
    resolver = CollectionResolver()

    path = resolver.resolve("nonexistent")
    assert path is None


def test_resolve_directory_without_pyproject(mock_collections, tmp_path):
    """Test directory without pyproject.toml is not resolved."""
    # Create directory without pyproject.toml
    invalid = mock_collections["user"] / "invalid"
    invalid.mkdir(parents=True)

    resolver = CollectionResolver()
    path = resolver.resolve("invalid")
    assert path is None


def test_list_collections(mock_collections):
    """Test listing all available collections."""
    resolver = CollectionResolver()

    collections = resolver.list_collections()
    collection_dict = dict(collections)

    # Should have 2 collections: foundation and custom
    assert len(collections) == 2

    # Foundation should be from user (overrides bundled)
    assert "foundation" in collection_dict
    assert "user" in str(collection_dict["foundation"])

    # Custom should be from project (overrides user)
    assert "custom" in collection_dict
    assert "project" in str(collection_dict["custom"])


def test_list_collections_empty():
    """Test listing when no collections available."""
    resolver = CollectionResolver()
    # With default (real) search paths that don't exist
    resolver.search_paths = [Path("/nonexistent1"), Path("/nonexistent2")]

    collections = resolver.list_collections()
    assert collections == []
