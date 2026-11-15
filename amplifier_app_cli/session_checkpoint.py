"""Fail-closed checkpoint at the loop's settled canonical append boundary.

Direct calls remain separate from registered hook diagnostics. Prospective
autosaves share this failure latch; ordinary unbound autosaves stay best-effort.
The loop calls it only after the effective results are in canonical context.
Foundation owns the on-disk format and durability receipt; this host owns the
storage location and, for a shared root, the existing writer fence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
import asyncio
import inspect
import uuid

from .session_store import SessionStore


_BLOCKED = "Durable checkpoint failed; owner reconciliation is required."
_INTERRUPTED = "Durable checkpoint was interrupted; owner reconciliation is required."


class CheckpointBlocked(RuntimeError):
    """Safe dispatch refusal, never a carrier for the private primary failure."""


@dataclass(repr=False)
class _CheckpointFailure:
    primary: BaseException = field(repr=False)
    interrupted: bool
    revocation_error: BaseException | None = field(default=None, repr=False)
    coordinator_error: BaseException | None = field(default=None, repr=False)


def _stored_checkpoint_failure(session, store):
    if store is not None:
        failure = vars(store).get("_cli_checkpoint_failures", {}).get(
            getattr(session, "session_id", None)
        )
        if isinstance(failure, _CheckpointFailure):
            return failure
    return None


def _checkpoint_failure(session, store=None):
    failure = _stored_checkpoint_failure(session, store)
    if failure is not None:
        return failure  # A broken coordinator cannot hide an existing latch.
    try:
        getter = getattr(session.coordinator, "get_capability", None)
        failure = getter("_cli.checkpoint_failure") if callable(getter) else None
    except BaseException as error:
        # An unavailable readiness read is not evidence that dispatch is safe.
        # Retain it before any further coordinator access, then block safely.
        if store is not None:
            return latch_checkpoint_failure(session, store, error)
        return _CheckpointFailure(
            error, isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
        )
    if isinstance(failure, _CheckpointFailure):
        if store is not None:
            vars(store).setdefault("_cli_checkpoint_failures", {})[session.session_id] = failure
        return failure
    return None


def latch_checkpoint_failure(session, store, error: BaseException) -> _CheckpointFailure:
    """Retain the first actual failure privately, without formatting or replay.

    Cancellation, KeyboardInterrupt and SystemExit inside a safe hook classify
    an interrupted checkpoint, not task cancellation or host termination.
    Neither a surviving graph receipt nor an older media
    ACK permits delivery after this latch. Reconciliation is owner-driven;
    installing another callback must not clear it.
    """
    failure = _stored_checkpoint_failure(session, store)
    if failure is not None:
        return failure
    failure = _CheckpointFailure(
        error, isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
    )
    # Core's actual RustSession has no mutable __dict__. Keep runtime-only
    # custody in the actual store FIRST. Coordinator publication is auxiliary;
    # its own failure must neither replace the primary nor escape a safe hook.
    vars(store).setdefault("_cli_checkpoint_failures", {})[session.session_id] = failure
    try:
        session.coordinator.register_capability("_cli.checkpoint_failure", failure)
    except BaseException as secondary:
        failure.coordinator_error = secondary
    try:
        from .session_media import CLISessionMedia

        host = session.coordinator.get_capability("cli.session_media")
    except BaseException as secondary:
        if failure.coordinator_error is None:
            failure.coordinator_error = secondary
        return failure

    if isinstance(host, CLISessionMedia) and host.session is session and host.native is store:
        if host.durable:
            try:
                host.store.revoke()
            except BaseException as secondary:
                failure.revocation_error = secondary
    return failure


def check_checkpoint_ready(session, store=None) -> None:
    """Reject later operations with fixed safe text, never rethrow the primary."""
    failure = _checkpoint_failure(session, store)
    if failure is not None:
        raise CheckpointBlocked(_INTERRUPTED if failure.interrupted else _BLOCKED) from None


def checkpoint_hook(session, store, callback):
    """Wrap a registered Core hook (event, data) -> HookResult.

    Core may format/log/swallow callback BaseExceptions. This boundary returns
    its real deny action instead, retains the original only in private local
    custody, and blocks provider preparation/count/send independently of Core's
    handling of that result. Wrap the entire child callback, including its
    throttle/context lookup, not only its final write. Direct checkpoint calls
    remain separate and retain their original failure semantics.
    """
    async def guarded(event, data):
        from amplifier_core.models import HookResult

        if vars(store).get("_canonical_binding") is None:
            result = callback(event, data)
            return await result if inspect.isawaitable(result) else result
        try:
            check_checkpoint_ready(session, store)
            result = callback(event, data)
            result = await result if inspect.isawaitable(result) else result
            check_checkpoint_ready(session, store)
            return result
        except BaseException as error:
            failure = latch_checkpoint_failure(session, store, error)
        # Outside the exception handler: no original exception escapes into
        # Core's diagnostic sink, including cause/context/notes/opaque __str__.
        reason = _INTERRUPTED if failure.interrupted else _BLOCKED
        return HookResult(action="deny", reason=reason, user_message=reason)

    return guarded


def install_session_checkpoint(
    session: Any,
    store: SessionStore,
    *,
    session_id: str,
    bundle: str = "unknown",
    root_state: Any = None,
    metadata_factory: Callable[[], dict] | None = None,
    canonical_admit: Callable[[], None] | None = None,
) -> None:
    """Install a fresh callback for this session, without inheriting authority.

    No debounce, best-effort exception handling or cancellation-path write is
    permitted here. A failed save must stop the next provider dispatch.
    """

    async def perform_checkpoint() -> Any:
        context = session.coordinator.get("context")
        if context is None or not callable(getattr(context, "get_messages", None)):
            raise RuntimeError("Canonical context is unavailable for checkpoint")
        messages = await context.get_messages()
        # Pass only fields owned by this checkpoint. Foundation merges them
        # with current metadata under its lock; replaying an unlocked snapshot
        # could overwrite another owner's concurrent non-naming updates.
        metadata = {
            **(metadata_factory() if metadata_factory else {}),
            "session_id": session_id,
            "last_updated": datetime.now(UTC).isoformat(),
        }
        if bundle != "unknown":
            metadata["bundle"] = bundle
        if root_state is not None:
            return root_state.checkpoint(
                store, messages, bundle=bundle, metadata=metadata
            )
        return save_runtime_checkpoint(session, store, session_id, messages, metadata)

    async def checkpoint() -> Any:
        check_checkpoint_ready(session, store)
        try:
            return await perform_checkpoint()
        except BaseException as error:
            if binding is not None:
                latch_checkpoint_failure(session, store, error)
            raise

    binding = vars(store).get("_canonical_binding")
    if binding is not None:
        if not callable(canonical_admit):
            raise ValueError("Prospective checkpoints require actual asset admission")

        def save_canonical(messages, metadata):
            check_checkpoint_ready(session, store)
            # This callback receives a full canonical snapshot, not a settled
            # suffix. Each invocation is one explicit rewrite intent. Unknown
            # completion remains in Foundation's exact reconciliation state.
            try:
                binding.check()
                return store.save_versioned(
                    session_id, messages, metadata, expected=binding.expected,
                    operation_id=uuid.uuid4().hex, admit=canonical_admit,
                )
            except BaseException as error:
                latch_checkpoint_failure(session, store, error)
                raise

        session.coordinator.register_capability("cli.canonical_checkpoint_save", save_canonical)

    session.coordinator.register_capability("cli.session_store", store)
    session.coordinator.register_capability("session.durable_checkpoint", checkpoint)


def save_runtime_checkpoint(session, store, session_id, messages, metadata):
    """Select the explicit installed prospective writer, never legacy fallback."""
    if vars(store).get("_canonical_binding") is not None:
        check_checkpoint_ready(session, store)
        try:
            callback = session.coordinator.get_capability("cli.canonical_checkpoint_save")
            if not callable(callback):
                raise RuntimeError("Prospective session has no owned checkpoint operation")
            return callback(messages, metadata)
        except BaseException as error:
            latch_checkpoint_failure(session, store, error)
            raise
    return store.save(session_id, messages, metadata)