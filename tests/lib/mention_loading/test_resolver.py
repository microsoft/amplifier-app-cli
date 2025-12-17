"""Tests for AppMentionResolver with explicit prefix syntax."""

import tempfile
from pathlib import Path

import pytest
from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver


@pytest.fixture
def temp_context_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        bundled_dir = tmpdir_path / "bundled"
        bundled_context = bundled_dir / "context"
        project_dir = tmpdir_path / "project"
        user_dir = tmpdir_path / "user"

        bundled_dir.mkdir()
        bundled_context.mkdir()
        project_dir.mkdir()
        user_dir.mkdir()

        # Bundled context files
        (bundled_context / "base.md").write_text("bundled context content")
        (bundled_context / "shared.md").write_text("bundled shared")

        # CWD files (via @file.md)
        (tmpdir_path / "cwd_file.md").write_text("cwd content")

        yield {
            "root": tmpdir_path,
            "bundled": bundled_dir,
            "project": project_dir,
            "user": user_dir,
        }


def test_resolver_cwd_file(temp_context_dirs, monkeypatch):
    """Test @file.md resolves from CWD."""
    monkeypatch.chdir(temp_context_dirs["root"])

    resolver = AppMentionResolver()

    path = resolver.resolve("@cwd_file.md")
    assert path is not None
    assert path.read_text() == "cwd content"


def test_resolver_relative_to(temp_context_dirs):
    """Test relative_to takes priority over CWD."""
    base_dir = temp_context_dirs["bundled"]
    (base_dir / "relative.md").write_text("relative content")

    resolver = AppMentionResolver()
    resolver.relative_to = base_dir

    path = resolver.resolve("@relative.md")
    assert path is not None
    assert path.read_text() == "relative content"


def test_resolver_relative_path_syntax(temp_context_dirs, monkeypatch):
    """Test ./relative.md syntax with relative_to."""
    base_dir = temp_context_dirs["bundled"]
    (base_dir / "relative.md").write_text("relative content")

    # Need to be in a directory for ./ syntax to work
    monkeypatch.chdir(base_dir)

    resolver = AppMentionResolver()
    resolver.relative_to = base_dir

    path = resolver.resolve("@./relative.md")
    assert path is not None
    assert path.read_text() == "relative content"


def test_resolver_missing_file(temp_context_dirs):
    """Test returns None for missing files."""
    resolver = AppMentionResolver()
    resolver.relative_to = temp_context_dirs["root"]

    path = resolver.resolve("@missing.md")
    assert path is None


def test_resolver_home_directory(temp_context_dirs):
    """Test @~/ resolves to home directory."""
    home = Path.home()
    test_file = home / "test_resolver_home.md"
    test_file.write_text("home content")

    try:
        resolver = AppMentionResolver()

        path = resolver.resolve("@~/test_resolver_home.md")
        assert path is not None
        assert path.read_text() == "home content"
    finally:
        test_file.unlink()


def test_resolver_path_traversal_blocked(temp_context_dirs):
    """Test path traversal attempts are blocked."""
    resolver = AppMentionResolver(enable_collections=True)

    # Should return None for path traversal attempts in collection references
    path = resolver.resolve("@foundation:../../etc/passwd")
    assert path is None
