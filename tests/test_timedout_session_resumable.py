"""A timed-out sub-session must still be resumable -- without adding an
unbounded await to the cancellation path.

THE DEFECT
    spawn_sub_session persisted the child transcript only AFTER a successful
    ``await child_session.execute(...)``. tool-delegate's wall-clock timeout
    CANCELS that await, so the save never ran and nothing about the timed-out
    sub-session reached SessionStore. The session_id handed back to the caller
    therefore could not be resumed:
    "Session not found. May have expired or never existed."

THE CONSTRAINT
    Rescuing the transcript from the CANCELLATION path would mean awaiting
    ``context.get_messages()`` while already unwinding a deadline -- an await
    that can block past the very deadline that caused the unwind, recreating
    the hang the timeout exists to bound.

THE FIX UNDER TEST (option (b))
    Checkpoint the transcript DURING normal execution, at ``provider:request``
    -- the only point in the orchestrator loop where the message list is
    guaranteed tool-pair-balanced. The cancellation path gains no new code,
    no new await, and no new write.

Tests below pin, in order:
  1. the contract the acceptance names -- the advertised session_id resumes
     and yields the preserved transcript;
  2. the hard invariant -- nothing is awaited on the cancellation path
     (proved by hanging ``get_messages()`` and showing the unwind is
     unaffected AND that it was never called after cancellation began);
  3. the balanced-boundary choice (provider:request, never provider:response);
  4. that a failing checkpoint never takes down the run it protects;
  5. the throttle and the disable escape hatch.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.anyio

SUB_SESSION_ID = "parent0000000000-child00000000000_test-agent"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _home(isolated_home):
    """Every test here reaches SessionStore() -> Path.home(). See conftest.isolated_home."""
    return isolated_home


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeHooks:
    """Event-keyed hook registry (the real one is keyed; MagicMock is not)."""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def register(self, event, handler, priority=0, name=None):
        self.handlers.setdefault(event, []).append(handler)

        def _unregister():
            try:
                self.handlers.get(event, []).remove(handler)
            except ValueError:
                pass

        return _unregister

    async def emit(self, event, data):
        for handler in list(self.handlers.get(event, [])):
            await handler(event, data)


class FakeContext:
    """Minimal context module.

    ``hang_get_messages`` flips the behaviour of get_messages() to "never
    returns" -- the instrument for proving the cancellation path does not
    await it.
    """

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages: list[dict] = list(messages or [])
        self.hang_get_messages = False
        self.get_messages_calls = 0
        self.factory = None

    async def set_system_prompt_factory(self, factory) -> None:
        self.factory = factory

    async def add_message(self, message: dict) -> None:
        self.messages.append(message)

    async def get_messages(self) -> list[dict]:
        self.get_messages_calls += 1
        if self.hang_get_messages:
            await asyncio.Event().wait()  # never returns
        return list(self.messages)


def _make_parent_session() -> MagicMock:
    parent = MagicMock()
    parent.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        "agents": {},
    }
    parent.session_id = "parent-123"
    parent.trace_id = "trace-abc"
    parent.loader = None
    parent.coordinator = MagicMock()
    parent.coordinator.config = {"agents": {}}
    parent.coordinator.get = MagicMock(return_value=None)
    parent.coordinator.get_capability = MagicMock(return_value=None)
    parent.coordinator.display_system = MagicMock()
    parent.coordinator.approval_system = MagicMock()
    parent.coordinator.cancellation = MagicMock()
    return parent


def _make_child_session(
    context: FakeContext, hooks: FakeHooks, execute_impl
) -> MagicMock:
    child = MagicMock()
    child.session_id = SUB_SESSION_ID

    def _get(name):
        if name == "hooks":
            return hooks
        if name == "context":
            return context
        return None

    child.coordinator = MagicMock()
    child.coordinator.get = MagicMock(side_effect=_get)
    child.coordinator.get_capability = MagicMock(return_value=None)
    child.coordinator.register_capability = MagicMock()
    child.coordinator.mount = AsyncMock()
    child.coordinator.collect_contributions = AsyncMock(return_value=[])
    child.coordinator.display_system = MagicMock()
    child.coordinator.approval_system = MagicMock()
    child.coordinator.cancellation = MagicMock()
    child.initialize = AsyncMock()
    child.execute = AsyncMock(side_effect=execute_impl)
    child.cleanup = AsyncMock()
    return child


def _spawn_patches(child_session: MagicMock):
    """The heavy-dependency patch stack shared by every spawn in this file."""
    return (
        patch(
            "amplifier_app_cli.session_spawner.AmplifierSession",
            return_value=child_session,
        ),
        patch(
            "amplifier_app_cli.session_spawner.generate_sub_session_id",
            return_value=SUB_SESSION_ID,
        ),
        patch(
            "amplifier_app_cli.session_spawner.bridge_child_cost",
            new_callable=AsyncMock,
        ),
        patch(
            "amplifier_app_cli.session_spawner._extract_bundle_context",
            return_value=None,
        ),
        patch("amplifier_app_cli.paths.create_foundation_resolver"),
    )


async def _spawn(child_session: MagicMock, parent_session: MagicMock):
    from amplifier_app_cli.session_spawner import spawn_sub_session

    p1, p2, p3, p4, p5 = _spawn_patches(child_session)
    with p1, p2, p3, p4, p5:
        return await spawn_sub_session(
            agent_name="test-agent",
            instruction="Do something long",
            parent_session=parent_session,
            agent_configs={"test-agent": {"description": "A test agent"}},
        )


async def _spawn_until_timeout(
    child_session: MagicMock, parent_session: MagicMock, timeout_s: float = 0.2
) -> float:
    """Spawn under a wall-clock timeout, exactly as tool-delegate does.

    Returns elapsed seconds. Fails loudly (rather than hanging the suite)
    if the unwind never completes.
    """

    async def _under_timeout():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(timeout_s):
                await _spawn(child_session, parent_session)

    started = time.monotonic()
    task = asyncio.ensure_future(_under_timeout())
    done, pending = await asyncio.wait({task}, timeout=5.0)
    elapsed = time.monotonic() - started
    if task in pending:
        task.cancel()
        pytest.fail(
            "spawn_sub_session did not unwind within 5s of a 0.2s timeout -- "
            "the cancellation path awaited something unbounded"
        )
    exc = task.exception()
    assert exc is None, f"unexpected error unwinding the timeout: {exc!r}"
    return elapsed


# ---------------------------------------------------------------------------
# 1. The contract the acceptance names
# ---------------------------------------------------------------------------


class TestTimedOutSessionIsResumable:
    async def test_timed_out_spawn_leaves_a_loadable_session(
        self, tmp_path, monkeypatch
    ):
        """The advertised session_id exists in SessionStore after a timeout."""
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        hooks = FakeHooks()
        context = FakeContext()

        async def execute_impl(instruction):
            # iteration 1: nothing in the transcript yet
            await hooks.emit("provider:request", {"iteration": 1})
            context.messages.append({"role": "user", "content": "Do something long"})
            context.messages.append({"role": "assistant", "content": "partial work"})
            # iteration 2: tool-pair-balanced boundary -> checkpoint
            await hooks.emit("provider:request", {"iteration": 2})
            await asyncio.Event().wait()  # straggler

        child = _make_child_session(context, hooks, execute_impl)
        await _spawn_until_timeout(child, _make_parent_session())

        store = SessionStore()
        assert store.exists(SUB_SESSION_ID), (
            "a timed-out sub-session must still be present in SessionStore"
        )

        transcript, metadata = store.load(SUB_SESSION_ID)
        assert [m["content"] for m in transcript] == [
            "Do something long",
            "partial work",
        ]
        assert metadata["status"] == "in_progress", (
            "a checkpoint must be labelled in_progress, never complete"
        )
        assert metadata["config"], "resume needs metadata['config'] to reconstruct"
        assert metadata["agent_name"] == "test-agent"

    async def test_timed_out_session_round_trips_through_resume(
        self, tmp_path, monkeypatch
    ):
        """The full recovery move: resume the advertised id, get the transcript.

        This is the acceptance criterion end to end -- spawn, time out, then
        resume_sub_session(session_id) and observe the preserved messages
        restored into the resumed session's context.
        """
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        hooks = FakeHooks()
        context = FakeContext()

        async def execute_impl(instruction):
            await hooks.emit("provider:request", {"iteration": 1})
            context.messages.append({"role": "user", "content": "Do something long"})
            context.messages.append({"role": "assistant", "content": "partial work"})
            await hooks.emit("provider:request", {"iteration": 2})
            await asyncio.Event().wait()

        child = _make_child_session(context, hooks, execute_impl)
        await _spawn_until_timeout(child, _make_parent_session())

        # --- now resume the session_id the caller was handed -----------------
        from amplifier_app_cli.session_spawner import resume_sub_session

        resumed_context = FakeContext()
        resumed_hooks = FakeHooks()
        resumed = _make_child_session(
            resumed_context, resumed_hooks, AsyncMock(return_value="resumed response")
        )
        resumed.execute = AsyncMock(return_value="resumed response")

        with (
            patch(
                "amplifier_app_cli.session_spawner.AmplifierSession",
                return_value=resumed,
            ),
            patch("amplifier_app_cli.ui.CLIApprovalSystem"),
            patch("amplifier_app_cli.ui.CLIDisplaySystem"),
            patch("amplifier_app_cli.paths.create_foundation_resolver"),
        ):
            result = await resume_sub_session(SUB_SESSION_ID, "carry on")

        assert result["session_id"] == SUB_SESSION_ID
        assert result["output"] == "resumed response"
        restored = [m.get("content") for m in resumed_context.messages]
        assert "Do something long" in restored and "partial work" in restored, (
            "resume must restore the transcript preserved by the checkpoint"
        )


class TestNonResumableIsStatedExplicitly:
    """The acceptance's SECOND branch, for the cases option (b) cannot cover.

    The acceptance is disjunctive -- "either the advertised session_id is
    genuinely resumable ... OR the result states explicitly that it is not
    resumable and directs the caller to re-delegate". Option (b) satisfies the
    first branch for every checkpointed session (covered above).

    Two residual cases are NOT checkpointed and so land on the second branch:
      * the subprocess spawn path (session_spawner.py:637 returns before any
        checkpointing);
      * checkpointing explicitly disabled via the env knob.
    Plus the pre-existing cases: an expired/pruned session, or an id that never
    existed.

    For all of them the app-side resume surface must say, in words, that the
    session is not resumable and that the caller should re-delegate -- never
    leave "retry the resume" as a plausible reading.

    SCOPE, STATED HONESTLY: this pins the boundary THIS repo owns
    (``resume_sub_session``'s raised error, which reaches the
    ``delegate:error`` event, the logs, and every non-foundation caller such
    as recipes and programmatic callers). The MODEL-facing half is not ours:
    tool-delegate's ``except FileNotFoundError`` handler builds its own
    message and discards ``str(e)``, so making this text reach the ToolResult
    needs a one-line foundation change, specified in the PR body. That is a
    real, disclosed gap -- not something this test pretends to cover.
    """

    async def test_missing_session_says_not_resumable_and_says_re_delegate(
        self, tmp_path, monkeypatch
    ):
        from amplifier_app_cli.session_spawner import resume_sub_session

        with pytest.raises(FileNotFoundError) as excinfo:
            await resume_sub_session("never-existed-session-id", "carry on")

        message = str(excinfo.value)
        assert "not resumable" in message.lower(), (
            "the second branch of the acceptance requires the result to state "
            f"EXPLICITLY that the session is not resumable; got: {message!r}"
        )
        assert "re-delegate" in message.lower(), (
            "the second branch of the acceptance requires the caller be "
            f"directed to re-delegate; got: {message!r}"
        )

    async def test_disabled_checkpointing_lands_on_the_non_resumable_branch(
        self, tmp_path, monkeypatch
    ):
        """End to end: a timeout with checkpointing off yields branch 2, in words.

        This is the disjunction resolving the other way in a real run -- spawn,
        time out, and observe that the advertised session_id is genuinely
        absent AND that asking to resume it says so explicitly.
        """
        from amplifier_app_cli.session_spawner import resume_sub_session

        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "-1")

        hooks = FakeHooks()
        context = FakeContext()

        async def execute_impl(instruction):
            await hooks.emit("provider:request", {"iteration": 1})
            context.messages.append({"role": "assistant", "content": "partial work"})
            await asyncio.Event().wait()

        child = _make_child_session(context, hooks, execute_impl)
        await _spawn_until_timeout(child, _make_parent_session())

        assert not SessionStore().exists(SUB_SESSION_ID)

        with pytest.raises(FileNotFoundError) as excinfo:
            await resume_sub_session(SUB_SESSION_ID, "carry on")

        message = str(excinfo.value).lower()
        assert "not resumable" in message and "re-delegate" in message, (
            "a timed-out, non-checkpointed session must report its own "
            f"non-resumability in words; got: {message!r}"
        )


# ---------------------------------------------------------------------------
# 2. The hard invariant
# ---------------------------------------------------------------------------


class TestNoUnboundedAwaitOnCancellationPath:
    async def test_hanging_get_messages_does_not_delay_the_unwind(
        self, tmp_path, monkeypatch
    ):
        """The invariant, proved two ways.

        ``context.get_messages()`` is made to hang forever from the moment
        cancellation begins. If ANY code awaited it while unwinding, the
        0.2s timeout would never complete -- the harness fails loudly at 5s
        instead of hanging the suite. We additionally assert the call count
        did not move after cancellation started, so the test cannot pass by
        accident (e.g. via a short-circuit that skipped the await for an
        unrelated reason).
        """
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        hooks = FakeHooks()
        context = FakeContext()
        calls_when_cancelled: list[int] = []

        async def execute_impl(instruction):
            await hooks.emit("provider:request", {"iteration": 1})
            context.messages.append({"role": "user", "content": "work"})
            await hooks.emit("provider:request", {"iteration": 2})
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # The deadline has fired. From here on, any await on
                # get_messages() would never return.
                context.hang_get_messages = True
                calls_when_cancelled.append(context.get_messages_calls)
                raise

        child = _make_child_session(context, hooks, execute_impl)
        elapsed = await _spawn_until_timeout(child, _make_parent_session())

        assert calls_when_cancelled, "the child was never actually cancelled"
        assert context.get_messages_calls == calls_when_cancelled[0], (
            "get_messages() was awaited on the cancellation path -- that is the "
            "unbounded await this design exists to forbid"
        )
        assert elapsed < 2.0, (
            f"unwinding a 0.2s timeout took {elapsed:.2f}s; the cancellation "
            "path is doing work it must not do"
        )

    async def test_the_probe_would_catch_a_violating_implementation(self):
        """Inverted control -- proof the probe above is not vacuous.

        The unpatched code ALSO has no await on its cancellation path, so the
        probe passes before the fix as well. That makes it a regression guard
        rather than a defect reproduction, and a guard is worthless unless it
        can fail. This control reproduces the shape of the REJECTED option
        (a) -- a best-effort save while unwinding -- and shows the probe's
        instrument (a hanging ``get_messages``) does catch it.

        It also demonstrates concretely why option (a) is unsafe: once the
        deadline's CancelledError has been delivered and caught, a fresh
        await is NOT re-cancelled. It simply blocks -- past the very deadline
        that caused the unwind.
        """
        context = FakeContext()

        async def violating_unwind():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                context.hang_get_messages = True
                await context.get_messages()  # option (a): the forbidden await
                raise

        async def _under_timeout():
            async with asyncio.timeout(0.2):
                await violating_unwind()

        task = asyncio.ensure_future(_under_timeout())
        _done, pending = await asyncio.wait({task}, timeout=1.0)
        try:
            assert task in pending, (
                "the violating control completed -- the probe's instrument does "
                "not actually detect an await on the cancellation path, so the "
                "invariant test above proves nothing"
            )
        finally:
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 - teardown of a cancelled probe
                pass

    async def test_cleanup_still_runs_and_the_timeout_still_propagates(
        self, tmp_path, monkeypatch
    ):
        """Checkpointing must not swallow the timeout or skip child cleanup."""
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        hooks = FakeHooks()
        context = FakeContext()

        async def execute_impl(instruction):
            await hooks.emit("provider:request", {"iteration": 1})
            await asyncio.Event().wait()

        child = _make_child_session(context, hooks, execute_impl)
        parent = _make_parent_session()
        await _spawn_until_timeout(child, parent)

        child.cleanup.assert_awaited()
        parent.coordinator.cancellation.unregister_child.assert_called()
        assert hooks.handlers.get("provider:request") == [], (
            "the checkpoint hook must be unregistered even on the timeout path"
        )


# ---------------------------------------------------------------------------
# 3. The balanced-boundary choice
# ---------------------------------------------------------------------------


class TestCheckpointBoundary:
    async def test_checkpoint_is_wired_to_provider_request_only(
        self, tmp_path, monkeypatch
    ):
        """provider:request is the only tool-pair-balanced point in the loop.

        Checkpointing after a response would persist an assistant message
        whose tool_calls have no matching results; resuming that transcript
        reproduces "No tool call found for function call output".
        """
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        hooks = FakeHooks()
        context = FakeContext()
        seen: dict[str, list[str]] = {}

        async def execute_impl(instruction):
            seen["events"] = sorted(e for e, hs in hooks.handlers.items() if hs)
            return "done"

        child = _make_child_session(context, hooks, execute_impl)
        await _spawn(child, _make_parent_session())

        assert "provider:request" in seen["events"]
        assert "provider:response" not in seen["events"]


# ---------------------------------------------------------------------------
# 4. A failing checkpoint never takes down the run it protects
# ---------------------------------------------------------------------------


class TestCheckpointIsBestEffort:
    async def test_failing_checkpoint_does_not_break_the_run(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        hooks = FakeHooks()
        context = FakeContext()
        attempts: list[int] = []

        real_save = SessionStore.save

        def exploding_save(self, session_id, transcript, metadata):
            attempts.append(1)
            if metadata.get("status") == "in_progress":
                raise OSError("disk is on fire")
            return real_save(self, session_id, transcript, metadata)

        async def execute_impl(instruction):
            await hooks.emit("provider:request", {"iteration": 1})
            context.messages.append({"role": "assistant", "content": "ok"})
            return "agent response"

        child = _make_child_session(context, hooks, execute_impl)

        with patch.object(SessionStore, "save", exploding_save):
            result = await _spawn(child, _make_parent_session())

        assert result["output"] == "agent response"
        assert len(attempts) >= 2, "checkpoint and final save should both be attempted"
        transcript, metadata = SessionStore().load(SUB_SESSION_ID)
        assert metadata["status"] == "complete"

    async def test_missing_hook_registry_still_pre_registers_the_session(
        self, tmp_path, monkeypatch
    ):
        """No hooks -> no mid-run checkpoints, but the id must still resolve."""
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "0")

        context = FakeContext()

        async def execute_impl(instruction):
            await asyncio.Event().wait()

        child = _make_child_session(context, FakeHooks(), execute_impl)
        child.coordinator.get = MagicMock(
            side_effect=lambda name: context if name == "context" else None
        )

        await _spawn_until_timeout(child, _make_parent_session())
        assert SessionStore().exists(SUB_SESSION_ID)


# ---------------------------------------------------------------------------
# 5. Throttle and escape hatch
# ---------------------------------------------------------------------------


class TestThrottleAndEscapeHatch:
    async def test_interval_throttles_mid_run_checkpoints(self, tmp_path, monkeypatch):
        """A large interval collapses N provider calls to the one pre-registration."""
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "3600")

        hooks = FakeHooks()
        context = FakeContext()
        checkpoints: list[str] = []

        real_save = SessionStore.save

        def counting_save(self, session_id, transcript, metadata):
            checkpoints.append(metadata.get("status", "?"))
            return real_save(self, session_id, transcript, metadata)

        async def execute_impl(instruction):
            for i in range(5):
                await hooks.emit("provider:request", {"iteration": i + 1})
            await asyncio.Event().wait()

        child = _make_child_session(context, hooks, execute_impl)
        with patch.object(SessionStore, "save", counting_save):
            await _spawn_until_timeout(child, _make_parent_session())

        assert checkpoints == ["in_progress"], (
            f"expected exactly one (pre-registration) checkpoint, got {checkpoints}"
        )

    async def test_negative_interval_disables_checkpointing_entirely(
        self, tmp_path, monkeypatch
    ):
        """The documented escape hatch, and its cost, pinned in one test."""
        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "-1")

        hooks = FakeHooks()
        context = FakeContext()

        async def execute_impl(instruction):
            await hooks.emit("provider:request", {"iteration": 1})
            await asyncio.Event().wait()

        child = _make_child_session(context, hooks, execute_impl)
        await _spawn_until_timeout(child, _make_parent_session())

        assert not SessionStore().exists(SUB_SESSION_ID), (
            "with checkpointing disabled the pre-fix behaviour is restored: "
            "a timed-out sub-session leaves no store record at all"
        )

    async def test_invalid_interval_falls_back_to_the_default(self, monkeypatch):
        from amplifier_app_cli.session_spawner import (
            _DEFAULT_CHECKPOINT_INTERVAL_S,
            _checkpoint_interval_s,
        )

        monkeypatch.setenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S", "not-a-number")
        assert _checkpoint_interval_s() == _DEFAULT_CHECKPOINT_INTERVAL_S

        monkeypatch.delenv("AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S")
        assert _checkpoint_interval_s() == _DEFAULT_CHECKPOINT_INTERVAL_S
