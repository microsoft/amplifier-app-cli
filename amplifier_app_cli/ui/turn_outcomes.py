"""Build bounded turn outcomes from runtime evidence."""

from __future__ import annotations

from decimal import Decimal
from time import monotonic

from .git_yield import GitDiffSnapshot
from .outcome_ledger import OutcomeLedger
from .outcome_ledger import OutcomeYield
from .outcome_ledger import TurnOutcome
from .outcome_ledger import YieldKind
from .runtime_status import RuntimeStatusTracker


def is_shell_tool_name(name: object) -> bool:
    """Return whether a tool activity represents a real shell command."""
    normalized = str(name).strip().lower().rsplit(":", maxsplit=1)[-1]
    normalized = normalized.replace("-", "_")
    return normalized in {
        "bash",
        "exec",
        "exec_command",
        "run_command",
        "shell",
    } or normalized.endswith(("_bash", "_exec_command", "_shell"))


def build_turn_outcome(
    *,
    session_id: str,
    outcome_ledger: OutcomeLedger,
    runtime_status: RuntimeStatusTracker | None,
    started_at: float,
    response: str,
    cancelled: bool,
    starting_tool_keys: set[tuple[str, str]],
    starting_diff: GitDiffSnapshot,
    ending_diff: GitDiffSnapshot,
    active_mode: str | None = None,
) -> TurnOutcome:
    """Classify one turn's bounded cost, usage, and concrete yield evidence."""
    elapsed = max(0.0, monotonic() - started_at)
    usage = runtime_status.telemetry_snapshot().turn if runtime_status else None
    cost = usage.cost_usd if usage and usage.cost_usd is not None else Decimal("0")
    tokens = usage.total_tokens if usage else 0
    cached_percent = usage.cache_percent if usage else None
    new_tools = (
        [
            tool
            for tool in runtime_status.tool_snapshot()
            if tool.terminal
            and (tool.session_id, tool.tool_call_id) not in starting_tool_keys
        ]
        if runtime_status is not None
        else []
    )

    yields: list[OutcomeYield] = []
    if cancelled:
        yields.append(OutcomeYield(YieldKind.INTERRUPTED, "interrupted"))
    else:
        diff_delta = ending_diff.delta_from(starting_diff)
        file_tools = [
            tool
            for tool in new_tools
            if any(
                marker in tool.tool_name.lower()
                for marker in ("write", "edit", "patch", "replace")
            )
        ]
        test_tools = [
            tool
            for tool in new_tools
            if any(
                marker in f"{tool.tool_name} {tool.command}".lower()
                for marker in ("pytest", "npm test", "uv run pytest", "test runner")
            )
        ]
        shell_tools = [tool for tool in new_tools if is_shell_tool_name(tool.tool_name)]
        if diff_delta is not None and diff_delta.files:
            suffix = "file" if diff_delta.files == 1 else "files"
            yields.append(OutcomeYield(YieldKind.FILES, f"{diff_delta.files} {suffix}"))
            if diff_delta.additions or diff_delta.deletions:
                yields.append(OutcomeYield(YieldKind.DIFF, diff_delta.diff_label))
        elif file_tools:
            suffix = "file" if len(file_tools) == 1 else "files"
            yields.append(OutcomeYield(YieldKind.FILES, f"{len(file_tools)} {suffix}"))
        if test_tools:
            passed = all(tool.status.value == "succeeded" for tool in test_tools)
            yields.append(
                OutcomeYield(YieldKind.TESTS, "tests ✔" if passed else "tests ✘")
            )
        if not yields and shell_tools:
            suffix = "cmd" if len(shell_tools) == 1 else "cmds"
            yields.append(
                OutcomeYield(YieldKind.COMMANDS, f"{len(shell_tools)} {suffix}")
            )
        if not yields and response.strip():
            label = "plan ready" if active_mode == "plan" else "answer"
            yields.append(OutcomeYield(YieldKind.ANSWER, label))

    turn_number = len(outcome_ledger.entries) + 1
    return TurnOutcome(
        turn_id=f"{session_id}:turn:{turn_number}",
        checkpoint_id=f"{session_id[:8]}-{turn_number:04d}",
        cost=cost,
        elapsed_seconds=elapsed,
        tokens=tokens,
        cached_percent=cached_percent,
        yields=tuple(yields[:3]),
        interrupted=cancelled,
    )


__all__ = ["build_turn_outcome", "is_shell_tool_name"]
