"""Actual installed Rust Core/Python exit and Foundation lifetime witnesses.

No Core execute/cleanup mocks, native method assignments or alternate inference
loop. Discovery-only loader fixtures reuse the maintained prospective root rig.
Actual CLI root/child caller replacements are a separate integration join.
"""

import asyncio
import inspect
import sys
from types import SimpleNamespace

import pytest

from amplifier_core import HookResult
from amplifier_core.message_models import ChatRequest, Message
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding, VersionedSessionConflict

from amplifier_app_cli.session_binding_custody import binding_custody, require_binding_available
from amplifier_app_cli.session_checkpoint import CheckpointBlocked, save_runtime_checkpoint
from amplifier_app_cli.session_execution import (
    SessionExecutionUnsettled, execute_session, install_session_execution,
    require_session_execution_idle, session_execution, wait_session_execution,
)
from amplifier_app_cli.session_hooks import (
    seal_session_hook_graph, session_hook_coordinator, session_hooks, transition_session_hook_graph,
)
from amplifier_app_cli.session_media import mount_session_media
from test_session_media_versioned_runtime import Runtime, files

pytestmark = pytest.mark.asyncio


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    if sys.platform != "linux":
        pytest.skip("Real prospective source custody is Linux-only")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    current = Runtime(tmp_path)
    yield current
    # Private fixture teardown only. Production retained custody is never reset.
    # Every async test joins its owned tasks before this closes fixture handles.
    for binding in current.bindings:
        binding.close()


def held(runtime):
    runtime.binding.check()
    assert binding_custody(runtime.binding) is not None
    with pytest.raises(RuntimeError, match="custody"):
        require_binding_available(runtime.binding)
    with pytest.raises(VersionedSessionConflict, match="retains the source lease"):
        VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=runtime.binding.expected)


def fresh_after_close(runtime):
    assert binding_custody(runtime.binding) is None
    with pytest.raises(VersionedSessionConflict, match="closed"):
        runtime.binding.check()
    fresh = VersionedSessionBinding.resume(runtime.binding.directory, session_id="root", expected=runtime.binding.expected)
    try:
        fresh.check()
    finally:
        fresh.close()


async def turns():
    # A scheduler barrier, not a timer purporting to prove Python exit.
    for _ in range(12):
        await asyncio.sleep(0)


