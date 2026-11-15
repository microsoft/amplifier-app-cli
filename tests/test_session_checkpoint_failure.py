"""Finite real-Core hook/privacy and real-Foundation custody regressions.

Only the failing I/O/admission boundary is injected. The session, participating
hook graph, exclusive lifetime binding, media store and provider wrapper are real.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import logging
import sys

import pytest
from rich.console import Console

from amplifier_core import AmplifierSession, HookResult
from amplifier_core.message_models import ChatRequest, Message
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding

from amplifier_app_cli.incremental_save import register_incremental_save
from amplifier_app_cli.session_checkpoint import install_session_checkpoint, save_runtime_checkpoint
from amplifier_app_cli.session_hooks import install_session_hook_graph, seal_session_hook_graph, session_hooks
from amplifier_app_cli.session_media import install_session_media, mount_session_media
from amplifier_app_cli.session_store import SessionStore
from test_session_media_graph import Loader


SECRET = "opaque-checkpoint-private-sentinel"


class PrivateFailure(OSError):
    def __str__(self):
        self.stringifications += 1
        return SECRET


class PrivateCancellation(asyncio.CancelledError):
    def __str__(self):
        self.stringifications += 1
        return SECRET


def primary(cancelled=False):
    error = (PrivateCancellation if cancelled else PrivateFailure)(SECRET)
    error.stringifications = 0
    error.__cause__ = OSError(SECRET + "-cause")
    error.__context__ = OSError(SECRET + "-context")
    error.add_note(SECRET + "-note")
    return error


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@asynccontextmanager
async def runtime(tmp_path, *, child=False):
    if sys.platform != "linux":
        pytest.skip("Actual prospective Foundation binding custody is Linux-only")
    parent = tmp_path / "sessions"
    parent.mkdir(mode=0o700)
    sid = "parent_child" if child else "root"
    owner = VersionedSessionBinding.create_new(
        parent / sid, session_id=sid, operation_id="allocate", admit=lambda: None,
    )
    native = SessionStore(parent, canonical_binding=owner)
    plan = {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
            "providers": [{"module": "fixture-provider"}]}
    session = AmplifierSession(deepcopy(plan), session_id=sid)
    session.coordinator.loader = Loader()
    state = {"error": None, "admissions": 0}

    def admit():
        state["admissions"] += 1
        if state["error"] is not None:
            raise state["error"]

    try:
        install_session_hook_graph(
            session, owner="cli.interactive",
            host_participants=("host.incremental_save", "host.child_observers", "host.media"),
        )
        install_session_checkpoint(session, native, session_id=sid, canonical_admit=admit)
        host = install_session_media(session, native)
        await session.initialize()
        await mount_session_media(session)
        context = session.coordinator.get("context")
        context.rows[:] = [{"role": "user", "content": "settled"}]
        await session.coordinator.get_capability("session.durable_checkpoint")()
        yield session, native, owner, host, state
    finally:
        await session.cleanup()
        owner.close()


@pytest.fixture
def diagnostic_entries(monkeypatch):
    entries = []
    log = logging.Logger._log
    print_ = Console.print

    def at_logger_entry(self, level, msg, args, *rest, **kwargs):
        entries.append((msg, args, kwargs.get("exc_info")))
        return log(self, level, msg, args, *rest, **kwargs)

    def at_console_entry(self, *args, **kwargs):
        entries.append((args, kwargs))
        return print_(self, *args, **kwargs)

    monkeypatch.setattr(logging.Logger, "_log", at_logger_entry)
    monkeypatch.setattr(Console, "print", at_console_entry)
    return entries


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["admit", "metadata", "cancel"])
async def test_root_actual_tool_post_failure_never_formats_private_error(
    tmp_path, monkeypatch, diagnostic_entries, capfd, failure_stage,
):
    async with runtime(tmp_path) as (session, native, owner, host, state):
        hook = register_incremental_save(session, native, "root", "fixture", {})
        assert hook is not None
        seal_session_hook_graph(session)
        graph_receipt = session.coordinator.get_capability("session.media.tool_post").graph_receipt
        wrapped = session.coordinator.get("providers")["fixture-provider"]
        selected = next(p for p, w in host.wrappers.values() if w is wrapped)
        error = primary(cancelled=failure_stage == "cancel")
        if failure_stage == "metadata":
            def fail_metadata(*args):
                raise error
            monkeypatch.setattr(native, "_save_metadata", fail_metadata)
        else:
            state["error"] = error
        before_calls = state["admissions"]
        result = await session.coordinator.hooks.emit("tool:post", {"tool_name": "fixture"})
        assert error.stringifications == 0
        assert result.action == "deny"
        assert "checkpoint" in result.reason.lower()
        from amplifier_app_cli.session_checkpoint import CheckpointBlocked
        # The primary remains private, intact and associated with both owners.
        failure = session.coordinator.get_capability("_cli.checkpoint_failure")
        assert failure.primary is error
        assert vars(native)["_cli_checkpoint_failures"]["root"] is failure
        assert failure.interrupted is (failure_stage == "cancel")
        assert error.__cause__.args == (SECRET + "-cause",)
        assert error.__context__.args == (SECRET + "-context",)
        assert error.__notes__ == [SECRET + "-note"]
        assert session.coordinator.get_capability("session.media.tool_post").validate_receipt(graph_receipt)
        stable = snapshot(owner.directory)
        for _ in range(2):
            again = await session.coordinator.hooks.emit("tool:post", {"tool_name": "fixture"})
            assert again.action == "deny"
        request = ChatRequest(messages=[Message(role="user", content="settled")])
        for operation in (lambda: host.prepare_provider(selected),):
            with pytest.raises(CheckpointBlocked) as blocked:
                operation()
            assert blocked.value.__cause__ is None and blocked.value.__context__ is None
        for operation in (wrapped.request_budget, wrapped.complete):
            with pytest.raises(CheckpointBlocked) as blocked:
                await operation(request)
            assert blocked.value.__cause__ is None and blocked.value.__context__ is None
        with pytest.raises(CheckpointBlocked):
            await session.coordinator.get_capability("session.durable_checkpoint")()
        with pytest.raises(CheckpointBlocked):
            save_runtime_checkpoint(session, native, "root", [], {})
        assert selected.observed == []
        assert state["admissions"] == before_calls + 1
        assert stable == snapshot(owner.directory)
        owner.check()  # Custody was not released to recover from failure.
        with pytest.raises(ValueError):
            VersionedSessionBinding.resume(owner.directory, session_id="root", expected=owner.expected)
        assert SECRET not in repr(diagnostic_entries)
        captured = capfd.readouterr()
        assert SECRET not in captured.out + captured.err
        assert error.stringifications == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["admit", "context", "cancel"])
async def test_shared_child_hook_wrapper_denies_actual_core_without_raw_diagnostics(
    tmp_path, monkeypatch, diagnostic_entries, capfd, failure_stage,
):
    from amplifier_app_cli.session_checkpoint import CheckpointBlocked, checkpoint_hook
    # Read-only use of the child's real writer function. Its owner integrates
    # this shared wrapper at the registration callsite in a separate change.
    from amplifier_app_cli.session_spawner import _write_checkpoint

    async with runtime(tmp_path, child=True) as (session, native, owner, host, state):
        callbacks = []

        async def child_request(event, data):
            callbacks.append(event)
            await _write_checkpoint(session, native, session.session_id, {})
            return HookResult()

        session_hooks(session, "host.child_observers").register(
            "provider:request", checkpoint_hook(session, native, child_request),
            priority=999, name="fixture-child-checkpoint",
        )
        seal_session_hook_graph(session)
        wrapped = session.coordinator.get("providers")["fixture-provider"]
        selected = next(p for p, w in host.wrappers.values() if w is wrapped)
        error = primary(cancelled=failure_stage == "cancel")
        if failure_stage == "context":
            async def fail_messages():
                raise error
            monkeypatch.setattr(session.coordinator.get("context"), "get_messages", fail_messages)
        else:
            state["error"] = error
        before = state["admissions"]
        result = await session.coordinator.hooks.emit("provider:request", {})
        assert result.action == "deny"
        assert error.stringifications == 0
        failure = session.coordinator.get_capability("_cli.checkpoint_failure")
        assert failure.primary is error
        assert failure.interrupted is (failure_stage == "cancel")
        assert not asyncio.current_task().cancelling()  # Not a claim Core cancelled.
        stable = snapshot(owner.directory)
        for _ in range(2):
            assert (await session.coordinator.hooks.emit("provider:request", {})).action == "deny"
        assert callbacks == ["provider:request"]
        request = ChatRequest(messages=[Message(role="user", content="settled")])
        for method in (wrapped.request_budget, wrapped.complete):
            with pytest.raises(CheckpointBlocked) as blocked:
                await method(request)
            assert blocked.value.__cause__ is None and blocked.value.__context__ is None
            assert not getattr(blocked.value, "__notes__", ())
        with pytest.raises(CheckpointBlocked):
            await session.coordinator.get_capability("session.durable_checkpoint")()
        assert state["admissions"] == before + (failure_stage != "context")
        assert stable == snapshot(owner.directory)
        assert selected.observed == []
        owner.check()
        assert SECRET not in repr(diagnostic_entries)
        captured = capfd.readouterr()
        assert SECRET not in captured.out + captured.err
        assert error.stringifications == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_call", ["durable", "runtime", "canonical"])
async def test_first_direct_failure_preserves_primary_but_next_call_is_safe_and_effect_free(
    tmp_path, checkpoint_call,
):
    from amplifier_app_cli.session_checkpoint import CheckpointBlocked

    async with runtime(tmp_path) as (session, native, owner, host, state):
        error = primary()
        state["error"] = error
        before = state["admissions"]
        if checkpoint_call == "durable":
            async def invoke():
                return await session.coordinator.get_capability("session.durable_checkpoint")()
        elif checkpoint_call == "runtime":
            async def invoke():
                return save_runtime_checkpoint(session, native, "root", [], {})
        else:
            async def invoke():
                return session.coordinator.get_capability("cli.canonical_checkpoint_save")([], {})
        with pytest.raises(PrivateFailure) as caught:
            await invoke()
        assert caught.value is error
        assert session.coordinator.get_capability("_cli.checkpoint_failure").primary is error
        stable = snapshot(owner.directory)
        for _ in range(2):
            with pytest.raises(CheckpointBlocked) as blocked:
                await invoke()
            assert blocked.value.__cause__ is None and blocked.value.__context__ is None
        assert stable == snapshot(owner.directory)
        assert state["admissions"] == before + 1
        assert error.stringifications == 0
        owner.check()


@pytest.mark.asyncio
async def test_cached_counted_candidate_cannot_send_or_recount_after_checkpoint_failure(tmp_path):
    from amplifier_app_cli.session_checkpoint import CheckpointBlocked

    async with runtime(tmp_path) as (session, native, owner, host, state):
        register_incremental_save(session, native, "root", "fixture", {})
        seal_session_hook_graph(session)
        wrapped = session.coordinator.get("providers")["fixture-provider"]
        selected = next(p for p, w in host.wrappers.values() if w is wrapped)
        request = ChatRequest(messages=[Message(role="user", content="settled")])
        await wrapped.request_budget(request)
        assert len(selected.observed) == 1
        epoch = host.store.epoch
        state["error"] = primary()
        assert (await session.coordinator.hooks.emit("tool:post", {})).action == "deny"
        assert host.store.epoch == epoch + 1
        with pytest.raises(CheckpointBlocked):
            await wrapped.complete(request)
        with pytest.raises(CheckpointBlocked):
            await wrapped.request_budget(request)
        assert len(selected.observed) == 1  # Prior count only; zero later count/send.
        owner.check()


@pytest.mark.asyncio
async def test_latch_during_real_request_scope_prevents_first_count_and_send(tmp_path):
    from amplifier_app_cli.session_checkpoint import CheckpointBlocked

    async with runtime(tmp_path) as (session, native, owner, host, state):
        register_incremental_save(session, native, "root", "fixture", {})
        seal_session_hook_graph(session)
        wrapped = session.coordinator.get("providers")["fixture-provider"]
        selected = next(p for p, w in host.wrappers.values() if w is wrapped)
        overlays = []

        async def overlay(request):
            overlays.append("entered")
            assert (await session.coordinator.hooks.emit("tool:post", {})).action == "deny"
            return request, None

        session.coordinator.register_capability("computer_use.prepare_request", overlay)
        state["error"] = primary()
        request = ChatRequest(messages=[Message(role="user", content="settled")])
        with pytest.raises(CheckpointBlocked):
            await wrapped.request_budget(request)
        with pytest.raises(CheckpointBlocked):
            await wrapped.complete(request)
        assert overlays == ["entered"]
        assert selected.observed == []


@pytest.mark.asyncio
async def test_successful_bound_hook_continues_without_latch_or_revocation(tmp_path):
    async with runtime(tmp_path) as (session, native, owner, host, state):
        register_incremental_save(session, native, "root", "fixture", {})
        seal_session_hook_graph(session)
        epoch = host.store.epoch
        before = owner.expected
        assert (await session.coordinator.hooks.emit("tool:post", {})).action == "continue"
        assert owner.expected != before
        assert session.coordinator.get_capability("_cli.checkpoint_failure") is None
        assert host.store.epoch == epoch
        provider = session.coordinator.get("providers")["fixture-provider"]
        request = ChatRequest(messages=[Message(role="user", content="settled")])
        await provider.request_budget(request)
        await provider.complete(request)
        selected = next(p for p, w in host.wrappers.values() if w is provider)
        assert len(selected.observed) == 2


@pytest.mark.asyncio
async def test_secondary_revocation_failure_does_not_replace_primary_or_replay(
    tmp_path, monkeypatch, diagnostic_entries, capfd,
):
    from types import SimpleNamespace
    from amplifier_app_cli.session_checkpoint import CheckpointBlocked

    async with runtime(tmp_path) as (session, native, owner, host, state):
        register_incremental_save(session, native, "root", "fixture", {})
        seal_session_hook_graph(session)
        error, secondary = primary(), primary()
        state["error"] = error
        actual_revoke = host.store.revoke
        revocations = []

        def revoke_then_fail():
            actual_revoke()
            revocations.append("revoked")
            raise secondary

        monkeypatch.setattr(host.store, "revoke", revoke_then_fail)
        assert (await session.coordinator.hooks.emit("tool:post", {})).action == "deny"
        failure = session.coordinator.get_capability("_cli.checkpoint_failure")
        assert failure.primary is error and failure.revocation_error is secondary
        stable = snapshot(owner.directory)

        def no_new_operation():
            pytest.fail("failure latch generated another operation ID")

        monkeypatch.setattr(
            "amplifier_app_cli.session_checkpoint.uuid", SimpleNamespace(uuid4=no_new_operation),
        )
        assert (await session.coordinator.hooks.emit("tool:post", {})).action == "deny"
        with pytest.raises(CheckpointBlocked):
            save_runtime_checkpoint(session, native, "root", [], {})
        with pytest.raises(CheckpointBlocked):
            await session.coordinator.get_capability("session.durable_checkpoint")()
        assert revocations == ["revoked"]
        assert stable == snapshot(owner.directory)
        assert error.stringifications == secondary.stringifications == 0
        assert SECRET not in repr(diagnostic_entries)
        captured = capfd.readouterr()
        assert SECRET not in captured.out + captured.err


@pytest.mark.asyncio
async def test_unbound_legacy_autosave_keeps_best_effort_behavior(tmp_path, monkeypatch):
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="legacy",
    )
    native = SessionStore(tmp_path)
    from test_session_media_graph import Context
    context = Context()
    context.rows[:] = [{"role": "user", "content": "legacy"}]
    await session.coordinator.mount("context", context)
    register_incremental_save(session, native, "legacy", "fixture", {})

    def fail_save(*args):
        raise OSError("ordinary legacy failure")

    monkeypatch.setattr(native, "save", fail_save)
    result = await session.coordinator.hooks.emit("tool:post", {})
    assert result.action == "continue"
    assert session.coordinator.get_capability("_cli.checkpoint_failure") is None
    assert not vars(native).get("_cli_checkpoint_failures")
    await session.cleanup()
