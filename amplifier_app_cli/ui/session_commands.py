"""Capability-backed commands used by the interactive session palette."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core_commands import CoreCommandService
from .command_catalog import BUILTIN_COMMAND_REGISTRY
from .command_registry import CommandOwner
from .interaction_state import NeedsYouQueue, PermissionDecision
from .interaction_state import PermissionSlot, TrustState
from .governance import DenialLog
from .improve_workflow import ImproveWorkflow
from .mcp_commands import McpCommandService
from .outcome_ledger import OutcomeLedger
from .runtime_status import RuntimeStatusTracker
from .task_status import TaskStatusTracker
from .transcript_blocks import AnswerBlock
from .transcript_blocks import DiffBlock
from .transcript_blocks import TranscriptBlock

_MAX_COMMAND_OUTPUT = 12_000
_MAX_DIFF_OUTPUT = 262_144
_MAX_DIFF_FILES = 20
_MAX_DIFF_FILE_LINES = 400


@dataclass(frozen=True, slots=True)
class SessionCommandResult:
    text: str = ""
    prompt: str = ""
    transient: bool = False
    blocks: tuple[TranscriptBlock, ...] = ()

    def __post_init__(self) -> None:
        if not self.text and not self.prompt and not self.blocks:
            raise ValueError("session command result cannot be empty")


class SessionCommandService:
    """Resolve palette commands from typed session state and safe subprocesses."""

    def __init__(
        self,
        *,
        session_id: str,
        bundle_name: str,
        trust_state: TrustState,
        outcome_ledger: OutcomeLedger,
        needs_you: NeedsYouQueue,
        runtime_status: RuntimeStatusTracker | None = None,
        task_tracker: TaskStatusTracker | None = None,
        denial_log: DenialLog | None = None,
        improve_workflow: ImproveWorkflow | None = None,
        cwd: Path | None = None,
        session: Any | None = None,
        coordinator: Any | None = None,
        core_commands: CoreCommandService | None = None,
        mcp_commands: McpCommandService | None = None,
    ) -> None:
        self._session_id = session_id
        self._bundle_name = bundle_name.removeprefix("bundle:") or "unknown"
        self._trust = trust_state
        self._ledger = outcome_ledger
        self._needs_you = needs_you
        self._runtime = runtime_status
        self._tasks = task_tracker
        self._denials = denial_log
        self._improve = improve_workflow or ImproveWorkflow(
            outcome_ledger=outcome_ledger,
            denial_log=denial_log,
            runtime_status=runtime_status,
            trust_state=trust_state,
        )
        self._cwd = (cwd or Path.cwd()).resolve()
        self._core = core_commands or CoreCommandService(
            session=session,
            coordinator=coordinator,
            session_id=session_id,
            bundle_name=bundle_name,
            cwd=self._cwd,
        )
        self._mcp = mcp_commands or McpCommandService(coordinator, self._cwd)

    @property
    def mcp_palette_prompts(self) -> tuple[tuple[str, str, str], ...]:
        return self._mcp.palette_prompts

    @property
    def model_names(self) -> tuple[str, ...]:
        return self._core.model_names

    def supports(self, command: str) -> bool:
        spec = BUILTIN_COMMAND_REGISTRY.resolve(command)
        return self._mcp.supports(command) or (
            spec is not None
            and spec.owner
            in {CommandOwner.CORE, CommandOwner.SESSION, CommandOwner.MCP}
        )

    async def execute(self, command: str, args: str = "") -> SessionCommandResult:
        spec = BUILTIN_COMMAND_REGISTRY.resolve(command)
        if spec is not None and spec.owner is CommandOwner.CORE:
            result = await self._core.execute(command, args)
            return SessionCommandResult(result.text, result.prompt, result.transient)
        if (
            spec is not None
            and spec.owner is CommandOwner.MCP
            or self._mcp.supports(command)
        ):
            result = await self._mcp.execute(command, args)
            return SessionCommandResult(result.text, result.prompt, result.transient)
        if spec is None or spec.owner is not CommandOwner.SESSION:
            return SessionCommandResult(f"Unsupported session command: {command}")
        handler = getattr(self, spec.handler)
        result = handler(args.strip())
        if asyncio.iscoroutine(result):
            return await result
        return result

    def _tasks_result(self, args: str) -> SessionCommandResult:
        if self._tasks is None:
            return SessionCommandResult("Agent lanes are unavailable in this terminal.")
        counts = self._tasks.counts()
        summary = self._tasks.footer_summary() or "no agent lanes yet"
        return SessionCommandResult(
            f"Agent lanes: {summary} · {counts.total} total",
            transient=True,
        )

    def _ledger_result(self, args: str) -> SessionCommandResult:
        summary = self._ledger.summary()
        cache = self._session_cache_percent()
        cheapest = (
            f"${summary.cheapest_shipped_cost:.2f}"
            if summary.cheapest_shipped_cost is not None
            else "n/a"
        )
        dearest = (
            f"${summary.dearest_shipped_cost:.2f}"
            if summary.dearest_shipped_cost is not None
            else "n/a"
        )
        return SessionCommandResult(
            "\n".join(
                (
                    f"Session ledger {self._session_id[:6]} · {self._bundle_name}",
                    f"{summary.turns} turns · ${summary.session_cost:.2f} · "
                    f"{summary.shipped_turns} shipped · "
                    f"{summary.answer_only_turns} answer-only · "
                    f"{summary.interrupted_turns} interrupted",
                    f"cheapest shipped {cheapest} · dearest {dearest} · "
                    f"cache hit {cache if cache is not None else 0}%",
                )
            )
        )

    def _permissions_result(self, args: str) -> SessionCommandResult:
        if not args or args == "show":
            return SessionCommandResult(
                f"Trust preset {self._trust.active.name}: "
                f"{self._trust.active.summary()}\n"
                "Usage: `/permissions preset <name>` | "
                "`/permissions set <slot> <auto|ask|block>`"
            )
        parts = args.split()
        if len(parts) == 2 and parts[0] == "preset":
            try:
                preset = self._trust.activate(parts[1])
            except ValueError as error:
                return SessionCommandResult(str(error))
            return SessionCommandResult(
                f"Trust preset {preset.name}: {preset.summary()}", transient=True
            )
        if len(parts) == 3 and parts[0] == "set":
            try:
                preset = self._trust.set_slot(
                    PermissionSlot(parts[1]), PermissionDecision(parts[2])
                )
            except ValueError:
                return SessionCommandResult(
                    "Unknown slot or decision. Slots: read, test, write, net, "
                    "spend, subagent, outside-project. Decisions: auto, ask, block."
                )
            return SessionCommandResult(
                f"Trust preset custom: {preset.summary()}", transient=True
            )
        return SessionCommandResult(
            "Usage: `/permissions [show|preset <name>|set <slot> <auto|ask|block>]`"
        )

    def _context_result(self, args: str) -> SessionCommandResult:
        if self._runtime is None:
            return SessionCommandResult("Runtime context telemetry is unavailable.")
        telemetry = self._runtime.telemetry_snapshot()
        usage = telemetry.session
        return SessionCommandResult(
            "\n".join(
                (
                    "Context usage",
                    f"input {usage.input_tokens:,} · output {usage.output_tokens:,} · "
                    f"total {usage.total_tokens:,}",
                    f"cache read {usage.cache_read_tokens:,} · "
                    f"cache hit {usage.cache_percent or 0}% · "
                    f"requests {usage.request_count}",
                )
            )
        )

    def _answer_result(self, args: str) -> SessionCommandResult:
        if not args:
            return SessionCommandResult(
                "Usage: /answer decision-1=yes; decision-2=not yet"
            )
        answers: dict[str, str] = {}
        for assignment in args.split(";"):
            decision_id, separator, answer = assignment.strip().partition("=")
            if not separator or not decision_id.strip() or not answer.strip():
                return SessionCommandResult(
                    "Usage: /answer decision-1=yes; decision-2=not yet"
                )
            answers[decision_id.strip()] = answer.strip()
        try:
            answered = self._needs_you.answer_many(answers)
        except (KeyError, ValueError) as error:
            return SessionCommandResult(str(error))
        suffix = "decision" if len(answered) == 1 else "decisions"
        return SessionCommandResult(
            f"{len(answered)} {suffix} answered · applies at next step boundary",
            transient=True,
        )

    def _rewind_result(self, args: str) -> SessionCommandResult:
        entries = self._ledger.entries
        if not entries:
            return SessionCommandResult("No rewind checkpoints yet.")
        lines = ["Rewind checkpoints"]
        for entry in entries[-8:]:
            yield_text = entry.yield_summary or "no recorded yield"
            lines.append(f"{entry.checkpoint_id} · ${entry.cost:.2f} · {yield_text}")
        lines.append("Select a checkpoint with ctrl-r to fork from that turn.")
        return SessionCommandResult("\n".join(lines))

    async def _diff_result(self, args: str) -> SessionCommandResult:
        options = frozenset(args.split())
        if not options <= {"staged", "full"}:
            return SessionCommandResult("Usage: /diff [staged] [full]")
        command = ["git", "diff", "--no-color"]
        command.append("--unified=3" if "full" in options else "--unified=2")
        if "staged" in options:
            command.insert(2, "--cached")
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=self._cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(
                    _read_stream_bounded(process.stdout, _MAX_DIFF_OUTPUT),
                    _read_stream_bounded(process.stderr, _MAX_COMMAND_OUTPUT),
                    process.wait(),
                ),
                timeout=8,
            )
        except asyncio.TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            return SessionCommandResult("Could not read git diff: timed out")
        except OSError as error:
            return SessionCommandResult(f"Could not read git diff: {error}")
        text = (stdout or stderr).decode("utf-8", errors="replace")
        text = text.strip()
        if process.returncode:
            return SessionCommandResult(text or "Could not read git diff.")
        if not text:
            return SessionCommandResult("Working tree has no diff.")
        blocks, dropped_files = parse_diff_blocks(text)
        if not blocks:
            return SessionCommandResult(text)
        result_blocks: tuple[TranscriptBlock, ...] = blocks
        if dropped_files:
            result_blocks += (
                AnswerBlock(
                    f"…and {dropped_files} more changed file(s) not shown "
                    f"(/diff shows at most {_MAX_DIFF_FILES} files)"
                ),
            )
        return SessionCommandResult(blocks=result_blocks)

    def _review_result(self, args: str) -> SessionCommandResult:
        scope = args or "the current working tree"
        return SessionCommandResult(
            prompt=(
                f"Review {scope}. Lead with concrete bugs, regressions, security "
                "risks, and missing tests. Do not modify files."
            )
        )

    def _doctor_result(self, args: str) -> SessionCommandResult:
        checks = (
            ("runtime telemetry", self._runtime is not None),
            ("task hooks", self._tasks is not None),
            ("outcome ledger", True),
            ("trust state", True),
            ("governance", self._denials is not None),
        )
        lines = ["Amplifier doctor"]
        lines.extend(f"{'✔' if ready else '✘'} {label}" for label, ready in checks)
        return SessionCommandResult("\n".join(lines))

    async def _improve_result(self, args: str) -> SessionCommandResult:
        return SessionCommandResult(await self._improve.execute(args))

    def _session_cache_percent(self) -> int | None:
        if self._runtime is None:
            return None
        return self._runtime.telemetry_snapshot().session.cache_percent


def parse_diff_blocks(diff_text: str) -> tuple[tuple[DiffBlock, ...], int]:
    """Parse ``git diff`` output into bounded per-file ``DiffBlock``s.

    Returns the parsed blocks plus how many changed files were dropped by the
    ``_MAX_DIFF_FILES`` cap. Per-file bodies are capped at
    ``_MAX_DIFF_FILE_LINES`` lines with an inline accounting note.
    """
    chunks: list[list[str]] = []
    current: list[str] | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current = [line]
            chunks.append(current)
        elif current is not None:
            current.append(line)
    blocks = tuple(
        block
        for chunk in chunks[:_MAX_DIFF_FILES]
        if (block := _diff_block_from_chunk(chunk)) is not None
    )
    dropped = max(0, len(chunks) - _MAX_DIFF_FILES)
    return blocks, dropped


def _diff_block_from_chunk(lines: list[str]) -> DiffBlock | None:
    """Build one ``DiffBlock`` from a single ``diff --git`` file chunk."""
    old_path: str | None = None
    new_path: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    binary = False
    body_start = len(lines)
    for index, line in enumerate(lines):
        if line.startswith("@@"):
            body_start = index
            break
        if line.startswith("--- "):
            old_path = _strip_diff_path(line[4:])
        elif line.startswith("+++ "):
            new_path = _strip_diff_path(line[4:])
        elif line.startswith("rename from "):
            rename_from = line.removeprefix("rename from ").strip()
        elif line.startswith("rename to "):
            rename_to = line.removeprefix("rename to ").strip()
        elif line.startswith("Binary files "):
            binary = True
    move_path: str | None = None
    if rename_from and rename_to:
        path, move_path = rename_from, rename_to
    else:
        path = new_path or old_path or _path_from_git_header(lines[0])
    if path is None:
        return None
    body = lines[body_start:]
    added = sum(
        1 for line in body if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in body if line.startswith("-") and not line.startswith("---")
    )
    if binary and not body:
        body = ["(binary file · no text diff)"]
    if not body:
        body = ["(no content changes)"]
    if len(body) > _MAX_DIFF_FILE_LINES:
        kept = body[: _MAX_DIFF_FILE_LINES - 1]
        body = [*kept, f"… +{len(body) - len(kept)} more diff lines not shown"]
    return DiffBlock(
        path=path,
        diff_text="\n".join(body),
        added=added,
        removed=removed,
        move_path=move_path,
    )


def _strip_diff_path(raw: str) -> str | None:
    """Normalize a ``---``/``+++`` header path; ``/dev/null`` becomes None."""
    path = raw.split("\t", 1)[0].strip().strip('"')
    if not path or path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path or None


def _path_from_git_header(header: str) -> str | None:
    """Recover the file path from a ``diff --git a/x b/y`` header line."""
    _, separator, path = header.partition(" b/")
    return path.strip().strip('"') or None if separator else None


async def _read_stream_bounded(
    stream: asyncio.StreamReader,
    limit: int,
) -> bytes:
    """Drain a subprocess stream while retaining at most ``limit`` bytes."""
    retained = bytearray()
    while chunk := await stream.read(8_192):
        remaining = limit - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
    return bytes(retained)


__all__ = ["SessionCommandResult", "SessionCommandService", "parse_diff_blocks"]
