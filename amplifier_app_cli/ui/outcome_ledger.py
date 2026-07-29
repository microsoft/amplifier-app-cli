"""Bounded per-session outcome ledger for spend-versus-yield reporting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

_MAX_LEDGER_ENTRIES = 1_000
_MAX_YIELDS_PER_TURN = 3
_MAX_LABEL_CHARS = 120
_MAX_ID_CHARS = 128


class YieldKind(str, Enum):
    FILES = "files"
    DIFF = "diff"
    TESTS = "tests"
    COMMANDS = "commands"
    ANSWER = "answer"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class OutcomeYield:
    kind: YieldKind
    label: str

    def __post_init__(self) -> None:
        label = _single_line(self.label, _MAX_LABEL_CHARS)
        if not label:
            raise ValueError("yield label cannot be empty")
        object.__setattr__(self, "label", label)


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    turn_id: str
    checkpoint_id: str
    cost: Decimal | str | float
    elapsed_seconds: float
    tokens: int
    cached_percent: int | None = None
    yields: tuple[OutcomeYield, ...] = ()
    interrupted: bool = False

    def __post_init__(self) -> None:
        turn_id = _single_line(self.turn_id, _MAX_ID_CHARS)
        checkpoint_id = _single_line(self.checkpoint_id, _MAX_ID_CHARS)
        if not turn_id or not checkpoint_id:
            raise ValueError("turn_id and checkpoint_id are required")
        try:
            cost = Decimal(str(self.cost))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("cost must be a finite non-negative decimal") from error
        if not cost.is_finite() or cost < 0:
            raise ValueError("cost must be a finite non-negative decimal")
        if self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        if self.tokens < 0:
            raise ValueError("tokens must be non-negative")
        if self.cached_percent is not None and not 0 <= self.cached_percent <= 100:
            raise ValueError("cached_percent must be between 0 and 100")
        if len(self.yields) > _MAX_YIELDS_PER_TURN:
            raise ValueError("a turn can report at most three yield fields")
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "checkpoint_id", checkpoint_id)
        object.__setattr__(self, "cost", cost)
        object.__setattr__(self, "yields", tuple(self.yields))

    @property
    def shipped(self) -> bool:
        if self.interrupted:
            return False
        for item in self.yields:
            if item.kind in {YieldKind.FILES, YieldKind.DIFF}:
                return True
            if item.kind == YieldKind.TESTS and not _tests_failed(item.label):
                return True
        return False

    @property
    def yield_summary(self) -> str:
        return " · ".join(item.label for item in self.yields)

    @property
    def decimal_cost(self) -> Decimal:
        """Return the cost after the post-init normalization invariant."""
        if not isinstance(self.cost, Decimal):
            raise RuntimeError("turn cost was not normalized")
        return self.cost


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    turns: int
    session_cost: Decimal
    shipped_turns: int
    answer_only_turns: int
    interrupted_turns: int
    cheapest_shipped_cost: Decimal | None
    dearest_shipped_cost: Decimal | None
    cache_hit_percent: int | None


class OutcomeLedger:
    """Record immutable turn outcomes and expose compact session aggregates."""

    def __init__(self, *, max_entries: int = _MAX_LEDGER_ENTRIES) -> None:
        if isinstance(max_entries, bool) or max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: list[TurnOutcome] = []
        self._turn_ids: set[str] = set()

    @property
    def entries(self) -> tuple[TurnOutcome, ...]:
        return tuple(self._entries)

    @property
    def latest(self) -> TurnOutcome | None:
        return self._entries[-1] if self._entries else None

    def record(self, outcome: TurnOutcome) -> None:
        if outcome.turn_id in self._turn_ids:
            raise ValueError(f"turn already recorded: {outcome.turn_id}")
        if len(self._entries) >= self._max_entries:
            removed = self._entries.pop(0)
            self._turn_ids.remove(removed.turn_id)
        self._entries.append(outcome)
        self._turn_ids.add(outcome.turn_id)

    def restore_records(self, records: object) -> None:
        """Restore valid persisted outcomes without trusting session metadata."""
        if not isinstance(records, list):
            return
        for record in records[-self._max_entries :]:
            if not isinstance(record, dict):
                continue
            raw_yields = record.get("yields", [])
            if not isinstance(raw_yields, list):
                continue
            try:
                yields = tuple(
                    OutcomeYield(YieldKind(item["kind"]), item["label"])
                    for item in raw_yields[:_MAX_YIELDS_PER_TURN]
                    if isinstance(item, dict)
                    and isinstance(item.get("kind"), str)
                    and isinstance(item.get("label"), str)
                )
                outcome = TurnOutcome(
                    turn_id=record["turn_id"],
                    checkpoint_id=record["checkpoint_id"],
                    cost=record.get("cost", "0"),
                    elapsed_seconds=float(record.get("elapsed_seconds", 0)),
                    tokens=int(record.get("tokens", 0)),
                    cached_percent=record.get("cached_percent"),
                    yields=yields,
                    interrupted=bool(record.get("interrupted", False)),
                )
                self.record(outcome)
            except (KeyError, TypeError, ValueError):
                continue

    def checkpoint(self, checkpoint_id: str) -> TurnOutcome | None:
        clean = _single_line(checkpoint_id, _MAX_ID_CHARS)
        return next(
            (
                entry
                for entry in reversed(self._entries)
                if entry.checkpoint_id == clean
            ),
            None,
        )

    def summary(self) -> LedgerSummary:
        shipped = [entry for entry in self._entries if entry.shipped]
        answer_only = [
            entry
            for entry in self._entries
            if not entry.interrupted
            and entry.yields
            and all(item.kind == YieldKind.ANSWER for item in entry.yields)
        ]
        costs = [entry.decimal_cost for entry in shipped]
        cached_entries = [
            entry for entry in self._entries if entry.cached_percent is not None
        ]
        cached_tokens = sum(entry.tokens for entry in cached_entries)
        cached_weight = 0
        for entry in cached_entries:
            if entry.cached_percent is not None:
                cached_weight += entry.tokens * entry.cached_percent
        cache_hit_percent = (
            round(cached_weight / cached_tokens) if cached_tokens else None
        )
        return LedgerSummary(
            turns=len(self._entries),
            session_cost=sum(
                (entry.decimal_cost for entry in self._entries), Decimal("0")
            ),
            shipped_turns=len(shipped),
            answer_only_turns=len(answer_only),
            interrupted_turns=sum(entry.interrupted for entry in self._entries),
            cheapest_shipped_cost=min(costs) if costs else None,
            dearest_shipped_cost=max(costs) if costs else None,
            cache_hit_percent=cache_hit_percent,
        )

    def footer_yield(self) -> str:
        latest = self.latest
        return "▲" if latest is not None and latest.shipped else ""

    def as_records(self) -> list[dict[str, Any]]:
        return [
            {
                "turn_id": entry.turn_id,
                "checkpoint_id": entry.checkpoint_id,
                "cost": str(entry.cost),
                "elapsed_seconds": entry.elapsed_seconds,
                "tokens": entry.tokens,
                "cached_percent": entry.cached_percent,
                "yields": [
                    {"kind": item.kind.value, "label": item.label}
                    for item in entry.yields
                ],
                "interrupted": entry.interrupted,
            }
            for entry in self._entries
        ]


def _single_line(value: object, limit: int) -> str:
    text = "".join(character for character in str(value) if ord(character) >= 32)
    return " ".join(text.split())[:limit]


def _tests_failed(label: str) -> bool:
    normalized = label.casefold()
    return "✘" in label or "fail" in normalized or "error" in normalized


__all__ = [
    "LedgerSummary",
    "OutcomeLedger",
    "OutcomeYield",
    "TurnOutcome",
    "YieldKind",
]
