"""A native session can move between updated hosts without losing its name."""

import pytest

from amplifier_app_cli.session_store import SessionStore
from amplifier_foundation.session.metadata import SessionMetadataStore


def test_external_name_survives_cli_checkpoint_and_cli_rename_is_shared(tmp_path):
    store = SessionStore(tmp_path)
    messages = [{"role": "user", "content": "Existing conversation"}]
    store.save("web-session", messages, {"bundle": "anchors", "other_host": True})
    metadata = SessionMetadataStore(tmp_path / "web-session")
    metadata.set_name("Created in the web")
    stale = store.get_metadata("web-session")
    metadata.set_name("Changed in another client")
    store.save("web-session", messages, {**stale, "turn_count": 1})
    assert store.get_metadata("web-session")["name"] == "Changed in another client"
    result = store.rename("web-session", "CLI name " + "x" * 100)
    assert metadata.read()["name"] == result["name"]
    assert metadata.read()["name_source"] == "manual"
    assert metadata.read()["other_host"] is True
    assert store.load("web-session")[0] == messages


def test_missing_session_rename_does_not_create_it(tmp_path):
    store = SessionStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.rename("missing", "A name")
    assert not (tmp_path / "missing").exists()
