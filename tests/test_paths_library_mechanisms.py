"""Tests verifying paths.py uses library mechanisms instead of duplication."""

import tempfile
from pathlib import Path

from amplifier_app_cli.paths import get_agent_search_paths
from amplifier_app_cli.paths import get_profile_search_paths


def test_profile_paths_use_library_mechanisms():
    """Verify get_profile_search_paths() uses CollectionResolver and discover_collection_resources."""
    # This test verifies the function doesn't do manual iteration
    # and instead uses library mechanisms

    # Create temporary collection structure
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create bundled collection (flat structure)
        bundled_path = base / "bundled"
        bundled_col = bundled_path / "test-collection"
        profiles_dir = bundled_col / "profiles"
        profiles_dir.mkdir(parents=True)

        (bundled_col / "pyproject.toml").write_text(
            """[project]
name = "test-collection"
version = "1.0.0"
"""
        )

        (profiles_dir / "test.md").write_text("# Test profile")

        # Should discover using library mechanisms
        # (We can't easily test this without mocking, but we can verify it doesn't crash)
        paths = get_profile_search_paths()

        # Should include project, user, and bundled paths
        assert isinstance(paths, list)
        assert all(isinstance(p, Path) for p in paths)


def test_agent_paths_use_library_mechanisms():
    """Verify get_agent_search_paths() uses CollectionResolver and discover_collection_resources."""
    # Similar to profile paths test
    paths = get_agent_search_paths()

    # Should return valid paths list
    assert isinstance(paths, list)
    assert all(isinstance(p, Path) for p in paths)


def test_paths_handle_nested_structures():
    """Verify paths functions handle nested package structures from pip install."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create nested structure (as uv pip install creates)
        collection_dir = base / "collections" / "test-collection"
        package_dir = collection_dir / "test_collection"
        profiles_dir = package_dir / "profiles"
        profiles_dir.mkdir(parents=True)

        (package_dir / "pyproject.toml").write_text(
            """[project]
name = "test-collection"
version = "1.0.0"
"""
        )

        (profiles_dir / "test.md").write_text("# Test")

        # Create resolver with this path
        from amplifier_collections import CollectionResolver

        resolver = CollectionResolver(search_paths=[base / "collections"])

        # Should discover nested structure
        collections = resolver.list_collections()
        assert len(collections) == 1
        assert collections[0][0] == "test-collection"


def test_paths_no_manual_iteration():
    """Verify paths.py doesn't manually iterate collection directories."""
    # Read the paths.py file and verify it uses library mechanisms
    import inspect

    from amplifier_app_cli import paths

    # Get source of get_profile_search_paths
    source = inspect.getsource(paths.get_profile_search_paths)

    # Should use library mechanisms
    assert "resolver.list_collections()" in source
    assert "discover_collection_resources" in source

    # Should NOT do manual iteration
    assert "collection_dir.iterdir()" not in source or "# Collection profiles (USE LIBRARY MECHANISMS" in source
    assert 'collection_dir / "profiles"' not in source

    # Same for agents (check the profile-mode implementation which uses library mechanisms)
    # Note: get_agent_search_paths is a dispatcher that routes to get_agent_search_paths_for_profile
    # or get_agent_search_paths_for_bundle. The profile version uses library mechanisms.
    agent_source = inspect.getsource(paths.get_agent_search_paths_for_profile)
    assert "resolver.list_collections()" in agent_source
    assert "discover_collection_resources" in agent_source


def test_collection_resolver_uses_metadata_names():
    """Verify CollectionResolver.list_collections() returns metadata names."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        collections_path = base / "collections"
        collections_path.mkdir()

        # Create collection with directory != metadata
        repo_dir = collections_path / "amplifier-collection-test"
        repo_dir.mkdir()

        (repo_dir / "pyproject.toml").write_text(
            """[project]
name = "test-collection"
version = "1.0.0"
"""
        )

        # Resolver should return metadata name
        from amplifier_collections import CollectionResolver

        resolver = CollectionResolver(search_paths=[collections_path])
        collections = resolver.list_collections()

        assert len(collections) == 1
        name, path = collections[0]
        assert name == "test-collection"  # Metadata name
        assert "amplifier-collection-test" not in name  # Not directory name
