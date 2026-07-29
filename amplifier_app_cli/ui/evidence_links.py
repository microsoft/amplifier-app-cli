"""Conservative evidence links from final-answer claims to terminal tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ._evidence_matching import EvidenceClaim
from ._evidence_matching import EvidenceKind
from ._evidence_matching import split_claims
from ._evidence_matching import supporting_tool_ids
from .runtime_values import BoundedText
from .runtime_values import MAX_SOURCE_SCAN_CHARS
from .runtime_values import ToolActivitySnapshot
from .runtime_values import bounded_text
from .runtime_values import clean_line

MAX_ANSWERS = 128
MAX_ANSWER_CHARS = MAX_SOURCE_SCAN_CHARS
MAX_TOOLS_PER_ANSWER = 256

_SUPER_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    number: int
    marker: str
    claim_id: str
    kind: EvidenceKind
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class EvidenceRevealSnapshot:
    answer_id: str
    answer: str
    source_chars: int | None
    truncated: bool
    revealed: bool
    annotated_answer: str
    claims: tuple[EvidenceClaim, ...]
    links: tuple[EvidenceLink, ...]


@dataclass(frozen=True, slots=True)
class _ClaimMapping:
    claim: EvidenceClaim
    tool_call_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AnswerRecord:
    answer_id: str
    answer: BoundedText
    tools: tuple[ToolActivitySnapshot, ...]
    mappings: tuple[_ClaimMapping, ...]


class EvidenceLinkModel:
    """Keep a bounded set of final answers and their supporting tool evidence."""

    def __init__(self, *, max_answers: int = MAX_ANSWERS) -> None:
        if (
            not isinstance(max_answers, int)
            or isinstance(max_answers, bool)
            or max_answers <= 0
        ):
            raise ValueError("max_answers must be positive")
        self._max_answers = max_answers
        self._records: dict[str, _AnswerRecord] = {}

    @property
    def answer_ids(self) -> tuple[str, ...]:
        return tuple(self._records)

    def record(
        self,
        answer_id: str,
        final_answer: str,
        tools: Iterable[ToolActivitySnapshot],
    ) -> EvidenceRevealSnapshot:
        clean_id = clean_line(answer_id, 128)
        if not clean_id:
            raise ValueError("answer_id is required")
        if clean_id in self._records:
            raise ValueError(f"answer already recorded: {clean_id}")
        if not isinstance(final_answer, str):
            raise TypeError("final_answer must be a string")
        answer = bounded_text(final_answer, MAX_ANSWER_CHARS)
        terminal_tools = _terminal_tools(tools)
        mappings = tuple(
            _ClaimMapping(claim, supporting_tool_ids(claim, terminal_tools))
            for claim in split_claims(answer.preview)
        )
        if len(self._records) >= self._max_answers:
            del self._records[next(iter(self._records))]
        self._records[clean_id] = _AnswerRecord(
            clean_id, answer, terminal_tools, mappings
        )
        snapshot = self.snapshot(clean_id)
        assert snapshot is not None
        return snapshot

    def snapshot(
        self, answer_id: str, *, reveal: bool = False
    ) -> EvidenceRevealSnapshot | None:
        record = self._records.get(clean_line(answer_id, 128))
        if record is None:
            return None
        if not reveal:
            claims = tuple(_without_links(mapping.claim) for mapping in record.mappings)
            return _snapshot(
                record, claims=claims, links=(), annotated=record.answer.preview
            )

        links: list[EvidenceLink] = []
        revealed_claims: list[EvidenceClaim] = []
        for mapping in record.mappings:
            numbers: list[int] = []
            if mapping.claim.kind is not None:
                for tool_call_id in mapping.tool_call_ids:
                    number = len(links) + 1
                    numbers.append(number)
                    links.append(
                        EvidenceLink(
                            number,
                            _superscript(number),
                            mapping.claim.claim_id,
                            mapping.claim.kind,
                            tool_call_id,
                        )
                    )
            revealed_claims.append(_with_links(mapping.claim, tuple(numbers)))
        annotated = _annotate(record.answer.preview, revealed_claims, links)
        return _snapshot(
            record,
            claims=tuple(revealed_claims),
            links=tuple(links),
            annotated=annotated,
            revealed=True,
        )

    def reveal(self, answer_id: str) -> EvidenceRevealSnapshot | None:
        return self.snapshot(answer_id, reveal=True)

    def resolve(self, answer_id: str, link_number: int) -> ToolActivitySnapshot | None:
        if (
            not isinstance(link_number, int)
            or isinstance(link_number, bool)
            or link_number <= 0
        ):
            return None
        record = self._records.get(clean_line(answer_id, 128))
        if record is None:
            return None
        revealed = self.snapshot(answer_id, reveal=True)
        if revealed is None:
            return None
        target = next(
            (link for link in revealed.links if link.number == link_number), None
        )
        if target is None:
            return None
        return next(
            (tool for tool in record.tools if tool.tool_call_id == target.tool_call_id),
            None,
        )

    def terminal_tools(self, answer_id: str) -> tuple[ToolActivitySnapshot, ...]:
        record = self._records.get(clean_line(answer_id, 128))
        return record.tools if record is not None else ()


def _without_links(claim: EvidenceClaim) -> EvidenceClaim:
    return EvidenceClaim(claim.claim_id, claim.text, claim.start, claim.end, claim.kind)


def _with_links(claim: EvidenceClaim, numbers: tuple[int, ...]) -> EvidenceClaim:
    return EvidenceClaim(
        claim.claim_id, claim.text, claim.start, claim.end, claim.kind, numbers
    )


def _snapshot(
    record: _AnswerRecord,
    *,
    claims: tuple[EvidenceClaim, ...],
    links: tuple[EvidenceLink, ...],
    annotated: str,
    revealed: bool = False,
) -> EvidenceRevealSnapshot:
    return EvidenceRevealSnapshot(
        answer_id=record.answer_id,
        answer=record.answer.preview,
        source_chars=record.answer.source_chars,
        truncated=record.answer.truncated,
        revealed=revealed,
        annotated_answer=annotated,
        claims=claims,
        links=links,
    )


def _terminal_tools(
    tools: Iterable[ToolActivitySnapshot],
) -> tuple[ToolActivitySnapshot, ...]:
    if isinstance(tools, (str, bytes)):
        raise TypeError("tools must contain ToolActivitySnapshot values")
    unique: dict[str, ToolActivitySnapshot] = {}
    for tool in tools:
        if not isinstance(tool, ToolActivitySnapshot):
            raise TypeError("tools must contain ToolActivitySnapshot values")
        if tool.terminal:
            unique.pop(tool.tool_call_id, None)
            unique[tool.tool_call_id] = tool
    return tuple(unique.values())[-MAX_TOOLS_PER_ANSWER:]


def _superscript(number: int) -> str:
    return str(number).translate(_SUPER_DIGITS)


def _annotate(
    answer: str, claims: list[EvidenceClaim], links: list[EvidenceLink]
) -> str:
    markers = {link.number: link.marker for link in links}
    inserts: dict[int, list[str]] = {}
    for claim in claims:
        visible = [markers[number] for number in claim.link_numbers]
        if visible:
            inserts[claim.end] = visible
    if not inserts:
        return answer
    output: list[str] = []
    previous = 0
    for position, values in sorted(inserts.items()):
        output.append(answer[previous:position])
        output.append("\u2009" + ",".join(values))
        previous = position
    output.append(answer[previous:])
    return "".join(output)


__all__ = [
    "EvidenceClaim",
    "EvidenceKind",
    "EvidenceLink",
    "EvidenceLinkModel",
    "EvidenceRevealSnapshot",
]
