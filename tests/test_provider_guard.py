"""Tests for the resume-time provider-mismatch guard (issue #208, Option A).

Covers amplifier_app_cli.provider_guard:
- last_writing_provider(): parse events.jsonl, return the LAST llm:response provider/model
- active_provider_aliases()/active_provider_display()/active_model(): config_data readers
- check_resume_provider(): the decision table (silent / warn+proceed / warn+confirm)

Tests use a fake console (records .print() calls, scripts .input() answers) so no
real terminal is required, and pass is_tty= explicitly per the spec's test plan.
"""

from __future__ import annotations

import json
import logging

from amplifier_app_cli.provider_guard import active_model
from amplifier_app_cli.provider_guard import active_provider_aliases
from amplifier_app_cli.provider_guard import active_provider_display
from amplifier_app_cli.provider_guard import check_resume_provider
from amplifier_app_cli.provider_guard import last_writing_provider


class FakeConsole:
    """Minimal console double: records prints, scripts input() answers."""

    def __init__(self, answer: str = "n"):
        self.printed: list[str] = []
        self._answer = answer
        self.input_calls = 0

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))

    def input(self, prompt: str = "") -> str:
        self.input_calls += 1
        return self._answer


def _write_events(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _llm_response(provider, model=None):
    data = {"provider": provider}
    if model is not None:
        data["model"] = model
    return {"event": "llm:response", "data": data}


def _config(module="provider-anthropic", model=None, instance_id=None, id_=None):
    provider_entry: dict = {"module": module}
    if model is not None:
        provider_entry["config"] = {"model": model}
    if instance_id is not None:
        provider_entry["instance_id"] = instance_id
    if id_ is not None:
        provider_entry["id"] = id_
    return {"providers": [provider_entry]}


# ---------------------------------------------------------------------------
# last_writing_provider
# ---------------------------------------------------------------------------


def test_last_writing_provider_returns_last_match(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {"event": "session:start", "data": {}},
            _llm_response("openai", "gpt-5.5"),
            {"event": "tool:pre", "data": {}},
            _llm_response("anthropic", "claude-fable-5"),
        ],
    )
    assert last_writing_provider(events) == ("anthropic", "claude-fable-5")


def test_last_writing_provider_missing_file(tmp_path):
    assert last_writing_provider(tmp_path / "nope.jsonl") == (None, None)


def test_last_writing_provider_no_llm_response(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {"event": "prompt:submit", "data": {}},
            {"event": "tool:pre", "data": {"tool_name": "bash"}},
        ],
    )
    assert last_writing_provider(events) == (None, None)


def test_huge_line_safety(tmp_path):
    """A huge non-matching line must not be loaded/parsed; the real result must still surface."""
    events = tmp_path / "events.jsonl"
    huge_request = json.dumps(
        {"event": "llm:request", "data": {"blob": "x" * (2 * 1024 * 1024)}}
    )
    lines = [huge_request, json.dumps(_llm_response("anthropic", "claude-fable-5"))]
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")

    import time

    start = time.monotonic()
    result = last_writing_provider(events)
    elapsed = time.monotonic() - start

    assert result == ("anthropic", "claude-fable-5")
    assert (
        elapsed < 2.0
    )  # generous ceiling; structural guarantee is line-by-line reading


# ---------------------------------------------------------------------------
# config_data readers
# ---------------------------------------------------------------------------


def test_active_provider_aliases_includes_module_and_stripped():
    aliases = active_provider_aliases(_config(module="provider-anthropic"))
    assert aliases == {"provider-anthropic", "anthropic"}


def test_active_provider_aliases_empty_when_no_providers():
    assert active_provider_aliases({}) == set()
    assert active_provider_aliases({"providers": []}) == set()


def test_active_provider_display_strips_prefix():
    assert active_provider_display(_config(module="provider-anthropic")) == "anthropic"


def test_active_model_reads_config_model():
    assert active_model(_config(model="claude-sonnet-5")) == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# check_resume_provider decision table
# ---------------------------------------------------------------------------


def test_mismatch_non_interactive_proceeds(tmp_path, caplog):
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(events_dir / "events.jsonl", [_llm_response("openai", "gpt-5.5")])
    console = FakeConsole()

    with caplog.at_level(logging.WARNING):
        result = check_resume_provider(
            "sess1",
            _config(module="provider-anthropic"),
            console,
            base_dir=tmp_path,
            is_tty=False,
        )

    assert result is True
    assert any("Provider mismatch" in msg for msg in console.printed)
    assert any("Provider mismatch" in record.message for record in caplog.records)


def test_mismatch_interactive_abort(tmp_path):
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(events_dir / "events.jsonl", [_llm_response("openai", "gpt-5.5")])
    console = FakeConsole(answer="n")

    result = check_resume_provider(
        "sess1",
        _config(module="provider-anthropic"),
        console,
        base_dir=tmp_path,
        is_tty=True,
    )

    assert result is False
    assert console.input_calls == 1


def test_mismatch_interactive_confirm(tmp_path):
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(events_dir / "events.jsonl", [_llm_response("openai", "gpt-5.5")])
    console = FakeConsole(answer="y")

    result = check_resume_provider(
        "sess1",
        _config(module="provider-anthropic"),
        console,
        base_dir=tmp_path,
        is_tty=True,
    )

    assert result is True


def test_same_provider_silent(tmp_path):
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(
        events_dir / "events.jsonl",
        [_llm_response("anthropic", "claude-fable-5")],
    )
    console = FakeConsole()

    result = check_resume_provider(
        "sess1",
        _config(module="provider-anthropic", model="claude-sonnet-5"),
        console,
        base_dir=tmp_path,
        is_tty=True,
    )

    assert result is True
    assert console.printed == []
    assert console.input_calls == 0


def test_alias_match_named_instance(tmp_path):
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(events_dir / "events.jsonl", [_llm_response("anthropic")])
    console = FakeConsole()

    result = check_resume_provider(
        "sess1",
        _config(module="provider-anthropic", instance_id="anthropic-sonnet"),
        console,
        base_dir=tmp_path,
        is_tty=True,
    )

    assert result is True
    assert console.printed == []


def test_no_active_providers_silent(tmp_path):
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(events_dir / "events.jsonl", [_llm_response("openai")])
    console = FakeConsole()

    result = check_resume_provider("sess1", {}, console, base_dir=tmp_path, is_tty=True)

    assert result is True
    assert console.printed == []


def test_model_only_change_silent(tmp_path):
    """Documents the §2.1.3 policy: same-provider model swaps never warn."""
    events_dir = tmp_path / "sess1"
    events_dir.mkdir()
    _write_events(
        events_dir / "events.jsonl",
        [_llm_response("anthropic", "claude-fable-5")],
    )
    console = FakeConsole()

    result = check_resume_provider(
        "sess1",
        _config(module="provider-anthropic", model="claude-sonnet-5"),
        console,
        base_dir=tmp_path,
        is_tty=True,
    )

    assert result is True
    assert console.printed == []


def test_no_events_file_silent(tmp_path):
    """No events.jsonl at all (brand-new/never-written session) -> silent proceed."""
    console = FakeConsole()
    result = check_resume_provider(
        "sess-missing",
        _config(module="provider-anthropic"),
        console,
        base_dir=tmp_path,
        is_tty=True,
    )
    assert result is True
    assert console.printed == []
