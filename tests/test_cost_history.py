"""Tests for cumulative-session-cost restoration on resume (issue #284).

Covers amplifier_app_cli.cost_history:
- sum_prior_cost_usd(): parse events.jsonl and sum data.usage.cost_usd
- restore_session_cost(): register a synthetic session.cost contributor

The persisted event shape mirrors a real llm:response line: cost lives at
data.usage.cost_usd and is stringified (Decimal is not JSON-serializable).
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock

from amplifier_app_cli.cost_history import restore_session_cost
from amplifier_app_cli.cost_history import sum_prior_cost_usd


def _write_events(path, events):
    """Write a list of event dicts as JSONL to path."""
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _llm_response(cost, **usage):
    """Build an llm:response event with cost at data.usage.cost_usd."""
    usage = {"input_tokens": 10, "output_tokens": 5, **usage}
    if cost is not None:
        usage["cost_usd"] = cost
    return {"event": "llm:response", "data": {"model": "test", "usage": usage}}


# --------------------------------------------------------------------------
# sum_prior_cost_usd
# --------------------------------------------------------------------------


def test_sum_returns_none_for_missing_file(tmp_path):
    assert sum_prior_cost_usd(tmp_path / "nope.jsonl") is None


def test_sum_returns_none_when_no_cost_events(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [{"event": "session:start", "data": {}}])
    assert sum_prior_cost_usd(events) is None


def test_sum_single_llm_response(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_llm_response("0.178059")])
    assert sum_prior_cost_usd(events) == Decimal("0.178059")


def test_sum_multiple_llm_responses(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {"event": "session:start", "data": {}},
            _llm_response("0.10"),
            {"event": "tool:call", "data": {}},
            _llm_response("0.05"),
            _llm_response("0.02"),
        ],
    )
    assert sum_prior_cost_usd(events) == Decimal("0.17")


def test_sum_ignores_null_cost_and_bad_lines(tmp_path):
    events = tmp_path / "events.jsonl"
    # A malformed JSON line, a null-cost response, and a valid one.
    content = "\n".join(
        [
            "{not valid json",
            json.dumps(_llm_response(None)),
            json.dumps(_llm_response("0.03")),
        ]
    )
    events.write_text(content + "\n", encoding="utf-8")
    assert sum_prior_cost_usd(events) == Decimal("0.03")


def test_sum_ignores_cost_on_non_llm_response_events(tmp_path):
    events = tmp_path / "events.jsonl"
    # A different event type that happens to carry a usage.cost_usd must be ignored.
    _write_events(
        events,
        [
            {"event": "other:event", "data": {"usage": {"cost_usd": "9.99"}}},
            _llm_response("0.04"),
        ],
    )
    assert sum_prior_cost_usd(events) == Decimal("0.04")


# --------------------------------------------------------------------------
# restore_session_cost
# --------------------------------------------------------------------------


def test_restore_registers_history_contributor(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_llm_response("0.10"), _llm_response("0.05")])

    coordinator = MagicMock()
    registered = {}

    def capture_register(channel, name, callback):
        registered[(channel, name)] = callback

    coordinator.register_contributor = capture_register

    total = restore_session_cost(coordinator, "sess-abc", events)

    assert total == Decimal("0.15")
    key = ("session.cost", "history:sess-abc")
    assert key in registered
    # Contributor payload matches the provider modules' stringified shape.
    assert registered[key]() == {"cost_usd": "0.15"}


def test_restore_no_events_registers_nothing(tmp_path):
    coordinator = MagicMock()
    total = restore_session_cost(coordinator, "sess-x", tmp_path / "missing.jsonl")
    assert total is None
    coordinator.register_contributor.assert_not_called()


def test_restore_zero_cost_registers_nothing(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_llm_response("0")])
    coordinator = MagicMock()
    total = restore_session_cost(coordinator, "sess-x", events)
    assert total is None
    coordinator.register_contributor.assert_not_called()


def test_restore_swallows_registration_error(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_llm_response("0.10")])
    coordinator = MagicMock()
    coordinator.register_contributor.side_effect = RuntimeError("boom")
    # Must never raise even if the kernel call fails.
    assert restore_session_cost(coordinator, "sess-x", events) is None


def test_restore_and_fresh_provider_sum_without_double_count(tmp_path):
    """The history contributor + a fresh provider contributor sum correctly.

    Mirrors resume: the provider re-mounts with a zeroed accumulator and only
    counts NEW turns; the history contributor supplies the pre-resume total.
    Together they reproduce the true cumulative cost with no double counting.
    """
    events = tmp_path / "events.jsonl"
    _write_events(events, [_llm_response("0.10"), _llm_response("0.05")])

    contributors = []
    coordinator = MagicMock()
    coordinator.register_contributor = lambda ch, name, cb: contributors.append(cb)

    restore_session_cost(coordinator, "sess-abc", events)  # history: 0.15

    # Simulate a fresh per-mount provider contributor that recorded one new turn.
    contributors.append(lambda: {"cost_usd": "0.07"})

    # Reproduce how collect_contributions + sum_cost_usd aggregate the channel:
    # every registered contributor is summed (str or Decimal payloads accepted).
    total = sum(Decimal(str(cb()["cost_usd"])) for cb in contributors)
    assert total == Decimal("0.22")
