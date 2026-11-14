"""Tests for spawn cost bridge helpers.

_sum_cost_usd and _bridge_child_cost live in amplifier_foundation.bundle._prepared
and are imported directly from there (app-cli delegates, not reimplements).
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def test_skips_no_activity_entries_in_mixed_list():
    result = sum_cost_usd(
        [
            {"cost_usd": Decimal("0.03")},
            None,
            {"cost_usd": Decimal("0.02")},
        ]
    )
    assert result == Decimal("0.05")


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
    # Legacy Foundation emits Decimal; the completeness-aware bridge emits an
    # exact JSON-safe string. Both preserve the same known value without floats.
    assert isinstance(result["cost_usd"], (str, Decimal))
    assert Decimal(result["cost_usd"]) == Decimal("0.07")
    assert result.get("complete", True) is True


@pytest.mark.asyncio
async def test_bridge_swallows_exception_and_logs(caplog):
    """_bridge_child_cost never raises — errors are logged as warnings."""
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

    # Failure must never escape the app-layer spawn wrapper. The strict contract
    # below additionally requires that accounting failure remain visible.
    assert any(
        record.levelname == "WARNING" and "child session cost" in record.message
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
async def test_spawn_bridge_skips_registration_when_no_cost():
    """If child has no cost data, no contributor is registered on parent."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(return_value=[])

    parent_coord = MagicMock()
    parent_coord.register_contributor = MagicMock()

    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="test-child-456",
    )

    parent_coord.register_contributor.assert_not_called()


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

    assert ("session.cost", "delegate:resumed-child-789") in registered


@pytest.mark.asyncio
async def test_resume_bridge_accumulates_incremental_costs():
    """Resuming the same session twice correctly accumulates incremental costs.

    Each resume_sub_session call creates a FRESH child coordinator.  The provider
    re-mounts from zero, so the child's session.cost channel only contains costs
    for THAT resume's turns — not the full session history.

    _bridge_child_cost therefore passes the incremental cost for each resume.

    register_contributor in amplifier-core APPENDS (coordinator.rs: .push(entry)) —
    it does NOT overwrite on duplicate name.  Both entries are returned by
    collect_contributions and summed correctly by _sum_cost_usd.

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
    # returns both, _sum_cost_usd sums them — no key uniqueness required.
    assert channel1 == channel2 == "session.cost"
    assert name1 == name2 == "delegate:test-child-xyz"

    # Verify incremental values and that their sum is correct
    _, _, cb1 = all_register_calls[0]
    _, _, cb2 = all_register_calls[1]
    assert isinstance(cb1()["cost_usd"], (str, Decimal))
    assert isinstance(cb2()["cost_usd"], (str, Decimal))
    assert Decimal(cb1()["cost_usd"]) == Decimal("0.04")
    assert Decimal(cb2()["cost_usd"]) == Decimal("0.06")

    # Simulate what collect_contributions + _sum_cost_usd would produce:
    # both entries are returned, summed to $0.10 (no double-counting)
    total = sum_cost_usd([cb1(), cb2()])
    assert total == Decimal("0.10"), (
        f"Expected $0.10 from two incremental contributions, got {total!r}"
    )
