"""Restore cumulative session cost without mutable fork ancestry."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from amplifier_foundation import sanitize_message
from amplifier_foundation.session import count_turns, get_turn_boundaries, slice_to_turn
from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.messages import is_real_user_message

logger = logging.getLogger(__name__)

SESSION_COST_CHANNEL = "session.cost"
_LLM_RESPONSE_EVENT = "llm:response"
_PROMPT_SUBMIT_EVENT = "prompt:submit"
_RESTORED_LINEAGE_MARKER = "amplifier_app_cli.restored_fork_cost"
_BOUNDARY_KEY = "fork_cost_boundary"
_BOUNDARY_VERSION = 1
_MAX_BOUNDARY_TURNS = 4_096
_MAX_BOUNDARY_BYTES = 64 * 1024


@dataclass(frozen=True)
class ForkLineageCost:
    """The restored fork subtotal and reasons that inherited evidence is incomplete."""

    total: Decimal
    diagnostics: tuple[str, ...]

    @property
    def incomplete(self) -> bool:
        return bool(self.diagnostics)


@dataclass(frozen=True)
class _CaptureCosts:
    by_turn: dict[int, Decimal]
    diagnostics: tuple[str, ...]
    final_fence_line: int | None
    warnings: tuple[str, ...] = ()


def ci_events_path(session_dir: Path) -> Path:
    """Return the only CI capture selected by the active relocation policy."""
    raw_root = os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", "").strip()
    root = Path(raw_root).expanduser() if raw_root and "${" not in raw_root else None
    if root is not None and root.is_absolute():
        project_slug = session_dir.parent.parent.name
        capture_dir = root / project_slug / "sessions" / session_dir.name
        return SessionHistoryStore(capture_dir).events_path
    return SessionHistoryStore(session_dir).events_path


def session_events_path(session_dir: Path) -> Path:
    """Resolve CI relocation before the ordinary-session native-log fallback."""
    events_path = ci_events_path(session_dir)
    return events_path if events_path.exists() else session_dir / "events.jsonl"


def _decimal(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def sum_prior_cost_usd(events_path: Path, *, session_id: str | None = None) -> Decimal | None:
    """Read ordinary-session cost through Foundation's streaming CI reader."""
    session_dir = events_path.parent
    if session_dir.name == "context-intelligence":
        session_dir = session_dir.parent
    history = SessionHistoryStore(session_dir, events_path=events_path, session_id=session_id)
    total: Decimal | None = None
    for event in history.iter_events():
        if event.get("event") != _LLM_RESPONSE_EVENT:
            continue
        usage = event["data"].get("usage")
        amount = _decimal(usage.get("cost_usd") if isinstance(usage, dict) else None)
        if amount is not None:
            total = (total or Decimal("0")) + amount
    return total


def _add(diagnostics: set[str], code: str) -> None:
    diagnostics.add(code)


def _capture_stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


@dataclass(frozen=True)
class _NativeTurn:
    """One Foundation turn and its optional CI-prompt anchor."""

    number: int
    message_index: int
    prompt: str | None
    unsupported: bool


def _result_ids(message: dict[str, Any]) -> set[str]:
    """Match Foundation history's persisted tool-result identity rules."""
    found: set[str] = set()
    if isinstance(message.get("tool_call_id"), str):
        found.add(message["tool_call_id"])
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in (
                "tool_result",
                "function_call_output",
            ):
                continue
            identity = (
                block.get("tool_use_id")
                or block.get("tool_call_id")
                or block.get("call_id")
                or block.get("id")
            )
            if isinstance(identity, str):
                found.add(identity)
    return found


def _prompt_text(message: dict[str, Any]) -> str | None:
    """Extract exactly the prompt text emitted by supported live prompt builders."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return None


def _native_turns(messages: list[dict[str, Any]]) -> list[_NativeTurn]:
    """Project every Foundation user turn onto optional real-human prompt anchors."""
    turns: list[_NativeTurn] = []
    for number, message_index in enumerate(get_turn_boundaries(messages), start=1):
        message = messages[message_index]
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        is_anchor = (
            is_real_user_message(message)
            and not metadata.get("ephemeral")
            and not _result_ids(message)
        )
        prompt = _prompt_text(message) if is_anchor else None
        turns.append(_NativeTurn(number, message_index, prompt, is_anchor and prompt is None))
    return turns


def _canonical_saved_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Mirror SessionStore's transcript persistence before hashing a prefix."""
    canonical: list[dict[str, Any]] = []
    try:
        for message in messages:
            if not isinstance(message, dict):
                return None
            if message.get("role") in ("system", "developer"):
                continue
            canonical.append(sanitize_message(message))
    except Exception:
        return None
    return canonical


