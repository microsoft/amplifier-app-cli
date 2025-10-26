"""
Tests for collections.utils module.

Test search path utilities (APP LAYER POLICY).
"""

from pathlib import Path

from amplifier_app_cli.collections.utils import get_collection_search_paths


def test_get_collection_search_paths_returns_three_paths():
    """Test that search paths returns bundled, user, and project paths."""
    paths = get_collection_search_paths()

    assert len(paths) == 3
    assert all(isinstance(p, Path) for p in paths)


def test_get_collection_search_paths_order():
    """
    Test search paths are in correct precedence order.

    Order (lowest to highest):
    1. Bundled (package data)
    2. User global (~/.amplifier/collections)
    3. Project local (.amplifier/collections)
    """
    paths = get_collection_search_paths()

    # Check that paths contain expected components
    assert "data/collections" in str(paths[0]) or "amplifier_app_cli" in str(paths[0])
    assert ".amplifier/collections" in str(paths[1])
    assert str(paths[1]).startswith(str(Path.home()))
    assert ".amplifier/collections" in str(paths[2])
    assert str(paths[2]).startswith(str(Path.cwd()))


def test_get_collection_search_paths_is_stable():
    """Test that multiple calls return same paths."""
    paths1 = get_collection_search_paths()
    paths2 = get_collection_search_paths()

    assert paths1 == paths2
