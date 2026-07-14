import asyncio
import subprocess
from decimal import Decimal

import pytest

from amplifier_app_cli.ui.interaction_state import NeedsYouQueue, TrustState
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger, OutcomeYield
from amplifier_app_cli.ui.outcome_ledger import TurnOutcome, YieldKind
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui import session_commands
from amplifier_app_cli.ui.session_commands import SessionCommandService
from amplifier_app_cli.ui.task_status import TaskStatusTracker
from amplifier_app_cli.ui.transcript_blocks import CodeExcerptBlock
from amplifier_app_cli.commands.session import _select_history_messages


def _service(tmp_path):
    runtime = RuntimeStatusTracker("017954f1")
    runtime.seed_session_cost("0")
    runtime.consume(
        "llm:response",
        {
            "session_id": "017954f1",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_tokens": 800,
                "cost_usd": "0.12",
            },
        },
    )
    ledger = OutcomeLedger()
    ledger.record(
        TurnOutcome(
            "turn-1",
            "017954-0001",
            Decimal("0.12"),
            2.0,
            1200,
            80,
            (OutcomeYield(YieldKind.TESTS, "tests ✔"),),
        )
    )
    return SessionCommandService(
        session_id="017954f1",
        bundle_name="foundation",
        trust_state=TrustState(),
        outcome_ledger=ledger,
        needs_you=NeedsYouQueue(),
        runtime_status=runtime,
        task_tracker=TaskStatusTracker("017954f1"),
        cwd=tmp_path,
    )


def test_resume_history_selection_is_display_only_and_bounded() -> None:
    transcript = [
        {"role": "system", "content": "system"},
        *(
            {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
            for index in range(12)
        ),
        {"role": "tool", "content": "tool"},
    ]

    assert _select_history_messages(transcript, no_history=True) == []
    assert [message["content"] for message in _select_history_messages(transcript)] == [
        str(index) for index in range(2, 12)
    ]
    assert [
        message["content"]
        for message in _select_history_messages(transcript, max_messages=0)
    ] == [str(index) for index in range(12)]
    assert len(transcript) == 14


@pytest.mark.asyncio
async def test_ledger_context_and_rewind_are_backed_by_typed_state(tmp_path):
    service = _service(tmp_path)

    ledger = await service.execute("/ledger")
    context = await service.execute("/context")
    rewind = await service.execute("/rewind")

    assert "1 turns · $0.12 · 1 shipped" in ledger.text
    assert "cache hit 80%" in ledger.text
    assert "total 1,200" in context.text
    assert "017954-0001" in rewind.text


@pytest.mark.asyncio
async def test_permissions_selects_a_known_preset(tmp_path):
    service = _service(tmp_path)

    result = await service.execute("/permissions", "preset build")

    assert result.transient is True
    assert "Trust preset build" in result.text
    assert "auto read,test" in result.text


@pytest.mark.asyncio
async def test_permissions_edits_an_individual_trust_slot(tmp_path):
    service = _service(tmp_path)

    result = await service.execute("/permissions", "set write auto")

    assert result.transient is True
    assert "Trust preset custom" in result.text
    assert "auto read,write" in result.text


@pytest.mark.asyncio
async def test_review_returns_a_prompt_instead_of_claiming_work_happened(tmp_path):
    result = await _service(tmp_path).execute("/review", "session persistence")

    assert not result.text
    assert "Review session persistence" in result.prompt
    assert "Do not modify files" in result.prompt


@pytest.mark.asyncio
async def test_diff_uses_git_without_a_shell_and_bounds_output(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "app.py").write_text("before = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True
    )

    result = await _service(tmp_path).execute("/diff", "staged")

    assert "app.py" in result.text
    assert "1 file changed" in result.text
    assert not result.blocks


@pytest.mark.asyncio
async def test_diff_emits_a_typed_context_bounded_code_excerpt(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "app.py").write_text("before = 1\nafter = 2\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True
    )

    result = await _service(tmp_path).execute("/diff", "staged full")

    assert not result.text
    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert isinstance(block, CodeExcerptBlock)
    assert block.language == "diff"
    assert "@@" in block.code
    assert block.changed_lines


@pytest.mark.asyncio
async def test_diff_rejects_unknown_options(tmp_path):
    result = await _service(tmp_path).execute("/diff", "everything")

    assert result.text == "Usage: /diff [staged] [full]"


@pytest.mark.asyncio
async def test_diff_timeout_kills_and_reaps_process(tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.returncode = None
            self.killed = False
            self.waited = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.waited = True
            return self.returncode or 0

    process = FakeProcess()
    monkeypatch.setattr(
        session_commands.asyncio,
        "create_subprocess_exec",
        lambda *args, **kwargs: asyncio.sleep(0, result=process),
    )

    async def timeout(awaitable, timeout):
        awaitable.cancel()
        await asyncio.sleep(0)
        raise asyncio.TimeoutError

    monkeypatch.setattr(session_commands.asyncio, "wait_for", timeout)

    result = await _service(tmp_path).execute("/diff")

    assert result.text == "Could not read git diff: timed out"
    assert process.killed is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_improve_only_proposes_changes(tmp_path):
    result = await _service(tmp_path).execute("/improve")

    assert result.text.startswith("Improve report (proposal only)")


@pytest.mark.asyncio
async def test_answer_command_batches_deferred_decisions(tmp_path):
    service = _service(tmp_path)
    first = service._needs_you.defer("Use SQLite?", "storage")
    second = service._needs_you.defer("Ship today?", "release")

    result = await service.execute(
        "/answer", f"{first.decision_id}=yes; {second.decision_id}=not yet"
    )

    assert result.transient is True
    assert "2 decisions answered" in result.text
    assert len(service._needs_you.answered) == 2
