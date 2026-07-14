from decimal import Decimal
import json
import re
from unittest.mock import MagicMock

import pytest

from amplifier_app_cli.ui.improve_evidence import ApprovalEvidence, ImproveEvidence
from amplifier_app_cli.ui.improve_evidence import McpServerEvidence
from amplifier_app_cli.ui.improve_evidence import RuntimeImproveEvidenceSource
from amplifier_app_cli.ui.improve_workflow import ConfigEdit
from amplifier_app_cli.ui.improve_workflow import ConfiguratorImprovePersistence
from amplifier_app_cli.ui.improve_workflow import ImproveWorkflow
from amplifier_app_cli.ui.governance import DenialLog
from amplifier_app_cli.ui.interaction_state import TrustState
from amplifier_app_cli.ui.mcp_commands import McpCommandService
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger, OutcomeYield
from amplifier_app_cli.ui.outcome_ledger import TurnOutcome, YieldKind
from amplifier_app_cli.ui.runtime_status import RuntimeStatusTracker
from amplifier_app_cli.ui.safety_classifier import ActionRequest, CapabilityClass


def _ledger(turns: int = 3) -> OutcomeLedger:
    ledger = OutcomeLedger()
    for index in range(turns):
        ledger.record(
            TurnOutcome(
                f"turn-{index}",
                f"checkpoint-{index}",
                Decimal("0.10"),
                1.0,
                100,
                40,
                (OutcomeYield(YieldKind.ANSWER, "answer"),),
            )
        )
    return ledger


def _runtime() -> RuntimeStatusTracker:
    runtime = RuntimeStatusTracker("session")
    runtime.consume(
        "llm:response",
        {
            "session_id": "session",
            "usage": {
                "input_tokens": 4_000,
                "output_tokens": 100,
                "cache_read_tokens": 1_000,
            },
        },
    )
    return runtime


def _evidence() -> ImproveEvidence:
    approvals = tuple(
        ApprovalEvidence("Allow shell: git status?", "Allow once") for _ in range(3)
    )
    return ImproveEvidence(
        approvals=approvals,
        prompts=(
            "Review module 1 for regressions",
            "Review module 2 for regressions",
            "Review module 3 for regressions",
        ),
        memory_entries=("Project uses pytest", "Project uses pytest"),
        mcp_servers=(McpServerEvidence("github", 1_200, 0),),
    )


def _workflow(*, persistence=None, evidence=None, denial_log=None) -> ImproveWorkflow:
    return ImproveWorkflow(
        outcome_ledger=_ledger(),
        denial_log=denial_log,
        runtime_status=_runtime(),
        trust_state=TrustState(),
        evidence_source=lambda: evidence or _evidence(),
        persistence=persistence,
    )


def _report_id(text: str) -> str:
    match = re.search(r"improve-[a-f0-9]{10}", text)
    assert match is not None
    return match.group(0)


@pytest.mark.asyncio
async def test_report_distinguishes_actionable_and_advisory_findings() -> None:
    persisted = []
    workflow = _workflow(persistence=lambda edits: persisted.append(edits))

    report = await workflow.execute("inspect")

    assert "Improve report (proposal only)" in report
    assert "read-only command" not in report
    assert "Extract recurring prompt as skill candidate" in report
    assert "Deduplicate repeated memory context" in report
    assert "Retire unused MCP server: github" in report
    assert report.count("[advisory]") == 2
    assert report.count("[config edit]") == 1
    assert "2 advisory findings are never written automatically" in report
    assert "Nothing changed" in report
    assert persisted == []


