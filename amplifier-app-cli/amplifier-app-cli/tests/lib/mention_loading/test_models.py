"""Tests for mention loading models."""

from pathlib import Path

from amplifier_foundation.mentions import ContextFile


def test_context_file_creation():
    """Test ContextFile can be created with required fields."""
    ctx_file = ContextFile(
        content="test content",
        paths=[Path("/path/to/file.md")],
        content_hash="abc123",
    )

    assert ctx_file.content == "test content"
    assert len(ctx_file.paths) == 1
    assert ctx_file.paths[0] == Path("/path/to/file.md")
    assert ctx_file.content_hash == "abc123"


def test_context_file_multiple_paths():
    """Test ContextFile can track multiple paths for same content."""
    paths = [Path("/path1/file.md"), Path("/path2/file.md"), Path("/path3/file.md")]

    ctx_file = ContextFile(content="shared content", paths=paths, content_hash="def456")

    assert len(ctx_file.paths) == 3
    assert all(p in ctx_file.paths for p in paths)
