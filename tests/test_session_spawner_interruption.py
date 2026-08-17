"""Deterministic cancellation coverage for in-process sub-sessions."""

import asyncio
from typing import TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_app_cli.session_spawner import resume_sub_session, spawn_sub_session

pytestmark = pytest.mark.anyio
SYNC_TIMEOUT_SECONDS = 5
T = TypeVar("T")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


class FakeHooks:
    def __init__(self):
        self.unregister = MagicMock()
        self.emit = AsyncMock()

    def register(self, event, handler, priority=0, name=None):
        return self.unregister


def make_child(transcript):
    hooks = FakeHooks()
    context = MagicMock()
    context.get_messages = AsyncMock(return_value=transcript)
    context.add_message = AsyncMock()

    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability.return_value = None
    coordinator.mount = AsyncMock()
    coordinator.collect_contributions = AsyncMock(return_value=[])
    coordinator.cancellation = MagicMock()

    def get_component(name):
        return {"hooks": hooks, "context": context}.get(name)

    coordinator.get = get_component

    child = MagicMock()
    child.coordinator = coordinator
    child.initialize = AsyncMock()
    child.cleanup = AsyncMock()
    return child, hooks, context


def make_parent(display=None):
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get.return_value = None
    coordinator.get_capability.return_value = None
    coordinator.display_system = display or MagicMock()
    coordinator.cancellation = MagicMock()

    parent = MagicMock()
    parent.coordinator = coordinator
    parent.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"}
    }
    parent.session_id = "parent-123"
    parent.trace_id = "trace-abc"
    parent.loader = None
    return parent


async def wait_for_event(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=SYNC_TIMEOUT_SECONDS)


async def await_task(
    task: asyncio.Task[T], *, timeout: float = SYNC_TIMEOUT_SECONDS
) -> T:
    return await asyncio.wait_for(task, timeout=timeout)


async def test_await_task_timeout_cancels_and_reaps_task():
    blocker = asyncio.Event()
    cancellation_observed = asyncio.Event()

    async def wait_forever():
        try:
            await blocker.wait()
        finally:
            cancellation_observed.set()

    task = asyncio.create_task(wait_forever())

    with pytest.raises(TimeoutError):
        await await_task(task, timeout=0.01)

    assert task.done()
    assert task.cancelled()
    assert cancellation_observed.is_set()


