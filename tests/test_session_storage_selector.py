"""Exercise the real normal storage selector, not a patched path factory."""

from pathlib import Path

import pytest

from amplifier_app_cli.project_utils import get_project_slug
from amplifier_app_cli.session_storage_owner import default_owner_factory, default_session_directory
from amplifier_foundation.paths.resolution import get_amplifier_home


@pytest.mark.parametrize("session_id", ["ordinary", "child_123.agent", "a" * 180])
def test_default_selector_uses_public_home_without_creating_paths(tmp_path, monkeypatch, session_id):
    workspace = tmp_path / "project"
    workspace.mkdir()
    home = tmp_path / "private-home"
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    expected = get_amplifier_home() / "projects" / get_project_slug() / "sessions" / session_id

    assert default_session_directory(session_id) == expected
    assert default_owner_factory(session_id, purpose="preview") is None
    assert not home.exists()


def test_default_selector_matches_native_store_location(tmp_path, monkeypatch):
    from amplifier_app_cli.session_store import SessionStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native-home"))
    selected = default_session_directory("prospective")
    assert isinstance(selected, Path)
    native = SessionStore()
    assert selected == native.base_dir / "prospective"
    assert not selected.exists()
