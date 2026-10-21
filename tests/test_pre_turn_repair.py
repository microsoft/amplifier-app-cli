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
import signal
from copy import deepcopy
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from rich.console import Console

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
        "repair_transcript_if_needed": main.repair_transcript_if_needed,
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


def _headless_messages(case):
    messages = (
        _healthy_messages()[1:]
        if case == "healthy"
        else _ordering_violation_messages()[1:]
        + [{"role": "assistant", "content": "Observed result recorded"}]
    )
    # Persist a canonical assistant payload: native serialization omits None.
    messages[1]["content"] = ""
    receipt = next(m for m in messages if m["role"] == "tool")
    receipt.update(
        caller="fixture-caller",
        cost={"amount": "0.125", "currency": "USD"},
        extra={"receipt": ["fixture", 7]},
        metadata={"source": "observed", "timestamp": "2026-01-01T00:00:00+00:00"},
    )
    if case == "parallel":
        missing = deepcopy(messages[1]["tool_calls"][0])
        missing["id"] = "tc_missing"
        messages[1]["tool_calls"].append(missing)
    return messages


def _without_seq(message):
    """Ignore only Simple's documented sequence restamping, not receipt fields."""
    result = deepcopy(message)
    if "metadata" in result:
        result["metadata"].pop("_seq", None)
        if not result["metadata"]:
            result.pop("metadata")
    return result


def _assert_headless_repair(messages, original, case):
    assert diagnose_transcript(messages)["status"] == "healthy"
    results = [m for m in messages if m["role"] == "tool"]
    observed = [m for m in results if m["tool_call_id"] == "tc_1"]
    assert len(observed) == 1
    assert _without_seq(observed[0]) == next(m for m in original if m["role"] == "tool")
    assert observed[0]["content"] != SYNTHETIC_TOOL_RESULT_CONTENT
    assert {m["tool_call_id"] for m in results} == (
        {"tc_1", "tc_missing"} if case == "parallel" else {"tc_1"}
    )
    if case == "parallel":
        assert results[1]["tool_call_id"] == "tc_missing"
        assert results[1]["content"] == SYNTHETIC_TOOL_RESULT_CONTENT
    call_index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
    assert messages[call_index + 1 : call_index + 1 + len(results)] == results
    assert [_without_seq(m) for m in messages if m["role"] == "user"] == [
        m for m in original if m["role"] == "user"
    ]
    stripped = [_without_seq(m) for m in messages]
    assert all(m in stripped for m in original)


