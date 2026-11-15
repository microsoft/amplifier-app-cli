"""CLI-owned Python exit witness over the selected public Core orchestrator.

RustSession's cancelled await is not a Python-exit witness. Keep one shielded
waiter for its public execute call, and attest the *actual selected* Python
execute's finally through Foundation's explicit host adapter edge. This does
not create a second inference path or run the orchestrator in another task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

_CAPABILITY = "cli.session_execution"
_BLOCKED = "Session execution did not settle; source ownership must be retained."


class SessionExecutionUnsettled(RuntimeError):
    """Fixed, public-safe refusal; private original failures stay with the owner."""


@dataclass(eq=False, repr=False)
class _Invocation:
    generation: int
    exited: asyncio.Future = field(repr=False)
    entered: bool = False
    native: asyncio.Task | None = field(default=None, repr=False)


class _OrchestratorProxy:
    """Delegate the exact selected endpoint, including its optional attributes."""

    __slots__ = ("_owner", "execute")

    def __init__(self, owner):
        object.__setattr__(self, "_owner", owner)

        @wraps(owner.selected.execute)
        async def execute(*args, **kwargs):
            invocation = owner.enter()
            try:
                return await owner.selected.execute(*args, **kwargs)
            finally:
                # The selected execute's own finally has finished, even on error.
                if not invocation.exited.done():
                    invocation.exited.set_result(None)

        object.__setattr__(self, "execute", execute)

    def __getattr__(self, name):
        return getattr(self._owner.selected, name)

    def __setattr__(self, name, value):
        setattr(self._owner.selected, name, value)

    def __delattr__(self, name):
        delattr(self._owner.selected, name)


class SessionExecution:
    """One actual session/selection and serial, strongly retained dispatches."""

    def __init__(self, session, selected, native):
        self.session = session
        self.selected = selected
        self.native = native
        self.proxy = _OrchestratorProxy(self)
        self.generation = 0
        self.invocation: _Invocation | None = None
        self.failure: BaseException | None = None
        self.closed = False
        self.mounted = False

    def _fail(self, error):
        if self.failure is None:
            self.failure = error
        # Existing checkpoint/provider guards block any trailing save or request
        # after an unresolved dispatch; no new storage engine or ACK is installed.
        from .session_checkpoint import latch_checkpoint_failure
        latch_checkpoint_failure(self.session, self.native, self.failure)

    def _identity(self):
        if (self.session.coordinator.get_capability(_CAPABILITY) is not self
                or self.session.coordinator.get("orchestrator") is not self.proxy):
            error = SessionExecutionUnsettled(_BLOCKED)
            self._fail(error)
            raise error from None

    def arm(self):
        self._identity()
        if self.closed or self.failure is not None or not self.idle:
            raise SessionExecutionUnsettled(_BLOCKED) from None
        self.generation += 1
        invocation = _Invocation(self.generation, asyncio.get_running_loop().create_future())
        self.invocation = invocation  # BEFORE Core can schedule Python entry.
        return invocation

    def enter(self):
        self._identity()
        invocation = self.invocation
        if (self.closed or self.failure is not None or invocation is None
                or invocation.entered or invocation.generation != self.generation):
            error = SessionExecutionUnsettled(_BLOCKED)
            self._fail(error)
            raise error from None
        invocation.entered = True
        return invocation

    @property
    def idle(self):
        invocation = self.invocation
        return invocation is None or (
            invocation.entered and invocation.exited.done()
            and invocation.native is not None and invocation.native.done()
        )

    def require_idle(self):
        # Before dispatch, even a failed construction may be cleaned up. Nothing
        # could have entered through this owner yet. A dispatched unknown is not
        # equivalent to that setup case, even when Python has not entered.
        if self.mounted or self.invocation is not None:
            self._identity()
        if self.failure is not None or not self.idle:
            raise SessionExecutionUnsettled(_BLOCKED) from None


def session_execution(session) -> SessionExecution | None:
    """Read the actual host-owned witness, not an inherited capability token."""
    getter = getattr(session.coordinator, "get_capability", None)
    if not callable(getter) or asyncio.iscoroutinefunction(getter):
        return None
    host = getter("cli.session_media")
    owner = getattr(host, "execution", None)
    published = getter(_CAPABILITY)
    installed = getattr(host, "_execution_owner", None)
    if isinstance(installed, SessionExecution):
        # Both public/mutable references can disappear together. The actual
        # media owner's once-installed strong anchor distinguishes that loss
        # from legacy/setup where execution was never installed. It cannot
        # grant readiness: every live reference must still match exactly.
        if (installed.session is not session or owner is not installed
                or published is not installed):
            error = SessionExecutionUnsettled(_BLOCKED)
            installed._fail(error)
            raise error from None
    if isinstance(published, SessionExecution) and published is not owner:
        error = SessionExecutionUnsettled(_BLOCKED)
        published._fail(error)
        raise error from None
    if not isinstance(owner, SessionExecution):
        return None  # Legacy/setup cleanup before an execution owner exists.
    if owner.session is not session or published is not owner:
        error = SessionExecutionUnsettled(_BLOCKED)
        owner._fail(error)
        raise error from None
    return owner


async def install_session_execution(session) -> SessionExecution | None:
    """Mount once over the exact loaded endpoint, through the issuer's view.

    Call after initialize and before seal, including loop-live's provider-return
    path. Repeated media mounting must not wrap a proxy or remint its witness.
    """
    from amplifier_core import ModuleCoordinator
    from .session_hooks import CLIHookGraph, HOOK_GRAPH_CAPABILITY, session_hook_coordinator

    if type(session.coordinator) is not ModuleCoordinator:
        return None  # A structural test double cannot establish this witness.
    graph = session.coordinator.get_capability(HOOK_GRAPH_CAPABILITY)
    if not isinstance(graph, CLIHookGraph):
        return None  # No invented qualified graph for legacy hosts/test doubles.
    host = session.coordinator.get_capability("cli.session_media")
    if graph.session is not session or host is None or host.session is not session:
        raise SessionExecutionUnsettled(_BLOCKED) from None
    previous = session_execution(session)
    if previous is not None:
        previous._identity()
        return previous
    selected = session.coordinator.get("orchestrator")
    if selected is None or not callable(getattr(selected, "execute", None)):
        raise SessionExecutionUnsettled(_BLOCKED) from None
    owner = SessionExecution(session, selected, host.native)
    # Retain ownership before the first await, including failed construction.
    host._execution_owner = owner
    host.execution = owner
    session.coordinator.register_capability(_CAPABILITY, owner)
    await session_hook_coordinator(session, "host.media").mount_adapter(
        "orchestrator", owner.proxy, previous=selected,
    )
    owner.mounted = True
    return owner


def require_session_execution_idle(session) -> None:
    """Synchronous media-close precondition; never starts physical cleanup."""
    owner = session_execution(session)
    if owner is not None:
        owner.require_idle()


async def wait_session_execution(session) -> None:
    """Join before Core/media cleanup; cancelled cleanup retains uncertainty.

    Shield only the owner-held futures. A cleanup waiter cannot cancel the
    Python orchestrator or decide an unentered armed invocation is idle.
    """
    owner = session_execution(session)
    if owner is None:
        return
    invocation = owner.invocation
    if invocation is not None and not owner.idle and owner.failure is None:
        try:
            if invocation.native is not None:
                # Native errors are handled by execute_session. Completion alone
                # is not proof of Python exit, so always check the witness below.
                await asyncio.wait({invocation.native})
            if not invocation.entered:
                raise SessionExecutionUnsettled(_BLOCKED)
            await asyncio.shield(invocation.exited)
        except BaseException as error:
            owner._fail(error)
            raise
    owner.require_idle()


async def execute_session(session, prompt: str) -> Any:
    """Run Core once; join Python exit or latch unresolved dispatch.

    One owned Task retains the public Core waiter, not a second orchestrator.
    Shielding from *dispatch* closes the cancelled-before-Python-entry gap: the
    native dispatch is not dropped by cancellation of this CLI caller. Repeated
    caller cancellation requests stop cooperatively, then keep joining. There
    is deliberately no timeout that pretends to prove exit.

    A native failure before Python entry has no exit witness: retain that
    generation and latch checkpoint/provider refusal before unwinding. Callers
    must honor that latch for final saves, including any legacy unbound writer.
    """
    owner = session_execution(session)
    if owner is None:
        from amplifier_core import ModuleCoordinator
        from .session_hooks import CLIHookGraph, HOOK_GRAPH_CAPABILITY
        from .session_media import CLISessionMedia

        coordinator = session.coordinator
        getter = getattr(coordinator, "get_capability", None)
        if callable(getter) and not asyncio.iscoroutinefunction(getter):
            host = getter("cli.session_media")
            if (isinstance(getter(HOOK_GRAPH_CAPABILITY), CLIHookGraph)
                    or (isinstance(host, CLISessionMedia) and host.execution is not None)
                    or (type(coordinator) is ModuleCoordinator
                        and isinstance(coordinator.get("orchestrator"), _OrchestratorProxy))):
                # Missing ownership in an admitted graph is not legacy opt-out.
                error = SessionExecutionUnsettled(_BLOCKED)
                if isinstance(host, CLISessionMedia) and host.session is session:
                    from .session_checkpoint import latch_checkpoint_failure
                    latch_checkpoint_failure(session, host.native, error)
                raise error from None
        # The installer deliberately issues no witness for unqualified legacy
        # hosts/structural doubles. Preserve their old execute contract only;
        # this fallback makes no Python-exit or source-custody guarantee.
        return await session.execute(prompt)
    invocation = owner.arm()
    interrupted = None
    native_error = None
    result = None
    try:
        invocation.native = asyncio.create_task(session.execute(prompt))
    except BaseException as error:
        owner._fail(error)
        raise
    while True:
        try:
            result = await asyncio.shield(invocation.native)
            break
        except asyncio.CancelledError as error:
            if interrupted is None:
                interrupted = error  # Preserve the first cancellation object.
                try:
                    session.coordinator.cancellation.request_immediate()
                except BaseException as secondary:
                    owner._fail(secondary)
            if invocation.native.done():
                break
        except BaseException as error:
            native_error = error
            break
    # Consume native cancellation/error, including a native exception racing an
    # outer cancellation. Never format it or feed it into a new diagnostic sink.
    if invocation.native.done() and not invocation.native.cancelled():
        native_error = invocation.native.exception()
    if not invocation.entered:
        owner._fail(
            native_error if native_error is not None else
            interrupted if interrupted is not None else SessionExecutionUnsettled(_BLOCKED)
        )
    else:
        while not invocation.exited.done():
            try:
                await asyncio.shield(invocation.exited)
            except asyncio.CancelledError as error:
                if interrupted is None:
                    interrupted = error
    try:
        owner.require_idle()
    except SessionExecutionUnsettled:
        if interrupted is not None:
            raise interrupted
        raise
    if interrupted is not None:
        raise interrupted
    if native_error is not None:
        raise native_error
    return result
