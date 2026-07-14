"""Deterministic safety primitives for classifier-gated tool approval."""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from typing import Protocol
import unicodedata

_MAX_ACTION_CHARS = 4_096
_MAX_IDENTIFIER_CHARS = 120
_MAX_OBSERVATIONS = 256
_MAX_OBSERVATION_CHARS = 32_768
_MAX_TRANSCRIPT_CHARS = 262_144
_MAX_TOOL_RESULT_CHARS = 262_144
_MAX_FINDINGS = 8


def _clean_text(value: str, *, limit: int, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError("text values must be strings")
    if len(value) > limit:
        raise ValueError(f"text exceeds {limit} characters")
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = "".join(
        character
        for character in normalized
        if (multiline and character in {"\n", "\t"})
        or not unicodedata.category(character).startswith("C")
    )
    if len(cleaned) > limit:
        raise ValueError(f"text exceeds {limit} characters")
    return cleaned if multiline else " ".join(cleaned.split())


class CapabilityClass(str, Enum):
    READ = "read"
    TEST = "test"
    WRITE = "write"
    SHELL = "shell"
    NETWORK = "net"
    SPEND = "spend"
    SUBAGENT = "subagent"
    OUTSIDE_PROJECT = "outside-project"


@dataclass(frozen=True, slots=True)
class ActionRequest:
    request_id: str
    capability: CapabilityClass
    action: str
    within_project: bool = False
    target: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityClass):
            raise TypeError("capability must be a CapabilityClass")
        if type(self.within_project) is not bool:
            raise TypeError("within_project must be a bool")
        request_id = _clean_text(self.request_id, limit=_MAX_IDENTIFIER_CHARS)
        action = _clean_text(self.action, limit=_MAX_ACTION_CHARS)
        target = _clean_text(self.target, limit=_MAX_ACTION_CHARS)
        if not request_id:
            raise ValueError("request_id is required")
        if not action:
            raise ValueError("action is required")
        if self.capability == CapabilityClass.OUTSIDE_PROJECT and self.within_project:
            raise ValueError("outside-project actions cannot be within_project")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "target", target)


class ObservationKind(str, Enum):
    USER_MESSAGE = "user-message"
    TOOL_CALL = "tool-call"


@dataclass(frozen=True, slots=True)
class ClassifierObservation:
    kind: ObservationKind
    content: str
    tool_name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ObservationKind):
            raise TypeError("kind must be an ObservationKind")
        content = _clean_text(
            self.content, limit=_MAX_OBSERVATION_CHARS, multiline=True
        )
        tool_name = _clean_text(self.tool_name, limit=_MAX_IDENTIFIER_CHARS)
        if not content.strip():
            raise ValueError("observation content is required")
        if self.kind == ObservationKind.TOOL_CALL and not tool_name:
            raise ValueError("tool calls require a tool_name")
        if self.kind == ObservationKind.USER_MESSAGE and tool_name:
            raise ValueError("user messages cannot have a tool_name")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "tool_name", tool_name)


@dataclass(frozen=True, slots=True)
class ReasoningBlindTranscript:
    """Classifier input that cannot represent assistant reasoning or tool results."""

    observations: tuple[ClassifierObservation, ...] = ()

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        if len(observations) > _MAX_OBSERVATIONS:
            raise ValueError("classifier transcript has too many observations")
        if any(not isinstance(item, ClassifierObservation) for item in observations):
            raise TypeError("transcript accepts only ClassifierObservation values")
        if sum(len(item.content) for item in observations) > _MAX_TRANSCRIPT_CHARS:
            raise ValueError("classifier transcript is too large")
        object.__setattr__(self, "observations", observations)


class InjectionShape(str, Enum):
    AUTHORITY_OVERRIDE = "authority-override"
    ROLE_IMPERSONATION = "role-impersonation"
    SECRET_EXTRACTION = "secret-extraction"
    CONCEALED_ACTION = "concealed-action"
    TOOL_DIRECTIVE = "tool-directive"


