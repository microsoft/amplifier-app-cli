"""Tests for spawn cost bridge helpers.

Use the installed Foundation public exports (app-cli delegates, not reimplements).
Exact totals use Decimal internally; bridge snapshots use JSON-safe money strings.
Inactivity is distinct from present-but-unknown spend and from known-free activity.
"""

import json
import logging
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_core import AmplifierSession
from amplifier_foundation import bridge_child_cost, sum_cost_usd


def test_sums_single_contribution():
    result = sum_cost_usd([{"cost_usd": Decimal("0.05")}])
    assert result == Decimal("0.05")


def test_sums_multiple_contributions():
    result = sum_cost_usd(
        [
            {"cost_usd": Decimal("0.03")},
            {"cost_usd": Decimal("0.05")},
            {"cost_usd": Decimal("0.01")},
        ]
    )
    assert result == Decimal("0.09")


def test_returns_none_for_empty_list():
    result = sum_cost_usd([])
    assert result is None


def test_returns_none_when_all_none():
    result = sum_cost_usd([{"cost_usd": None}, None, {}])
    assert result is None


def test_accepts_string_cost_usd():
    result = sum_cost_usd([{"cost_usd": "0.05"}])
    assert result == Decimal("0.05")
    assert isinstance(result, Decimal)


def test_present_unknown_cost_makes_mixed_total_unknown():
    result = sum_cost_usd(
        [
            {"cost_usd": Decimal("0.03")},
            None,
            {"cost_usd": None},
            {"cost_usd": Decimal("0.02")},
        ]
    )
    assert result is None


def test_inactive_contributor_does_not_hide_known_cost():
    result = sum_cost_usd(
        [{"cost_usd": Decimal("0.03")}, None, {"cost_usd": "0.02"}]
    )
    assert result == Decimal("0.05")
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    "contribution",
    [
        pytest.param({"cost_usd": None}, id="null-cost"),
        pytest.param({}, id="missing-cost"),
        pytest.param({"cost_usd": "0.02", "complete": False}, id="incomplete-cost"),
        pytest.param(
            {"cost_usd": None, "known_subtotal_usd": "0.02", "complete": False},
            id="labelled-subtotal",
        ),
        pytest.param({"cost_usd": "invalid"}, id="malformed-cost"),
        pytest.param({"cost_usd": "NaN"}, id="nonfinite-cost"),
        pytest.param({"cost_usd": "-0.02"}, id="negative-cost"),
        pytest.param({"cost_usd": 0.02}, id="inexact-float"),
        pytest.param({"cost_usd": False}, id="boolean-is-not-zero"),
    ],
)
def test_unknown_contribution_is_not_a_free_or_exact_partial_total(contribution):
    assert sum_cost_usd([contribution]) is None
    assert sum_cost_usd([{"cost_usd": "0.03"}, contribution]) is None


@pytest.mark.parametrize("zero", [Decimal("0"), "0", 0])
def test_known_free_is_an_exact_decimal_zero(zero):
    result = sum_cost_usd([None, {"cost_usd": zero}])
    assert result == Decimal("0")
    assert isinstance(result, Decimal)


@pytest.mark.parametrize("unknown", [
    {"cost_usd": None},
    {"cost_usd": None, "known_subtotal_usd": "0.02", "complete": False},
    {"cost_usd": "0.02", "complete": False},
    {"cost_usd": "invalid"},
])
def test_present_unknown_cost_never_becomes_a_partial_total(unknown):
    assert sum_cost_usd([{"cost_usd": "0.03"}, None, unknown]) is None


def test_known_zero_is_not_absent_or_unknown():
    assert sum_cost_usd([None, {"cost_usd": "0"}]) == Decimal("0")
    assert sum_cost_usd([None]) is None


