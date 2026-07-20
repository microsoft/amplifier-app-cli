"""Bounded claim splitting and conservative evidence matching."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

from .runtime_values import MAX_SOURCE_SCAN_CHARS
from .runtime_values import ToolActivitySnapshot
from .runtime_values import ToolActivityStatus

MAX_CLAIMS = 256
MAX_LINKS_PER_CLAIM = 3
MAX_INLINE_ITEMS = 8
MAX_INLINE_CHARS = 512

_SENTENCE_END = frozenset(".!?")
_TEST_WORD = re.compile(
    r"\b(?:tests?|pytest|unittest|nosetests|jest|vitest|mocha|rspec)\b",
    re.IGNORECASE,
)
_SUCCESS_WORD = re.compile(
    r"\b(?:pass(?:ed|es)?|succeed(?:ed|s)?|successful|green|clean)\b",
    re.IGNORECASE,
)
_FAILURE_WORD = re.compile(
    r"\b(?:fail(?:ed|s|ure)?|errored|unsuccessful)\b", re.IGNORECASE
)
_NO_TESTS_FAILED = re.compile(r"\bno\s+tests?\s+failed\b", re.IGNORECASE)
_TEST_COUNT = re.compile(
    r"\b(?P<count>\d[\d,]*)\s+(?:tests?\s+)?"
    r"(?P<outcome>passed|failed)\b",
    re.IGNORECASE,
)
_TEST_COMMANDS = re.compile(
    r"(?:^|[;&|\s])(?:"
    r"pytest|py\.test|nosetests|tox|jest|vitest|mocha|rspec|"
    r"cargo\s+test|go\s+test|dotnet\s+test|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test|"
    r"python(?:3)?\s+-m\s+unittest|"
    r"(?:mvnw?|gradlew?)\s+[^;&|]*test|make\s+test"
    r")(?:$|[;&|\s])",
    re.IGNORECASE,
)
_FILE_ACTION = re.compile(
    r"\b(?:add(?:ed)?|creat(?:e|ed)|delet(?:e|ed)|edit(?:ed)?|"
    r"modif(?:y|ied)|mov(?:e|ed)|remov(?:e|ed)|renam(?:e|ed)|"
    r"sav(?:e|ed)|updat(?:e|ed)|writ(?:e|ten)|chang(?:e|ed))\b",
    re.IGNORECASE,
)
_FILE_PATH = re.compile(
    r"(?<![\w./-])(?:\.\.?/|/)?(?:[\w@+.-]+/)*"
    r"[\w@+-][\w@+.-]*\.[A-Za-z][A-Za-z0-9]{0,11}"
    r"(?![\w./-])"
)
_COMMAND_ACTION = re.compile(
    r"\b(?:command|execut(?:e|ed)|ran|run|runn(?:ing)?|invok(?:e|ed))\b",
    re.IGNORECASE,
)
_MUTATION_TOOL = re.compile(
    r"(?:^|[_\-.])(?:apply|create|delete|edit|move|patch|remove|rename|"
    r"save|update|write)(?:$|[_\-.])",
    re.IGNORECASE,
)
_MUTATION_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:apply_patch|cp|git\s+apply|install|mkdir|mv|rm|"
    r"sed\s+-i|tee|touch|truncate)\b|(?:^|[^<])>>?\s*\S+",
    re.IGNORECASE,
)


class EvidenceKind(str, Enum):
    TESTS = "tests"
    FILE = "file"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_id: str
    text: str
    start: int
    end: int
    kind: EvidenceKind | None
    link_numbers: tuple[int, ...] = ()


def split_claims(answer: str) -> tuple[EvidenceClaim, ...]:
    spans: list[tuple[int, int]] = []
    offset = 0
    fence: str | None = None
    for line in answer.splitlines(keepends=True):
        body = line.rstrip("\n")
        stripped = body.lstrip()
        marker = stripped[:3] if stripped[:3] in {"```", "~~~"} else None
        if marker is not None:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            offset += len(line)
            continue
        if fence is None:
            spans.extend(_line_claim_spans(body, offset))
            if len(spans) >= MAX_CLAIMS:
                break
        offset += len(line)
    claims = []
    for index, (start, end) in enumerate(spans[:MAX_CLAIMS], start=1):
        text = answer[start:end]
        claims.append(
            EvidenceClaim(
                claim_id=f"claim-{index}",
                text=text,
                start=start,
                end=end,
                kind=_claim_kind(text),
            )
        )
    return tuple(claims)


def supporting_tool_ids(
    claim: EvidenceClaim, tools: tuple[ToolActivitySnapshot, ...]
) -> tuple[str, ...]:
    if claim.kind == EvidenceKind.TESTS:
        match = next(
            (
                tool
                for tool in reversed(tools)
                if _supports_test_claim(claim.text, tool)
            ),
            None,
        )
        return (match.tool_call_id,) if match is not None else ()
    if claim.kind == EvidenceKind.FILE:
        return _file_support(claim.text, tools)
    if claim.kind == EvidenceKind.COMMAND:
        return _command_support(claim.text, tools)
    return ()


def _line_claim_spans(line: str, offset: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    inline_ticks = 0
    index = 0
    while index < len(line):
        if line[index] == "`":
            run = 1
            while index + run < len(line) and line[index + run] == "`":
                run += 1
            inline_ticks = 0 if inline_ticks == run else run
            index += run
            continue
        if (
            inline_ticks == 0
            and line[index] in _SENTENCE_END
            and (index + 1 == len(line) or line[index + 1].isspace())
        ):
            _append_trimmed_span(spans, line, start, index + 1, offset)
            start = index + 1
        index += 1
    _append_trimmed_span(spans, line, start, len(line), offset)
    return spans


def _append_trimmed_span(
    spans: list[tuple[int, int]], line: str, start: int, end: int, offset: int
) -> None:
    while start < end and line[start].isspace():
        start += 1
    while end > start and line[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((offset + start, offset + end))


def _claim_kind(text: str) -> EvidenceKind | None:
    paths = _file_paths(text)
    without_paths = text
    for path in paths:
        without_paths = without_paths.replace(path, " ")
    test_shape = bool(
        _TEST_WORD.search(without_paths) and _claim_outcome(without_paths) is not None
    )
    file_shape = bool(_FILE_ACTION.search(text) and paths)
    command_shape = bool(_COMMAND_ACTION.search(text) and _inline_code(text))
    if test_shape and file_shape:
        return None
    if test_shape:
        return EvidenceKind.TESTS
    if file_shape and command_shape:
        return None
    if file_shape:
        return EvidenceKind.FILE
    if command_shape:
        return EvidenceKind.COMMAND
    return None


def _claim_outcome(text: str) -> ToolActivityStatus | None:
    if _NO_TESTS_FAILED.search(text):
        return ToolActivityStatus.SUCCEEDED
    if _FAILURE_WORD.search(text):
        return ToolActivityStatus.FAILED
    if _SUCCESS_WORD.search(text):
        return ToolActivityStatus.SUCCEEDED
    return None


def _supports_test_claim(text: str, tool: ToolActivitySnapshot) -> bool:
    expected = _claim_outcome(text)
    if expected is None or tool.status != expected or not _is_test_tool(tool):
        return False
    named_commands = _inline_code(text) if _COMMAND_ACTION.search(text) else ()
    if named_commands and not all(
        _command_is_part_of(command, tool.command) for command in named_commands
    ):
        return False
    count = _TEST_COUNT.search(text)
    if count is None:
        return True
    result = tool.result.preview if tool.result is not None else ""
    expected_count = count.group("count").replace(",", "")
    expected_outcome = count.group("outcome").lower()
    return any(
        match.group("count").replace(",", "") == expected_count
        and match.group("outcome").lower() == expected_outcome
        for match in _TEST_COUNT.finditer(result)
    )


def _is_test_tool(tool: ToolActivitySnapshot) -> bool:
    name = tool.tool_name.lower().replace("-", "_")
    if any(part in name.split("_") for part in ("test", "pytest", "jest", "vitest")):
        return True
    return _TEST_COMMANDS.search(tool.command) is not None


def _file_support(
    text: str, tools: tuple[ToolActivitySnapshot, ...]
) -> tuple[str, ...]:
    selected: list[str] = []
    for path in _file_paths(text):
        tool = next(
            (
                candidate
                for candidate in reversed(tools)
                if candidate.status == ToolActivityStatus.SUCCEEDED
                and _is_mutation_tool(candidate)
                and path in _tool_paths(candidate)
            ),
            None,
        )
        if tool is None:
            return ()
        if tool.tool_call_id not in selected:
            selected.append(tool.tool_call_id)
        if len(selected) > MAX_LINKS_PER_CLAIM:
            return ()
    return tuple(selected)


def _file_paths(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0) for match in _FILE_PATH.finditer(text)))[
        :MAX_INLINE_ITEMS
    ]


def _tool_paths(tool: ToolActivitySnapshot) -> frozenset[str]:
    sources = [tool.command, tool.summary, tool.input.preview]
    if tool.result is not None:
        sources.append(tool.result.preview)
    return frozenset(
        path
        for source in sources
        for path in _file_paths(source[:MAX_SOURCE_SCAN_CHARS])
    )


def _is_mutation_tool(tool: ToolActivitySnapshot) -> bool:
    return bool(
        _MUTATION_TOOL.search(tool.tool_name) or _MUTATION_COMMAND.search(tool.command)
    )


def _command_support(
    text: str, tools: tuple[ToolActivitySnapshot, ...]
) -> tuple[str, ...]:
    expected = _claim_outcome(text)
    selected: list[str] = []
    for command in _inline_code(text):
        tool = next(
            (
                candidate
                for candidate in reversed(tools)
                if (expected is None or candidate.status == expected)
                and _command_is_part_of(command, candidate.command)
            ),
            None,
        )
        if tool is None:
            return ()
        if tool.tool_call_id not in selected:
            selected.append(tool.tool_call_id)
        if len(selected) > MAX_LINKS_PER_CLAIM:
            return ()
    return tuple(selected)


def _inline_code(text: str) -> tuple[str, ...]:
    values: list[str] = []
    index = 0
    while index < len(text) and len(values) < MAX_INLINE_ITEMS:
        start = text.find("`", index)
        if start < 0:
            break
        ticks = 1
        while start + ticks < len(text) and text[start + ticks] == "`":
            ticks += 1
        marker = "`" * ticks
        end = text.find(marker, start + ticks)
        if end < 0:
            break
        value = " ".join(text[start + ticks : end].split())[:MAX_INLINE_CHARS]
        if value:
            values.append(value)
        index = end + ticks
    return tuple(dict.fromkeys(values))


def _command_is_part_of(claimed: str, actual: str) -> bool:
    claimed_tokens = _shell_tokens(claimed)
    actual_tokens = _shell_tokens(actual)
    if not claimed_tokens or len(claimed_tokens) > len(actual_tokens):
        return False
    width = len(claimed_tokens)
    return any(
        actual_tokens[index : index + width] == claimed_tokens
        for index in range(len(actual_tokens) - width + 1)
    )


def _shell_tokens(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command[:MAX_SOURCE_SCAN_CHARS]))
    except ValueError:
        return ()
