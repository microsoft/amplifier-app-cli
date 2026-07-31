"""Wiring test for the resume-time provider-mismatch guard (issue #208).

Asserts that ``_prepare_resume_context`` (the single choke point every resume
path funnels through) actually calls the guard and honors an abort by raising
``SystemExit(0)`` -- and that a normal proceed returns the usual 8-tuple with
no exit. ``resolve_config`` is monkeypatched to keep the test light (avoids
exercising the full bundle/provider resolution machinery).
"""

from __future__ import annotations

import json

import pytest

from amplifier_app_cli.commands import session as session_module
from amplifier_app_cli.session_store import SessionStore


def _seed_session(base_dir, session_id: str):
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "metadata.json").write_text(
        json.dumps({"session_id": session_id, "bundle": "foundation"}),
        encoding="utf-8",
    )
    (session_dir / "transcript.jsonl").write_text("", encoding="utf-8")


@pytest.fixture
def resume_env(tmp_path, monkeypatch):
    session_id = "wiring-test-session"
    _seed_session(tmp_path, session_id)

    monkeypatch.setattr(
        session_module, "SessionStore", lambda: SessionStore(base_dir=tmp_path)
    )
    monkeypatch.setattr(
        session_module,
        "resolve_config",
        lambda **kwargs: ({"providers": [{"module": "provider-anthropic"}]}, None),
    )
    # Avoid touching real first-run/provider auto-install machinery.
    monkeypatch.setattr("amplifier_app_cli.commands.init.check_first_run", lambda: None)

    return session_id


def test_guard_abort_raises_system_exit(resume_env, monkeypatch):
    # The choke point imports check_resume_provider locally from ..provider_guard
    # at call time, so patch it at the source module.
    monkeypatch.setattr(
        "amplifier_app_cli.provider_guard.check_resume_provider", lambda *a, **kw: False
    )

    with pytest.raises(SystemExit) as exc_info:
        session_module._prepare_resume_context(
            resume_env,
            lambda: [],
            session_module.console,
        )
    assert exc_info.value.code == 0


def test_guard_proceed_returns_normal_tuple(resume_env, monkeypatch):
    monkeypatch.setattr(
        "amplifier_app_cli.provider_guard.check_resume_provider", lambda *a, **kw: True
    )

    result = session_module._prepare_resume_context(
        resume_env,
        lambda: [],
        session_module.console,
    )

    assert result[0] == resume_env
    assert len(result) == 8