@pytest.mark.asyncio
async def test_spawn_bridge_registers_child_cost_on_parent():
    """After spawn_sub_session completes, parent coordinator has a delegate contributor."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.07")}]
    )

    parent_coord = MagicMock()
    registered = {}

    def capture_register(channel, name, callback):
        registered[(channel, name)] = callback

    parent_coord.register_contributor = capture_register

    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="test-child-123",
    )

    key = ("session.cost", "delegate:test-child-123")
    assert key in registered
    result = registered[key]()
    assert result == {"cost_usd": "0.07"}
    assert json.loads(json.dumps(result)) == result
    assert sum_cost_usd([result]) == Decimal("0.07")
    child_coord.collect_contributions.assert_awaited_once_with("session.cost")
    # A caller cannot corrupt the frozen contribution by mutating its snapshot.
    result["cost_usd"] = "99"
    assert registered[key]() == {"cost_usd": "0.07"}
    assert result.get("complete", True) is True


@pytest.mark.asyncio
async def test_bridge_swallows_exception_and_registers_unknown_cost(caplog):
    """A collection error is not a spawn error, but must not erase unknown spend."""
    child_coord = MagicMock()
    # Simulate a failure inside collect_contributions
    child_coord.collect_contributions = AsyncMock(side_effect=RuntimeError("simulated"))

    parent_coord = MagicMock()
    parent_coord.register_contributor = MagicMock()

    # Must not raise
    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="test-child-err",
    )

    child_coord.collect_contributions.assert_awaited_once_with("session.cost")
    parent_coord.register_contributor.assert_called_once()
    channel, name, callback = parent_coord.register_contributor.call_args.args
    assert (channel, name) == ("session.cost", "delegate:test-child-err")
    result = callback()
    assert result == {
        "cost_usd": None,
        "known_subtotal_usd": None,
        "complete": False,
    }
    assert sum_cost_usd([result]) is None
    assert sum_cost_usd([{"cost_usd": "0.03"}, result]) is None
    assert any(
        record.levelno == logging.WARNING
        and "Failed to collect child session cost for test-child-err; cost unknown"
        in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_failed_collection_registers_unknown_cost():
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(side_effect=RuntimeError("simulated"))
    parent_coord = MagicMock()
    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="failed-child",
    )
    channel, name, callback = parent_coord.register_contributor.call_args.args
    assert (channel, name) == ("session.cost", "delegate:failed-child")
    assert callback() == {
        "cost_usd": None, "known_subtotal_usd": None, "complete": False,
    }


@pytest.mark.asyncio
async def test_bridge_preserves_unknown_and_separate_known_subtotal():
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(return_value=[
        {"cost_usd": "0.03"},
        {"cost_usd": None, "known_subtotal_usd": "0.02", "complete": False},
    ])
    parent_coord = MagicMock()
    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="partial-child",
    )
    channel, name, callback = parent_coord.register_contributor.call_args.args
    assert (channel, name) == ("session.cost", "delegate:partial-child")
    assert callback() == {
        "cost_usd": None, "known_subtotal_usd": "0.05", "complete": False,
    }
    assert sum_cost_usd([{"cost_usd": "0.10"}, callback()]) is None
    assert json.loads(json.dumps(callback())) == callback()


@pytest.mark.asyncio
@pytest.mark.parametrize("contributions", [[], [None], [None, None]])
async def test_spawn_bridge_skips_registration_when_no_activity(contributions):
    """An empty/fresh child has no activity, not evidence of a known-zero cost."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(return_value=contributions)

    parent_coord = MagicMock()
    parent_coord.register_contributor = MagicMock()

    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="test-child-456",
    )

    parent_coord.register_contributor.assert_not_called()
    child_coord.collect_contributions.assert_awaited_once_with("session.cost")
    assert sum_cost_usd(contributions) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contributions", "expected"),
    [
        pytest.param(
            [{"cost_usd": None}],
            {"cost_usd": None, "known_subtotal_usd": None, "complete": False},
            id="unknown-only",
        ),
        pytest.param(
            [{}],
            {"cost_usd": None, "known_subtotal_usd": None, "complete": False},
            id="missing-cost",
        ),
        pytest.param(
            [{"cost_usd": "0.03"}, None, {"cost_usd": None}, {"cost_usd": "0.02"}],
            {"cost_usd": None, "known_subtotal_usd": "0.05", "complete": False},
            id="mixed-known-and-unknown",
        ),
        pytest.param(
            [{"cost_usd": "0.04", "complete": False}],
            {"cost_usd": None, "known_subtotal_usd": "0.04", "complete": False},
            id="explicit-incomplete",
        ),
        pytest.param(
            [
                {"cost_usd": "0.03"},
                {"cost_usd": None, "known_subtotal_usd": "0.02", "complete": False},
            ],
            {"cost_usd": None, "known_subtotal_usd": "0.05", "complete": False},
            id="inherited-partial-subtotal",
        ),
        pytest.param(
            [{"cost_usd": Decimal("0")}, {"cost_usd": "0"}, {"cost_usd": 0}],
            {"cost_usd": "0"},
            id="known-free",
        ),
        pytest.param(
            [{"cost_usd": "0"}, {"cost_usd": None}],
            {"cost_usd": None, "known_subtotal_usd": "0", "complete": False},
            id="zero-subtotal-is-not-known-free",
        ),
    ],
)
async def test_bridge_preserves_unknown_subtotals_and_known_zero(contributions, expected):
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(return_value=contributions)
    parent_coord = MagicMock()

    await bridge_child_cost(child_coord, parent_coord, "contract-child")

    child_coord.collect_contributions.assert_awaited_once_with("session.cost")
    parent_coord.register_contributor.assert_called_once()
    channel, name, callback = parent_coord.register_contributor.call_args.args
    assert (channel, name) == ("session.cost", "delegate:contract-child")
    result = callback()
    assert result == expected
    assert json.loads(json.dumps(result)) == expected
    if expected["cost_usd"] is None:
        assert sum_cost_usd([result]) is None
        assert sum_cost_usd([{"cost_usd": "0.10"}, result]) is None
    else:
        assert sum_cost_usd([result]) == Decimal("0")
        assert sum_cost_usd([{"cost_usd": "0.10"}, result]) == Decimal("0.10")