@pytest.fixture
def headless_resume(isolated_home, tmp_path, monkeypatch):
    """Real initializer, Simple context, Foundation and native store; no provider.

    Bundle/session construction, first-run setup and writer acquisition are
    doubles. The normal native-persistence opt-out avoids taking host locks.
    """
    from amplifier_app_cli import session_handoff, session_runner, shared_root_state
    from amplifier_app_cli.session_store import SessionStore
    from amplifier_module_context_simple import SimpleContextManager

    main = importlib.import_module("amplifier_app_cli.main")
    monkeypatch.delenv("AMPLIFIER_HOME", raising=False)
    monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", str(tmp_path / "ci"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "amplifier_app_cli.commands.init.check_first_run", lambda: False
    )
    monkeypatch.setattr(
        shared_root_state, "_shared_root_platform_supported", lambda: False
    )
    monkeypatch.setattr(
        shared_root_state, "warn_windows_native_persistence", lambda: None
    )
    private_console = Console(file=StringIO())
    monkeypatch.setattr(main, "console", private_console)
    close_tty = Mock()
    monkeypatch.setattr(main, "close_dedicated_tty_input", close_tty)
    store = SessionStore()
    monkeypatch.setattr(main, "SessionStore", lambda: store)
    monkeypatch.setattr(session_runner, "SessionStore", lambda: store)

    def make(case="late", *, root=None):
        original = _headless_messages(case)
        store.save(
            "headless-resume",
            original,
            {
                "session_id": "headless-resume",
                "created": "2026-01-01T00:00:00+00:00",
                "name": "Receipt fixture",
                "description": "Preserve observed evidence",
                "fixture_metadata": {"keep": [1, 2]},
            },
        )
        seed, seed_metadata = store.load("headless-resume")
        save = Mock(wraps=store.save)
        monkeypatch.setattr(store, "save", save)
        context = SimpleContextManager(
            max_tokens=100_000, compaction_notice_enabled=False
        )
        hooks = MagicMock()
        hooks.emit = AsyncMock()
        coordinator = MagicMock(session_state={})
        coordinator.get.side_effect = lambda key: {
            "context": context,
            "hooks": hooks,
            "providers": {},
        }.get(key)
        coordinator.get_capability.return_value = None
        session = SimpleNamespace(
            session_id="headless-resume",
            config={},
            coordinator=coordinator,
            execute=AsyncMock(return_value="fixture response"),
            cleanup=AsyncMock(),
        )

        async def create_bundle_session(**kwargs):
            assert await context.get_messages() == []
            session.config = kwargs["config"].config
            return session

        monkeypatch.setattr(
            session_runner, "_create_bundle_session", create_bundle_session
        )
        if root is not None:
            monkeypatch.setattr(
                shared_root_state, "_shared_root_platform_supported", lambda: True
            )
            root.read.return_value = (seed, seed_metadata)
            monkeypatch.setattr(
                session_handoff, "acquire_root", AsyncMock(return_value=root)
            )
            # No real writer registration for this deliberately refusing fake writer.
            monkeypatch.setattr(session_handoff.CLIHandoff, "start", AsyncMock())

        async def invoke(output_format="json"):
            await main.execute_single(
                prompt="Continue the fixture",
                config={},
                search_paths=[],
                verbose=False,
                session_id=session.session_id,
                bundle_name="fixture-bundle",
                initial_transcript=deepcopy(seed),
                output_format=output_format,
            )

        return SimpleNamespace(
            context=context,
            session=session,
            store=store,
            save=save,
            original=deepcopy(original),
            seed=seed,
            seed_metadata=seed_metadata,
            invoke=invoke,
            close_tty=close_tty,
            console=private_console,
        )

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["healthy", "late", "parallel"])
@pytest.mark.parametrize("output_format", ["json", "json-trace", "text"])
async def test_execute_single_repairs_and_checkpoints_before_dispatch(
    headless_resume, capsys, case, output_format
):
    """The actual headless boundary must see repaired memory AND disk on entry."""
    env = headless_resume(case)
    before_execute = []
    original_sigint = signal.getsignal(signal.SIGINT)

    async def execute(prompt):
        messages = await env.context.get_messages()
        _assert_headless_repair(messages, env.original, case)
        assert env.save.call_count == (0 if case == "healthy" else 1)
        persisted, metadata = env.store.load(env.session.session_id)
        assert [_without_seq(m) for m in persisted] == [
            _without_seq(m) for m in messages
        ]
        assert all(metadata[k] == v for k, v in env.seed_metadata.items())
        before_execute.extend(deepcopy(messages))
        await env.context.add_message({"role": "user", "content": prompt})
        await env.context.add_message(
            {"role": "assistant", "content": "fixture response"}
        )
        return "fixture response"

    env.session.execute.side_effect = execute
    await env.invoke(output_format)
    assert signal.getsignal(signal.SIGINT) == original_sigint
    env.session.execute.assert_awaited_once_with("Continue the fixture")
    env.session.cleanup.assert_awaited_once()
    env.close_tty.assert_called_once()
    assert env.save.call_count == (1 if case == "healthy" else 2)
    persisted, metadata = env.store.load(env.session.session_id)
    assert persisted == await env.context.get_messages()
    assert all(metadata[k] == v for k, v in env.seed_metadata.items())
    assert metadata["bundle"] == "fixture-bundle"
    backup = env.store.base_dir / env.session.session_id / "transcript.jsonl.backup"
    backup_messages = [
        json.loads(line) for line in backup.read_text(encoding="utf-8").splitlines()
    ]
    assert [_without_seq(m) for m in backup_messages] == [
        _without_seq(m) for m in before_execute
    ]
    output = capsys.readouterr()
    if output_format != "text":
        payload = json.loads(output.out)  # Exactly one object; no repair diagnostics.
        assert payload["status"] == "success"
        assert payload["response"] == "fixture response"
        assert env.console._file.getvalue() == ""
        if output_format == "json-trace":
            assert payload["execution_trace"] == []
    else:
        assert "fixture response" in env.console._file.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["native", "refusing-root"])
