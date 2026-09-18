"""Resume discovery distinguishes saved conversations from retained diagnostics."""

import json

import click
from click.testing import CliRunner

from amplifier_app_cli.commands import session
from amplifier_app_cli.session_store import SessionStore


def test_log_only_directories_are_not_resume_candidates(tmp_path):
    store = SessionStore(tmp_path)
    orphan = tmp_path / "log-only"
    orphan.mkdir()
    log = orphan / "events.jsonl"
    log.write_text('{"event":"session:end"}\n')
    store.save("saved-empty", [], {})
    store.save("saved-full", [{"role": "user", "content": "hello"}], {})
    store.save("saved-full_child", [], {})
    assert set(store.list_sessions()) == {"saved-empty", "saved-full"}
    assert set(store.list_sessions(top_level_only=False)) == {
        "saved-empty",
        "saved-full",
        "saved-full_child",
    }
    assert log.read_text() == '{"event":"session:end"}\n'
    assert list(orphan.iterdir()) == [log]


def test_resume_menu_labels_messages_honestly_and_preserves_transcript(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path)
    messages = [
        {"role": "user", "content": "Inspect"},
        {"role": "assistant", "content": "Working", "tool_calls": [{"id": "call"}]},
        {"role": "tool", "tool_call_id": "call", "content": "result"},
        {
            "role": "user",
            "content": "<system-reminder>injected</system-reminder>",
            "metadata": {"ephemeral": True, "persisted": True},
        },
        {"role": "assistant", "content": "Done"},
    ]
    store.save("first", messages, {"turn_count": 2})
    store.save("second", [], {})
    path = tmp_path / "first" / "transcript.jsonl"
    path.write_text(path.read_text() + "\n")
    before = path.read_bytes()
    monkeypatch.setattr(session, "SessionStore", lambda: store)
    monkeypatch.setattr(session.Prompt, "ask", lambda *a, **kw: "q")

    @click.command()
    @click.pass_context
    def menu(ctx):
        session._interactive_resume_impl(ctx, 10, click.Command("resume"))

    result = CliRunner().invoke(menu)
    assert result.exit_code == 0, result.output
    assert "5 messages" in result.output
    assert "0 messages" in result.output
    assert "turns" not in result.output
    assert path.read_bytes() == before
    assert (
        json.loads((tmp_path / "first" / "metadata.json").read_text())["turn_count"]
        == 2
    )


def test_no_resumable_sessions_does_not_select_an_orphan(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    (tmp_path / "log-only").mkdir()
    monkeypatch.setattr(session, "SessionStore", lambda: store)

    @click.command()
    @click.pass_context
    def menu(ctx):
        session._interactive_resume_impl(ctx, 10, click.Command("resume"))

    result = CliRunner().invoke(menu)
    assert result.exit_code == 0, result.output
    assert "No sessions found to resume" in result.output
