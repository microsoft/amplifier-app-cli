"""Tests for mention loader with explicit resolution."""

import tempfile
from pathlib import Path

import pytest
from amplifier_app_cli.lib.mention_loading.loader import MentionLoader
from amplifier_app_cli.lib.mention_loading.resolver import MentionResolver


@pytest.fixture
def temp_test_files():
    """Create temporary test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        (tmpdir_path / "simple.md").write_text("Simple file content")

        (tmpdir_path / "with_mentions.md").write_text("File with mentions:\n@simple.md\n@another.md")

        (tmpdir_path / "another.md").write_text("Another file content")

        (tmpdir_path / "recursive_a.md").write_text("File A mentions @recursive_b.md")
        (tmpdir_path / "recursive_b.md").write_text("File B mentions @recursive_a.md")

        (tmpdir_path / "duplicate1.md").write_text("Shared content")
        (tmpdir_path / "duplicate2.md").write_text("Shared content")

        yield tmpdir_path


def test_loader_single_file(temp_test_files):
    """Test loading a single file without mentions."""
    # Use relative_to so @simple.md resolves correctly
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
        relative_to=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("@simple.md", relative_to=temp_test_files)

    assert len(messages) == 1
    assert messages[0].role == "developer"
    assert "Simple file content" in messages[0].content
    assert "simple.md" in messages[0].content


def test_loader_recursive_loading(temp_test_files):
    """Test recursive loading of mentions."""
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
        relative_to=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("@with_mentions.md", relative_to=temp_test_files)

    assert len(messages) == 3

    # Check content (content is always str for loaded files, but type is str | list[ContentBlock])
    contents = [m.content for m in messages if isinstance(m.content, str)]
    assert len(contents) == 3
    assert any("File with mentions" in c for c in contents)
    assert any("Simple file content" in c for c in contents)
    assert any("Another file content" in c for c in contents)


def test_loader_cycle_detection(temp_test_files):
    """Test cycle detection prevents infinite loops."""
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
        relative_to=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("@recursive_a.md", relative_to=temp_test_files)

    assert len(messages) == 2

    # Check content (content is always str for loaded files, but type is str | list[ContentBlock])
    contents = [m.content for m in messages if isinstance(m.content, str)]
    assert len(contents) == 2
    assert any("File A mentions" in c for c in contents)
    assert any("File B mentions" in c for c in contents)


def test_loader_missing_file_silent_skip(temp_test_files):
    """Test missing files are silently skipped."""
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
        relative_to=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("@missing.md @simple.md", relative_to=temp_test_files)

    assert len(messages) == 1
    assert "Simple file content" in messages[0].content


def test_loader_deduplication(temp_test_files):
    """Test content deduplication with multiple paths."""
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
        relative_to=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("@duplicate1.md @duplicate2.md", relative_to=temp_test_files)

    assert len(messages) == 1
    assert "Shared content" in messages[0].content
    assert "duplicate1.md" in messages[0].content
    assert "duplicate2.md" in messages[0].content


def test_loader_no_mentions(temp_test_files):
    """Test text without mentions returns empty list."""
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("No mentions here")

    assert len(messages) == 0


def test_loader_message_format(temp_test_files):
    """Test message format includes @mention path and header.

    Note: Provider adds <context_file> wrapper around developer messages.
    Loader only adds [Context from @mention → path] header.
    """
    resolver = MentionResolver(
        bundled_data_dir=temp_test_files,
        project_context_dir=temp_test_files,
        user_context_dir=temp_test_files,
        relative_to=temp_test_files,
    )
    loader = MentionLoader(resolver)

    messages = loader.load_mentions("@simple.md", relative_to=temp_test_files)

    assert len(messages) == 1
    # Content is always str for loaded files, but need type guard for pyright
    content = messages[0].content
    assert isinstance(content, str)
    # Should start with [Context from @mention → /abs/path]
    assert content.startswith("[Context from ")
    assert "@simple.md →" in content
    assert "simple.md]" in content
    assert "\n\n" in content
    assert "Simple file content" in content