async def test_execute_single_failed_repair_checkpoint_never_dispatches_or_retries(
    headless_resume, capsys, writer
):
    root = None
    if writer == "refusing-root":
        root = SimpleNamespace(
            read=Mock(),
            checkpoint=Mock(side_effect=RuntimeError("writer refused")),
            release=Mock(),
        )
    env = headless_resume(root=root)
    if root is None:
        env.save.side_effect = OSError("checkpoint failed")
    original_sigint = signal.getsignal(signal.SIGINT)
    with pytest.raises(SystemExit) as exc:
        await env.invoke()
    assert exc.value.code == 1
    assert signal.getsignal(signal.SIGINT) == original_sigint
    env.session.execute.assert_not_awaited()
    env.session.cleanup.assert_awaited_once()
    env.close_tty.assert_called_once()
    assert env.store.load(env.session.session_id) == (env.seed, env.seed_metadata)
    if root is None:
        assert env.save.call_count == 1
    else:
        root.checkpoint.assert_called_once()
        root.release.assert_called_once()
        env.save.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["error"] == (
        "checkpoint failed" if root is None else "writer refused"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_execute_single_repair_checkpoint_does_not_suppress_failed_turn_save(
    headless_resume, capsys, failure
):
    env = headless_resume()
    appended = []

    async def execute(prompt):
        assert env.save.call_count == 1
        messages = await env.context.get_messages()
        _assert_headless_repair(messages, env.original, "late")
        assert env.store.load(env.session.session_id)[0] == messages
        await env.context.add_message({"role": "user", "content": prompt})
        await env.context.add_message(
            {"role": "assistant", "content": "partial response"}
        )
        appended.extend(deepcopy(await env.context.get_messages()))
        raise failure("turn interrupted")

    async def cleanup():
        assert env.save.call_count == 2
        assert env.store.load(env.session.session_id)[0] == appended

    env.session.execute.side_effect = execute
    env.session.cleanup.side_effect = cleanup
    original_sigint = signal.getsignal(signal.SIGINT)
    expected = SystemExit if failure is RuntimeError else asyncio.CancelledError
    with pytest.raises(expected):
        await env.invoke()
    assert signal.getsignal(signal.SIGINT) == original_sigint
    env.session.cleanup.assert_awaited_once()
    env.close_tty.assert_called_once()
    assert env.save.call_count == 2
    assert (
        env.store.load(env.session.session_id)[0][-1]["content"] == "partial response"
    )
    output = capsys.readouterr().out
    if failure is RuntimeError:
        assert json.loads(output)["status"] == "error"
    else:
        assert output == ""


@pytest.mark.asyncio
async def test_execute_single_rechecks_handoff_after_repair_checkpoint(
    headless_resume, monkeypatch
):
    from amplifier_app_cli.session_handoff import CLIHandoff

    env = headless_resume()
    controls = []

    async def start(control):
        controls.append(control)

    async def finish(control, save, after_cleanup):
        # Only the ownership protocol is faked, not repair, persistence or dispatch.
        assert control.requested.is_set()
        await save()
        await control.initialized.cleanup()
        await after_cleanup()

    monkeypatch.setattr(CLIHandoff, "start", start)
    monkeypatch.setattr(CLIHandoff, "finish", finish)
    real_get_messages = env.context.get_messages

    async def get_messages():
        # Count only after initialization: scan first, then _persist_session.
        if controls:
            get_messages.calls += 1
            if get_messages.calls == 2:
                controls[0].requested.set()
        return await real_get_messages()

    get_messages.calls = 0
    monkeypatch.setattr(env.context, "get_messages", get_messages)
    await env.invoke()
    env.session.execute.assert_not_awaited()
    assert env.save.call_count == 1
    env.session.cleanup.assert_awaited_once()
    env.close_tty.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ImportError, RuntimeError, asyncio.CancelledError])
