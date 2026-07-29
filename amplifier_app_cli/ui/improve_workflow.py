"""Evidence-backed, confirm-before-write session improvement workflow."""

from __future__ import annotations

import hashlib
import inspect
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import cast, Protocol

from .governance import DenialLog
from .improve_evidence import ApprovalEvidence, ImproveEvidence, McpServerEvidence
from .interaction_state import TrustState
from .mcp_commands import McpConfigError, McpConfigStore
from .outcome_ledger import OutcomeLedger
from .runtime_status import RuntimeStatusTracker

_MAX_EVIDENCE_ITEMS = 512
_MAX_VALUE_CHARS = 2_048
_PROMPT_THRESHOLD = 3
_MCP_MIN_SESSION_TURNS = 3
_MCP_EDIT = re.compile(r"^mcpServers\.([A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_SENSITIVE = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+"
)


class ImproveProposalKind(str, Enum):
    SKILL_CANDIDATE = "skill-candidate"
    MEMORY_DEDUP = "memory-dedup"
    MCP_RETIREMENT = "mcp-retirement"


class ImproveReportStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ConfigEdit:
    path: str
    value: bool | int | str


@dataclass(frozen=True, slots=True)
class ImproveProposal:
    proposal_id: str
    kind: ImproveProposalKind
    summary: str
    evidence: str
    edit: ConfigEdit | None = None


@dataclass(frozen=True, slots=True)
class ImproveReport:
    report_id: str
    proposals: tuple[ImproveProposal, ...]
    status: ImproveReportStatus = ImproveReportStatus.PENDING


class ImprovePersistence(Protocol):
    def __call__(self, edits: tuple[ConfigEdit, ...]) -> Awaitable[None] | None: ...


class _ConfiguratorPersistence(Protocol):
    def config_set(self, path: str, value: bool | int | str) -> object: ...

    def save(self, *, scope: str) -> object: ...


EvidenceSource = Callable[[], Awaitable[ImproveEvidence] | ImproveEvidence]


class ConfiguratorImprovePersistence:
    """Persist each validated edit through the configuration store that owns it."""

    def __init__(
        self,
        configurator: object,
        *,
        scope: str = "project",
        mcp_config_path: Path | None = None,
    ) -> None:
        if scope not in {"project", "global"}:
            raise ValueError("improve persistence scope must be project or global")
        if not callable(getattr(configurator, "config_set", None)) or not callable(
            getattr(configurator, "save", None)
        ):
            raise TypeError("configurator does not support config_set/save")
        self._configurator = cast(_ConfiguratorPersistence, configurator)
        self._scope = scope
        self._mcp_store = McpConfigStore(
            mcp_config_path or Path.cwd() / ".amplifier" / "mcp.json"
        )

    async def __call__(self, edits: tuple[ConfigEdit, ...]) -> None:
        for edit in edits:
            _validate_edit(edit)
        mcp_names = [
            match.group(1)
            for edit in edits
            if (match := _MCP_EDIT.fullmatch(edit.path)) is not None
        ]
        settings_edits = tuple(
            edit for edit in edits if _MCP_EDIT.fullmatch(edit.path) is None
        )
        if mcp_names:
            config = self._mcp_store.read()
            servers = config["mcpServers"]
            missing = [name for name in mcp_names if name not in servers]
            if missing:
                raise RuntimeError(
                    f"MCP server is no longer configured: {', '.join(missing)}"
                )
            for name in mcp_names:
                del servers[name]
            self._mcp_store.write(config)
        for edit in settings_edits:
            result = self._configurator.config_set(edit.path, edit.value)
            if inspect.isawaitable(result):
                await result
        if settings_edits:
            saved = self._configurator.save(scope=self._scope)
            if inspect.isawaitable(saved):
                await saved