_INJECTION_PATTERNS: tuple[tuple[InjectionShape, re.Pattern[str]], ...] = (
    (
        InjectionShape.AUTHORITY_OVERRIDE,
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:previous|prior|above|system|developer)\s+"
            r"(?:instructions?|messages?|prompts?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionShape.ROLE_IMPERSONATION,
        re.compile(
            r"(?:<\s*(?:system|developer|assistant)\b|\b(?:system|developer)\s+"
            r"(?:message|instruction|prompt)\s*:)",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionShape.SECRET_EXTRACTION,
        re.compile(
            r"\b(?:reveal|print|exfiltrate|upload|send)\b.{0,80}\b"
            r"(?:secret|token|api[ -]?key|system prompt|credentials?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        InjectionShape.CONCEALED_ACTION,
        re.compile(
            r"\b(?:do not|don't|never)\s+(?:tell|show|mention|notify)\s+"
            r"(?:the\s+)?user\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionShape.TOOL_DIRECTIVE,
        re.compile(
            r"\b(?:run|execute|invoke|call)\s+(?:the\s+)?(?:following\s+)?"
            r"(?:tool|shell command)\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ProbeFinding:
    shape: InjectionShape
    excerpt: str


@dataclass(frozen=True, slots=True)
class InputProbeResult:
    tool_name: str
    flagged: bool
    findings: tuple[ProbeFinding, ...]
    fingerprint: str


class InjectionInputProbe:
    """Flag injection-shaped tool output before it enters model context."""

    def inspect(self, tool_name: str, content: str) -> InputProbeResult:
        clean_name = _clean_text(tool_name, limit=_MAX_IDENTIFIER_CHARS)
        clean_content = _clean_text(
            content, limit=_MAX_TOOL_RESULT_CHARS, multiline=True
        )
        if not clean_name:
            raise ValueError("tool_name is required")
        findings: list[ProbeFinding] = []
        for shape, pattern in _INJECTION_PATTERNS:
            for match in pattern.finditer(clean_content):
                start = max(0, match.start() - 32)
                end = min(len(clean_content), match.end() + 32)
                excerpt = " ".join(clean_content[start:end].split())[:160]
                findings.append(ProbeFinding(shape, excerpt))
                if len(findings) == _MAX_FINDINGS:
                    break
            if len(findings) == _MAX_FINDINGS:
                break
        fingerprint = sha256(clean_content.encode("utf-8")).hexdigest()[:16]
        return InputProbeResult(
            clean_name, bool(findings), tuple(findings), fingerprint
        )


class ClassifierStage(str, Enum):
    FAST_FILTER = "fast-filter"
    DELIBERATIVE = "cot"


class StageDisposition(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ClassifierEvidence:
    request: ActionRequest
    transcript: ReasoningBlindTranscript
    injection_shapes: tuple[InjectionShape, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.request, ActionRequest):
            raise TypeError("request must be an ActionRequest")
        if not isinstance(self.transcript, ReasoningBlindTranscript):
            raise TypeError("transcript must be reasoning-blind")
        shapes = tuple(dict.fromkeys(self.injection_shapes))
        if any(not isinstance(shape, InjectionShape) for shape in shapes):
            raise TypeError("injection_shapes must contain InjectionShape values")
        object.__setattr__(self, "injection_shapes", shapes)


@dataclass(frozen=True, slots=True)
class StageEvaluation:
    disposition: StageDisposition
    reason_code: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, StageDisposition):
            raise TypeError("disposition must be a StageDisposition")
        reason_code = _clean_text(self.reason_code, limit=_MAX_IDENTIFIER_CHARS)
        reason = _clean_text(self.reason, limit=_MAX_ACTION_CHARS)
        if not reason_code or not reason:
            raise ValueError("classifier evaluations require a reason")
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "reason", reason)


class StageEvaluator(Protocol):
    def evaluate(
        self, stage: ClassifierStage, evidence: ClassifierEvidence
    ) -> StageEvaluation: ...


class AsyncStageEvaluator(Protocol):
    def evaluate(
        self, stage: ClassifierStage, evidence: ClassifierEvidence
    ) -> Awaitable[StageEvaluation]: ...


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    allowed: bool
    stage: ClassifierStage
    reason_code: str
    reason: str
    fast_evaluation: StageEvaluation
    deliberative_evaluation: StageEvaluation | None = None


class ConservativeStageEvaluator:
    """Local fail-closed rules; production evaluators can implement the protocol."""

    _DESTRUCTIVE = re.compile(
        r"(?:\brm\s+-[^\n]*r[^\n]*f|\bgit\s+push\b[^\n]*(?:--force|-f\b)|"
        r"\bdrop\s+(?:database|table)\b|\bcurl\b[^\n]*\|\s*(?:sh|bash)\b)",
        re.IGNORECASE,
    )

    def evaluate(
        self, stage: ClassifierStage, evidence: ClassifierEvidence
    ) -> StageEvaluation:
        if evidence.injection_shapes:
            return StageEvaluation(
                StageDisposition.DENY,
                "injection-shaped-input",
                "untrusted tool output contains instruction-like content",
            )
        if stage == ClassifierStage.FAST_FILTER:
            if self._DESTRUCTIVE.search(evidence.request.action):
                return StageEvaluation(
                    StageDisposition.DENY,
                    "destructive-action",
                    "action has destructive or irreversible form",
                )
            return StageEvaluation(
                StageDisposition.REVIEW,
                "downside-needs-review",
                "action has real downside and needs deliberate classification",
            )
        return StageEvaluation(
            StageDisposition.DENY,
            "outside-user-authorization",
            "action is not clearly within user authorization",
        )


class TwoStageActionClassifier:
    """Run a fast filter, then a verdict-only deliberative stage when needed."""

    def __init__(
        self,
        evaluator: StageEvaluator | None = None,
        *,
        async_evaluator: AsyncStageEvaluator | None = None,
    ) -> None:
        if evaluator is None:
            from amplifier_app_cli.ui.authorization_stage import (
                ReasoningBlindStageEvaluator,
            )

            evaluator = ReasoningBlindStageEvaluator()
        self._evaluator = evaluator
        self._async_evaluator = async_evaluator

    def classify(self, evidence: ClassifierEvidence) -> ClassificationResult:
        if not isinstance(evidence, ClassifierEvidence):
            raise TypeError("evidence must be ClassifierEvidence")
        fast = self._evaluate(ClassifierStage.FAST_FILTER, evidence)
        if fast.disposition != StageDisposition.REVIEW:
            return ClassificationResult(
                fast.disposition == StageDisposition.ALLOW,
                ClassifierStage.FAST_FILTER,
                fast.reason_code,
                fast.reason,
                fast,
            )
        deliberate = self._evaluate(ClassifierStage.DELIBERATIVE, evidence)
        if deliberate.disposition == StageDisposition.REVIEW:
            deliberate = StageEvaluation(
                StageDisposition.DENY,
                "indeterminate-classification",
                "deliberative classifier did not reach a decision",
            )
        return ClassificationResult(
            deliberate.disposition == StageDisposition.ALLOW,
            ClassifierStage.DELIBERATIVE,
            deliberate.reason_code,
            deliberate.reason,
            fast,
            deliberate,
        )

    async def classify_async(
        self, evidence: ClassifierEvidence
    ) -> ClassificationResult:
        """Classify with the mounted async evaluator when one is configured."""

        if not isinstance(evidence, ClassifierEvidence):
            raise TypeError("evidence must be ClassifierEvidence")
        if self._async_evaluator is None:
            return self.classify(evidence)
        fast = await self._evaluate_async(ClassifierStage.FAST_FILTER, evidence)
        if fast.disposition != StageDisposition.REVIEW:
            return ClassificationResult(
                fast.disposition == StageDisposition.ALLOW,
                ClassifierStage.FAST_FILTER,
                fast.reason_code,
                fast.reason,
                fast,
            )
        deliberate = await self._evaluate_async(ClassifierStage.DELIBERATIVE, evidence)
        if deliberate.disposition == StageDisposition.REVIEW:
            deliberate = StageEvaluation(
                StageDisposition.DENY,
                "indeterminate-classification",
                "deliberative classifier did not reach a decision",
            )
        return ClassificationResult(
            deliberate.disposition == StageDisposition.ALLOW,
            ClassifierStage.DELIBERATIVE,
            deliberate.reason_code,
            deliberate.reason,
            fast,
            deliberate,
        )

    def _evaluate(
        self, stage: ClassifierStage, evidence: ClassifierEvidence
    ) -> StageEvaluation:
        try:
            result = self._evaluator.evaluate(stage, evidence)
            if not isinstance(result, StageEvaluation):
                raise TypeError("classifier evaluator returned an invalid result")
            return result
        except Exception:
            return StageEvaluation(
                StageDisposition.DENY,
                "classifier-unavailable",
                "classifier failed closed",
            )

    async def _evaluate_async(
        self, stage: ClassifierStage, evidence: ClassifierEvidence
    ) -> StageEvaluation:
        try:
            if self._async_evaluator is None:
                raise RuntimeError("async classifier is unavailable")
            result = await self._async_evaluator.evaluate(stage, evidence)
            if not isinstance(result, StageEvaluation):
                raise TypeError("classifier evaluator returned an invalid result")
            return result
        except Exception:
            return StageEvaluation(
                StageDisposition.DENY,
                "classifier-unavailable",
                "classifier failed closed",
            )


def probe_shapes(result: InputProbeResult | None) -> tuple[InjectionShape, ...]:
    if result is None:
        return ()
    if not isinstance(result, InputProbeResult):
        raise TypeError("probe result must be an InputProbeResult")
    return tuple(finding.shape for finding in result.findings)


__all__: Sequence[str] = (
    "ActionRequest",
    "AsyncStageEvaluator",
    "CapabilityClass",
    "ClassificationResult",
    "ClassifierEvidence",
    "ClassifierObservation",
    "ClassifierStage",
    "ConservativeStageEvaluator",
    "InjectionInputProbe",
    "InjectionShape",
    "InputProbeResult",
    "ObservationKind",
    "ProbeFinding",
    "ReasoningBlindTranscript",
    "StageDisposition",
    "StageEvaluation",
    "StageEvaluator",
    "TwoStageActionClassifier",
    "probe_shapes",
)
