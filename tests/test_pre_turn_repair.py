"""Tests for pre-turn transcript repair.

Verifies that the foundation's diagnose_transcript/repair_transcript
functions work correctly on in-memory context messages (no ``line_num``
keys) — the exact contract the pre-turn repair helper relies on.
"""

import json
from copy import deepcopy

import pytest

from amplifier_foundation.session import (
    diagnose_transcript,
    find_orphaned_tool_calls,
    repair_transcript,
)
from amplifier_foundation.session.diagnosis import (
    SYNTHETIC_TOOL_RESULT_CONTENT,
)


# ---------------------------------------------------------------------------
# Fixtures — reusable message sets
# ---------------------------------------------------------------------------


def _healthy_messages():
    """Messages with a completed tool call cycle — no damage."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                    "tool": "bash",
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_1",
            "name": "bash",
            "content": "file1.py\nfile2.py",
        },
        {"role": "assistant", "content": "Here are the files: file1.py, file2.py"},
    ]


def _orphaned_tool_call_messages():
    """Messages where Ctrl+C killed the tool execution mid-flight.

    The assistant requested ``bash`` (tc_1), but no tool result was ever
    recorded — the process was interrupted before the result could be
    written back.
    """
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                    "tool": "bash",
                }
            ],
        },
        # ← missing tool result for tc_1 — this is the orphan
        {"role": "user", "content": "What happened?"},
    ]


def _ordering_violation_messages():
    """Messages where a user message got interleaved between tool_call and
    its result — the result EXISTS but is in the wrong position.
    """
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                    "tool": "bash",
                }
            ],
        },
        # User message interrupts the tool call/result pair
        {"role": "user", "content": "Never mind"},
        {
            "role": "tool",
            "tool_call_id": "tc_1",
            "name": "bash",
            "content": "file1.py",
        },
    ]


def _incomplete_turn_messages():
    """Messages where tool results exist but the final assistant response
    is missing — the assistant never got to summarize.
    """
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                    "tool": "bash",
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_1",
            "name": "bash",
            "content": "file1.py",
        },
        # ← missing assistant summary — next message is the user
        {"role": "user", "content": "What happened?"},
    ]


# ---------------------------------------------------------------------------
# Tests — diagnose_transcript on in-memory messages (no line_num keys)
# ---------------------------------------------------------------------------


class TestDiagnoseTranscriptInMemory:
    """Verify diagnose_transcript works on messages without ``line_num``.

    The pre-turn repair operates on ``context.get_messages()`` output,
    which are plain dicts — no ``line_num`` annotation.  These tests
    confirm the foundation library accepts that format.
    """

    def test_healthy_messages_report_healthy(self):
        messages = _healthy_messages()
        diagnosis = diagnose_transcript(messages)

        assert diagnosis["status"] == "healthy"
        assert diagnosis["failure_modes"] == []
        assert diagnosis["orphaned_tool_ids"] == []
        assert diagnosis["recommended_action"] == "none"

    def test_orphaned_tool_call_detected(self):
        messages = _orphaned_tool_call_messages()
        diagnosis = diagnose_transcript(messages)

        assert diagnosis["status"] == "broken"
        assert "missing_tool_results" in diagnosis["failure_modes"]
        assert "tc_1" in diagnosis["orphaned_tool_ids"]
        assert diagnosis["recommended_action"] == "repair"

    def test_ordering_violation_detected(self):
        messages = _ordering_violation_messages()
        diagnosis = diagnose_transcript(messages)

        assert diagnosis["status"] == "broken"
        assert "ordering_violation" in diagnosis["failure_modes"]
        assert "tc_1" in diagnosis["misplaced_tool_ids"]

    def test_incomplete_turn_detected(self):
        messages = _incomplete_turn_messages()
        diagnosis = diagnose_transcript(messages)

        assert diagnosis["status"] == "broken"
        assert "incomplete_assistant_turn" in diagnosis["failure_modes"]


# ---------------------------------------------------------------------------
# Tests — repair_transcript produces valid context messages
# ---------------------------------------------------------------------------


class TestRepairTranscriptInMemory:
    """Verify repair_transcript returns messages suitable for
    ``context.set_messages()`` — plain dicts, no ``line_num`` keys.
    """

    def test_repair_injects_synthetic_tool_result(self):
        messages = _orphaned_tool_call_messages()
        diagnosis = diagnose_transcript(messages)
        repaired = repair_transcript(messages, diagnosis)

        # The orphaned tc_1 should now have a synthetic tool result
        tool_results = [m for m in repaired if m.get("role") == "tool"]
        assert len(tool_results) == 1
        assert tool_results[0]["tool_call_id"] == "tc_1"
        assert tool_results[0]["content"] == SYNTHETIC_TOOL_RESULT_CONTENT

    def test_repair_preserves_all_original_roles(self):
        messages = _orphaned_tool_call_messages()
        diagnosis = diagnose_transcript(messages)
        repaired = repair_transcript(messages, diagnosis)

        # Original system, user, and assistant messages must survive
        roles = [m["role"] for m in repaired]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_repair_produces_no_line_num_keys(self):
        messages = _orphaned_tool_call_messages()
        diagnosis = diagnose_transcript(messages)
        repaired = repair_transcript(messages, diagnosis)

        for msg in repaired:
            assert "line_num" not in msg, (
                f"line_num leaked into repaired message: {msg}"
            )

    def test_healthy_transcript_unchanged(self):
        messages = _healthy_messages()
        diagnosis = diagnose_transcript(messages)
        repaired = repair_transcript(messages, diagnosis)

        assert len(repaired) == len(messages)
        for orig, fixed in zip(messages, repaired):
            assert orig["role"] == fixed["role"]

    def test_ordering_violation_repaired(self):
        messages = _ordering_violation_messages()
        diagnosis = diagnose_transcript(messages)
        repaired = repair_transcript(messages, diagnosis)

        # The misplaced real tool result should be removed and replaced
        # with a synthetic result placed right after the assistant message.
        # Verify there's exactly one tool result and it's synthetic.
        tool_results = [m for m in repaired if m.get("role") == "tool"]
        assert len(tool_results) == 1
        assert tool_results[0]["content"] == SYNTHETIC_TOOL_RESULT_CONTENT

    def test_multi_tool_orphan_all_repaired(self):
        """Assistant with 2 tool_calls, both orphaned."""
        messages = [
            {"role": "user", "content": "Do two things"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_a",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                        "tool": "bash",
                    },
                    {
                        "id": "tc_b",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                        "tool": "read_file",
                    },
                ],
            },
            {"role": "user", "content": "What happened?"},
        ]
        diagnosis = diagnose_transcript(messages)
        assert diagnosis["status"] == "broken"
        assert set(diagnosis["orphaned_tool_ids"]) == {"tc_a", "tc_b"}

        repaired = repair_transcript(messages, diagnosis)
        tool_results = [m for m in repaired if m.get("role") == "tool"]
        assert len(tool_results) == 2
        result_ids = {m["tool_call_id"] for m in tool_results}
        assert result_ids == {"tc_a", "tc_b"}
