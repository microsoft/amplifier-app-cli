from __future__ import annotations

from amplifier_app_cli.ui.git_yield import GitDiffSnapshot
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger
from amplifier_app_cli.ui.outcome_ledger import YieldKind
from amplifier_app_cli.ui.turn_outcomes import build_turn_outcome
from amplifier_app_cli.ui.turn_outcomes import is_shell_tool_name


def _diff() -> GitDiffSnapshot:
    return GitDiffSnapshot(True)


def test_answer_only_turn_has_stable_checkpoint() -> None:
    outcome = build_turn_outcome(
        session_id="session-123456",
        outcome_ledger=OutcomeLedger(),
        runtime_status=None,
        started_at=0,
        response="answer",
        cancelled=False,
        starting_tool_keys=set(),
        starting_diff=_diff(),
        ending_diff=_diff(),
    )

    assert outcome.turn_id == "session-123456:turn:1"
    assert outcome.checkpoint_id == "session--0001"
    assert outcome.yields[0].kind is YieldKind.ANSWER


def test_interrupted_turn_never_claims_answer_yield() -> None:
    outcome = build_turn_outcome(
        session_id="session",
        outcome_ledger=OutcomeLedger(),
        runtime_status=None,
        started_at=0,
        response="partial answer",
        cancelled=True,
        starting_tool_keys=set(),
        starting_diff=_diff(),
        ending_diff=_diff(),
    )

    assert [item.kind for item in outcome.yields] == [YieldKind.INTERRUPTED]
    assert outcome.interrupted is True


def test_shell_tool_name_detection_is_narrow() -> None:
    assert is_shell_tool_name("functions:exec_command") is True
    assert is_shell_tool_name("run-command") is True
    assert is_shell_tool_name("delegate") is False