class ImproveWorkflow:
    """Generate immutable proposals, then apply only an explicitly named report."""

    def __init__(
        self,
        *,
        outcome_ledger: OutcomeLedger,
        denial_log: DenialLog | None,
        runtime_status: RuntimeStatusTracker | None,
        trust_state: TrustState,
        evidence_source: EvidenceSource | None = None,
        persistence: ImprovePersistence | None = None,
    ) -> None:
        self._ledger = outcome_ledger
        self._denials = denial_log
        self._runtime = runtime_status
        self._trust = trust_state
        self._source = evidence_source or ImproveEvidence
        self._persistence = persistence
        self._reports: dict[str, ImproveReport] = {}
        self._active_report_id = ""

    async def execute(self, args: str = "") -> str:
        parts = args.strip().split()
        action = parts[0].lower() if parts else "inspect"
        if action in {"inspect", "report", "show"}:
            if len(parts) > 1:
                return "Usage: /improve [inspect|apply <report-id>|cancel [report-id]]"
            return await self._inspect()
        if action in {"apply", "confirm"}:
            if len(parts) != 2:
                return "Usage: /improve apply <report-id>"
            return await self._apply(parts[1])
        if action == "cancel":
            if len(parts) > 2:
                return "Usage: /improve cancel [report-id]"
            return self._cancel(parts[1] if len(parts) == 2 else "")
        return "Usage: /improve [inspect|apply <report-id>|cancel [report-id]]"

    async def _inspect(self) -> str:
        evidence = self._source()
        if inspect.isawaitable(evidence):
            evidence = await evidence
        if not isinstance(evidence, ImproveEvidence):
            raise TypeError("improve evidence source returned an invalid snapshot")
        proposals = self._proposals(evidence)
        report_id = _report_id(proposals, evidence)
        report = self._reports.get(report_id)
        if report is None:
            report = ImproveReport(report_id, proposals)
            self._reports[report_id] = report
        self._active_report_id = report_id
        return self._format_report(report, evidence)

    async def _apply(self, report_id: str) -> str:
        report = self._reports.get(_clean_report_id(report_id))
        if report is None:
            return "Unknown improve report. Run /improve inspect first."
        if report.status == ImproveReportStatus.APPLIED:
            return f"Improve report {report.report_id} was already applied; no changes made."
        if report.status == ImproveReportStatus.CANCELLED:
            return f"Improve report {report.report_id} was cancelled; no changes made."
        if not report.proposals:
            return f"Improve report {report.report_id} has no changes to apply."
        edits = tuple(
            proposal.edit for proposal in report.proposals if proposal.edit is not None
        )
        advisory_count = len(report.proposals) - len(edits)
        if not edits:
            finding_label = "finding" if advisory_count == 1 else "findings"
            verb = "remains" if advisory_count == 1 else "remain"
            return (
                f"Improve report {report.report_id} has no actionable changes to apply; "
                f"{advisory_count} advisory {finding_label} {verb} unchanged."
            )
        if self._persistence is None:
            return "Improve persistence is unavailable; no changes were made."
        for edit in edits:
            _validate_edit(edit)
        try:
            result = self._persistence(edits)
            if inspect.isawaitable(result):
                await result
        except (McpConfigError, OSError, RuntimeError, TypeError, ValueError) as error:
            return f"Could not apply improve report: {_single_line(error, 240)}"
        applied = ImproveReport(
            report.report_id, report.proposals, ImproveReportStatus.APPLIED
        )
        self._reports[report.report_id] = applied
        advisory = (
            f" · {advisory_count} advisory findings unchanged" if advisory_count else ""
        )
        return (
            f"Applied improve report {report.report_id} · {len(edits)} config edits"
            f"{advisory}."
        )

    def _cancel(self, report_id: str) -> str:
        target = _clean_report_id(report_id or self._active_report_id)
        report = self._reports.get(target)
        if report is None:
            return "No matching improve report to cancel."
        if report.status == ImproveReportStatus.APPLIED:
            return (
                f"Improve report {target} was already applied and cannot be cancelled."
            )
        if report.status == ImproveReportStatus.CANCELLED:
            return f"Improve report {target} is already cancelled."
        self._reports[target] = ImproveReport(
            report.report_id, report.proposals, ImproveReportStatus.CANCELLED
        )
        return f"Cancelled improve report {target}; no changes were made."

    def _proposals(self, evidence: ImproveEvidence) -> tuple[ImproveProposal, ...]:
        proposals: list[ImproveProposal] = []
        proposals.extend(self._skill_proposals(evidence.prompts))
        memory = self._memory_proposal(evidence.memory_entries)
        if memory is not None:
            proposals.append(memory)
        proposals.extend(self._mcp_proposals(evidence.mcp_servers))
        unique = {proposal.proposal_id: proposal for proposal in proposals}
        return tuple(unique[key] for key in sorted(unique))

    def _skill_proposals(self, prompts: Sequence[str]) -> tuple[ImproveProposal, ...]:
        patterns = Counter(
            pattern
            for prompt in prompts[-_MAX_EVIDENCE_ITEMS:]
            for pattern in [_prompt_pattern(prompt)]
            if pattern
        )
        result = []
        for pattern, count in sorted(patterns.items()):
            if count < _PROMPT_THRESHOLD:
                continue
            key = _slug(pattern)[:40]
            result.append(
                _proposal(
                    ImproveProposalKind.SKILL_CANDIDATE,
                    key,
                    f"Extract recurring prompt as skill candidate: {pattern[:80]}",
                    f"same sanitized pattern occurred {count} times; advisory only",
                )
            )
        return tuple(result)

    def _memory_proposal(self, entries: Sequence[str]) -> ImproveProposal | None:
        normalized = [
            clean
            for item in entries[-_MAX_EVIDENCE_ITEMS:]
            if (clean := _normalized_text(item))
        ]
        duplicates = sum(
            count - 1 for count in Counter(normalized).values() if count > 1
        )
        if duplicates < 1 or self._runtime is None:
            return None
        usage = self._runtime.telemetry_snapshot().session
        if usage.input_tokens <= 0:
            return None
        return _proposal(
            ImproveProposalKind.MEMORY_DEDUP,
            "session-memory",
            "Deduplicate repeated memory context",
            f"{duplicates} duplicate entries across {usage.input_tokens:,} input tokens; "
            "advisory only",
        )

    def _mcp_proposals(
        self, servers: Sequence[McpServerEvidence]
    ) -> tuple[ImproveProposal, ...]:
        if self._ledger.summary().turns < _MCP_MIN_SESSION_TURNS:
            return ()
        result = []
        for server in sorted(servers, key=lambda item: item.name):
            if server.calls or not server.config_bytes:
                continue
            key = _slug(server.name)
            result.append(
                _proposal(
                    ImproveProposalKind.MCP_RETIREMENT,
                    key,
                    f"Retire unused MCP server: {server.name}",
                    f"0 calls over {self._ledger.summary().turns} turns; "
                    f"{server.config_bytes:,} measured config bytes",
                    ConfigEdit(f"mcpServers.{server.name}", False),
                )
            )
        return tuple(result)

    def _format_report(self, report: ImproveReport, evidence: ImproveEvidence) -> str:
        summary = self._ledger.summary()
        usage = self._runtime.telemetry_snapshot().session if self._runtime else None
        cache = usage.cache_percent if usage else None
        lines = [
            f"Improve report (proposal only) · {report.report_id} · {report.status.value}",
            f"Evidence: {summary.turns} turns · {len(evidence.approvals)} approvals · "
            f"{self._denials.total_count if self._denials else 0} denials · "
            f"{usage.input_tokens if usage else 0:,} input tokens · "
            f"cache {cache if cache is not None else 0}% · trust {self._trust.active.name}",
        ]
        if report.proposals:
            lines.extend(
                f"{index}. {item.summary} ({item.evidence})"
                + (" [advisory]" if item.edit is None else " [config edit]")
                for index, item in enumerate(report.proposals, 1)
            )
            if report.status == ImproveReportStatus.APPLIED:
                lines.append(
                    "This report was already applied; no further changes made."
                )
                advisory_count = sum(item.edit is None for item in report.proposals)
                if advisory_count:
                    finding_label = "finding" if advisory_count == 1 else "findings"
                    verb = "was" if advisory_count == 1 else "were"
                    lines.append(
                        f"{advisory_count} advisory {finding_label} {verb} not written."
                    )
            elif report.status == ImproveReportStatus.CANCELLED:
                lines.append("This report is cancelled; no changes were made.")
            else:
                edit_count = sum(item.edit is not None for item in report.proposals)
                advisory_count = len(report.proposals) - edit_count
                lines.append(
                    f"Nothing changed. Run /improve apply {report.report_id} to confirm "
                    f"{edit_count} config edits, or /improve cancel {report.report_id}."
                )
                if advisory_count:
                    lines.append(
                        f"{advisory_count} advisory findings are never written automatically."
                    )
        else:
            lines.append("No evidence-backed configuration changes proposed.")
            lines.append("Nothing changed.")
        return "\n".join(lines)


