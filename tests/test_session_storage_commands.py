"""Read-only real Click storage owner integration on synthetic roots."""

import json
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock

import click
import pytest
from click.testing import CliRunner

from amplifier_app_cli.commands.session import register_session_commands
from amplifier_app_cli.commands import session_storage
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX format owner")


def cli_for(tmp_path, monkeypatch):
    native = SessionStore(tmp_path / "sessions")
    cli = click.Group()
    register_session_commands(cli, interactive_chat=AsyncMock(), execute_single=AsyncMock(), get_module_search_paths=list)
    import amplifier_app_cli.commands.session as commands
    monkeypatch.setattr(commands, "SessionStore", lambda: native)
    return cli, native


def test_storage_preview_exact_primary_no_recovery_or_content_write(tmp_path, monkeypatch):
    cli, native = cli_for(tmp_path, monkeypatch)
    native.save("root", [{"role": "user", "content": "private synthetic body"}], {})
    folder = native.base_dir / "root"
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    events = []

    class Held:
        def check(self): events.append("check")
    class Root:
        held = Held()
        def release(self): events.append("release")
    monkeypatch.setattr(session_storage.SharedRootSession, "acquire", lambda identity: Root())
    result = CliRunner().invoke(cli, ["session", "storage", "preview", "root"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["state"] == "preview-only-not-applied"
    assert report["plan"]["records"] == 1 and report["plan"]["inline_images"] == 0
    assert "private synthetic body" not in result.output
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert events == ["check", "check", "release"]


def test_preview_refuses_pending_without_read_repair(tmp_path, monkeypatch):
    cli, native = cli_for(tmp_path, monkeypatch)
    native.save("root", [{"role": "user", "content": "safe"}], {})
    marker = native.base_dir / "root/transcript.jsonl.append-pending"
    marker.write_text("unsettled")
    result = CliRunner().invoke(cli, ["session", "storage", "preview", "root"])
    assert result.exit_code == 1 and "Pending" in result.output
    assert marker.read_text() == "unsettled"


def test_storage_surface_does_not_advertise_unqualified_apply(tmp_path, monkeypatch):
    cli, _ = cli_for(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli, ["session", "storage", "--help"])
    assert result.exit_code == 0
    assert "preview" in result.output and "verify-export" in result.output
    assert "\n  apply " not in result.output and "\n  resume " not in result.output
    failed = CliRunner().invoke(cli, ["session", "storage", "reconcile", str(tmp_path / "unknown")])
    assert failed.exit_code == 0
    assert json.loads(failed.output)["state"] == "interrupted-preserve-and-inspect-journal"
    assert not (tmp_path / "unknown").exists()
