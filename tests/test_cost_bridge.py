"""Tests for _sum_cost_usd helper in session_spawner."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.session_spawner import _sum_cost_usd


def test_sums_single_contribution():
    result = _sum_cost_usd([{"cost_usd": Decimal("0.05")}])
    assert result == Decimal("0.05")


def test_sums_multiple_contributions():
    result = _sum_cost_usd(
        [
            {"cost_usd": Decimal("0.03")},
            {"cost_usd": Decimal("0.05")},
            {"cost_usd": Decimal("0.01")},
        ]
    )
    assert result == Decimal("0.09")


def test_returns_none_for_empty_list():
    result = _sum_cost_usd([])
    assert result is None


def test_returns_none_when_all_none():
    result = _sum_cost_usd([{"cost_usd": None}, None, {}])
    assert result is None


def test_accepts_string_cost_usd():
    result = _sum_cost_usd([{"cost_usd": "0.05"}])
    assert result == Decimal("0.05")
    assert isinstance(result, Decimal)


def test_skips_none_entries_in_mixed_list():
    result = _sum_cost_usd(
        [
            {"cost_usd": Decimal("0.03")},
            None,
            {"cost_usd": None},
            {"cost_usd": Decimal("0.02")},
        ]
    )
    assert result == Decimal("0.05")


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

    from amplifier_app_cli.session_spawner import _bridge_child_cost
    await _bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="test-child-123",
    )

    key = ("session.cost", "delegate:test-child-123")
    assert key in registered
    result = registered[key]()
    assert result == {"cost_usd": Decimal("0.07")}


@pytest.mark.asyncio
async def test_spawn_bridge_skips_registration_when_no_cost():
    """If child has no cost data, no contributor is registered on parent."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(return_value=[])

    parent_coord = MagicMock()
    parent_coord.register_contributor = MagicMock()

    from amplifier_app_cli.session_spawner import _bridge_child_cost
    await _bridge_child_cost(
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

    from amplifier_app_cli.session_spawner import _bridge_child_cost
    await _bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="resumed-child-789",
    )

    assert ("session.cost", "delegate:resumed-child-789") in registered
