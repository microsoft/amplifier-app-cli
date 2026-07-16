"""Trust resolution and deny-and-continue governance state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from time import monotonic
import unicodedata

from .interaction_state import NeedsYouQueue
from .interaction_state import PermissionDecision
from .interaction_state import PermissionSlot
from .interaction_state import TrustPreset
from .safety_classifier import ActionRequest
from .safety_classifier import CapabilityClass
from .safety_classifier import ClassificationResult
from .safety_classifier import ClassifierEvidence
from .safety_classifier import InputProbeResult
from .safety_classifier import ReasoningBlindTranscript
from .safety_classifier import TwoStageActionClassifier
from .safety_classifier import probe_shapes
from .transcript_blocks import BlockedBlock

_MAX_DENIALS_RETAINED = 1_000


def _clean_reason(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("denial reason must be a string")
    if len(value) > 4_096:
        raise ValueError("denial reason exceeds 4096 characters")
    cleaned = "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if not unicodedata.category(character).startswith("C")
    )
    return " ".join(cleaned.split())


def _classification_detail(classification: ClassificationResult) -> str:
    """Surface a classifier's own debugging detail for a denial, if any.

    Prefers the deliberative-stage evaluation (the one that actually decided
    a two-stage classification), falling back to the fast-filter stage for
    denials resolved there. Both are StageEvaluation instances that may carry
    a non-contractual `detail` (see safety_classifier.StageEvaluation) -- most
    commonly repr(exc) from TwoStageActionClassifier's fail-closed exception
    handling. Static trust and heuristic classifier denials never set detail,
    so this returns "" for them; it only produces output for classifier-
    raised failures.
    """
    evaluation = (
        classification.deliberative_evaluation or classification.fast_evaluation
    )
    return evaluation.detail


class TrustPath(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    CLASSIFY = "classify"


@dataclass(frozen=True, slots=True)
class TrustResolution:
    path: TrustPath
    reason: str


_SLOT_BY_CAPABILITY: dict[CapabilityClass, PermissionSlot] = {
    CapabilityClass.READ: PermissionSlot.READ,
    CapabilityClass.TEST: PermissionSlot.TEST,
    CapabilityClass.WRITE: PermissionSlot.WRITE,
    CapabilityClass.NETWORK: PermissionSlot.NETWORK,
    CapabilityClass.SPEND: PermissionSlot.SPEND,
    CapabilityClass.SUBAGENT: PermissionSlot.SUBAGENT,
    CapabilityClass.OUTSIDE_PROJECT: PermissionSlot.OUTSIDE_PROJECT,
}


def resolve_trust(preset: TrustPreset, request: ActionRequest) -> TrustResolution:
    """Resolve a request without silently widening an incomplete preset."""

    if not isinstance(preset, TrustPreset):
        raise TypeError("preset must be a TrustPreset")
    if not isinstance(request, ActionRequest):
        raise TypeError("request must be an ActionRequest")
    if preset.classifier_gated:
        if request.capability == CapabilityClass.READ and request.within_project:
            return TrustResolution(TrustPath.ALLOW, "reads bypass classification")
        if request.capability == CapabilityClass.WRITE and request.within_project:
            return TrustResolution(
                TrustPath.ALLOW, "in-project writes bypass classification"
            )
        return TrustResolution(TrustPath.CLASSIFY, "capability has real downside")

    slot = _SLOT_BY_CAPABILITY.get(request.capability)
    slots = (slot,) if slot is not None else ()
    label = request.capability.value
    if request.capability == CapabilityClass.SHELL:
        slots = (
            PermissionSlot.READ,
            PermissionSlot.TEST,
            PermissionSlot.WRITE,
            PermissionSlot.NETWORK,
            PermissionSlot.SPEND,
            PermissionSlot.OUTSIDE_PROJECT,
        )
    elif (
        request.capability
        in {
            CapabilityClass.READ,
            CapabilityClass.WRITE,
        }
        and not request.within_project
    ):
        slots = (slot, PermissionSlot.OUTSIDE_PROJECT)
        label = PermissionSlot.OUTSIDE_PROJECT.value
    decisions = tuple(preset.decision_for(item) for item in slots if item)
    if PermissionDecision.BLOCK in decisions:
        decision = PermissionDecision.BLOCK
    elif PermissionDecision.ASK in decisions or not decisions:
        decision = PermissionDecision.ASK
    else:
        decision = PermissionDecision.AUTO
    if decision == PermissionDecision.AUTO:
        return TrustResolution(TrustPath.ALLOW, f"auto {label}")
    if decision == PermissionDecision.BLOCK:
        return TrustResolution(TrustPath.DENY, f"blocked {label}")
    return TrustResolution(TrustPath.ASK, f"ask {label}")


@dataclass(frozen=True, slots=True)
class DenialRecord:
    denial_id: str
    request_id: str
    capability: CapabilityClass
    action: str
    reason: str
    created_at: float
    consecutive_count: int
    total_count: int
    escalation_reasons: tuple[str, ...] = ()

    @property
    def escalation_due(self) -> bool:
        return bool(self.escalation_reasons)


class DenialLog:
    def __init__(
        self,
        *,
        consecutive_threshold: int = 3,
        total_threshold: int = 20,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if consecutive_threshold < 1 or total_threshold < 1:
            raise ValueError("denial thresholds must be positive")
        self._consecutive_threshold = consecutive_threshold
        self._total_threshold = total_threshold
        self._clock = clock
        self._records: list[DenialRecord] = []
        self._consecutive_count = 0
        self._total_count = 0

    @property
    def records(self) -> tuple[DenialRecord, ...]:
        return tuple(self._records)

    @property
    def consecutive_count(self) -> int:
        return self._consecutive_count

    @property
    def total_count(self) -> int:
        return self._total_count

    def record_denial(self, request: ActionRequest, reason: str) -> DenialRecord:
        if not isinstance(request, ActionRequest):
            raise TypeError("request must be an ActionRequest")
        clean_reason = _clean_reason(reason)
        if not clean_reason:
            raise ValueError("denial reason is required")
        self._consecutive_count += 1
        self._total_count += 1
        triggers: list[str] = []
        if self._consecutive_count == self._consecutive_threshold:
            triggers.append(f"{self._consecutive_threshold} consecutive denials")
        if self._total_count == self._total_threshold:
            triggers.append(f"{self._total_threshold} total denials")
        record = DenialRecord(
            f"denial-{self._total_count}",
            request.request_id,
            request.capability,
            request.action,
            clean_reason,
            self._clock(),
            self._consecutive_count,
            self._total_count,
            tuple(triggers),
        )
        self._records.append(record)
        if len(self._records) > _MAX_DENIALS_RETAINED:
            del self._records[: len(self._records) - _MAX_DENIALS_RETAINED]
        return record

    def record_non_denial(self) -> None:
        self._consecutive_count = 0


class GateDisposition(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class NeedsYouRequest:
    question: str
    reason: str

    def __post_init__(self) -> None:
        question = _clean_reason(self.question)
        reason = _clean_reason(self.reason)
        if not question or not reason:
            raise ValueError("needs-you requests require a question and reason")
        object.__setattr__(self, "question", question)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class ActionGateResult:
    request: ActionRequest
    disposition: GateDisposition
    reason_code: str
    reason: str
    continue_work: bool
    tool_result: str = ""
    classification: ClassificationResult | None = None
    denial: DenialRecord | None = None
    needs_you: NeedsYouRequest | None = None
    deferred_decision_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.request, ActionRequest):
            raise TypeError("request must be an ActionRequest")
        if not isinstance(self.disposition, GateDisposition):
            raise TypeError("disposition must be a GateDisposition")
        if type(self.continue_work) is not bool:
            raise TypeError("continue_work must be a bool")
        reason_code = _clean_reason(self.reason_code)
        reason = _clean_reason(self.reason)
        tool_result = _clean_reason(self.tool_result) if self.tool_result else ""
        if not reason_code or not reason:
            raise ValueError("gate results require a reason")
        if self.disposition == GateDisposition.DENY:
            if not self.continue_work or not tool_result or self.denial is None:
                raise ValueError("denials must carry deny-and-continue data")
        elif self.denial or self.needs_you or self.deferred_decision_id or tool_result:
            raise ValueError("non-denials cannot carry denial data")
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "tool_result", tool_result)

    @property
    def allowed(self) -> bool:
        return self.disposition == GateDisposition.ALLOW

    def to_blocked_block(self) -> BlockedBlock:
        if self.disposition != GateDisposition.DENY:
            raise ValueError("only denied actions render as blocked blocks")
        return BlockedBlock(
            f"blocked · {self.request.action}",
            f"{self.reason} · finding safer path",
        )


class ActionGovernor:
    """Apply a trust preset, classifier, and denial escalation policy."""

    def __init__(
        self,
        *,
        classifier: TwoStageActionClassifier | None = None,
        denial_log: DenialLog | None = None,
        needs_you: NeedsYouQueue | None = None,
    ) -> None:
        self.classifier = classifier or TwoStageActionClassifier()
        self.denial_log = denial_log or DenialLog()
        self.needs_you = needs_you

    def decide(
        self,
        preset: TrustPreset,
        request: ActionRequest,
        *,
        transcript: ReasoningBlindTranscript | None = None,
        probe_result: InputProbeResult | None = None,
    ) -> ActionGateResult:
        pending = self._resolve_policy(
            preset,
            request,
            transcript=transcript,
            probe_result=probe_result,
        )
        if isinstance(pending, ActionGateResult):
            return pending
        return self._complete_classification(request, self.classifier.classify(pending))

    async def decide_async(
        self,
        preset: TrustPreset,
        request: ActionRequest,
        *,
        transcript: ReasoningBlindTranscript | None = None,
        probe_result: InputProbeResult | None = None,
    ) -> ActionGateResult:
        """Apply policy using the provider-backed classifier when configured."""

        pending = self._resolve_policy(
            preset,
            request,
            transcript=transcript,
            probe_result=probe_result,
        )
        if isinstance(pending, ActionGateResult):
            return pending
        return self._complete_classification(
            request, await self.classifier.classify_async(pending)
        )

    def _resolve_policy(
        self,
        preset: TrustPreset,
        request: ActionRequest,
        *,
        transcript: ReasoningBlindTranscript | None,
        probe_result: InputProbeResult | None,
    ) -> ActionGateResult | ClassifierEvidence:
        """Resolve static trust or return the evidence requiring classification."""
        resolution = resolve_trust(preset, request)
        if resolution.path == TrustPath.ALLOW:
            self.denial_log.record_non_denial()
            return ActionGateResult(
                request,
                GateDisposition.ALLOW,
                "trusted-capability",
                resolution.reason,
                True,
            )
        if resolution.path == TrustPath.ASK:
            self.denial_log.record_non_denial()
            return ActionGateResult(
                request,
                GateDisposition.ASK,
                "approval-required",
                resolution.reason,
                False,
            )
        if resolution.path == TrustPath.DENY:
            return self._deny(request, "trust-slot-block", resolution.reason)

        return ClassifierEvidence(
            request,
            transcript or ReasoningBlindTranscript(),
            probe_shapes(probe_result),
        )

    def _complete_classification(
        self,
        request: ActionRequest,
        classification: ClassificationResult,
    ) -> ActionGateResult:
        """Convert a sync or async classifier verdict into one gate result path."""
        if classification.allowed:
            self.denial_log.record_non_denial()
            return ActionGateResult(
                request,
                GateDisposition.ALLOW,
                classification.reason_code,
                classification.reason,
                True,
                classification=classification,
            )
        return self._deny(
            request,
            classification.reason_code,
            classification.reason,
            classification,
            detail=_classification_detail(classification),
        )

    def _deny(
        self,
        request: ActionRequest,
        reason_code: str,
        reason: str,
        classification: ClassificationResult | None = None,
        *,
        detail: str = "",
    ) -> ActionGateResult:
        # Fold the classifier's own non-contractual debugging detail (see
        # StageEvaluation.detail) into the denial reason so it reaches every
        # surface that already renders `reason` -- the blocked-block the user
        # sees (to_blocked_block), the tool_result handed back to the agent,
        # and the denial log -- instead of being silently discarded on the
        # StageEvaluation this classification carries. Most denials (static
        # trust decisions, heuristic classifier verdicts) never set detail,
        # so this is a no-op for them; it only fires for classifier-raised
        # fail-closed denials, which used to be visible only via
        # logger.exception -- invisible in a full-screen TUI and absent from
        # session events entirely.
        if detail:
            reason = f"{reason} \u00b7 {detail}"
        denial = self.denial_log.record_denial(request, reason)
        needs_you_request: NeedsYouRequest | None = None
        decision_id = ""
        if denial.escalation_due:
            needs_you_request = NeedsYouRequest(
                f"Review blocked action: {request.action}?",
                f"{reason}; {' and '.join(denial.escalation_reasons)}",
            )
            if self.needs_you is not None:
                try:
                    decision = self.needs_you.defer(
                        needs_you_request.question, needs_you_request.reason
                    )
                    decision_id = decision.decision_id
                except ValueError:
                    # A full queue must not turn deny-and-continue into a halt.
                    decision_id = ""
        tool_result = (
            f"Action denied: {reason}. Route to a safer path, not around this "
            "policy; continue with unblocked work."
        )
        return ActionGateResult(
            request,
            GateDisposition.DENY,
            reason_code,
            reason,
            True,
            tool_result,
            classification,
            denial,
            needs_you_request,
            decision_id,
        )


__all__: Sequence[str] = (
    "ActionRequest",
    "ActionGateResult",
    "ActionGovernor",
    "CapabilityClass",
    "DenialLog",
    "DenialRecord",
    "GateDisposition",
    "NeedsYouRequest",
    "TrustPath",
    "TrustResolution",
    "resolve_trust",
)
