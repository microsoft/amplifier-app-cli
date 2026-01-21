"""Test find_session_global for cross-project session search."""

import tempfile
from pathlib import Path

from amplifier_app_cli.session_store import SessionStore, find_session_global


class TestFindSessionGlobal:
    """Tests for find_session_global function."""

    def test_finds_session_in_other_project(self, tmp_path, monkeypatch):
        """Should find a session that exists in a different project."""
        # Create a mock .amplifier/projects structure
        projects_dir = tmp_path / ".amplifier" / "projects"

        # Project A (simulating "current" project - empty)
        project_a = projects_dir / "-project-a" / "sessions"
        project_a.mkdir(parents=True)

        # Project B (has the session we're looking for)
        project_b = projects_dir / "-project-b" / "sessions"
        session_id = "abc12345-1234-5678-9abc-def012345678"
        session_dir = project_b / session_id
        session_dir.mkdir(parents=True)

        # Add minimal metadata to make it a valid session
        (session_dir / "metadata.json").write_text('{"name": "Test Session"}')

        # Patch Path.home() to return our temp directory
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Search for the session
        result = find_session_global("abc12345")

        assert result is not None
        found_id, found_dir = result
        assert found_id == session_id
        assert found_dir == project_b

    def test_returns_none_when_session_not_found(self, tmp_path, monkeypatch):
        """Should return None when session doesn't exist in any project."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project_a = projects_dir / "-project-a" / "sessions"
        project_a.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_session_global("nonexistent")

        assert result is None

    def test_returns_none_when_no_projects_exist(self, tmp_path, monkeypatch):
        """Should return None when .amplifier/projects doesn't exist."""
        # Don't create any projects directory
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_session_global("anything")

        assert result is None

    def test_finds_exact_match(self, tmp_path, monkeypatch):
        """Should find session with exact ID match."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"
        session_id = "exact-match-id"
        session_dir = project / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text("{}")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_session_global("exact-match-id")

        assert result is not None
        assert result[0] == session_id

    def test_finds_prefix_match(self, tmp_path, monkeypatch):
        """Should find session with prefix match."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"
        session_id = "prefix-12345678-full-id"
        session_dir = project / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text("{}")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_session_global("prefix-1234")

        assert result is not None
        assert result[0] == session_id

    def test_excludes_spawned_sessions_by_default(self, tmp_path, monkeypatch):
        """Should exclude spawned sub-sessions (containing underscore) by default."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"

        # Create a spawned sub-session (has underscore indicating parent_child)
        spawned_id = "parent-id-1234_spawned-agent"
        spawned_dir = project / spawned_id
        spawned_dir.mkdir(parents=True)
        (spawned_dir / "metadata.json").write_text("{}")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Should NOT find spawned session with top_level_only=True (default)
        result = find_session_global("parent-id")

        assert result is None

    def test_includes_spawned_sessions_when_requested(self, tmp_path, monkeypatch):
        """Should include spawned sub-sessions when top_level_only=False."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"

        spawned_id = "parent-id-1234_spawned-agent"
        spawned_dir = project / spawned_id
        spawned_dir.mkdir(parents=True)
        (spawned_dir / "metadata.json").write_text("{}")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Should find spawned session with top_level_only=False
        result = find_session_global("parent-id", top_level_only=False)

        assert result is not None
        assert result[0] == spawned_id

    def test_returns_most_recent_on_multiple_matches(self, tmp_path, monkeypatch):
        """When multiple sessions match, should return most recently modified."""
        import time

        projects_dir = tmp_path / ".amplifier" / "projects"

        # Create two sessions with same prefix in different projects
        project_a = projects_dir / "-project-a" / "sessions"
        session_a = project_a / "abc-older-session"
        session_a.mkdir(parents=True)
        (session_a / "metadata.json").write_text('{"name": "Older"}')

        # Small delay to ensure different mtimes
        time.sleep(0.01)

        project_b = projects_dir / "-project-b" / "sessions"
        session_b = project_b / "abc-newer-session"
        session_b.mkdir(parents=True)
        (session_b / "metadata.json").write_text('{"name": "Newer"}')

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_session_global("abc")

        assert result is not None
        # Should return the newer session
        assert result[0] == "abc-newer-session"

    def test_skips_hidden_directories(self, tmp_path, monkeypatch):
        """Should skip directories starting with dot."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"

        # Create a hidden directory that looks like a session
        hidden_dir = project / ".hidden-session"
        hidden_dir.mkdir(parents=True)
        (hidden_dir / "metadata.json").write_text("{}")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_session_global(".hidden")

        assert result is None

    def test_handles_empty_partial_id(self, tmp_path, monkeypatch):
        """Should return None for empty or whitespace-only partial ID."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"
        project.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Empty string
        assert find_session_global("") is None
        # Whitespace only
        assert find_session_global("   ") is None

    def test_strips_whitespace_from_partial_id(self, tmp_path, monkeypatch):
        """Should strip leading/trailing whitespace from partial ID."""
        projects_dir = tmp_path / ".amplifier" / "projects"
        project = projects_dir / "-myproject" / "sessions"
        session_id = "test-session-123"
        session_dir = project / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "metadata.json").write_text("{}")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Search with whitespace - should still find it
        result = find_session_global("  test-session  ")

        assert result is not None
        assert result[0] == session_id