async def test_actual_selected_adapter_signature_identity_and_repeated_turns(runtime):
    seen = []
    async def execute(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        seen.append((prompt, context, providers, tools, hooks, coordinator, kwargs))
        return "exact result " + prompt
    runtime.execute = execute
    opened = await runtime.open()
    actual = opened.session
    owner = session_execution(actual)
    selected = owner.selected
    assert actual.coordinator.get("orchestrator") is owner.proxy
    assert owner.proxy.execute.__wrapped__ is selected.execute
    assert inspect.signature(owner.proxy.execute) == inspect.signature(selected.execute)
    selected.streaming_sentinel = object()
    assert owner.proxy.streaming_sentinel is selected.streaming_sentinel
    owner.proxy.changed_by_host = "forwarded"
    assert selected.changed_by_host == "forwarded"
    graph = actual.coordinator.get_capability("cli.hooks.participating_graph")
    edges = graph.construction.participants_evidence()["adapters"]
    edge = next(row for row in edges if row["slot"] == ("orchestrator", None))
    assert edge["previous"] is selected and edge["installed"] is owner.proxy
    assert edge["participant"] == "host.media"
    try:
        for index in range(3):
            assert await execute_session(actual, str(index)) == "exact result " + str(index)
            assert owner.idle and owner.invocation.entered and owner.invocation.exited.done()
            assert owner.generation == index + 1
            assert seen[-1][1] is actual.coordinator.get("context")
            assert seen[-1][4] is actual.coordinator.hooks
            # Core's exact injected coordinator is forwarded, not the host view.
            assert seen[-1][5] is actual.coordinator
            assert seen[-1][6] == {}
        await install_session_execution(actual)  # No nested proxy, even after seal.
        assert session_execution(actual) is owner
        assert len(graph.construction.participants_evidence()["adapters"]) == len(edges)
        with transition_session_hook_graph(actual):
            session_hooks(actual, "host.mode").register("fixture:event", lambda e, d: HookResult(), name="witness-mode")
        assert await execute_session(actual, "after-transition") == "exact result after-transition"
        assert owner.generation == 4
    finally:
        await opened.cleanup()
    assert owner.closed
    fresh_after_close(runtime)
    with pytest.raises(SessionExecutionUnsettled):
        await execute_session(actual, "after-close")


async def test_actual_finally_blocked_cancel_joins_before_final_save_and_cleanup(runtime):
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
    native_tasks, checkpoints = [], []
    async def execute(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        native_tasks.append(asyncio.current_task())
        await context.add_message({"role": "user", "content": "private fixture"})
        checkpoints.append(await coordinator.get_capability("session.durable_checkpoint")())
        try:
            return "body result"
        finally:
            entered.set()
            await release.wait()
            exited.set()
    runtime.execute = execute
    opened = await runtime.open()
    actual, host = opened.session, runtime.host
    owner = session_execution(actual)
    physical_close = host.store.close
    close_calls = []
    def close():
        assert exited.is_set()
        held(runtime)
        physical_close()
        close_calls.append("once")
    host.store.close = close  # Observe the physical file boundary, never Core.
    trailing_saves = []
    async def caller():
        try:
            return await execute_session(actual, "finally fixture")
        finally:
            assert exited.is_set()  # No outer-finally checkpoint races Python.
            trailing_saves.append(await actual.coordinator.get_capability("session.durable_checkpoint")())
            await opened.cleanup()
    task = asyncio.create_task(caller())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel("original cancellation")
        await turns()
        assert not task.done() and not exited.is_set()
        assert not owner.idle and owner.invocation.entered
        assert not host.closed and not host.cleanup_attempted and not host.cleanup_settled
        assert list(opened.store._media_stores) == ["root"]
        assert host.wrappers and trailing_saves == []
        held(runtime)
        with pytest.raises(SessionExecutionUnsettled):
            await host.close()
        assert not host.cleanup_attempted
        # A concurrent cleanup waiter cannot cancel the owning native task.
        waiter = asyncio.create_task(wait_session_execution(actual))
        await turns()
        assert not waiter.done()
        task.cancel("second cancellation")
        await turns()
        assert not task.done() and not native_tasks[0].done()
        held(runtime)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError, match="original cancellation"):
            await task
        await waiter
    assert exited.is_set() and native_tasks[0].done()
    assert close_calls == ["once"] and host.cleanup_settled
    assert len(checkpoints) == len(trailing_saves) == 1
    fresh_after_close(runtime)


async def test_actual_never_entered_armed_cancel_during_core_lifecycle_hook(runtime):
    hook_entered, release_hook = asyncio.Event(), asyncio.Event()
    executions = []
    async def execute(**kwargs):
        executions.append("entered")
        return "done"
    runtime.execute = execute
    opened = await runtime.open(seal=False)
    actual = opened.session
    async def before_python(event, data):
        hook_entered.set()
        await release_hook.wait()
        return HookResult()
    session_hooks(actual, "host.mode").register("session:start", before_python, name="pre-python-dispatch")
    seal_session_hook_graph(actual)
    owner = session_execution(actual)
    task = asyncio.create_task(execute_session(actual, "preentry"))
    try:
        await asyncio.wait_for(hook_entered.wait(), 5)
        assert owner.invocation is not None and not owner.invocation.entered
        task.cancel("preentry cancellation")
        await turns()
        assert not task.done()  # Shielded native waiter has not been dropped.
        with pytest.raises(SessionExecutionUnsettled):
            require_session_execution_idle(actual)
        assert not runtime.host.closed and not runtime.host.cleanup_attempted
        held(runtime)
    finally:
        release_hook.set()
        with pytest.raises(asyncio.CancelledError, match="preentry cancellation"):
            await task
    assert owner.invocation.native.done()
    if owner.invocation.entered:
        assert executions == ["entered"] and owner.invocation.exited.done()
        await opened.cleanup()
        fresh_after_close(runtime)
    else:
        # Core may stop cooperatively before dispatch. No public Python-entry
        # witness can infer whether a dropped/queued dispatch could still enter.
        # This generation stays held, and trailing writes/providers are latched.
        assert executions == [] and owner.failure is not None
        await assert_unsettled_has_no_followup_effects(runtime, opened)


async def assert_unsettled_has_no_followup_effects(runtime, opened):
    held(runtime)
    before = files(runtime.parent)
    actual = opened.session
    with pytest.raises(CheckpointBlocked):
        await actual.coordinator.get_capability("session.durable_checkpoint")()
    with pytest.raises(CheckpointBlocked):
        save_runtime_checkpoint(actual, opened.store, "root", [{"role": "user", "content": "forbidden"}], {})
    provider = actual.coordinator.get("providers")["fixture-versioned-provider"]
    with pytest.raises(CheckpointBlocked):
        await provider.request_budget(ChatRequest(messages=[Message(role="user", content="forbidden")]))
    with pytest.raises(CheckpointBlocked):
        await provider.complete(ChatRequest(messages=[Message(role="user", content="forbidden")]))
    assert files(runtime.parent) == before
    with pytest.raises(SessionExecutionUnsettled):
        await opened.cleanup()
    assert not runtime.host.cleanup_attempted and not runtime.host.closed
    custody = binding_custody(runtime.binding)
    assert custody is not None and custody.error is opened._cleanup_error
    held(runtime)


async def test_cancelled_cleanup_waiter_latches_and_retains_exact_custody(runtime):
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(**kwargs):
        try:
            return "done"
        finally:
            entered.set()
            await release.wait()
    runtime.execute = execute
    opened = await runtime.open()
    actual = opened.session
    task = asyncio.create_task(execute_session(actual, "cleanup cancel"))
    cleanup = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        cleanup = asyncio.create_task(opened.cleanup())
        await turns()
        assert not cleanup.done()
        cleanup.cancel("private cleanup interrupted")
        with pytest.raises(asyncio.CancelledError, match="private cleanup interrupted") as caught:
            await cleanup
        owner = session_execution(actual)
        assert owner.failure is opened._cleanup_error is caught.value
        held(runtime)
        before = files(runtime.parent)
        with pytest.raises(CheckpointBlocked):
            await actual.coordinator.get_capability("session.durable_checkpoint")()
        assert files(runtime.parent) == before
        assert not runtime.host.closed and not runtime.host.cleanup_attempted
        assert not owner.invocation.native.done()
    finally:
        release.set()
        # Cancellation of cleanup cannot change Python execution into a pass or
        # erase its retained uncertainty, even after Python has now exited.
        with pytest.raises(SessionExecutionUnsettled):
            await task
    with pytest.raises(asyncio.CancelledError) as again:
        await opened.cleanup()
    assert again.value is opened._cleanup_error
    held(runtime)


async def test_actual_direct_core_media_callback_cannot_close_running_python(runtime):
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(**kwargs):
        try:
            return "done"
        finally:
            entered.set()
            await release.wait()
    runtime.execute = execute
    opened = await runtime.open()
    actual = opened.session
    task = asyncio.create_task(execute_session(actual, "direct cleanup callback"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # Real Rust cleanup swallows callback failures. The media guard must be
        # effective independently of its unit return value.
        await actual.cleanup()
        assert not runtime.host.closed and not runtime.host.cleanup_attempted
        assert not runtime.host.cleanup_settled
        assert list(opened.store._media_stores) == ["root"]
        held(runtime)
    finally:
        release.set()
        assert await task == "done"
    await opened.cleanup()
    assert runtime.host.cleanup_settled
    fresh_after_close(runtime)


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
async def test_actual_private_count_send_and_checkpoint_unchanged(runtime, image_format):
    responses = []
    async def execute(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        payload, ref = await runtime.stage(image_format, session=coordinator.session)
        receipt = await coordinator.get_capability("session.durable_checkpoint")()
        assert receipt.verify()
        responses.append(await runtime.observe(payload, session=coordinator.session))
        return "provider request unchanged"
    runtime.execute = execute
    opened = await runtime.open()
    try:
        assert await execute_session(opened.session, "pixels") == "provider request unchanged"
        assert responses and session_execution(opened.session).idle
    finally:
        await opened.cleanup()
    fresh_after_close(runtime)


async def test_actual_mount_replacement_is_not_new_orchestrator_authority(runtime):
    opened = await runtime.open()
    actual = opened.session
    owner = session_execution(actual)
    async def unexpected(**kwargs):
        raise AssertionError("replacement must not execute")
    with pytest.raises(ValueError, match="incomplete or inconsistent"):
        with transition_session_hook_graph(actual):
            await session_hook_coordinator(actual, "host.media").mount("orchestrator", SimpleNamespace(execute=unexpected))
    try:
        with pytest.raises(SessionExecutionUnsettled):
            await runtime.host.close()
        assert not runtime.host.cleanup_attempted
        with pytest.raises(SessionExecutionUnsettled):
            await execute_session(actual, "replaced")
        assert owner.invocation is None
        assert owner.failure is not None
        await assert_unsettled_has_no_followup_effects(runtime, opened)
    finally:
        assert not runtime.host.closed


async def test_repeated_postseal_media_mount_keeps_original_witness(runtime):
    # An already-installed witness is not remounted or replaced after seal.
    opened = await runtime.open()
    actual = opened.session
    owner = session_execution(actual)
    await mount_session_media(actual)  # Idempotent installed case.
    assert session_execution(actual) is owner
    assert actual.coordinator.get("orchestrator") is owner.proxy
    await opened.cleanup()


async def test_loop_live_branch_installs_exit_adapter_before_late_provider_setup(runtime, monkeypatch):
    """Actual graph/Core with controlled live-shaped discovery, not native live."""
    import test_session_media_versioned_runtime as fixtures
    base = fixtures.Loader
    class LiveLoader(base):
        async def load(self, module_id, config, **kwargs):
            if module_id == "loop-live":
                module_id = "fixture-versioned-loop"
            return await super().load(module_id, config, **kwargs)
    monkeypatch.setattr(fixtures, "Loader", LiveLoader)
    runtime.plan["session"]["orchestrator"] = "loop-live"
    observed = []
    async def execute(prompt, context, providers, tools, hooks, coordinator, **kwargs):
        assert coordinator is runtime.initialized.session.coordinator
        # Keep the original live adapter's public late registration capability
        # and coordinator; the execution proxy does not substitute its own.
        register = coordinator.get_capability("cli.media.execution_registration")
        original = coordinator.get("providers")["fixture-versioned-provider"]
        with register() as view:
            wrapped = coordinator.get_capability("cli.media.prepare_provider")(original)
            await view.mount_adapter("providers", wrapped, previous=original, name="fixture-versioned-provider")
        observed.append((original, wrapped))
        bridge = coordinator.get_capability("session.media.tool_post")
        assert bridge.validate_receipt(bridge.graph_receipt)
        return "late selection preserved"
    runtime.execute = execute
    opened = await runtime.open()
    owner = session_execution(opened.session)
    try:
        assert owner is not None and owner.mounted
        assert runtime.host.wrappers == {}  # Native live selection is still late.
        assert await execute_session(opened.session, "live shape") == "late selection preserved"
        assert observed[0][0] is not observed[0][1]
    finally:
        await opened.cleanup()
    fresh_after_close(runtime)


async def test_armed_without_native_waiter_is_not_assumed_idle(runtime):
    opened = await runtime.open()
    actual = opened.session
    owner = session_execution(actual)
    # Tracker-only control of the arm-before-dispatch seam. No Core execution
    # or exit is claimed from this deliberately unfinished dispatch.
    invocation = owner.arm()
    with pytest.raises(SessionExecutionUnsettled):
        require_session_execution_idle(actual)
    assert invocation.native is None and not invocation.entered
    assert not invocation.exited.done() and not owner.idle
    with pytest.raises(SessionExecutionUnsettled):
        await wait_session_execution(actual)
    assert owner.failure is not None
    await assert_unsettled_has_no_followup_effects(runtime, opened)


async def test_overlapping_dispatch_refuses_without_replacing_original_generation(runtime):
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(**kwargs):
        entered.set()
        await release.wait()
        return "first"
    runtime.execute = execute
    opened = await runtime.open()
    owner = session_execution(opened.session)
    first = asyncio.create_task(execute_session(opened.session, "first"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        invocation = owner.invocation
        with pytest.raises(SessionExecutionUnsettled):
            await execute_session(opened.session, "overlap")
        assert owner.invocation is invocation and owner.generation == 1
        assert owner.failure is None
        held(runtime)
    finally:
        release.set()
        assert await first == "first"
        await opened.cleanup()


async def test_adapter_itself_preserves_exact_exception_and_stream_attributes():
    """Adapter-only unit: no claim of Core's separate error translation."""
    from amplifier_app_cli.session_execution import _OrchestratorProxy
    sentinel = OSError("private adapter failure")
    exited = asyncio.get_running_loop().create_future()
    calls = []
    async def execute(prompt, *, stream, **kwargs):
        calls.append((prompt, stream, kwargs))
        raise sentinel
    stream = object()
    selected = SimpleNamespace(execute=execute, stream=stream)
    owner = SimpleNamespace(selected=selected, enter=lambda: SimpleNamespace(exited=exited))
    proxy = _OrchestratorProxy(owner)
    assert proxy.stream is stream
    assert inspect.signature(proxy.execute) == inspect.signature(execute)
    with pytest.raises(OSError) as caught:
        await proxy.execute("same", stream=stream, arbitrary=stream)
    assert caught.value is sentinel
    assert calls == [("same", stream, {"arbitrary": stream})]
    assert exited.done()


async def test_setup_cleanup_without_execution_witness_still_works(runtime):
    # Runtime setup failure occurs after actual Core initialize but before the
    # postinitialize media/witness mount. Original error identity is preserved.
    primary = OSError("private setup failure")
    with pytest.raises(OSError) as caught:
        await runtime.open(setup_error=primary)
    assert caught.value is primary
    host = runtime.created[-1].coordinator.get_capability("cli.session_media")
    assert host.closed and host.cleanup_settled and host.execution is None
    fresh_after_close(runtime)


@pytest.mark.parametrize("capabilities", [False, True])
async def test_portable_unqualified_legacy_execute_preserves_result_and_error(capabilities):
    from unittest.mock import AsyncMock, MagicMock
    coordinator = MagicMock() if capabilities else SimpleNamespace()
    if capabilities:
        # Unspecified MagicMock capabilities are not actual issued graph owners.
        assert await install_session_execution(SimpleNamespace(coordinator=coordinator)) is None
    result = object()
    execute = AsyncMock(return_value=result)
    session = SimpleNamespace(coordinator=coordinator, execute=execute)
    assert await execute_session(session, "legacy") is result
    execute.assert_awaited_once_with("legacy")
    primary = asyncio.CancelledError("legacy cancellation")
    execute.side_effect = primary
    with pytest.raises(asyncio.CancelledError) as caught:
        await execute_session(session, "legacy error")
    assert caught.value is primary


async def test_actual_core_without_participating_graph_keeps_legacy_execute():
    from amplifier_core import AmplifierSession
    from test_session_media_graph import Loader
    actual = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [{"module": "fixture-provider"}],
    }, session_id="legacy-no-graph")
    observed = []
    async def execute(prompt, **kwargs):
        observed.append(prompt)
        return "legacy actual Core"
    actual.coordinator.loader = Loader(execute)
    await actual.initialize()
    try:
        assert await install_session_execution(actual) is None
        assert await execute_session(actual, "ordinary") == "legacy actual Core"
        assert observed == ["ordinary"]
    finally:
        await actual.cleanup()


@pytest.mark.parametrize("missing", ["published", "host", "both"])
async def test_qualified_missing_owner_never_falls_back_or_exposes_primary(runtime, missing):
    from amplifier_app_cli.session_checkpoint import _stored_checkpoint_failure
    calls = []
    async def execute(**kwargs):
        calls.append("must not execute")
        return "unsafe"
    runtime.execute = execute
    opened = await runtime.open()
    actual, host = opened.session, runtime.host
    owner = session_execution(actual)
    class PrivateFailure(OSError):
        def __str__(self):
            raise AssertionError("private failure must not be formatted")
        __repr__ = __str__
    primary = PrivateFailure()
    owner._fail(primary)
    if missing in {"host", "both"}:
        host.execution = None
    if missing in {"published", "both"}:
        actual.coordinator.register_capability("cli.session_execution", None)
    try:
        with pytest.raises(SessionExecutionUnsettled, match="ownership must be retained"):
            await execute_session(actual, "missing owner")
        assert calls == [] and owner.invocation is None
        assert _stored_checkpoint_failure(actual, opened.store).primary is primary
        held(runtime)
    finally:
        host.execution = owner
        actual.coordinator.register_capability("cli.session_execution", owner)
    with pytest.raises(SessionExecutionUnsettled):
        await opened.cleanup()
    held(runtime)


@pytest.mark.parametrize("private_primary", [False, True])
async def test_missing_both_owner_refs_cleanup_retains_pending_python_and_lease(runtime, private_primary):
    from amplifier_app_cli.session_checkpoint import _stored_checkpoint_failure
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(**kwargs):
        try:
            return "selected body done"
        finally:
            entered.set()
            await release.wait()
            exited.set()
    runtime.execute = execute
    opened = await runtime.open()
    actual, host = opened.session, runtime.host
    owner = session_execution(actual)
    class PrivateFailure(OSError):
        def __str__(self):
            raise AssertionError("private primary must not be formatted")
        __repr__ = __str__
    primary = PrivateFailure() if private_primary else None
    task = asyncio.create_task(execute_session(actual, "missing both cleanup"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if primary is not None:
            owner._fail(primary)
        # Same public owner-loss fault already covered for dispatch. Cleanup
        # must not confuse it with genuinely never-installed setup/legacy.
        host.execution = None
        actual.coordinator.register_capability("cli.session_execution", None)
        assert host._execution_owner is owner  # Not a Boolean or serialized grant.
        with pytest.raises(SessionExecutionUnsettled) as caught:
            await opened.cleanup()
        original_cleanup_error = caught.value
        assert not exited.is_set() and not owner.invocation.native.done()
        assert not host.closed and not host.cleanup_attempted and not host.cleanup_settled
        assert list(opened.store._media_stores) == ["root"] and host.wrappers
        custody = binding_custody(runtime.binding)
        assert custody.session is actual and custody.error is original_cleanup_error
        held(runtime)
        assert _stored_checkpoint_failure(actual, opened.store).primary is (
            primary if primary is not None else owner.failure
        )
        with pytest.raises(SessionExecutionUnsettled):
            await install_session_execution(actual)  # No reinstallation/remint.
        assert owner.invocation.generation == 1
        with pytest.raises(SessionExecutionUnsettled):
            await host.close()
        await actual.cleanup()  # Actual Core callback must also keep media open.
        assert not host.closed and not host.cleanup_attempted
        held(runtime)
    finally:
        release.set()
        with pytest.raises(SessionExecutionUnsettled):
            await task
    assert exited.is_set() and owner.invocation.native.done()
    with pytest.raises(SessionExecutionUnsettled) as repeated:
        await opened.cleanup()
    assert repeated.value is original_cleanup_error
    assert not host.closed and not host.cleanup_attempted and not host.cleanup_settled
    assert binding_custody(runtime.binding).error is original_cleanup_error
    assert host._execution_owner is owner and owner.failure is not None
    held(runtime)
    # Close only the fixture's physical handle after all assertions and Python
    # exit. This does not clear production custody or replay owner cleanup.
    host.store.close()


async def test_actual_unbound_execution_latch_blocks_save_without_uuid_or_files(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from amplifier_core import AmplifierSession
    from amplifier_app_cli import session_checkpoint as checkpoints
    from amplifier_app_cli.session_checkpoint import install_session_checkpoint, _stored_checkpoint_failure
    from amplifier_app_cli.session_hooks import install_session_hook_graph
    from amplifier_app_cli.session_media import install_session_media
    from amplifier_app_cli.session_store import SessionStore
    from test_session_media_graph import Loader
    actual = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [{"module": "fixture-provider"}],
    }, session_id="unbound")
    actual.coordinator.loader = Loader()
    native = SessionStore(tmp_path / "sessions")
    install_session_hook_graph(actual, owner="fixture.unbound", host_participants=("host.media",))
    install_session_checkpoint(actual, native, session_id="unbound")
    host = install_session_media(actual, native)
    await actual.initialize()
    await mount_session_media(actual)
    seal_session_hook_graph(actual)
    try:
        owner = session_execution(actual)
        assert vars(native).get("_canonical_binding") is None
        owner.arm()  # Deliberately unresolved pre-dispatch control, no Core claim.
        with pytest.raises(SessionExecutionUnsettled):
            await wait_session_execution(actual)
        primary = _stored_checkpoint_failure(actual, native).primary
        before = files(tmp_path)
        saved = Mock(wraps=native.save)
        monkeypatch.setattr(native, "save", saved)
        uuid = Mock(side_effect=AssertionError("no new operation may be minted"))
        monkeypatch.setattr(checkpoints.uuid, "uuid4", uuid)
        with pytest.raises(CheckpointBlocked):
            save_runtime_checkpoint(actual, native, "unbound", [{"role": "user", "content": "forbidden"}], {})
        saved.assert_not_called()
        uuid.assert_not_called()
        assert files(tmp_path) == before
        assert _stored_checkpoint_failure(actual, native).primary is primary
        assert not host.closed and not host.cleanup_attempted
    finally:
        # Private file-handle teardown, not production uncertainty settlement.
        host.store.close()


async def test_portable_unlatched_unbound_save_does_not_read_coordinator(tmp_path):
    from unittest.mock import Mock
    from amplifier_app_cli.session_store import SessionStore
    native = SessionStore(tmp_path)
    primary = OSError("ordinary legacy save failure")
    saved = Mock(side_effect=primary)
    native.save = saved
    getter = Mock(side_effect=AssertionError("legacy save must not inspect coordinator"))
    session = SimpleNamespace(session_id="legacy", coordinator=SimpleNamespace(get_capability=getter))
    with pytest.raises(OSError) as caught:
        save_runtime_checkpoint(session, native, "legacy", [], {})
    assert caught.value is primary
    saved.assert_called_once_with("legacy", [], {})
    getter.assert_not_called()
    assert "_cli_checkpoint_failures" not in vars(native)
