"""Regression tests for ordinary and immutable fork cumulative cost recovery."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from amplifier_app_cli.cost_history import (
    _prefix_fingerprint,
    build_fork_cost_boundary,
    restore_fork_lineage_cost,
    restore_session_cost,
    sum_prior_cost_usd,
)
from amplifier_app_cli.session_store import SessionStore


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def _submit(owner: str, prompt: str) -> dict:
    return {"event": "prompt:submit", "data": {"session_id": owner, "prompt": prompt}}


def _response(owner: str, cost: str) -> dict:
    return {
        "event": "llm:response",
        "data": {"session_id": owner, "usage": {"cost_usd": cost}},
    }


def _messages(*prompts: str) -> list[dict]:
    result = []
    for prompt in prompts:
        result.extend((
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": f"{prompt} answer"},
        ))
    return result


def _registered(coordinator: MagicMock) -> dict:
    captured = {}
    coordinator.register_contributor = lambda channel, name, callback: captured.setdefault(
        (channel, name), callback
    )
    return captured


def _save(store: SessionStore, session_id: str, messages: list[dict], metadata: dict) -> Path:
    store.save_new(session_id, messages, {"session_id": session_id, **metadata})
    return store.base_dir / session_id


def _fork(
    store: SessionStore,
    parent_id: str,
    child_id: str,
    parent_messages: list[dict],
    parent_metadata: dict,
    turn: int,
) -> tuple[Path, dict]:
    child_messages = parent_messages[: turn * 2]
    boundary = build_fork_cost_boundary(
        parent_dir=store.base_dir / parent_id,
        parent_id=parent_id,
        parent_messages=parent_messages,
        parent_metadata={"session_id": parent_id, **parent_metadata},
        fork_turn=turn,
        child_messages=child_messages,
    )
    child = _save(
        store,
        child_id,
        child_messages,
        {
            "parent_id": parent_id,
            "forked_from_turn": turn,
            "fork_cost_boundary": boundary,
        },
    )
    return child, boundary


def test_ordinary_resume_keeps_legacy_cost_reader(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    _write_events(events, [_response("root", "0.10"), _response("root", "0.05")])
    coordinator = MagicMock()
    callbacks = _registered(coordinator)

    assert sum_prior_cost_usd(events, session_id="root") == Decimal("0.15")
    assert restore_session_cost(coordinator, "root", events) == Decimal("0.15")
    assert callbacks[("session.cost", "history:root")]() == {"cost_usd": "0.15"}


def test_timestamp_free_store_messages_create_verified_boundary_and_restore(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    parent_messages = _messages("first")
    parent = _save(store, "parent", parent_messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [_submit("parent", "first"), _response("parent", "0.10")],
    )

    child, boundary = _fork(store, "parent", "child", parent_messages, {}, 1)
    _write_events(child / "context-intelligence" / "events.jsonl", [_response("child", "0.20")])
    coordinator = MagicMock(session_state={})
    callbacks = _registered(coordinator)

    result = restore_fork_lineage_cost(coordinator, session_id="child", session_dir=child)

    assert all("timestamp" not in row for row in parent_messages)
    assert boundary["status"] == "verified"
    assert boundary["cumulative_cost_usd_by_turn"] == ["0.10"]
    assert result == type(result)(Decimal("0.30"), ())
    assert callbacks[("session.cost", "history:child")]() == {"cost_usd": "0.30"}


def test_boundary_fingerprint_matches_session_store_sanitization(tmp_path: Path) -> None:
    class NonSerializable:
        pass

    store = SessionStore(tmp_path / "sessions")
    parent_messages = _messages("first")
    parent = _save(store, "parent", parent_messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [_submit("parent", "first"), _response("parent", "0.10")],
    )
    child_messages = _messages("first")
    child_messages[1]["thinking_block"] = NonSerializable()
    boundary = build_fork_cost_boundary(
        parent_dir=parent,
        parent_id="parent",
        parent_messages=parent_messages,
        parent_metadata={},
        fork_turn=1,
        child_messages=child_messages,
    )
    child = _save(
        store,
        "child",
        child_messages,
        {
            "parent_id": "parent",
            "forked_from_turn": 1,
            "fork_cost_boundary": boundary,
        },
    )

    result = restore_fork_lineage_cost(MagicMock(session_state={}), session_id="child", session_dir=child)

    assert boundary["status"] == "verified"
    assert result == type(result)(Decimal("0.10"), ("missing_ci_capture:child",))


def test_historical_boundary_excludes_later_parent_turn_cost(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    parent_messages = _messages("first", "second")
    parent = _save(store, "parent", parent_messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [
            _submit("parent", "first"), _response("parent", "0.10"),
            _submit("parent", "second"), _response("parent", "0.20"),
        ],
    )

    _, boundary = _fork(store, "parent", "child", parent_messages, {}, 1)

    assert boundary["status"] == "verified"
    assert boundary["cumulative_cost_usd_by_turn"] == ["0.10"]


def test_nested_boundary_survives_parent_rewrite_and_removed_ancestors(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    a_messages = _messages("a-one")
    a = _save(store, "a", a_messages, {})
    _write_events(
        a / "context-intelligence" / "events.jsonl",
        [_submit("a", "a-one"), _response("a", "0.10")],
    )
    b, b_boundary = _fork(store, "a", "b", a_messages, {}, 1)
    assert b_boundary["status"] == "verified"

    b_messages = _messages("a-one", "b-two")
    store.save(
        "b",
        b_messages,
        {
            "session_id": "b",
            "parent_id": "a",
            "forked_from_turn": 1,
            "fork_cost_boundary": b_boundary,
        },
    )
    _write_events(
        b / "context-intelligence" / "events.jsonl",
        [_submit("b", "b-two"), _response("b", "0.30")],
    )
    c, c_boundary = _fork(
        store,
        "b",
        "c",
        b_messages,
        store.get_metadata("b"),
        2,
    )
    assert c_boundary["cumulative_cost_usd_by_turn"] == ["0.10", "0.40"]
    _write_events(c / "context-intelligence" / "events.jsonl", [_response("c", "0.40")])

    before = restore_fork_lineage_cost(MagicMock(session_state={}), session_id="c", session_dir=c)
    store.save("b", _messages("rewritten"), store.get_metadata("b"))
    rewritten_boundary = build_fork_cost_boundary(
        parent_dir=b,
        parent_id="b",
        parent_messages=_messages("rewritten"),
        parent_metadata=store.get_metadata("b"),
        fork_turn=1,
        child_messages=_messages("rewritten"),
    )
    assert rewritten_boundary["status"] == "unavailable"
    assert "fork_prefix_changed" in rewritten_boundary["reasons"]
    for path in (a, b):
        shutil.rmtree(path)
    after = restore_fork_lineage_cost(MagicMock(session_state={}), session_id="c", session_dir=c)

    assert before == after == type(before)(Decimal("0.80"), ())


def test_resume_uses_snapshot_after_child_rewrite_but_new_fork_is_unavailable(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    parent_messages = _messages("parent")
    parent = _save(store, "parent", parent_messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [_submit("parent", "parent"), _response("parent", "0.10")],
    )
    child, boundary = _fork(store, "parent", "child", parent_messages, {}, 1)
    child_messages = _messages("parent", "child")
    child_metadata = store.get_metadata("child")
    store.save("child", child_messages, child_metadata)
    _write_events(
        child / "context-intelligence" / "events.jsonl",
        [_submit("child", "child"), _response("child", "0.20")],
    )

    before_rewrite = restore_fork_lineage_cost(
        MagicMock(session_state={}), session_id="child", session_dir=child
    )
    rewritten_messages = _messages("rewritten")
    store.save("child", rewritten_messages, child_metadata)
    after_rewrite = restore_fork_lineage_cost(
        MagicMock(session_state={}), session_id="child", session_dir=child
    )
    _, new_boundary = _fork(
        store,
        "child",
        "new-child",
        rewritten_messages,
        store.get_metadata("child"),
        1,
    )

    assert boundary["status"] == "verified"
    assert before_rewrite == after_rewrite == type(before_rewrite)(Decimal("0.30"), ())
    assert new_boundary["status"] == "unavailable"
    assert "fork_prefix_changed" in new_boundary["reasons"]


def test_old_fork_without_boundary_is_incomplete_not_guessed(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    child = _save(
        store,
        "old-child",
        _messages("one"),
        {"parent_id": "gone-parent", "forked_from_turn": 1},
    )
    _write_events(child / "context-intelligence" / "events.jsonl", [_response("old-child", "0.20")])

    result = restore_fork_lineage_cost(
        MagicMock(session_state={}), session_id="old-child", session_dir=child
    )

    assert result.total == Decimal("0.20")
    assert result.diagnostics == ("missing_fork_cost_boundary",)


def test_duplicate_prompt_and_corrupt_or_unscoped_ci_make_boundary_unavailable(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    messages = _messages("same", "same")
    parent = _save(store, "parent", messages, {})
    events = parent / "context-intelligence" / "events.jsonl"
    _write_events(events, [_submit("parent", "same"), _response("parent", "0.10")])

    _, ambiguous = _fork(store, "parent", "ambiguous", messages, {}, 2)
    assert ambiguous["status"] == "unavailable"
    assert any(reason.startswith("missing_prompt_fence:parent") for reason in ambiguous["reasons"])

    events.write_text(
        json.dumps(_submit("parent", "same")) + "\n"
        + json.dumps(_response("parent", "0.10")) + "\n"
        + "{unfinished",
        encoding="utf-8",
    )
    _, corrupt = _fork(store, "parent", "corrupt", _messages("same"), {}, 1)
    assert corrupt["status"] == "unavailable"
    assert "ci_incomplete_event:parent" in corrupt["reasons"]


def test_resumed_prompt_cycle_is_not_blindly_associated_by_ordinal(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    messages = _messages("repeat", "repeat")
    parent = _save(store, "parent", messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [
            _submit("parent", "repeat"), _response("parent", "0.10"),
            _submit("parent", "repeat"), _response("parent", "0.20"),
            _submit("parent", "repeat"), _response("parent", "0.30"),
        ],
    )

    _, boundary = _fork(store, "parent", "child", messages, {}, 2)

    assert boundary["status"] == "unavailable"
    assert any(reason.startswith("missing_prompt_fence:parent") for reason in boundary["reasons"])


def test_restore_excludes_unscoped_and_foreign_costs_and_is_idempotent(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    child = _save(
        store,
        "child",
        _messages("one"),
        {
            "parent_id": "parent",
            "forked_from_turn": 1,
            "fork_cost_boundary": {
                "version": 1,
                "status": "verified",
                "inherited_turn_count": 1,
                "cumulative_cost_usd_by_turn": ["0.10"],
                "prefix_fingerprint": "",
                "provenance": {"owner": "parent", "fence": {}},
            },
        },
    )
    metadata = store.get_metadata("child")
    metadata["fork_cost_boundary"]["prefix_fingerprint"] = _prefix_fingerprint(
        _messages("one"), 1
    )
    store.save("child", _messages("one"), metadata)
    _write_events(
        child / "context-intelligence" / "events.jsonl",
        [
            _response("child", "0.20"),
            {"event": "llm:response", "data": {"usage": {"cost_usd": "6.00"}}},
            _response("foreign", "9.00"),
        ],
    )
    coordinator = MagicMock(session_state={})
    callbacks = _registered(coordinator)

    first = restore_fork_lineage_cost(coordinator, session_id="child", session_dir=child)
    second = restore_fork_lineage_cost(coordinator, session_id="child", session_dir=child)

    assert first.total == second.total == Decimal("0.30")
    assert "ci_unscoped_event:child" in first.diagnostics
    assert "foreign_cost:child" in first.diagnostics
    assert len(callbacks) == 1


def test_invalid_oversized_boundary_and_unstable_capture_are_explicit(tmp_path: Path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    messages = _messages("one")
    parent = _save(store, "parent", messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [_submit("parent", "one"), _response("parent", "0.10")],
    )
    stamps = iter(((1, 1, 1), (1, 1, 2)))
    monkeypatch.setattr(
        "amplifier_app_cli.cost_history._capture_stamp", lambda _path: next(stamps)
    )
    _, boundary = _fork(store, "parent", "unstable", messages, {}, 1)
    assert boundary["status"] == "unavailable"
    assert "unstable_ci_capture:parent" in boundary["reasons"]

    child = _save(
        store,
        "oversized",
        messages,
        {
            "parent_id": "parent",
            "forked_from_turn": 1,
            "fork_cost_boundary": {"version": 1, "status": "verified", "padding": "x" * 70_000},
        },
    )
    result = restore_fork_lineage_cost(
        MagicMock(session_state={}), session_id="oversized", session_dir=child
    )
    assert "oversized_fork_cost_boundary" in result.diagnostics

    invalid = _save(
        store,
        "invalid",
        messages,
        {
            "parent_id": "parent",
            "forked_from_turn": 1,
            "fork_cost_boundary": {"version": 1, "status": "verified"},
        },
    )
    invalid_result = restore_fork_lineage_cost(
        MagicMock(session_state={}), session_id="invalid", session_dir=invalid
    )
    assert "invalid_fork_cost_boundary" in invalid_result.diagnostics


def test_boundary_uses_selected_relocated_capture_only(tmp_path: Path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "projects" / "slug" / "sessions")
    messages = _messages("one")
    parent = _save(store, "parent", messages, {})
    _write_events(
        parent / "context-intelligence" / "events.jsonl",
        [_submit("parent", "one"), _response("parent", "9.00")],
    )
    relocated = tmp_path / "relocated" / "slug" / "sessions" / "parent" / "context-intelligence"
    _write_events(
        relocated / "events.jsonl",
        [_submit("parent", "one"), _response("parent", "0.10")],
    )
    monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", str(tmp_path / "relocated"))

    _, boundary = _fork(store, "parent", "child", messages, {}, 1)

    assert boundary["status"] == "verified"
    assert boundary["cumulative_cost_usd_by_turn"] == ["0.10"]


def test_missing_selected_child_capture_keeps_verified_snapshot_and_is_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    store = SessionStore(tmp_path / "projects" / "slug" / "sessions")
    parent_messages = _messages("parent")
    parent = _save(store, "parent", parent_messages, {})
    relocated = tmp_path / "relocated" / "slug" / "sessions"
    _write_events(
        relocated / "parent" / "context-intelligence" / "events.jsonl",
        [_submit("parent", "parent"), _response("parent", "0.10")],
    )
    monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", str(tmp_path / "relocated"))
    child, boundary = _fork(store, "parent", "child", parent_messages, {}, 1)
    _write_events(
        child / "context-intelligence" / "events.jsonl",
        [_response("child", "9.99")],
    )

    result = restore_fork_lineage_cost(MagicMock(session_state={}), session_id="child", session_dir=child)

    assert boundary["status"] == "verified"
    assert result.total == Decimal("0.10")
    assert result.diagnostics == ("missing_ci_capture:child",)