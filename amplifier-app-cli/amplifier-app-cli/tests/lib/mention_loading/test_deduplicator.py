"""Tests for content deduplicator."""

from pathlib import Path

from amplifier_foundation.mentions import ContentDeduplicator


def test_deduplicator_unique_content():
    """Test deduplicator handles unique content correctly."""
    dedup = ContentDeduplicator()

    dedup.add_file(Path("/path1/file1.md"), "content 1")
    dedup.add_file(Path("/path2/file2.md"), "content 2")

    files = dedup.get_unique_files()
    assert len(files) == 2

    contents = {f.content for f in files}
    assert "content 1" in contents
    assert "content 2" in contents


def test_deduplicator_duplicate_content():
    """Test deduplicator merges duplicate content from different paths."""
    dedup = ContentDeduplicator()

    content = "shared content"
    dedup.add_file(Path("/path1/file.md"), content)
    dedup.add_file(Path("/path2/file.md"), content)
    dedup.add_file(Path("/path3/file.md"), content)

    files = dedup.get_unique_files()
    assert len(files) == 1

    ctx_file = files[0]
    assert ctx_file.content == content
    assert len(ctx_file.paths) == 3
    assert Path("/path1/file.md") in ctx_file.paths
    assert Path("/path2/file.md") in ctx_file.paths
    assert Path("/path3/file.md") in ctx_file.paths


def test_deduplicator_same_path_twice():
    """Test deduplicator doesn't duplicate paths."""
    dedup = ContentDeduplicator()

    path = Path("/path/file.md")
    content = "content"

    dedup.add_file(path, content)
    dedup.add_file(path, content)

    files = dedup.get_unique_files()
    assert len(files) == 1
    assert len(files[0].paths) == 1


def test_deduplicator_hash_consistency():
    """Test deduplicator produces consistent hashes."""
    dedup = ContentDeduplicator()

    content = "test content"
    dedup.add_file(Path("/path1.md"), content)
    dedup.add_file(Path("/path2.md"), content)

    files = dedup.get_unique_files()
    assert len(files) == 1

    expected_hash = "6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72"
    assert files[0].content_hash == expected_hash
