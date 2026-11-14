"""Fail-closed checkpoint at the loop's settled canonical append boundary.

This is deliberately separate from best-effort tool:post/throttled autosaves.
The loop calls it only after the effective results are in canonical context.
Foundation owns the on-disk format and durability receipt; this host owns the
storage location and, for a shared root, the existing writer fence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Callable
from typing import Any
import uuid

from .session_store import SessionStore


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

    async def checkpoint() -> Any:
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

    binding = vars(store).get("_canonical_binding")
    if binding is not None:
        if not callable(canonical_admit):
            raise ValueError("Prospective checkpoints require actual asset admission")

        def save_canonical(messages, metadata):
            binding.check()
            # This callback receives a full canonical snapshot, not a settled
            # suffix. Each invocation is one explicit rewrite intent. Unknown
            # completion remains in Foundation's exact reconciliation state.
            return store.save_versioned(
                session_id, messages, metadata, expected=binding.expected,
                operation_id=uuid.uuid4().hex, admit=canonical_admit,
            )

        session.coordinator.register_capability("cli.canonical_checkpoint_save", save_canonical)

    session.coordinator.register_capability("cli.session_store", store)
    session.coordinator.register_capability("session.durable_checkpoint", checkpoint)


def save_runtime_checkpoint(session, store, session_id, messages, metadata):
    """Select the explicit installed prospective writer, never legacy fallback."""
    if vars(store).get("_canonical_binding") is not None:
        callback = session.coordinator.get_capability("cli.canonical_checkpoint_save")
        if not callable(callback):
            raise RuntimeError("Prospective session has no owned checkpoint operation")
        return callback(messages, metadata)
    return store.save(session_id, messages, metadata)