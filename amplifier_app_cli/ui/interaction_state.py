"""Typed state for trust, deferred decisions, and mid-turn steering."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from time import monotonic

from .steering import QueuedSteer, SteeringQueue

_MAX_DECISIONS = 100
_MAX_DECISION_TEXT = 4_096
_PERMISSION_CYCLE = ("chat", "build", "plan", "auto", "bypass")
TRUST_POLICY_VERSION = 2


def _safe_multiline(value: object, limit: int) -> str:
    return "".join(
        character
        for character in str(value)
        if character in {"\n", "\t"} or ord(character) >= 32
    )[:limit]


def _single_line(value: object, limit: int) -> str:
    return " ".join(_safe_multiline(value, limit).split())


class PermissionSlot(str, Enum):
    READ = "read"
    TEST = "test"
    WRITE = "write"
    NETWORK = "net"
    SPEND = "spend"
    SUBAGENT = "subagent"
    OUTSIDE_PROJECT = "outside-project"


class PermissionDecision(str, Enum):
    AUTO = "auto"
    ASK = "ask"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class TrustPreset:
    name: str
    auto: frozenset[PermissionSlot] = frozenset()
    ask: frozenset[PermissionSlot] = frozenset()
    block: frozenset[PermissionSlot] = frozenset()
    classifier_gated: bool = False

    def __post_init__(self) -> None:
        name = _single_line(self.name, 40)
        if not name:
            raise ValueError("trust preset name is required")
        if (
            (self.auto & self.ask)
            or (self.auto & self.block)
            or (self.ask & self.block)
        ):
            raise ValueError("trust preset slots must be disjoint")
        object.__setattr__(self, "name", name)

    def decision_for(self, slot: PermissionSlot) -> PermissionDecision:
        if slot in self.block:
            return PermissionDecision.BLOCK
        if slot in self.auto:
            return PermissionDecision.AUTO
        return PermissionDecision.ASK

    def summary(self) -> str:
        if self.classifier_gated:
            return "classifier-gated"
        groups = (
            ("auto", self.auto),
            ("ask", self.ask),
            ("block", self.block),
        )
        return " · ".join(
            f"{label} {','.join(slot.value for slot in sorted(slots, key=lambda item: item.value))}"
            for label, slots in groups
            if slots
        )

    @property
    def requires_risk_treatment(self) -> bool:
        """Return whether costly autonomous capabilities need red treatment."""
        return bool(self.auto & {PermissionSlot.NETWORK, PermissionSlot.SPEND})


DEFAULT_TRUST_PRESETS: tuple[TrustPreset, ...] = (
    TrustPreset(
        "chat",
        auto=frozenset({PermissionSlot.READ}),
        ask=frozenset(set(PermissionSlot) - {PermissionSlot.READ}),
    ),
    TrustPreset(
        "plan",
        auto=frozenset({PermissionSlot.READ}),
        block=frozenset(set(PermissionSlot) - {PermissionSlot.READ}),
    ),
    TrustPreset("brainstorm", block=frozenset(PermissionSlot)),
    TrustPreset(
        "build",
        auto=frozenset({PermissionSlot.READ, PermissionSlot.TEST}),
        ask=frozenset(
            {
                PermissionSlot.WRITE,
                PermissionSlot.NETWORK,
                PermissionSlot.SPEND,
                PermissionSlot.SUBAGENT,
                PermissionSlot.OUTSIDE_PROJECT,
            }
        ),
    ),
    TrustPreset("auto", classifier_gated=True),
    TrustPreset("bypass", auto=frozenset(PermissionSlot)),
)


class TrustState:
    def __init__(
        self,
        presets: tuple[TrustPreset, ...] = DEFAULT_TRUST_PRESETS,
        *,
        initial: str = "chat",
    ) -> None:
        self._presets = {preset.name: preset for preset in presets}
        if len(self._presets) != len(presets):
            raise ValueError("trust preset names must be unique")
        if initial not in self._presets:
            raise ValueError(f"unknown trust preset: {initial}")
        self._active = initial
        self._listeners: list[Callable[[], None]] = []

    @property
    def active(self) -> TrustPreset:
        return self._presets[self._active]

    @property
    def bypass_permissions(self) -> bool:
        """Return whether the explicit unrestricted posture is active."""
        return self._active == "bypass"

    def activate(self, name: str) -> TrustPreset:
        if name not in self._presets:
            raise ValueError(f"unknown trust preset: {name}")
        if name != self._active:
            self._active = name
            self._notify()
        return self.active

    def snapshot(self) -> dict[str, object]:
        """Return the complete active posture for durable session metadata."""
        active = self.active
        return {
            "name": active.name,
            "auto": sorted(slot.value for slot in active.auto),
            "ask": sorted(slot.value for slot in active.ask),
            "block": sorted(slot.value for slot in active.block),
            "classifier_gated": active.classifier_gated,
        }

    def restore(self, value: Mapping[str, object]) -> TrustPreset:
        """Restore a named or custom posture from validated metadata."""
        name = _single_line(value.get("name", ""), 40)
        if name in self._presets and name != "custom":
            return self.activate(name)

        def slots(key: str) -> frozenset[PermissionSlot]:
            raw = value.get(key, ())
            if not isinstance(raw, (list, tuple, set, frozenset)):
                raise ValueError(f"invalid trust slot group: {key}")
            return frozenset(PermissionSlot(str(item)) for item in raw)

        custom = TrustPreset(
            "custom",
            auto=slots("auto"),
            ask=slots("ask"),
            block=slots("block"),
            classifier_gated=bool(value.get("classifier_gated", False)),
        )
        assigned = custom.auto | custom.ask | custom.block
        if assigned != frozenset(PermissionSlot):
            raise ValueError("restored trust posture must assign every slot")
        self._presets[custom.name] = custom
        self._active = custom.name
        self._notify()
        return custom

    def restore_persisted(
        self,
        profile: object,
        posture: object,
        *,
        policy_version: object = None,
    ) -> bool:
        """Restore durable permission state, leaving the safe default untouched.

        The complete profile wins over the legacy posture name. A missing value
        is not an instruction to broaden permissions.
        """
        versioned = (
            isinstance(policy_version, int)
            and not isinstance(policy_version, bool)
            and policy_version >= TRUST_POLICY_VERSION
        )
        profile_name = (
            _single_line(profile.get("name", ""), 40)
            if isinstance(profile, Mapping)
            else ""
        )
        if not versioned and (profile_name == "bypass" or posture == "bypass"):
            return False
        if isinstance(profile, Mapping):
            self.restore(profile)
            return True
        if isinstance(posture, str) and posture:
            self.activate(posture)
            return True
        return False

    def cycle(self, offset: int = 1) -> TrustPreset:
        """Cycle the user-facing permission posture independently of modes."""
        try:
            index = _PERMISSION_CYCLE.index(self._active)
        except ValueError:
            index = -1 if offset >= 0 else 0
        return self.activate(
            _PERMISSION_CYCLE[(index + offset) % len(_PERMISSION_CYCLE)]
        )

    def set_slot(
        self,
        slot: PermissionSlot,
        decision: PermissionDecision,
    ) -> TrustPreset:
        """Create an active custom preset by changing one capability slot."""
        if not isinstance(slot, PermissionSlot):
            raise TypeError("slot must be a PermissionSlot")
        if not isinstance(decision, PermissionDecision):
            raise TypeError("decision must be a PermissionDecision")
        base = self.active
        if base.classifier_gated:
            auto = {PermissionSlot.READ, PermissionSlot.WRITE}
            ask = set(PermissionSlot) - auto
            block: set[PermissionSlot] = set()
        else:
            auto, ask, block = set(base.auto), set(base.ask), set(base.block)
        for group in (auto, ask, block):
            group.discard(slot)
        {
            PermissionDecision.AUTO: auto,
            PermissionDecision.ASK: ask,
            PermissionDecision.BLOCK: block,
        }[decision].add(slot)
        custom = TrustPreset(
            "custom",
            auto=frozenset(auto),
            ask=frozenset(ask),
            block=frozenset(block),
        )
        self._presets[custom.name] = custom
        self._active = custom.name
        self._notify()
        return custom

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


class DecisionStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    CONSUMED = "consumed"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class DeferredDecision:
    decision_id: str
    question: str
    reason: str
    created_at: float
    status: DecisionStatus = DecisionStatus.PENDING
    answer: str = ""
    dependencies: tuple[str, ...] = ()


class NeedsYouQueue:
    """Defer non-urgent questions without blocking unrelated work."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._next_id = 1
        self._decisions: list[DeferredDecision] = []
        self._listeners: list[Callable[[], None]] = []

    @property
    def pending(self) -> tuple[DeferredDecision, ...]:
        return tuple(
            decision
            for decision in self._decisions
            if decision.status == DecisionStatus.PENDING
        )

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    @property
    def answered(self) -> tuple[DeferredDecision, ...]:
        return tuple(
            decision
            for decision in self._decisions
            if decision.status == DecisionStatus.ANSWERED
        )

    @property
    def blocking(self) -> tuple[DeferredDecision, ...]:
        """Decisions whose dependencies cannot run until a safe boundary."""
        return tuple(
            decision
            for decision in self._decisions
            if decision.status in {DecisionStatus.PENDING, DecisionStatus.ANSWERED}
        )

    def defer(
        self,
        question: object,
        reason: object,
        dependencies: tuple[str, ...] = (),
    ) -> DeferredDecision:
        if len(self.blocking) >= _MAX_DECISIONS:
            raise ValueError("deferred decision limit reached")
        clean_question = _single_line(question, _MAX_DECISION_TEXT)
        clean_reason = _single_line(reason, _MAX_DECISION_TEXT)
        if not clean_question:
            raise ValueError("decision question cannot be empty")
        clean_dependencies = tuple(
            dict.fromkeys(
                dependency
                for raw in dependencies[:100]
                if (dependency := _single_line(raw, 200))
            )
        )
        decision = DeferredDecision(
            f"decision-{self._next_id}",
            clean_question,
            clean_reason,
            self._clock(),
            dependencies=clean_dependencies,
        )
        self._next_id += 1
        self._decisions.append(decision)
        self._notify()
        return decision

    def dependency_blocked(self, dependency: object) -> bool:
        """Return whether pending human input blocks this specific work item."""
        return bool(self.blocking_decisions((dependency,)))

    def blocking_decisions(
        self, dependencies: Iterable[object]
    ) -> tuple[DeferredDecision, ...]:
        """Return decisions blocking any explicitly declared dependency."""
        keys = {key for raw in dependencies if (key := _single_line(raw, 200))}
        if not keys:
            return ()
        return tuple(
            decision
            for decision in self.blocking
            if keys.intersection(decision.dependencies)
        )

    def answer(self, decision_id: str, answer: object) -> DeferredDecision:
        clean_answer = _single_line(answer, _MAX_DECISION_TEXT)
        if not clean_answer:
            raise ValueError("decision answer cannot be empty")
        return self._replace(decision_id, DecisionStatus.ANSWERED, clean_answer)

    def dismiss(self, decision_id: str) -> DeferredDecision:
        return self._replace(decision_id, DecisionStatus.DISMISSED, "")

    def answer_many(
        self, answers: Mapping[str, object]
    ) -> tuple[DeferredDecision, ...]:
        prepared: list[tuple[int, DeferredDecision, str]] = []
        by_id = {
            decision.decision_id: (index, decision)
            for index, decision in enumerate(self._decisions)
        }
        for decision_id, raw_answer in answers.items():
            if decision_id not in by_id:
                raise KeyError(f"unknown decision: {decision_id}")
            index, decision = by_id[decision_id]
            if decision.status != DecisionStatus.PENDING:
                raise ValueError(f"decision is already {decision.status.value}")
            clean_answer = _single_line(raw_answer, _MAX_DECISION_TEXT)
            if not clean_answer:
                raise ValueError("decision answer cannot be empty")
            prepared.append((index, decision, clean_answer))
        updated = tuple(
            replace(decision, status=DecisionStatus.ANSWERED, answer=answer)
            for _, decision, answer in prepared
        )
        for (index, _, _), decision in zip(prepared, updated, strict=True):
            self._decisions[index] = decision
        if updated:
            self._notify()
        return updated

    def consume_answered(self) -> tuple[DeferredDecision, ...]:
        consumed: list[DeferredDecision] = []
        for index, decision in enumerate(self._decisions):
            if decision.status != DecisionStatus.ANSWERED:
                continue
            updated = replace(decision, status=DecisionStatus.CONSUMED)
            self._decisions[index] = updated
            consumed.append(updated)
        if consumed:
            self._notify()
        return tuple(consumed)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _replace(
        self, decision_id: str, status: DecisionStatus, answer: str
    ) -> DeferredDecision:
        for index, decision in enumerate(self._decisions):
            if decision.decision_id != decision_id:
                continue
            if decision.status != DecisionStatus.PENDING:
                raise ValueError(f"decision is already {decision.status.value}")
            updated = replace(decision, status=status, answer=answer)
            self._decisions[index] = updated
            self._notify()
            return updated
        raise KeyError(f"unknown decision: {decision_id}")

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


__all__ = [
    "DEFAULT_TRUST_PRESETS",
    "DecisionStatus",
    "DeferredDecision",
    "NeedsYouQueue",
    "PermissionDecision",
    "PermissionSlot",
    "QueuedSteer",
    "SteeringQueue",
    "TRUST_POLICY_VERSION",
    "TrustPreset",
    "TrustState",
]
