"""The host adapter and shared Foundation layer use the same native files."""

import json
from pathlib import Path

import pytest

from amplifier_foundation.session.history import SessionHistoryError, SessionHistoryStore

from amplifier_app_cli.session_store import SessionStore


def test_foundation_and_cli_round_trip_provider_and_naming_metadata(tmp_path: Path):
    cli = SessionStore(tmp_path)
    history = SessionHistoryStore(tmp_path / "root")
    messages = [
        {"role": "user", "content": "hello", "metadata": {"future:field": [1, 2]}},
        {
            "role": "assistant", "content": "response",
            "metadata": {"openai:response_id": "resp_test", "openai:reasoning_items": [
                {"type": "reasoning", "encrypted_content": "opaque-test-continuation"}
            ]},
            "future_provider": {"signature": "opaque-test-signature"},
        },
    ]
    metadata = {"session_id": "root", "name": "Renamed session", "bundle": "bundle:anchors",
                "future_host": {"flag": True}, "parent_id": None}
    history.save(messages, metadata)
    assert cli.load("root") == (messages, metadata)
    messages.append({"role": "user", "content": "again"})
    cli.save("root", messages, metadata)
    assert history.load_messages() == messages
    assert history.load_metadata() == metadata
    cli.update_metadata("root", {"name": "New name"})
    assert history.load_metadata() == {**metadata, "name": "New name"}
    assert history.load_messages() == messages
    assert {p.name for p in (tmp_path / "root").iterdir()} == {
        "transcript.jsonl", "metadata.json", "transcript.jsonl.backup", "metadata.json.backup"
    }


def test_resume_does_not_read_or_modify_events_and_never_invents_messages(tmp_path: Path):
    cli = SessionStore(tmp_path)
    cli.save("root", [{"role": "user", "content": "saved"}], {"session_id": "root"})
    event_dir = tmp_path / "root" / "context-intelligence"
    event_dir.mkdir()
    events = event_dir / "events.jsonl"
    events.write_text('{"event":"prompt:submit","data":{"prompt":"not saved"}}\n{bad')
    before = events.read_bytes()
    assert cli.load("root")[0] == [{"role": "user", "content": "saved"}]
    assert events.read_bytes() == before


def test_backup_only_transcript_is_discoverable_and_corrupt_files_fail_closed(tmp_path: Path):
    cli = SessionStore(tmp_path)
    original = [{"role": "user", "content": "recoverable"}]
    cli.save("root", original, {"name": "older"})
    cli.save("root", original + [{"role": "assistant", "content": "newer"}], {"name": "newer"})
    session_dir = tmp_path / "root"
    (session_dir / "transcript.jsonl").unlink()
    (session_dir / "metadata.json").write_text("{broken")
    assert "root" in cli.list_sessions()
    assert cli.load("root") == (original, {"name": "older"})
    (session_dir / "transcript.jsonl.backup").write_text("{also broken")
    with pytest.raises(SessionHistoryError):
        cli.load("root")


def test_invalid_metadata_does_not_overwrite_the_saved_transcript(tmp_path: Path):
    cli = SessionStore(tmp_path)
    cli.save("root", [{"role": "user", "content": "original"}], {"name": "valid"})
    before = (tmp_path / "root" / "transcript.jsonl").read_bytes()
    with pytest.raises((ValueError, TypeError)):
        cli.save("root", [{"role": "user", "content": "replacement"}], {"invalid": float("nan")})
    assert (tmp_path / "root" / "transcript.jsonl").read_bytes() == before
    assert json.loads((tmp_path / "root" / "metadata.json").read_text()) == {"name": "valid"}
