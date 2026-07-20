from datetime import UTC
from datetime import datetime

import pytest

from amplifier_app_cli.ui.evidence_links import MAX_ANSWER_CHARS
from amplifier_app_cli.ui.evidence_links import MAX_TOOLS_PER_ANSWER
from amplifier_app_cli.ui.evidence_links import EvidenceKind
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.runtime_values import BoundedText
from amplifier_app_cli.ui.runtime_values import ToolActivitySnapshot
from amplifier_app_cli.ui.runtime_values import ToolActivityStatus


def _tool(
    tool_call_id: str,
    *,
    command: str = "",
    tool_name: str = "exec_command",
    status: ToolActivityStatus = ToolActivityStatus.SUCCEEDED,
    input_text: str = "",
    result: str = "",
) -> ToolActivitySnapshot:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    return ToolActivitySnapshot(
        tool_call_id=tool_call_id,
        session_id="session",
        tool_name=tool_name,
        status=status,
        command=command,
        summary=command,
        input=BoundedText(input_text, len(input_text), 1, False),
        result=BoundedText(result, len(result), 1, False),
        parallel_group_id="",
        started_at=now,
        completed_at=now if status != ToolActivityStatus.RUNNING else None,
        duration_seconds=1.2,
    )


def test_snapshot_is_zero_by_default_and_reveal_links_test_claim() -> None:
    model = EvidenceLinkModel()
    test_tool = _tool(
        "pytest-1", command="uv run pytest -q", result="1144 passed in 8.2s"
    )

    hidden = model.record(
        "answer-1", "The change is complete. All 1,144 tests passed.", [test_tool]
    )

    assert hidden.revealed is False
    assert hidden.annotated_answer == hidden.answer
    assert hidden.links == ()
    assert all(not claim.link_numbers for claim in hidden.claims)

    revealed = model.snapshot("answer-1", reveal=True)
    assert revealed is not None
    assert revealed.annotated_answer == (
        "The change is complete. All 1,144 tests passed.\u2009¹"
    )
    assert len(revealed.links) == 1
    assert revealed.links[0].marker == "¹"
    assert revealed.links[0].kind == EvidenceKind.TESTS
    assert revealed.links[0].tool_call_id == "pytest-1"
    assert model.resolve("answer-1", 1) is test_tool


def test_test_claim_needs_matching_terminal_test_evidence() -> None:
    model = EvidenceLinkModel()
    running = _tool(
        "pytest-running",
        command="pytest",
        status=ToolActivityStatus.RUNNING,
    )
    unrelated = _tool("list", command="ls", result="tests")
    mismatch = _tool("pytest-old", command="pytest", result="22 passed")

    model.record("answer", "42 tests passed.", [running, unrelated, mismatch])
    revealed = model.snapshot("answer", reveal=True)

    assert revealed is not None
    assert revealed.links == ()
    assert revealed.claims[0].kind == EvidenceKind.TESTS
    assert model.terminal_tools("answer") == (unrelated, mismatch)


def test_failed_test_claim_only_links_failed_test_run() -> None:
    model = EvidenceLinkModel()
    successful = _tool("green", command="pytest", result="10 passed")
    failed = _tool(
        "red",
        command="pytest",
        status=ToolActivityStatus.FAILED,
        result="2 failed, 8 passed",
    )

    model.record("answer", "2 tests failed.", [successful, failed])

    assert model.resolve("answer", 1) is failed


def test_named_test_command_does_not_link_a_different_test_run() -> None:
    model = EvidenceLinkModel()
    other_test = _tool("other", command="pytest tests/test_other.py", result="8 passed")

    model.record(
        "answer",
        "Ran `pytest tests/test_target.py`; 8 tests passed.",
        [other_test],
    )

    revealed = model.reveal("answer")
    assert revealed is not None
    assert revealed.links == ()


def test_file_claim_requires_exact_path_and_successful_mutation_tool() -> None:
    model = EvidenceLinkModel()
    read = _tool(
        "read",
        tool_name="read_file",
        input_text='{"path":"src/app.py"}',
    )
    wrong = _tool(
        "wrong",
        tool_name="apply_patch",
        input_text='{"path":"src/application.py"}',
    )
    edit = _tool(
        "edit",
        tool_name="apply_patch",
        input_text="*** Update File: src/app.py",
    )

    model.record("answer", "Updated `src/app.py`.", [read, wrong, edit])
    revealed = model.snapshot("answer", reveal=True)

    assert revealed is not None
    assert revealed.links[0].kind == EvidenceKind.FILE
    assert model.resolve("answer", 1) is edit


def test_multi_file_claim_is_unlinked_if_any_path_lacks_support() -> None:
    model = EvidenceLinkModel()
    edit = _tool(
        "edit",
        tool_name="apply_patch",
        input_text="*** Update File: src/app.py",
    )

    model.record("answer", "Updated `src/app.py` and `tests/test_app.py`.", [edit])
    revealed = model.snapshot("answer", reveal=True)

    assert revealed is not None
    assert revealed.claims[0].kind == EvidenceKind.FILE
    assert revealed.links == ()


