"""Writer notices never own or change native history."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from amplifier_app_cli.catalog_hints import HINT_DIRECTORY_ENV, notify_session_saved
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Private hint inbox v1 requires POSIX ownership/mode checks")


def test_opt_in_coalescing_and_saved_metadata(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.delenv(HINT_DIRECTORY_ENV, raising=False)
    assert notify_session_saved(session) is False
    inbox = tmp_path / "hints"
    monkeypatch.setenv(HINT_DIRECTORY_ENV, str(inbox))
    for _ in range(25):
        assert notify_session_saved(session)
    notices = list(inbox.glob("*.json"))
    assert len(notices) == 1
    assert notices[0].name == hashlib.sha256(str(session.resolve()).encode()).hexdigest() + ".json"
    assert json.loads(notices[0].read_text()) == {"version": 1, "sessionDirectory": str(session.resolve())}
    assert len(list(inbox.iterdir())) == 1
    assert notices[0].stat().st_mode & 0o077 == 0


def test_save_rename_and_new_save_emit_only_after_native_success(tmp_path, monkeypatch):
    inbox = tmp_path / "hints"
    monkeypatch.setenv(HINT_DIRECTORY_ENV, str(inbox))
    store = SessionStore(tmp_path / "sessions")
    messages = [{"role": "user", "content": "preserved request"}]
    store.save("one", messages, {"working_dir": str(tmp_path), "parent_id": None})
    notice = next(inbox.glob("*.json"))
    directory = Path(json.loads(notice.read_text())["sessionDirectory"])
    transcript = (directory / "transcript.jsonl").read_bytes()
    events = directory / "events.jsonl"
    events.write_bytes(b'{"preserve":"all historical events"}\n')
    notice.unlink()
    store.rename("one", "New title")
    assert notice.exists()
    assert store.get_metadata("one")["name"] == "New title"
    assert (directory / "transcript.jsonl").read_bytes() == transcript
    assert events.read_bytes() == b'{"preserve":"all historical events"}\n'
    store.save_new("two", messages, {"working_dir": str(tmp_path), "parent_id": "one"})
    assert len(list(inbox.glob("*.json"))) == 2
    with pytest.raises(FileExistsError):
        store.save_new("two", messages, {})
    assert len(list(inbox.glob("*.json"))) == 2
    monkeypatch.setattr("amplifier_app_cli.session_store.SessionHistoryStore.save", lambda *a, **k: (_ for _ in ()).throw(OSError("native save failed")))
    with pytest.raises(OSError, match="native save failed"):
        store.save("failed", messages, {})
    assert len(list(inbox.glob("*.json"))) == 2


def test_unavailable_inbox_cannot_fail_native_save(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("untouched")
    monkeypatch.setenv(HINT_DIRECTORY_ENV, str(blocked))
    store = SessionStore(tmp_path / "sessions")
    store.save("safe", [{"role": "user", "content": "retained"}], {"working_dir": str(tmp_path)})
    assert store.load("safe")[0][0]["content"] == "retained"
    assert blocked.read_text() == "untouched"
    monkeypatch.setenv(HINT_DIRECTORY_ENV, "relative-inbox")
    assert notify_session_saved(tmp_path / "sessions/safe") is False
    target = tmp_path / "actual"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv(HINT_DIRECTORY_ENV, str(link))
    assert notify_session_saved(tmp_path / "sessions/safe") is False
    assert list(target.iterdir()) == []