def _proposal(
    kind: ImproveProposalKind,
    key: str,
    summary: str,
    evidence: str,
    edit: ConfigEdit | None = None,
) -> ImproveProposal:
    proposal_id = f"{kind.value}-{_slug(key)}"
    return ImproveProposal(
        proposal_id,
        kind,
        _single_line(summary, 180),
        _single_line(evidence, 180),
        edit,
    )


def _report_id(proposals: Sequence[ImproveProposal], evidence: ImproveEvidence) -> str:
    material = (
        "|".join(
            (
                f"{item.proposal_id}:{item.edit.path}:{item.edit.value!r}"
                if item.edit is not None
                else f"{item.proposal_id}:advisory"
            )
            for item in proposals
        )
        or f"empty:{len(evidence.approvals)}:{len(evidence.prompts)}"
    )
    return f"improve-{hashlib.sha256(material.encode()).hexdigest()[:10]}"


def _validate_edit(edit: ConfigEdit) -> None:
    if not isinstance(edit, ConfigEdit):
        raise TypeError("improve edit must be a ConfigEdit")
    if _MCP_EDIT.fullmatch(edit.path) is None:
        raise ValueError("improve edit path is not allowed")
    if isinstance(edit.value, str):
        if not edit.value.strip() or len(edit.value) > _MAX_VALUE_CHARS:
            raise ValueError("improve edit value is invalid")
        if _SENSITIVE.search(edit.value):
            raise ValueError("improve edit value may contain a secret")
    elif not isinstance(edit.value, (bool, int)):
        raise TypeError("improve edit value must be scalar")
    if edit.value is not False:
        raise ValueError("MCP retirement edit must disable the server")