async def test_spawn_cancellation_persists_partial_transcript_and_unwinds_once():
    partial = [
        {"role": "user", "content": "work"},
        {"role": "assistant", "content": "partial"},
    ]
    child, hooks, context = make_child(partial)
    cancellation = asyncio.CancelledError("cancel spawn")
    child.execute = AsyncMock(side_effect=cancellation)
    parent = make_parent()
    store = MagicMock()

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        pytest.raises(asyncio.CancelledError) as raised,
    ):
        await spawn_sub_session(
            agent_name="test-agent",
            instruction="do work",
            parent_session=parent,
            agent_configs={"test-agent": {"description": "test"}},
            sub_session_id="parent-abcdef_test-agent",
        )

    assert raised.value is cancellation
    store.save.assert_called_once()
    session_id, saved_transcript, saved_metadata = store.save.call_args.args
    assert session_id == "parent-abcdef_test-agent"
    assert saved_transcript == partial
    assert saved_metadata["status"] == "interrupted"
    assert saved_metadata["session_id"] == session_id
    assert saved_metadata["parent_id"] == "parent-123"
    assert saved_metadata["trace_id"] == "trace-abc"
    assert saved_metadata["agent_name"] == "test-agent"
    assert saved_metadata["config"]["session"] == parent.config["session"]
    assert saved_metadata["agent_overlay"] == {"description": "test"}
    context.get_messages.assert_awaited_once()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    parent.coordinator.display_system.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_resume_cancellation_preserves_metadata_and_unwinds_once():
    original = [{"role": "user", "content": "first"}]
    partial = original + [{"role": "assistant", "content": "partial follow-up"}]
    child, hooks, context = make_child(partial)
    cancellation = asyncio.CancelledError("cancel resume")
    child.execute = AsyncMock(side_effect=cancellation)
    display = MagicMock()
    parent = make_parent()
    store = MagicMock()
    metadata = {
        "session_id": "resumed-child",
        "parent_id": "parent-123",
        "agent_name": "test-agent",
        "trace_id": "trace-abc",
        "config": {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"}
        },
        "working_dir": "/fixed/project",
        "resume_marker": "keep-me",
    }
    store.exists.return_value = True
    store.load.return_value = (original, metadata)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem", return_value=display),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        pytest.raises(asyncio.CancelledError) as raised,
    ):
        await resume_sub_session("resumed-child", "continue", parent)

    assert raised.value is cancellation
    store.save.assert_called_once()
    session_id, saved_transcript, saved_metadata = store.save.call_args.args
    assert session_id == "resumed-child"
    assert saved_transcript == partial
    assert saved_metadata["status"] == "interrupted"
    assert saved_metadata["resume_marker"] == "keep-me"
    assert saved_metadata["config"] == metadata["config"]
    assert saved_metadata["turn_count"] == len(partial)
    assert "last_updated" in saved_metadata
    context.get_messages.assert_awaited_once()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    display.push_nesting.assert_called_once_with()
    display.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_spawn_cancel_during_post_execute_get_messages_persists_interrupted_once():
    partial = [{"role": "assistant", "content": "completed before persistence"}]
    child, hooks, context = make_child(partial)
    child.execute = AsyncMock(return_value="done")
    parent = make_parent()
    store = MagicMock()
    transcript_started = asyncio.Event()
    transcript_cancellations = []
    transcript_reads = 0

    async def get_messages():
        nonlocal transcript_reads
        transcript_reads += 1
        if transcript_reads == 1:
            transcript_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                transcript_cancellations.append(error)
                raise
        return partial

    context.get_messages = AsyncMock(side_effect=get_messages)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
    ):
        task = asyncio.create_task(
            spawn_sub_session(
                agent_name="test-agent",
                instruction="do work",
                parent_session=parent,
                agent_configs={"test-agent": {"description": "test"}},
                sub_session_id="post-execute-cancel-spawn",
            )
        )
        await wait_for_event(transcript_started)
        task.cancel("cancel spawn transcript read")

        with pytest.raises(asyncio.CancelledError) as raised:
            await await_task(task)

    assert raised.value is transcript_cancellations[0]
    assert context.get_messages.await_count == 2
    store.save.assert_called_once()
    assert store.save.call_args.args[0] == "post-execute-cancel-spawn"
    assert store.save.call_args.args[1] == partial
    assert store.save.call_args.args[2]["status"] == "interrupted"
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    parent.coordinator.display_system.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_resume_cancel_during_post_execute_get_messages_persists_interrupted_once():
    original = [{"role": "user", "content": "first turn"}]
    partial = original + [{"role": "assistant", "content": "completed follow-up"}]
    child, hooks, context = make_child(partial)
    child.execute = AsyncMock(return_value="done")
    parent = make_parent()
    display = MagicMock()
    store = MagicMock()
    metadata = {
        "session_id": "post-execute-cancel-resume",
        "parent_id": "parent-123",
        "agent_name": "test-agent",
        "config": {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"}
        },
        "working_dir": "/fixed/project",
        "resume_marker": "preserved",
    }
    store.exists.return_value = True
    store.load.return_value = (original, metadata)
    transcript_started = asyncio.Event()
    transcript_cancellations = []
    transcript_reads = 0

    async def get_messages():
        nonlocal transcript_reads
        transcript_reads += 1
        if transcript_reads == 1:
            transcript_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                transcript_cancellations.append(error)
                raise
        return partial

    context.get_messages = AsyncMock(side_effect=get_messages)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem", return_value=display),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
    ):
        task = asyncio.create_task(
            resume_sub_session("post-execute-cancel-resume", "continue", parent)
        )
        await wait_for_event(transcript_started)
        task.cancel("cancel resume transcript read")

        with pytest.raises(asyncio.CancelledError) as raised:
            await await_task(task)

    assert raised.value is transcript_cancellations[0]
    assert context.get_messages.await_count == 2
    store.save.assert_called_once()
    session_id, saved_transcript, saved_metadata = store.save.call_args.args
    assert session_id == "post-execute-cancel-resume"
    assert saved_transcript == partial
    assert saved_metadata["status"] == "interrupted"
    assert saved_metadata["resume_marker"] == "preserved"
    assert saved_metadata["turn_count"] == len(partial)
    assert "last_updated" in saved_metadata
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    display.push_nesting.assert_called_once_with()
    display.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_spawn_setup_cancellation_unwinds_resources_once():
    child, hooks, _ = make_child([])
    child.execute = AsyncMock()
    parent = make_parent()
    cancellation = asyncio.CancelledError("cancel mention expansion")

    def get_capability(name):
        return object() if name == "mention_resolver" else None

    child.coordinator.get_capability.side_effect = get_capability

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch(
            "amplifier_foundation.mentions.expand_mentions_in_instruction",
            new=AsyncMock(side_effect=cancellation),
        ),
        pytest.raises(asyncio.CancelledError) as raised,
    ):
        await spawn_sub_session(
            agent_name="test-agent",
            instruction="expand @mention",
            parent_session=parent,
            agent_configs={"test-agent": {"description": "test"}},
            sub_session_id="setup-cancelled-child",
        )

    assert raised.value is cancellation
    child.execute.assert_not_awaited()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    parent.coordinator.display_system.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_resume_setup_cancellation_unwinds_resources_once():
    child, hooks, _ = make_child([])
    child.execute = AsyncMock()
    parent = make_parent()
    display = MagicMock()
    cancellation = asyncio.CancelledError("cancel resume mention expansion")
    store = MagicMock()
    store.exists.return_value = True
    store.load.return_value = (
        [],
        {
            "session_id": "resumed-child",
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {
                    "orchestrator": "loop-basic",
                    "context": "context-simple",
                }
            },
            "working_dir": "/fixed/project",
        },
    )

    def get_capability(name):
        return object() if name == "mention_resolver" else None

    child.coordinator.get_capability.side_effect = get_capability

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem", return_value=display),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        patch(
            "amplifier_foundation.mentions.expand_mentions_in_instruction",
            new=AsyncMock(side_effect=cancellation),
        ),
        pytest.raises(asyncio.CancelledError) as raised,
    ):
        await resume_sub_session("resumed-child", "expand @mention", parent)

    assert raised.value is cancellation
    child.execute.assert_not_awaited()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    display.push_nesting.assert_called_once_with()
    display.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_spawn_preserves_cancellation_when_persistence_and_teardown_fail():
    child, hooks, context = make_child([{"role": "assistant", "content": "partial"}])
    cancellation = asyncio.CancelledError("original cancellation")
    child.execute = AsyncMock(side_effect=cancellation)
    child.cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    hooks.unregister.side_effect = RuntimeError("hook unregister failed")
    parent = make_parent()
    parent.coordinator.cancellation.unregister_child.side_effect = RuntimeError(
        "token unregister failed"
    )
    parent.coordinator.display_system.pop_nesting.side_effect = RuntimeError(
        "display pop failed"
    )
    store = MagicMock()
    store.save.side_effect = OSError("save failed")

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        pytest.raises(asyncio.CancelledError) as raised,
    ):
        await spawn_sub_session(
            agent_name="test-agent",
            instruction="do work",
            parent_session=parent,
            agent_configs={"test-agent": {"description": "test"}},
            sub_session_id="failure-child",
        )

    assert raised.value is cancellation
    context.get_messages.assert_awaited_once_with()
    store.save.assert_called_once()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    parent.coordinator.display_system.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_successful_resume_clears_interrupted_status():
    child, _, context = make_child([{"role": "assistant", "content": "complete"}])
    child.execute = AsyncMock(return_value="done")
    store = MagicMock()
    metadata = {
        "session_id": "resumed-child",
        "parent_id": "parent-123",
        "agent_name": "test-agent",
        "config": {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"}
        },
        "working_dir": "/fixed/project",
        "status": "interrupted",
    }
    store.exists.return_value = True
    store.load.return_value = ([], metadata)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
    ):
        result = await resume_sub_session("resumed-child", "continue")

    assert set(result) == {"output", "session_id", "status", "turn_count", "metadata"}
    assert result["output"] == "done"
    store.save.assert_called_once()
    assert "status" not in store.save.call_args.args[2]
    context.get_messages.assert_awaited_once_with()


