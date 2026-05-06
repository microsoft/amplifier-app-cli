"""Tests for _sum_cost_usd helper in session_spawner."""

from decimal import Decimal

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
