"""Tests for mention path resolver."""

import tempfile
from pathlib import Path

import pytest
from amplifier_app_cli.lib.mention_loading.resolver import MentionResolver


@pytest.fixture
def temp_context_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        bundled_dir = tmpdir_path / "bundled"
        project_dir = tmpdir_path / "project"
        user_dir = tmpdir_path / "user"

        bundled_dir.mkdir()
        project_dir.mkdir()
        user_dir.mkdir()

        (bundled_dir / "bundled.md").write_text("bundled content")
        (project_dir / "project.md").write_text("project content")
        (user_dir / "user.md").write_text("user content")
        (bundled_dir / "shared.md").write_text("bundled shared")
        (project_dir / "shared.md").write_text("project shared")

        yield {
            "bundled": bundled_dir,
            "project": project_dir,
            "user": user_dir,
        }


def test_resolver_bundled_priority(temp_context_dirs):
    """Test bundled files take priority."""
    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
    )

    path = resolver.resolve("@shared.md")
    assert path is not None
    assert path.read_text() == "bundled shared"


def test_resolver_project_fallback(temp_context_dirs):
    """Test falls back to project context."""
    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
    )

    path = resolver.resolve("@project.md")
    assert path is not None
    assert path.read_text() == "project content"


def test_resolver_user_fallback(temp_context_dirs):
    """Test falls back to user context."""
    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
    )

    path = resolver.resolve("@user.md")
    assert path is not None
    assert path.read_text() == "user content"


def test_resolver_missing_file(temp_context_dirs):
    """Test returns None for missing files."""
    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
    )

    path = resolver.resolve("@missing.md")
    assert path is None


def test_resolver_relative_path(temp_context_dirs):
    """Test resolves relative paths."""
    base_dir = temp_context_dirs["bundled"]
    (base_dir / "relative.md").write_text("relative content")

    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
        relative_to=base_dir,
    )

    path = resolver.resolve("./relative.md")
    assert path is not None
    assert path.read_text() == "relative content"


def test_resolver_bundled_prefix(temp_context_dirs):
    """Test explicit bundled/ prefix."""
    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
    )

    path = resolver.resolve("@bundled/bundled.md")
    assert path is not None
    assert path.read_text() == "bundled content"


def test_resolver_strips_at_symbol(temp_context_dirs):
    """Test @ symbol is stripped from mentions."""
    resolver = MentionResolver(
        bundled_data_dir=temp_context_dirs["bundled"],
        project_context_dir=temp_context_dirs["project"],
        user_context_dir=temp_context_dirs["user"],
    )

    path = resolver.resolve("@bundled.md")
    assert path is not None
    assert path.name == "bundled.md"
