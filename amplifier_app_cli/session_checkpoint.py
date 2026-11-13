"""Fail-closed checkpoint at the loop's settled canonical append boundary.

This is deliberately separate from best-effort tool:post/throttled autosaves.
The loop calls it only after the effective results are in canonical context.
Foundation owns the on-disk format and durability receipt; this host owns the
storage location and, for a shared root, the existing writer fence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .session_store import SessionStore


def install_session_checkpoint(
    session: Any,
    store: SessionStore,
    *,
    session_id: str,
    bundle: str = "unknown",
    root_state: Any = None,
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
        # Read current metadata at the actual write, not from an init snapshot:
        # naming hooks and other owners may have saved changes since then.
        metadata = {
            **store.get_metadata_if_exists(session_id),
            "session_id": session_id,
            "last_updated": datetime.now(UTC).isoformat(),
        }
        metadata.setdefault("created", datetime.now(UTC).isoformat())
        metadata.setdefault("bundle", bundle)
        if root_state is not None:
            return root_state.checkpoint(
                store, messages, bundle=bundle, metadata=metadata
            )
        return store.save(session_id, messages, metadata)

    session.coordinator.register_capability("session.durable_checkpoint", checkpoint)