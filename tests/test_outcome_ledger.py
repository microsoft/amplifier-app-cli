from decimal import Decimal

import pytest

from amplifier_app_cli.main import _is_shell_tool_name
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.outcome_ledger import OutcomeYield
from amplifier_app_cli.ui.outcome_ledger import TurnOutcome
from amplifier_app_cli.ui.outcome_ledger import YieldKind


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("shell", True),
        ("exec_command", True),
        ("tool-bash", True),
        ("load_skill", False),
        ("delegate", False),
        ("todo", False),
    ],
)
def test_shell_tool_classification_does_not_count_agent_tools(name, expected):
    assert _is_shell_tool_name(name) is expected


def _outcome(
    turn: int,
    *,
    cost: str = "0.10",
    yields: tuple[OutcomeYield, ...] = (),
    interrupted: bool = False,
) -> TurnOutcome:
    return TurnOutcome(
        turn_id=f"turn-{turn}",
        checkpoint_id=f"checkpoint-{turn}",
        cost=cost,
        elapsed_seconds=4.2,
        tokens=1_200,
        cached_percent=80,
        yields=yields,
        interrupted=interrupted,
    )


def test_ledger_summarizes_spend_and_yield() -> None:
    ledger = OutcomeLedger()
    ledger.record(
        _outcome(
            1,
            cost="0.09",
            yields=(
                OutcomeYield(YieldKind.FILES, "3 files"),
                OutcomeYield(YieldKind.DIFF, "+142/-38"),
                OutcomeYield(YieldKind.TESTS, "tests passed"),
            ),
        )
    )
    ledger.record(
        _outcome(
            2,
            cost="0.41",
            yields=(OutcomeYield(YieldKind.ANSWER, "answer"),),
        )
    )
    ledger.record(
        _outcome(
            3,
            cost="0.04",
            yields=(OutcomeYield(YieldKind.INTERRUPTED, "interrupted"),),
            interrupted=True,
        )
    )

    summary = ledger.summary()
    assert summary.turns == 3
    assert summary.session_cost == Decimal("0.54")
    assert summary.shipped_turns == 1
    assert summary.answer_only_turns == 1
    assert summary.interrupted_turns == 1
    assert summary.cheapest_shipped_cost == Decimal("0.09")
    assert summary.dearest_shipped_cost == Decimal("0.09")
    assert summary.cache_hit_percent == 80
    assert ledger.footer_yield() == ""


def test_ledger_checkpoint_lookup_and_serialization() -> None:
    ledger = OutcomeLedger()
    outcome = _outcome(
        1,
        yields=(OutcomeYield(YieldKind.COMMANDS, "2 commands"),),
    )
    ledger.record(outcome)

    assert ledger.checkpoint("checkpoint-1") == outcome
    assert ledger.footer_yield() == ""
    assert ledger.as_records()[0]["cost"] == "0.10"
    assert ledger.as_records()[0]["yields"] == [
        {"kind": "commands", "label": "2 commands"}
    ]


def test_footer_yield_only_marks_material_or_passing_test_results() -> None:
    ledger = OutcomeLedger()
    ledger.record(
        _outcome(
            1,
            yields=(OutcomeYield(YieldKind.TESTS, "tests ✘"),),
        )
    )
    assert ledger.footer_yield() == ""

    ledger.record(
        _outcome(
            2,
            yields=(OutcomeYield(YieldKind.TESTS, "tests ✔"),),
        )
    )
    assert ledger.footer_yield() == "▲"


def test_ledger_restores_valid_records_and_skips_untrusted_metadata() -> None:
    source = OutcomeLedger()
    source.record(
        _outcome(
            1,
            yields=(OutcomeYield(YieldKind.ANSWER, "answer"),),
        )
    )
    records = source.as_records() + [
        {"turn_id": "broken", "checkpoint_id": "broken", "cost": "NaN"},
        "not a record",
    ]

    restored = OutcomeLedger()
    restored.restore_records(records)

    assert restored.entries == source.entries


def test_ledger_is_bounded_without_reusing_duplicate_turn_ids() -> None:
    ledger = OutcomeLedger(max_entries=2)
    ledger.record(_outcome(1))
    ledger.record(_outcome(2))
    ledger.record(_outcome(3))

    assert [entry.turn_id for entry in ledger.entries] == ["turn-2", "turn-3"]
    ledger.record(_outcome(1))
    assert [entry.turn_id for entry in ledger.entries] == ["turn-3", "turn-1"]
    with pytest.raises(ValueError, match="already recorded"):
        ledger.record(_outcome(3))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cost": "NaN"},
        {"cost": "-0.1"},
        {"elapsed_seconds": -1},
        {"tokens": -1},
        {"cached_percent": 101},
    ],
)
def test_turn_outcome_validates_metrics(kwargs) -> None:
    base = {
        "turn_id": "turn",
        "checkpoint_id": "checkpoint",
        "cost": "0",
        "elapsed_seconds": 0,
        "tokens": 0,
    }
    base.update(kwargs)

    with pytest.raises(ValueError):
        TurnOutcome(**base)


def test_turn_outcome_limits_yield_fields_to_three() -> None:
    yields = tuple(
        OutcomeYield(YieldKind.FILES, f"yield {index}") for index in range(4)
    )

    with pytest.raises(ValueError, match="at most three"):
        _outcome(1, yields=yields)