def _prefix_fingerprint(messages: list[dict[str, Any]], turns: int) -> str | None:
    """Hash Foundation's canonical native-turn prefix without retaining it."""
    canonical = _canonical_saved_messages(messages)
    if canonical is None or turns < 0 or turns > count_turns(canonical):
        return None
    try:
        prefix = (
            []
            if turns == 0
            else slice_to_turn(canonical, turns, handle_orphaned_tools="complete")
        )
        encoded = json.dumps(
            prefix, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _history_diagnostics(history: SessionHistoryStore, owner: str) -> set[str]:
    return {f"ci_{item.code}:{owner}" for item in history.diagnostics}


def _foreign_owner_cost_exists(events_path: Path, owner: str) -> bool:
    """Report foreign LLM cost rows Foundation intentionally filters out."""
    try:
        stream = events_path.open("rb")
    except OSError:
        return False
    with stream:
        for raw in stream:
            try:
                record = json.loads(raw)
            except (TypeError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or record.get("event") != _LLM_RESPONSE_EVENT:
                continue
            data = record.get("data")
            if not isinstance(data, dict):
                continue
            identity = data.get("session_id") or record.get("session_id")
            if isinstance(identity, str) and identity and identity != owner:
                usage = data.get("usage")
                if isinstance(usage, dict) and "cost_usd" in usage:
                    return True
    return False


def _scan_owner_turn_costs(
    session_dir: Path,
    owner: str,
    messages: list[dict[str, Any]],
    required_turns: range,
    *,
    inherited_native_turn_count: int,
) -> _CaptureCosts:
    """Associate owner costs with real prompt anchors in native turn space.

    Foundation defines every ``role=user`` row as a turn. Only non-ephemeral,
    non-tool-result human rows can be CI prompt anchors. Injected rows consume a
    native turn but carry the preceding anchored cost rather than inventing a
    submit, response, or fence of their own.
    """
    events_path = ci_events_path(session_dir)
    diagnostics: set[str] = set()
    native = _native_turns(messages)
    required = set(required_turns)
    if not required:
        return _CaptureCosts({}, (), None)
    if max(required) > len(native) or inherited_native_turn_count < 0:
        return _CaptureCosts({}, (f"invalid_fork_turn:{owner}",), None)

    native_by_number = {turn.number: turn for turn in native}
    warnings: set[str] = set()
    for turn in required:
        item = native_by_number[turn]
        if item.unsupported:
            _add(diagnostics, f"unsupported_prompt_anchor:{owner}:{turn}")
        if item.prompt is None and _non_anchor_has_assistant_output(messages, item, native):
            warnings.add(f"unmapped_non_anchor_output:{owner}:{turn}")
    if diagnostics:
        return _CaptureCosts({}, tuple(sorted(diagnostics)), None, tuple(sorted(warnings)))
    if not events_path.is_file():
        return _CaptureCosts({}, (f"missing_ci_capture:{owner}",), None, tuple(sorted(warnings)))

    owner_anchors = [
        turn for turn in native if turn.number > inherited_native_turn_count and turn.prompt is not None
    ]
    expected = [turn.prompt for turn in owner_anchors]
    target_anchors = [
        turn for turn in owner_anchors if turn.number in required
    ]
    before = _capture_stamp(events_path)
    event_prompts: list[str] = []
    first = SessionHistoryStore(session_dir, events_path=events_path, session_id=owner)
    for event in first.iter_events():
        if event.get("session_id") != owner or event.get("event") != _PROMPT_SUBMIT_EVENT:
            continue
        data = event.get("data")
        prompt = data.get("prompt") if isinstance(data, dict) else None
        if not isinstance(prompt, str):
            _add(diagnostics, f"invalid_prompt_submit:{owner}")
            continue
        event_prompts.append(prompt)
    diagnostics.update(_history_diagnostics(first, owner))
    exact_sequence = not diagnostics and event_prompts == expected
    transcript_counts = Counter(expected)
    event_counts = Counter(event_prompts)
    unique_turn_by_prompt = {
        turn.prompt: turn.number
        for turn in target_anchors
        if transcript_counts[turn.prompt] == 1 and event_counts[turn.prompt] == 1
    }

    costs = {turn: Decimal("0") for turn in required}
    submits: set[int] = set()
    responses: set[int] = set()
    current_turn: int | None = None
    sequence_index = 0
    final_fence_line: int | None = None
    second = SessionHistoryStore(session_dir, events_path=events_path, session_id=owner)
    for event in second.iter_events():
        if event.get("session_id") != owner:
            continue
        if event.get("event") == _PROMPT_SUBMIT_EVENT:
            data = event.get("data")
            prompt = data.get("prompt") if isinstance(data, dict) else None
            if exact_sequence and sequence_index < len(owner_anchors):
                current_turn = owner_anchors[sequence_index].number
            else:
                current_turn = unique_turn_by_prompt.get(prompt) if isinstance(prompt, str) else None
            sequence_index += 1
            if current_turn in required:
                submits.add(current_turn)
            continue
        if event.get("event") != _LLM_RESPONSE_EVENT or current_turn not in required:
            continue
        data = event.get("data")
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict) or "cost_usd" not in usage:
            _add(diagnostics, f"missing_cost:{owner}")
            continue
        amount = _decimal(usage["cost_usd"])
        if amount is None:
            _add(diagnostics, f"invalid_cost:{owner}")
            continue
        costs[current_turn] += amount
        responses.add(current_turn)
        final_fence_line = event.get("line") if isinstance(event.get("line"), int) else final_fence_line
    diagnostics.update(_history_diagnostics(second, owner))
    if _foreign_owner_cost_exists(events_path, owner):
        _add(diagnostics, f"foreign_cost:{owner}")
    if before != _capture_stamp(events_path):
        _add(diagnostics, f"unstable_ci_capture:{owner}")
    for turn in required:
        if native_by_number[turn].prompt is None:
            continue
        if turn not in submits:
            _add(diagnostics, f"missing_prompt_fence:{owner}:{turn}")
        elif turn not in responses:
            _add(diagnostics, f"missing_response_cost:{owner}:{turn}")
    return _CaptureCosts(costs, tuple(sorted(diagnostics)), final_fence_line, tuple(sorted(warnings)))


def _non_anchor_has_assistant_output(
    messages: list[dict[str, Any]], turn: _NativeTurn, native: list[_NativeTurn]
) -> bool:
    """Identify output after an injected turn that cannot prove a CI mapping."""
    next_index = next(
        (item.message_index for item in native if item.number == turn.number + 1),
        len(messages),
    )
    return any(message.get("role") == "assistant" for message in messages[turn.message_index + 1 : next_index])


def _unavailable_boundary(owner: str, *reasons: str) -> dict[str, Any]:
    return {
        "version": _BOUNDARY_VERSION,
        "status": "unavailable",
        "inherited_turn_count": 0,
        "cumulative_cost_usd_by_turn": [],
        "prefix_fingerprint": None,
        "provenance": {"owner": owner, "fence": "unavailable"},
        "reasons": sorted(set(reasons)) or ["unavailable"],
    }


def _verified_boundary(metadata: dict[str, Any]) -> tuple[list[Decimal] | None, int, tuple[str, ...]]:
    """Validate the persisted schema of an immutable cost projection."""
    raw = metadata.get(_BOUNDARY_KEY)
    if not isinstance(raw, dict):
        return None, 0, ("missing_fork_cost_boundary",)
    if raw.get("version") != _BOUNDARY_VERSION:
        return None, 0, ("unsupported_fork_cost_boundary",)
    try:
        encoded_size = len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        return None, 0, ("invalid_fork_cost_boundary",)
    if encoded_size > _MAX_BOUNDARY_BYTES:
        return None, 0, ("oversized_fork_cost_boundary",)
    if raw.get("status") != "verified":
        reasons = raw.get("reasons")
        stable = tuple(str(item) for item in reasons) if isinstance(reasons, list) else ()
        return None, 0, ("unavailable_fork_cost_boundary", *stable)
    turns = raw.get("inherited_turn_count")
    values = raw.get("cumulative_cost_usd_by_turn")
    fingerprint = raw.get("prefix_fingerprint")
    if (
        type(turns) is not int
        or turns < 0
        or turns > _MAX_BOUNDARY_TURNS
        or not isinstance(values, list)
        or len(values) != turns
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
    ):
        return None, 0, ("invalid_fork_cost_boundary",)
    totals: list[Decimal] = []
    prior = Decimal("0")
    for value in values:
        amount = _decimal(value)
        if amount is None or amount < prior:
            return None, 0, ("invalid_fork_cost_boundary",)
        totals.append(amount)
        prior = amount
    return totals, turns, ()


def _verified_boundary_prefix(
    metadata: dict[str, Any], messages: list[dict[str, Any]]
) -> tuple[list[Decimal] | None, int, tuple[str, ...]]:
    """Validate a boundary before projecting it into a new fork."""
    totals, turns, errors = _verified_boundary(metadata)
    if errors or totals is None:
        return totals, turns, errors
    raw = metadata[_BOUNDARY_KEY]
    if _prefix_fingerprint(messages, turns) != raw["prefix_fingerprint"]:
        return None, 0, ("fork_prefix_changed",)
    return totals, turns, ()


def build_fork_cost_boundary(
    *,
    parent_dir: Path,
    parent_id: str,
    parent_messages: list[dict[str, Any]],
    parent_metadata: dict[str, Any],
    fork_turn: int,
    child_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the immutable inherited-cost projection written with a child."""
    canonical_parent = _canonical_saved_messages(parent_messages)
    canonical_child = _canonical_saved_messages(child_messages)
    if canonical_parent is None or canonical_child is None:
        return _unavailable_boundary(parent_id, "invalid_inherited_prefix")
    child_turns = count_turns(canonical_child)
    if fork_turn < 1 or fork_turn != child_turns:
        return _unavailable_boundary(parent_id, "invalid_fork_turn")
    if fork_turn > _MAX_BOUNDARY_TURNS:
        return _unavailable_boundary(parent_id, "fork_cost_boundary_too_large")

    inherited: list[Decimal] = []
    parent_inherited_turns = 0
    warnings: tuple[str, ...] = ()
    if "forked_from_turn" in parent_metadata:
        inherited, parent_inherited_turns, errors = _verified_boundary_prefix(
            parent_metadata, canonical_parent
        )
        if errors or inherited is None:
            return _unavailable_boundary(parent_id, *errors)

    if fork_turn <= parent_inherited_turns:
        totals = inherited[:fork_turn]
        fence: dict[str, Any] = {"kind": "projected_boundary", "turn": fork_turn}
    else:
        observation = _scan_owner_turn_costs(
            parent_dir,
            parent_id,
            canonical_parent,
            range(parent_inherited_turns + 1, fork_turn + 1),
            inherited_native_turn_count=parent_inherited_turns,
        )
        if observation.diagnostics:
            return _unavailable_boundary(parent_id, *observation.diagnostics)
        warnings = observation.warnings
        totals = list(inherited)
        running = totals[-1] if totals else Decimal("0")
        for turn in range(parent_inherited_turns + 1, fork_turn + 1):
            running += observation.by_turn[turn]
            totals.append(running)
        fence = {
            "kind": "capture_end",
            "line": observation.final_fence_line,
            "turn": fork_turn,
        }

    fingerprint = _prefix_fingerprint(canonical_child, fork_turn)
    if fingerprint is None:
        return _unavailable_boundary(parent_id, "invalid_inherited_prefix")
    boundary: dict[str, Any] = {
        "version": _BOUNDARY_VERSION,
        "status": "verified",
        "inherited_turn_count": fork_turn,
        "cumulative_cost_usd_by_turn": [str(value) for value in totals],
        "prefix_fingerprint": fingerprint,
        "provenance": {"owner": parent_id, "fence": fence},
        **({"warnings": list(warnings)} if warnings else {}),
    }
    if len(json.dumps(boundary, ensure_ascii=False).encode("utf-8")) > _MAX_BOUNDARY_BYTES:
        return _unavailable_boundary(parent_id, "fork_cost_boundary_too_large")
    return boundary


def _sum_owner_ci_cost(session_dir: Path, owner: str) -> tuple[Decimal, tuple[str, ...]]:
    """Sum child-owned spend only; inherited cost comes only from metadata."""
    events_path = ci_events_path(session_dir)
    diagnostics: set[str] = set()
    if not events_path.is_file():
        return Decimal("0"), (f"missing_ci_capture:{owner}",)
    before = _capture_stamp(events_path)
    history = SessionHistoryStore(session_dir, events_path=events_path, session_id=owner)
    total = Decimal("0")
    for event in history.iter_events():
        if event.get("session_id") != owner:
            continue
        if event.get("event") != _LLM_RESPONSE_EVENT:
            continue
        usage = event["data"].get("usage")
        if not isinstance(usage, dict) or "cost_usd" not in usage:
            _add(diagnostics, f"missing_cost:{owner}")
            continue
        amount = _decimal(usage["cost_usd"])
        if amount is None:
            _add(diagnostics, f"invalid_cost:{owner}")
            continue
        total += amount
    diagnostics.update(_history_diagnostics(history, owner))
    if _foreign_owner_cost_exists(events_path, owner):
        _add(diagnostics, f"foreign_cost:{owner}")
    if before != _capture_stamp(events_path):
        _add(diagnostics, f"unstable_ci_capture:{owner}")
    return total, tuple(sorted(diagnostics))


def _restoration_marker(coordinator: Any) -> set[str] | None:
    state = getattr(coordinator, "session_state", None)
    if not isinstance(state, dict):
        return None
    marker = state.get(_RESTORED_LINEAGE_MARKER)
    if marker is None:
        marker = set()
        state[_RESTORED_LINEAGE_MARKER] = marker
    return marker if isinstance(marker, set) else None


def restore_fork_lineage_cost(
    coordinator: Any, *, session_id: str, session_dir: Path
) -> ForkLineageCost:
    """Restore immutable inherited cost plus current child-owned CI spend.

    No parent directory is resolved or read.  Pre-boundary forks are incomplete,
    not reconstructed from mutable timestamps or native event logs.
    """
    diagnostics: set[str] = set()
    try:
        history = SessionHistoryStore(session_dir)
        metadata = history.load_metadata()
    except Exception:
        metadata = {}
        _add(diagnostics, "unreadable_fork_metadata")
    inherited, _, errors = _verified_boundary(metadata)
    diagnostics.update(errors)
    inherited_total = inherited[-1] if inherited else Decimal("0")
    own_total, own_diagnostics = _sum_owner_ci_cost(session_dir, session_id)
    diagnostics.update(own_diagnostics)
    total = inherited_total + own_total

    marker = _restoration_marker(coordinator)
    if total > 0 and (marker is None or session_id not in marker):
        try:
            coordinator.register_contributor(
                SESSION_COST_CHANNEL,
                f"history:{session_id}",
                lambda total=total: {"cost_usd": str(total)},
            )
        except Exception:
            logger.warning(
                "Failed to restore fork lineage cost for %s; continuing without it",
                session_id,
                exc_info=True,
            )
            _add(diagnostics, f"registration_failed:{session_id}")
        else:
            if marker is not None:
                marker.add(session_id)
    return ForkLineageCost(total=total, diagnostics=tuple(sorted(diagnostics)))


def restore_session_cost(
    coordinator: Any,
    session_id: str,
    events_path: Path,
) -> Decimal | None:
    """Re-seed ordinary resume cost via a synthetic contributor."""
    prior_total = sum_prior_cost_usd(events_path, session_id=session_id)
    if prior_total is None or prior_total <= 0:
        return None
    try:
        coordinator.register_contributor(
            SESSION_COST_CHANNEL,
            f"history:{session_id}",
            lambda total=prior_total: {"cost_usd": str(total)},
        )
    except Exception:
        logger.warning(
            "Failed to restore prior session cost for %s; continuing without it",
            session_id,
            exc_info=True,
        )
        return None
    logger.info("Restored $%s cumulative session cost for %s", prior_total, session_id)
    return prior_total