def _prompt_pattern(prompt: str) -> str:
    text = _single_line(prompt, 240)
    if not text or text.startswith("/") or _SENSITIVE.search(text):
        return ""
    text = re.sub(r"\b\d+(?:\.\d+)*\b", "{n}", text.lower())
    text = re.sub(r"(?:\.?\.?/)?[\w.-]+(?:/[\w.-]+)+", "{path}", text)
    return text if len(text.split()) >= 3 else ""


def _normalized_text(value: str) -> str:
    return " ".join(value.lower().split())[:_MAX_VALUE_CHARS]


def _slug(value: object) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-_")
    return slug[:64] or "item"


def _clean_report_id(value: object) -> str:
    clean = _single_line(value, 80)
    return clean if re.fullmatch(r"improve-[a-f0-9]{10}", clean) else ""


def _single_line(value: object, limit: int) -> str:
    text = "".join(character for character in str(value) if ord(character) >= 32)
    return " ".join(text.split())[:limit]


__all__ = [
    "ApprovalEvidence",
    "ConfigEdit",
    "ConfiguratorImprovePersistence",
    "ImproveEvidence",
    "ImprovePersistence",
    "ImproveProposal",
    "ImproveProposalKind",
    "ImproveReport",
    "ImproveReportStatus",
    "ImproveWorkflow",
    "McpServerEvidence",
]