@pytest.mark.asyncio
async def test_resume_bridge_registers_child_cost_on_parent():
    """resume_sub_session also bridges child costs after execute()."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.04")}]
    )

    parent_coord = MagicMock()
    registered = {}

    def capture_register(channel, name, callback):
        registered[(channel, name)] = callback

    parent_coord.register_contributor = capture_register

    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="resumed-child-789",
    )

    key = ("session.cost", "delegate:resumed-child-789")
    assert key in registered
    assert registered[key]() == {"cost_usd": "0.04"}
    assert sum_cost_usd([registered[key]()]) == Decimal("0.04")


@pytest.mark.asyncio
async def test_resume_bridge_accumulates_incremental_costs():
    """Resuming the same session twice correctly accumulates incremental costs.

    Each resume_sub_session call creates a FRESH child coordinator.  The provider
    re-mounts from zero, so the child's session.cost channel only contains costs
    for THAT resume's turns — not the full session history.

    bridge_child_cost therefore passes the incremental cost for each resume.

    register_contributor in amplifier-core APPENDS (coordinator.rs: .push(entry)) —
    it does NOT overwrite on duplicate name.  Both entries are returned by
    collect_contributions and summed correctly by sum_cost_usd.

    Verified properties:
    - Both calls use the same (channel, name) key — standard contributor identity.
    - Each callback carries only the incremental cost of its resume.
    - sum_cost_usd([cb1(), cb2()]) == first_cost + second_cost (no double-count).
    """

    parent_coord = MagicMock()
    all_register_calls: list[tuple] = []

    def capture_register(channel, name, callback):
        all_register_calls.append((channel, name, callback))

    parent_coord.register_contributor = capture_register

    # First resume: fresh child coordinator accumulated $0.04 (turn 1 only)
    child_coord_1 = MagicMock()
    child_coord_1.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.04")}]
    )
    await bridge_child_cost(
        child_coordinator=child_coord_1,
        parent_coordinator=parent_coord,
        child_session_id="test-child-xyz",
    )

    # Second resume: fresh child coordinator accumulated $0.06 (turn 2 only)
    child_coord_2 = MagicMock()
    child_coord_2.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.06")}]
    )
    await bridge_child_cost(
        child_coordinator=child_coord_2,
        parent_coordinator=parent_coord,
        child_session_id="test-child-xyz",
    )

    assert len(all_register_calls) == 2, "Expected exactly two register_contributor calls"

    channel1, name1, _ = all_register_calls[0]
    channel2, name2, _ = all_register_calls[1]

    # Same channel + name: register_contributor appends both, collect_contributions
    # returns both, sum_cost_usd sums them — no key uniqueness required.
    assert channel1 == channel2 == "session.cost"
    assert name1 == name2 == "delegate:test-child-xyz"

    # Verify incremental values and that their sum is correct
    _, _, cb1 = all_register_calls[0]
    _, _, cb2 = all_register_calls[1]
    assert cb1() == {"cost_usd": "0.04"}
    assert cb2() == {"cost_usd": "0.06"}

    # Simulate what collect_contributions + sum_cost_usd would produce:
    # both entries are returned, summed to $0.10 (no double-counting)
    total = sum_cost_usd([cb1(), cb2()])
    assert total == Decimal("0.10"), (
        f"Expected $0.10 from two incremental contributions, got {total!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("second_cost", ["0.06", None])
async def test_real_core_collects_both_resume_snapshots_without_hiding_unknown(second_cost):
    """Exercise Core's append semantics, not just the registration spy above.

    These sessions need no modules initialized: only public contribution APIs run.
    """
    config = {"session": {"orchestrator": "unused", "context": "unused"}}
    parent = AmplifierSession(config=config, session_id="cost-parent")
    for cost in ("0.04", second_cost):
        child = AmplifierSession(config=config, session_id="cost-child")
        child.coordinator.register_contributor(
            "session.cost", "provider:test", lambda value=cost: {"cost_usd": value}
        )
        await bridge_child_cost(child.coordinator, parent.coordinator, "cost-child")

    snapshots = await parent.coordinator.collect_contributions("session.cost")

    assert len(snapshots) == 2
    assert snapshots[0] == {"cost_usd": "0.04"}
    if second_cost is None:
        assert snapshots[1] == {
            "cost_usd": None,
            "known_subtotal_usd": None,
            "complete": False,
        }
        assert sum_cost_usd(snapshots) is None
    else:
        assert snapshots[1] == {"cost_usd": "0.06"}
        assert sum_cost_usd(snapshots) == Decimal("0.10")