async def test_spawn_repeated_cancellation_cannot_interrupt_save_or_cleanup():
    partial = [{"role": "assistant", "content": "partial spawn"}]
    child, hooks, context = make_child(partial)
    parent = make_parent()
    store = MagicMock()
    execute_started = asyncio.Event()
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    execution_cancellations = []

    async def execute(_instruction):
        execute_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            execution_cancellations.append(error)
            raise

    async def get_messages():
        transcript_started.set()
        await release_transcript.wait()
        return partial

    async def cleanup():
        cleanup_started.set()
        await release_cleanup.wait()

    child.execute = AsyncMock(side_effect=execute)
    context.get_messages = AsyncMock(side_effect=get_messages)
    child.cleanup = AsyncMock(side_effect=cleanup)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
    ):
        task = asyncio.create_task(
            spawn_sub_session(
                agent_name="test-agent",
                instruction="do work",
                parent_session=parent,
                agent_configs={"test-agent": {"description": "test"}},
                sub_session_id="repeated-cancel-spawn",
            )
        )
        await wait_for_event(execute_started)
        task.cancel("original spawn cancellation")
        await wait_for_event(transcript_started)
        task.cancel("cancel while reading transcript")
        release_transcript.set()
        await wait_for_event(cleanup_started)
        task.cancel("cancel while cleaning up")
        release_cleanup.set()

        with pytest.raises(asyncio.CancelledError) as raised:
            await await_task(task)

    assert raised.value is execution_cancellations[0]
    store.save.assert_called_once()
    assert store.save.call_args.args[1] == partial
    assert store.save.call_args.args[2]["status"] == "interrupted"
    context.get_messages.assert_awaited_once_with()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    parent.coordinator.display_system.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_resume_repeated_cancellation_cannot_interrupt_save_or_cleanup():
    original = [{"role": "user", "content": "first turn"}]
    partial = original + [{"role": "assistant", "content": "partial resume"}]
    child, hooks, context = make_child(partial)
    parent = make_parent()
    display = MagicMock()
    store = MagicMock()
    store.exists.return_value = True
    store.load.return_value = (
        original,
        {
            "session_id": "repeated-cancel-resume",
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {
                    "orchestrator": "loop-basic",
                    "context": "context-simple",
                }
            },
            "working_dir": "/fixed/project",
            "resume_marker": "preserved",
        },
    )
    execute_started = asyncio.Event()
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    execution_cancellations = []

    async def execute(_instruction):
        execute_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            execution_cancellations.append(error)
            raise

    async def get_messages():
        transcript_started.set()
        await release_transcript.wait()
        return partial

    async def cleanup():
        cleanup_started.set()
        await release_cleanup.wait()

    child.execute = AsyncMock(side_effect=execute)
    context.get_messages = AsyncMock(side_effect=get_messages)
    child.cleanup = AsyncMock(side_effect=cleanup)

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem", return_value=display),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
    ):
        task = asyncio.create_task(
            resume_sub_session("repeated-cancel-resume", "continue", parent)
        )
        await wait_for_event(execute_started)
        task.cancel("original resume cancellation")
        await wait_for_event(transcript_started)
        task.cancel("cancel while reading resumed transcript")
        release_transcript.set()
        await wait_for_event(cleanup_started)
        task.cancel("cancel while cleaning up resumed child")
        release_cleanup.set()

        with pytest.raises(asyncio.CancelledError) as raised:
            await await_task(task)

    assert raised.value is execution_cancellations[0]
    store.save.assert_called_once()
    saved_metadata = store.save.call_args.args[2]
    assert store.save.call_args.args[1] == partial
    assert saved_metadata["status"] == "interrupted"
    assert saved_metadata["resume_marker"] == "preserved"
    context.get_messages.assert_awaited_once_with()
    hooks.unregister.assert_called_once_with()
    parent.coordinator.cancellation.unregister_child.assert_called_once_with(
        child.coordinator.cancellation
    )
    display.push_nesting.assert_called_once_with()
    display.pop_nesting.assert_called_once_with()
    child.cleanup.assert_awaited_once_with()