@pytest.mark.asyncio
async def test_explicit_apply_persists_once_and_repeated_apply_is_idempotent() -> None:
    persisted = []
    workflow = _workflow(persistence=lambda edits: persisted.append(edits))
    report_id = _report_id(await workflow.execute("report"))

    applied = await workflow.execute(f"apply {report_id}")
    repeated = await workflow.execute(f"confirm {report_id}")

    assert "Applied improve report" in applied
    assert len(persisted) == 1
    assert persisted[0] == (ConfigEdit("mcpServers.github", False),)
    assert "already applied" in repeated
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_cancel_prevents_later_apply_and_is_idempotent() -> None:
    persisted = []
    workflow = _workflow(persistence=lambda edits: persisted.append(edits))
    report_id = _report_id(await workflow.execute())

    cancelled = await workflow.execute(f"cancel {report_id}")
    repeated = await workflow.execute(f"cancel {report_id}")
    apply_after_cancel = await workflow.execute(f"apply {report_id}")

    assert "no changes were made" in cancelled
    assert "already cancelled" in repeated
    assert "was cancelled" in apply_after_cancel
    assert "cancelled; no changes were made" in await workflow.execute("inspect")
    assert persisted == []


@pytest.mark.asyncio
async def test_unavailable_persistence_keeps_report_pending() -> None:
    workflow = _workflow()
    report_id = _report_id(await workflow.execute())

    first = await workflow.execute(f"apply {report_id}")
    second = await workflow.execute(f"apply {report_id}")

    assert "persistence is unavailable" in first
    assert "persistence is unavailable" in second


@pytest.mark.asyncio
async def test_advisory_only_report_never_calls_persistence() -> None:
    persisted = []
    evidence = ImproveEvidence(
        prompts=(
            "Review module 1 for regressions",
            "Review module 2 for regressions",
            "Review module 3 for regressions",
        )
    )
    workflow = _workflow(
        persistence=lambda edits: persisted.append(edits), evidence=evidence
    )
    report_id = _report_id(await workflow.execute("inspect"))

    result = await workflow.execute(f"apply {report_id}")

    assert "no actionable changes" in result
    assert "1 advisory finding remains unchanged" in result
    assert persisted == []


@pytest.mark.asyncio
async def test_repeated_inspect_deduplicates_the_report_and_proposals() -> None:
    workflow = _workflow()

    first = await workflow.execute("inspect")
    second = await workflow.execute("inspect")

    assert _report_id(first) == _report_id(second)
    assert second.count("Extract recurring prompt") == 1
    assert second.count("Deduplicate repeated memory") == 1
    assert second.count("Retire unused MCP") == 1


@pytest.mark.asyncio
async def test_unsafe_or_unproven_patterns_are_not_proposed() -> None:
    evidence = ImproveEvidence(
        approvals=tuple(
            ApprovalEvidence("Allow shell: git status; rm -rf .?", "Allow once")
            for _ in range(5)
        ),
        prompts=(
            "API_TOKEN=top-secret do the deployment",
            "API_TOKEN=top-secret do the deployment",
            "API_TOKEN=top-secret do the deployment",
        ),
        memory_entries=("", ""),
        mcp_servers=(McpServerEvidence("github", 0, 0),),
    )

    report = await _workflow(evidence=evidence).execute()

    assert "No evidence-backed configuration changes proposed" in report


@pytest.mark.asyncio
async def test_observed_mcp_call_prevents_retirement_proposal() -> None:
    evidence = ImproveEvidence(mcp_servers=(McpServerEvidence("github", 64, 1),))

    report = await _workflow(evidence=evidence).execute()

    assert "Retire unused MCP server" not in report
    assert "No evidence-backed configuration changes proposed" in report


@pytest.mark.asyncio
async def test_read_only_allowlist_is_not_proposed_without_a_runtime_consumer() -> None:
    denials = DenialLog()
    denials.record_denial(
        ActionRequest("request-1", CapabilityClass.SHELL, "git status"),
        "outside user authorization",
    )

    report = await _workflow(denial_log=denials).execute()

    assert "1 denials" in report
    assert "read-only command" not in report
    assert "Extract recurring prompt" in report


