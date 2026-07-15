"""Tests for `amplifier session repair` / `amplifier session diagnose` (issue #208).

Covers amplifier_app_cli.commands.session:
- _diagnose_session(): read-only health report
- _repair_session(): backup -> repair -> write -> self-verify (COMPLETE strategy)

These run against the conftest-shadowed local amplifier-foundation checkout,
which (post lock-bump) matches the installed wheel -- both expose the repair
API used here (session_info, diagnose_transcript, repair_transcript, etc.).

A "broken" transcript is an assistant message with a tool_calls entry and no
following tool-role message with a matching tool_call_id (orphaned tool_use ->
"missing_tool_results" failure mode).
"""

from __future__ import annotations

import json

import pytest
from amplifier_foundation.session import TRANSCRIPT_FILENAME
from amplifier_foundation.session import diagnose_transcript
from amplifier_foundation.session import load_transcript_with_lines

from amplifier_app_cli.commands.session import _diagnose_session
from amplifier_app_cli.commands.session import _repair_session
from amplifier_app_cli.session_store import SessionStore


def _seed(
    base_dir, session_id: str, transcript: list[dict], metadata: dict | None = None
):
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(metadata or {"session_id": session_id, "bundle": "foundation"}),
        encoding="utf-8",
    )
    lines = "\n".join(json.dumps(e) for e in transcript)
    (session_dir / TRANSCRIPT_FILENAME).write_text(
        lines + ("\n" if lines else ""), encoding="utf-8"
    )
    return session_dir


BROKEN_TRANSCRIPT = [
    {"role": "user", "content": "run a command"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "toolu_orphan1", "function": {"name": "bash"}}],
    },
]

HEALTHY_TRANSCRIPT = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
]


def _capture(monkeypatch):
    """Patch console.print to capture rendered output as a list of strings."""
    printed: list[str] = []
    import amplifier_app_cli.commands.session as session_module

    monkeypatch.setattr(
        session_module.console,
        "print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    return printed


def test_repair_broken_makes_healthy(tmp_path, monkeypatch):
    printed = _capture(monkeypatch)
    session_id = "abcdef1234567890"
    session_dir = _seed(tmp_path, session_id, BROKEN_TRANSCRIPT)
    store = SessionStore(base_dir=tmp_path)

    _repair_session(store, session_id)

    entries = load_transcript_with_lines(session_dir)
    verify = diagnose_transcript(entries)
    assert verify["status"] == "healthy"

    backups = list(session_dir.glob(f"{TRANSCRIPT_FILENAME}.bak-repair-*"))
    assert len(backups) == 1

    text = "\n".join(printed)
    assert "Repaired" in text
    assert "Verified" in text


def test_repair_healthy_is_noop(tmp_path, monkeypatch):
    printed = _capture(monkeypatch)
    session_id = "healthy1234567890"
    session_dir = _seed(tmp_path, session_id, HEALTHY_TRANSCRIPT)
    store = SessionStore(base_dir=tmp_path)

    before = (session_dir / TRANSCRIPT_FILENAME).read_bytes()
    _repair_session(store, session_id)
    after = (session_dir / TRANSCRIPT_FILENAME).read_bytes()

    assert before == after
    backups = list(session_dir.glob(f"{TRANSCRIPT_FILENAME}.bak-repair-*"))
    assert backups == []

    text = "\n".join(printed)
    assert "already healthy" in text


def test_repair_partial_id(tmp_path, monkeypatch):
    printed = _capture(monkeypatch)
    session_id = "abcdef1234567890"
    _seed(tmp_path, session_id, BROKEN_TRANSCRIPT)
    store = SessionStore(base_dir=tmp_path)

    _repair_session(store, "abcdef")

    text = "\n".join(printed)
    assert "Repaired" in text


def test_repair_missing_session_errors(tmp_path, monkeypatch):
    _capture(monkeypatch)
    store = SessionStore(base_dir=tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        _repair_session(store, "nope")

    assert exc_info.value.code == 1


def test_diagnose_reports_broken(tmp_path, monkeypatch):
    printed = _capture(monkeypatch)
    session_id = "brokendiag12345678"
    _seed(tmp_path, session_id, BROKEN_TRANSCRIPT)
    store = SessionStore(base_dir=tmp_path)

    _diagnose_session(store, session_id)

    text = "\n".join(printed)
    assert "broken" in text
    assert "missing_tool_results" in text
    assert "amplifier session repair" in text


def test_diagnose_reports_healthy(tmp_path, monkeypatch):
    printed = _capture(monkeypatch)
    session_id = "healthydiag1234567"
    _seed(tmp_path, session_id, HEALTHY_TRANSCRIPT)
    store = SessionStore(base_dir=tmp_path)

    _diagnose_session(store, session_id)

    text = "\n".join(printed)
    assert "healthy" in text
    assert "no repair needed" in text


def test_import_guard_message(tmp_path, monkeypatch):
    """When foundation lacks the repair API, a clean actionable error is shown."""
    session_id = "guardtest123456789"
    _seed(tmp_path, session_id, HEALTHY_TRANSCRIPT)
    store = SessionStore(base_dir=tmp_path)
    printed = _capture(monkeypatch)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "amplifier_foundation.session":
            raise ImportError("simulated stale foundation")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SystemExit) as exc_info:
        _repair_session(store, session_id)

    assert exc_info.value.code == 1
    text = "\n".join(printed)
    assert "uv lock --upgrade-package amplifier-foundation" in text