async def test_resume_get_messages_failure_saves_loaded_transcript_once():
    original = [{"role": "user", "content": "recover me"}]
    child, _, context = make_child([])
    cancellation = asyncio.CancelledError("cancel resume")
    child.execute = AsyncMock(side_effect=cancellation)
    context.get_messages = AsyncMock(side_effect=RuntimeError("context unavailable"))
    store = MagicMock()
    store.exists.return_value = True
    store.load.return_value = (
        original,
        {
            "session_id": "fallback-resume",
            "parent_id": "parent-123",
            "agent_name": "test-agent",
            "config": {
                "session": {
                    "orchestrator": "loop-basic",
                    "context": "context-simple",
                }
            },
            "working_dir": "/fixed/project",
        },
    )

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        pytest.raises(asyncio.CancelledError) as raised,
    ):
        await resume_sub_session("fallback-resume", "continue")

    assert raised.value is cancellation
    context.get_messages.assert_awaited_once_with()
    store.save.assert_called_once()
    assert store.save.call_args.args[1] == original
    assert store.save.call_args.args[2]["status"] == "interrupted"
    child.cleanup.assert_awaited_once_with()


async def test_spawn_cancellation_after_normal_save_does_not_save_twice():
    child, _, context = make_child([{"role": "assistant", "content": "complete"}])
    child.execute = AsyncMock(return_value="done")
    parent = make_parent()
    store = MagicMock()
    bridge_started = asyncio.Event()

    async def blocking_bridge(**_kwargs):
        bridge_started.set()
        await asyncio.Event().wait()

    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        patch(
            "amplifier_app_cli.session_spawner.bridge_child_cost",
            side_effect=blocking_bridge,
        ),
    ):
        task = asyncio.create_task(
            spawn_sub_session(
                agent_name="test-agent",
                instruction="do work",
                parent_session=parent,
                agent_configs={"test-agent": {"description": "test"}},
                sub_session_id="cancel-after-save",
            )
        )
        await wait_for_event(bridge_started)
        task.cancel("cancel after normal save")
        with pytest.raises(asyncio.CancelledError):
            await await_task(task)

    context.get_messages.assert_awaited_once_with()
    store.save.assert_called_once()
    assert "status" not in store.save.call_args.args[2]
    child.cleanup.assert_awaited_once_with()