def test_file_named_tests_is_not_misclassified_as_a_test_result() -> None:
    model = EvidenceLinkModel()
    edit = _tool(
        "edit",
        tool_name="apply_patch",
        input_text="*** Update File: tests/test_app.py",
    )
    pytest = _tool("pytest", command="pytest", result="10 passed")

    model.record("answer", "Updated `tests/test_app.py` successfully.", [edit, pytest])
    revealed = model.reveal("answer")

    assert revealed is not None
    assert revealed.claims[0].kind == EvidenceKind.FILE
    assert model.resolve("answer", 1) is edit


def test_mixed_file_and_test_assertion_is_not_partially_linked() -> None:
    model = EvidenceLinkModel()
    edit = _tool(
        "edit",
        tool_name="apply_patch",
        input_text="*** Update File: src/app.py",
    )
    pytest = _tool("pytest", command="pytest", result="10 passed")

    model.record("answer", "Updated `src/app.py` and all tests passed.", [edit, pytest])
    revealed = model.reveal("answer")

    assert revealed is not None
    assert revealed.claims[0].kind is None
    assert revealed.links == ()


def test_command_claim_requires_an_explicit_command_and_actual_execution() -> None:
    model = EvidenceLinkModel()
    ruff = _tool("ruff", command="uv run ruff check amplifier_app_cli tests")
    build = _tool(
        "build",
        command="npm run build",
        status=ToolActivityStatus.FAILED,
        result="build failed",
    )

    model.record(
        "answer",
        "Ran `ruff check` successfully. The build passed.",
        [ruff, build],
    )
    revealed = model.snapshot("answer", reveal=True)

    assert revealed is not None
    assert len(revealed.links) == 1
    assert revealed.links[0].kind == EvidenceKind.COMMAND
    assert model.resolve("answer", 1) is ruff
    assert revealed.claims[1].kind is None


def test_claim_splitter_ignores_fenced_code_and_inline_punctuation() -> None:
    model = EvidenceLinkModel()
    test_tool = _tool("tests", command="pytest", result="5 passed")
    answer = (
        "Use `value.with.period` here.\n```text\nTests passed.\n```\nFive tests passed."
    )

    model.record("answer", answer, [test_tool])
    snapshot = model.snapshot("answer", reveal=True)

    assert snapshot is not None
    assert [claim.text for claim in snapshot.claims] == [
        "Use `value.with.period` here.",
        "Five tests passed.",
    ]
    assert len(snapshot.links) == 1


def test_records_are_bounded_sanitized_and_keep_only_terminal_tools() -> None:
    model = EvidenceLinkModel(max_answers=2)
    tools = [_tool(f"tool-{index}", command="echo ok") for index in range(300)]
    duplicate = _tool("tool-299", command="echo replacement")
    model.record("one", "first", [])
    model.record(
        "two",
        "\x1b[31manswer\x1b[0m\u202e" + ("x" * MAX_ANSWER_CHARS),
        [*tools, duplicate],
    )
    model.record("three", "third", [])

    snapshot = model.snapshot("two")
    assert snapshot is not None
    assert snapshot.answer.startswith("answerx")
    assert "\x1b" not in snapshot.answer
    assert "\u202e" not in snapshot.answer
    assert snapshot.truncated is True
    assert 0 < len(snapshot.answer) <= MAX_ANSWER_CHARS
    assert len(model.terminal_tools("two")) == MAX_TOOLS_PER_ANSWER
    assert model.terminal_tools("two")[-1].command == "echo replacement"
    assert model.answer_ids == ("two", "three")


def test_invalid_or_missing_links_do_not_resolve() -> None:
    model = EvidenceLinkModel()
    model.record("answer", "No evidence claim.", [])

    assert model.resolve("missing", 1) is None
    assert model.resolve("answer", 0) is None
    assert model.resolve("answer", True) is None
    assert model.resolve("answer", "1") is None  # type: ignore[arg-type]
    assert model.resolve("answer", 99) is None


def test_record_validates_boundaries_and_duplicate_ids() -> None:
    model = EvidenceLinkModel()
    with pytest.raises(ValueError, match="answer_id"):
        model.record("\x1b[31m", "answer", [])
    with pytest.raises(TypeError, match="final_answer"):
        model.record("answer", 123, [])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ToolActivitySnapshot"):
        model.record("answer", "answer", [object()])  # type: ignore[list-item]

    model.record("answer", "answer", [])
    with pytest.raises(ValueError, match="already recorded"):
        model.record("answer", "replacement", [])

    with pytest.raises(ValueError, match="positive"):
        EvidenceLinkModel(max_answers=0)
    with pytest.raises(ValueError, match="positive"):
        EvidenceLinkModel(max_answers=1.5)  # type: ignore[arg-type]
