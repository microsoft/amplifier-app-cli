"""
Tests for ProfileLoader with collections support.

Tests collection:profiles/name.md syntax.
Per AGENTS.md: Test real behavior with real bundled collections.
"""

from amplifier_app_cli.profile_system.loader import ProfileLoader


def test_find_profile_file_with_collection_syntax():
    """
    Test finding profile using collection syntax.

    Uses REAL foundation collection: foundation:profiles/base.md
    """
    loader = ProfileLoader()

    # Find profile using collection syntax
    path = loader.find_profile_file("foundation:profiles/base.md")
    assert path is not None
    assert path.name == "base.md"
    assert path.exists()


def test_find_profile_file_collection_not_found():
    """Test finding profile when collection doesn't exist."""
    loader = ProfileLoader()

    path = loader.find_profile_file("nonexistent:profiles/base.md")
    assert path is None


def test_find_profile_file_resource_not_found():
    """Test finding profile when collection exists but resource doesn't."""
    loader = ProfileLoader()

    path = loader.find_profile_file("foundation:profiles/nonexistent.md")
    assert path is None


def test_find_profile_file_natural_collection_syntax():
    """Test natural collection:name syntax (user-friendly)."""
    loader = ProfileLoader()

    # Natural syntax: foundation:base (auto-adds profiles/ and .md)
    path = loader.find_profile_file("foundation:base")
    assert path is not None
    assert path.name == "base.md"
    assert path.exists()

    # Also test with .md extension
    path2 = loader.find_profile_file("foundation:base.md")
    assert path2 is not None
    assert path2.name == "base.md"


def test_find_profile_file_simple_name_still_works():
    """Test that simple profile names still work (backward compat)."""
    loader = ProfileLoader()

    # Find by simple name (searches local paths)
    path = loader.find_profile_file("base")
    assert path is not None
    assert path.name == "base.md"
