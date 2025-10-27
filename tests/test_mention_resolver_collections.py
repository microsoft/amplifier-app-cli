"""
Tests for MentionResolver with collections support.

Tests new @collection:path, @user:path, @project:path patterns.

Per AGENTS.md: Use real bundled collections (ruthless simplicity).
"""

from pathlib import Path

import pytest
from amplifier_app_cli.lib.mention_loading.resolver import MentionResolver


@pytest.fixture
def temp_user_project_dirs(tmp_path: Path, monkeypatch):
    """
    Create temp user and project directories with shortcuts.

    Uses REAL bundled collections (foundation, developer-expertise).
    Per AGENTS.md: Ruthless simplicity - test real behavior, not mocks.
    """
    # User shortcut files
    user_amplifier = tmp_path / "user" / ".amplifier" / "custom"
    user_amplifier.mkdir(parents=True)
    (user_amplifier / "file.md").write_text("User custom file")

    # Project shortcut files
    project_amplifier = tmp_path / "project" / ".amplifier" / "notes"
    project_amplifier.mkdir(parents=True)
    (project_amplifier / "note.md").write_text("Project note")

    # Mock Path.home() and Path.cwd() to use temp dirs
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path / "project")

    return {
        "user": tmp_path / "user",
        "project": tmp_path / "project",
    }


def test_resolve_collection_reference():
    """
    Test @collection:path syntax with REAL bundled collections.

    Tests against actual foundation collection in amplifier_app_cli/data/collections/.
    Per AGENTS.md: Test real behavior, not mocks.
    """
    resolver = MentionResolver()

    # Resolve @foundation:context/IMPLEMENTATION_PHILOSOPHY.md (symlink to real file)
    path = resolver.resolve("@foundation:context/IMPLEMENTATION_PHILOSOPHY.md")
    assert path is not None
    assert path.name == "IMPLEMENTATION_PHILOSOPHY.md"
    # Note: Symlink resolves to actual file (data/context/), not collection path
    assert path.exists()
    assert path.is_file()


def test_resolve_user_shortcut(temp_user_project_dirs):
    """Test @user:path shortcut."""
    resolver = MentionResolver()

    # Resolve @user:custom/file.md
    path = resolver.resolve("@user:custom/file.md")
    assert path is not None
    assert path.name == "file.md"
    assert ".amplifier/custom" in str(path)


def test_resolve_project_shortcut(temp_user_project_dirs):
    """Test @project:path shortcut."""
    resolver = MentionResolver()

    # Resolve @project:notes/note.md
    path = resolver.resolve("@project:notes/note.md")
    assert path is not None
    assert path.name == "note.md"
    assert ".amplifier/notes" in str(path)


def test_resolve_collection_not_found():
    """Test @collection:path when collection doesn't exist."""
    resolver = MentionResolver()

    path = resolver.resolve("@nonexistent:some/file.md")
    assert path is None


def test_resolve_collection_resource_not_found():
    """Test @collection:path when resource doesn't exist in collection."""
    resolver = MentionResolver()

    # Foundation exists but this resource doesn't
    path = resolver.resolve("@foundation:nonexistent/file.md")
    assert path is None


def test_resolve_path_traversal_blocked():
    """Test that path traversal is blocked in collection references."""
    resolver = MentionResolver()

    # Should block path traversal
    path = resolver.resolve("@foundation:../../../etc/passwd")
    assert path is None


def test_resolve_bundle_still_works():
    """
    Test that @bundle: (deprecated) still works for backward compat.

    Uses REAL bundled context files in amplifier_app_cli/data/context/.
    """
    resolver = MentionResolver()

    # collections-overview.md exists in data/context/ (created in Phase 1)
    path = resolver.resolve("@bundle:collections-overview.md")
    assert path is not None
    assert path.name == "collections-overview.md"
    assert "data/context" in str(path)