async def test_shared_repair_checkpoint_errors_propagate(failure):
    from amplifier_app_cli.session_runner import repair_transcript_if_needed

    context = SimpleNamespace(
        get_messages=AsyncMock(return_value=_ordering_violation_messages()),
        set_messages=AsyncMock(),
    )
    session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: context))
    save = AsyncMock(side_effect=failure("checkpoint failed"))
    with pytest.raises(failure, match="checkpoint failed"):
        await repair_transcript_if_needed(session, save)
    context.set_messages.assert_awaited_once()
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_repair_leaves_ambiguous_receipts_broken_without_discarding_evidence():
    from amplifier_app_cli.session_runner import repair_transcript_if_needed
    from amplifier_module_context_simple import SimpleContextManager

    original = _headless_messages("late")
    duplicate = deepcopy(next(m for m in original if m["role"] == "tool"))
    duplicate["content"] = "A different observed result"
    original.append(duplicate)
    assert diagnose_transcript(original)["status"] == "broken"
    context = SimpleContextManager(max_tokens=100_000, compaction_notice_enabled=False)
    await context.set_messages(original)
    session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: context))
    save = AsyncMock()
    await repair_transcript_if_needed(session, save)
    messages = await context.get_messages()
    assert [_without_seq(m) for m in messages] == original
    assert diagnose_transcript(messages)["status"] == "broken"
    save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["diagnose", "foundation", "get", "set", "lookup"])
async def test_shared_repair_scan_and_context_failures_are_best_effort(
    monkeypatch, stage
):
    from amplifier_app_cli.session_runner import repair_transcript_if_needed

    context = SimpleNamespace(
        get_messages=AsyncMock(return_value=_ordering_violation_messages()),
        set_messages=AsyncMock(),
    )
    lookup = Mock(return_value=context)
    if stage == "diagnose":
        monkeypatch.setattr(
            "amplifier_foundation.session.diagnose_transcript",
            Mock(side_effect=RuntimeError("diagnosis failed")),
        )
    elif stage == "foundation":
        monkeypatch.delattr("amplifier_foundation.session.repair_transcript")
    elif stage in ("get", "set"):
        getattr(context, f"{stage}_messages").side_effect = RuntimeError(
            "context failed"
        )
    else:
        lookup.side_effect = RuntimeError("lookup failed")
    session = SimpleNamespace(coordinator=SimpleNamespace(get=lookup))
    save = AsyncMock()
    await repair_transcript_if_needed(session, save)
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["absent", "no-get", "no-set", "empty", "healthy"])
async def test_shared_repair_unavailable_or_healthy_context_does_not_save(state):
    from amplifier_app_cli.session_runner import repair_transcript_if_needed

    context = SimpleNamespace(
        get_messages=AsyncMock(
            return_value=[] if state == "empty" else _healthy_messages()
        ),
        set_messages=AsyncMock(),
    )
    if state == "absent":
        context = None
    elif state in ("no-get", "no-set"):
        delattr(context, "get_messages" if state == "no-get" else "set_messages")
    session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: context))
    save = AsyncMock()
    await repair_transcript_if_needed(session, save)
    save.assert_not_awaited()