@pytest.mark.asyncio
async def test_configurator_adapter_retires_server_from_project_mcp_store(
    tmp_path,
) -> None:
    configurator = MagicMock()
    path = tmp_path / ".amplifier" / "mcp.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "uvx", "args": ["github-mcp"]},
                    "docs": {"url": "https://example.test/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    persistence = ConfiguratorImprovePersistence(configurator, mcp_config_path=path)

    await persistence((ConfigEdit("mcpServers.github", False),))

    assert set(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]) == {"docs"}
    configurator.config_set.assert_not_called()
    configurator.save.assert_not_called()


@pytest.mark.asyncio
async def test_configurator_adapter_rejects_unapproved_paths_before_mutation() -> None:
    configurator = MagicMock()
    persistence = ConfiguratorImprovePersistence(configurator)

    with pytest.raises(ValueError, match="not allowed"):
        await persistence((ConfigEdit("providers.openai.api_key", "secret"),))

    with pytest.raises(ValueError, match="not allowed"):
        await persistence((ConfigEdit("mcpServers.github.enabled", False),))

    configurator.config_set.assert_not_called()
    configurator.save.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_evidence_reads_context_approval_and_measured_mcp_usage(
    tmp_path,
) -> None:
    runtime = RuntimeStatusTracker("session")
    runtime.consume(
        "tool:pre",
        {
            "session_id": "session",
            "tool_call_id": "call-1",
            "tool_name": "mcp__github__issues",
            "tool_input": {},
        },
    )
    messages = [
        {"role": "user", "content": "Review module 1 for regressions"},
        {"role": "memory", "content": "Use pytest"},
    ]
    history = [ApprovalEvidence("Allow shell: git status?", "Allow once")]
    path = tmp_path / ".amplifier" / "mcp.json"
    path.parent.mkdir()
    server_config = {"command": "uvx", "args": ["github-mcp"]}
    path.write_text(
        json.dumps({"mcpServers": {"github": server_config}}), encoding="utf-8"
    )
    source = RuntimeImproveEvidenceSource(
        context_messages=lambda: _async_value(messages),
        approval_history=lambda: history,
        config={"mcp": {"servers": {"ignored": {"context_tokens": 999_999}}}},
        runtime_status=runtime,
        mcp_config_path=path,
    )

    evidence = await source()

    assert evidence.prompts == ("Review module 1 for regressions",)
    assert evidence.memory_entries == ("Use pytest",)
    assert evidence.approvals == tuple(history)
    measured_bytes = len(
        json.dumps(
            {"github": server_config},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    )
    assert evidence.mcp_servers == (McpServerEvidence("github", measured_bytes, 1),)


@pytest.mark.asyncio
async def test_report_confirm_apply_changes_the_same_store_used_by_mcp_commands(
    tmp_path,
) -> None:
    path = tmp_path / ".amplifier" / "mcp.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "uvx", "args": ["github-mcp"]},
                    "docs": {"command": "uvx", "args": ["docs-mcp"]},
                }
            }
        ),
        encoding="utf-8",
    )
    configurator = MagicMock()
    workflow = _workflow(
        evidence=ImproveEvidence(mcp_servers=(McpServerEvidence("github", 48, 0),)),
        persistence=ConfiguratorImprovePersistence(configurator, mcp_config_path=path),
    )

    report = await workflow.execute("inspect")
    before = json.loads(path.read_text(encoding="utf-8"))
    applied = await workflow.execute(f"apply {_report_id(report)}")
    listed = await McpCommandService(None, tmp_path).execute("/mcp", "list")

    assert set(before["mcpServers"]) == {"github", "docs"}
    assert "Applied improve report" in applied
    assert set(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]) == {"docs"}
    assert "docs · configured command" in listed.text
    assert "github · configured" not in listed.text
    configurator.config_set.assert_not_called()
    configurator.save.assert_not_called()


async def _async_value(value):
    return value
