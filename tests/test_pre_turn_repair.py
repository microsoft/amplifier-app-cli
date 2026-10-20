"""Tests for pre-turn transcript repair.

Verifies that the foundation's diagnose_transcript/repair_transcript
functions work correctly on in-memory context messages (no ``line_num``
keys) — the exact contract the pre-turn repair helper relies on.
"""

import ast
import asyncio
import importlib
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from amplifier_foundation.session import (
    diagnose_transcript,
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
        original = deepcopy(messages)
        diagnosis = diagnose_transcript(messages)
        repaired = repair_transcript(messages, diagnosis)

        # Moving the observed receipt changes chronology, not its payload.
        tool_results = [m for m in repaired if m.get("role") == "tool"]
        assert len(tool_results) == 1
        assert tool_results[0] == original[-1]
        assert tool_results[0]["content"] == "file1.py"
        call_index = next(i for i, m in enumerate(repaired) if m.get("tool_calls"))
        assert repaired[call_index + 1] == tool_results[0]
        assert diagnose_transcript(repaired)["status"] == "healthy"
        assert messages == original

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


def _installed_interactive_helpers(**bindings):
    """Execute unchanged helper bodies, not a CLI launch or a save shim."""
    main = importlib.import_module("amplifier_app_cli.main")
    source = Path(main.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    interactive = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "interactive_chat"
    )
    names = {"_extract_model_name", "_save_session", "_repair_transcript_if_needed"}
    functions = [
        node
        for node in interactive.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    assert {node.name for node in functions} == names
    namespace = {
        "datetime": datetime,
        "UTC": UTC,
        "Path": Path,
        "logger": logging.getLogger(__name__),
        **bindings,
    }
    # Trusted installed CLI definitions only; never transcript or user input.
    exec(  # noqa: S102
        compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["healthy", "late", "parallel"])
async def test_pre_turn_receipts_survive_native_save_and_resume(
    tmp_path, monkeypatch, case
):
    """Real installed Foundation/context/store; no shared-root lock or full REPL."""
    from amplifier_app_cli import session_runner
    from amplifier_app_cli.session_store import SessionStore
    from amplifier_module_context_simple import SimpleContextManager

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))
    monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", str(tmp_path / "ci"))
    monkeypatch.chdir(tmp_path)
    if case == "healthy":
        messages = _healthy_messages()[1:]
    else:
        messages = _ordering_violation_messages()[1:]
        messages.append({"role": "assistant", "content": "Observed result recorded"})
    receipt = next(m for m in messages if m["role"] == "tool")
    receipt["metadata"] = {"receipt": 7, "source": "observed"}
    if case == "parallel":
        missing = deepcopy(messages[1]["tool_calls"][0])
        missing["id"] = "tc_missing"
        messages[1]["tool_calls"].append(missing)
    original = deepcopy(messages)
    context = SimpleContextManager(max_tokens=100_000, compaction_notice_enabled=False)
    await context.set_messages(messages)
    # Initialization legitimately adds metadata such as _seq.
    canonical = deepcopy(await context.get_messages())
    store = SessionStore(base_dir=tmp_path / "sessions")
    store.save(
        "fixture",
        canonical,
        {
            "session_id": "fixture",
            "name": "Receipt test",
            "description": "Keep observed results",
            "created": "2026-01-01T00:00:00+00:00",
        },
    )
    seed_metadata = store.get_metadata("fixture")
    save = Mock(wraps=store.save)
    monkeypatch.setattr(store, "save", save)
    session = MagicMock(config={})
    session.coordinator.get.side_effect = lambda key: (
        context if key == "context" else None
    )
    session.coordinator.get_capability.return_value = None
    helpers = _installed_interactive_helpers(
        session=session,
        store=store,
        actual_session_id="fixture",
        config={"providers": [{"config": {"model": "fixture-model"}}]},
        bundle_name="fixture-bundle",
        initialized=SimpleNamespace(root_state=None),
        handoff=SimpleNamespace(requested=asyncio.Event()),
    )
    monkeypatch.setattr(session_runner, "SessionStore", lambda: store)
    monkeypatch.setattr(
        session_runner, "_create_bundle_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(
        "amplifier_app_cli.commands.init.check_first_run", lambda: False
    )
    await helpers["_repair_transcript_if_needed"]()
    assert save.call_count == (0 if case == "healthy" else 1)
    # Context may re-stamp _seq on repair; retain this initialized canonical form.
    repaired = deepcopy(await context.get_messages())
    expected_receipt = next(m for m in repaired if m.get("tool_call_id") == "tc_1")
    assert all(
        expected_receipt["metadata"][k] == v for k, v in receipt["metadata"].items()
    )
    for _ in range(4):
        await helpers["_save_session"]()
        persisted, metadata = store.load("fixture")
        results = [m for m in persisted if m["role"] == "tool"]
        observed = [m for m in results if m["tool_call_id"] == "tc_1"]
        assert len(observed) == 1
        assert observed[0]["content"] == receipt["content"]
        assert observed[0]["name"] == expected_receipt["name"]
        assert observed[0]["metadata"] == expected_receipt["metadata"]
        assert len(results) == (2 if case == "parallel" else 1)
        call_index = next(i for i, m in enumerate(persisted) if m.get("tool_calls"))
        assert persisted[call_index + 1 : call_index + 1 + len(results)] == results
        if case == "parallel":
            missing_results = [m for m in results if m["tool_call_id"] == "tc_missing"]
            assert len(missing_results) == 1
            assert missing_results[0]["content"] == SYNTHETIC_TOOL_RESULT_CONTENT
        assert diagnose_transcript(persisted)["status"] == "healthy"
        assert [m["content"] for m in persisted if m["role"] == "user"] == [
            m["content"] for m in original if m["role"] == "user"
        ]
        assert all(
            metadata[key] == seed_metadata[key]
            for key in ("created", "name", "description")
        )
        assert metadata["bundle"] == "fixture-bundle"
        assert metadata["model"] == "fixture-model"
        assert store.get_metadata("fixture") == metadata
        context = SimpleContextManager(
            max_tokens=100_000, compaction_notice_enabled=False
        )
        cfg = session_runner.SessionConfig(
            config={},
            search_paths=[],
            verbose=False,
            session_id="fixture",
            initial_transcript=persisted,
        )
        initialized = await session_runner.create_initialized_session(cfg, MagicMock())
        assert await context.get_messages() == persisted
        helpers.update(session=initialized.session, initialized=initialized)
        saves_before = save.call_count
        await helpers["_repair_transcript_if_needed"]()
        assert save.call_count == saves_before
    await helpers["_save_session"]()
    assert store.load("fixture")[0] == persisted
    backup = store.base_dir / "fixture" / "transcript.jsonl.backup"
    assert [
        json.loads(line) for line in backup.read_text(encoding="utf-8").splitlines()
    ] == persisted
    assert messages == original
