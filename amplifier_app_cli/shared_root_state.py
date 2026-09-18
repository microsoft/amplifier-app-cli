"""CLI policy around Foundation's shared root-session checkpoint.

Foundation owns the lock, checkpoint format, identity validation, and durable
I/O.  This module intentionally contains no compatibility implementation of
those mechanisms: when the required Foundation API is absent, opening a root
fails loudly instead of silently falling back to an unlocked writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_core.utils.truncate import redact_secrets

from .session_store import BUNDLE_PREFIX

if TYPE_CHECKING:
    from .session_store import SessionStore


class SharedRootStateUnavailableError(RuntimeError):
    """The installed Foundation does not implement the WARM shared-state API."""


def shared_state_available() -> bool:
    """Probe the required Foundation API without creating a session directory."""

    try:
        _shared_api()
    except SharedRootStateUnavailableError:
        return False
    return True


def _shared_api() -> tuple[type[Any], type[Any]]:
    """Import the frozen Foundation interface only when a root is opened."""

    try:
        from amplifier_foundation.session.shared_state import HeldSession
        from amplifier_foundation.session.shared_state import SharedSessionStore
    except ImportError as exc:
        raise SharedRootStateUnavailableError(
            "Root session sharing requires amplifier-foundation "
            "session.shared_state; install the compatible Foundation release."
        ) from exc
    return SharedSessionStore, HeldSession


def _workspace() -> Path:
    try:
        workspace = Path.cwd().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Root session sharing requires a resolvable working directory.") from exc
    if not workspace.is_dir():
        raise RuntimeError("Root session sharing requires a working directory.")
    return workspace


def _portable_bundle(bundle: str) -> str:
    """Convert CLI's legacy display prefix into the portable checkpoint value."""

    return bundle.removeprefix(BUNDLE_PREFIX)


def _checkpoint_resume(checkpoint: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Translate one validated Foundation checkpoint to CLI resume inputs."""

    messages = checkpoint.get("messages")
    metadata = checkpoint.get("metadata")
    bundle = checkpoint.get("bundle")
    if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
        raise RuntimeError("Shared root checkpoint has invalid messages.")
    if not isinstance(metadata, dict) or not isinstance(bundle, str) or not bundle:
        raise RuntimeError("Shared root checkpoint has invalid metadata or bundle.")
    # Native projections retain their historic prefix.  The shared format does
    # not; keeping that distinction here avoids leaking a CLI convention into
    # Foundation's portable data.
    return list(messages), {**metadata, "bundle": f"{BUNDLE_PREFIX}{bundle}"}


@dataclass
class SharedRootSession:
    """One process-bound Foundation writer handle for a top-level CLI root."""

    session_id: str
    workspace: Path
    held: Any

    @classmethod
    def acquire(cls, session_id: str) -> "SharedRootSession":
        """Acquire before constructing provider context or writing a projection."""

        SharedSessionStore, _ = _shared_api()
        workspace = _workspace()
        store = SharedSessionStore(workspace, session_id)
        held = store.acquire(app="amplifier-cli")
        return cls(session_id=session_id, workspace=workspace, held=held)

    def read(self) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """Read the authoritative checkpoint through the live held capability."""

        checkpoint = self.held.read()
        return _checkpoint_resume(checkpoint) if checkpoint is not None else None

    def checkpoint(
        self,
        native_store: "SessionStore",
        messages: list[dict[str, Any]],
        *,
        bundle: str,
        metadata: dict[str, Any],
    ) -> None:
        """Commit authority first, then update the CLI-native compatibility view."""

        self.held.write(
            messages,
            bundle=_portable_bundle(bundle),
            metadata=redact_secrets(metadata),
        )
        # Projections never feed back into authority.  The Foundation write
        # above either succeeded completely or this native write is not reached.
        native_store.save(self.session_id, messages, metadata)

    def release(self) -> None:
        """Release only after the caller's final checkpoint and cleanup."""

        self.held.release()


def read_shared_root(session_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Read a root without taking a writer lock (history/resume discovery only)."""

    SharedSessionStore, _ = _shared_api()
    checkpoint = SharedSessionStore(_workspace(), session_id).read()
    return _checkpoint_resume(checkpoint) if checkpoint is not None else None


def list_shared_root_ids() -> list[str]:
    """Return metadata-only shared root IDs for this exact canonical workspace."""

    SharedSessionStore, _ = _shared_api()
    return SharedSessionStore.list_ids(_workspace())


def is_shared_root(session_id: str) -> bool:
    """Return whether the exact root has common authority without taking it."""

    try:
        return read_shared_root(session_id) is not None
    except SharedRootStateUnavailableError:
        return False


def resolve_root_session_id(native_store: "SessionStore", partial_id: str) -> str:
    """Resolve one root ID from native or shared state without copying either."""

    native_ids = native_store.list_sessions()
    try:
        shared_ids = list_shared_root_ids()
    except SharedRootStateUnavailableError:
        shared_ids = []
    all_ids = list(dict.fromkeys([*native_ids, *shared_ids]))
    if partial_id in all_ids:
        return partial_id
    matches = [session_id for session_id in all_ids if session_id.startswith(partial_id)]
    if not matches:
        raise FileNotFoundError(f"No session found matching '{partial_id}'")
    if len(matches) > 1:
        previews = ", ".join(f"{match[:12]}..." for match in matches[:3])
        suffix = f" and {len(matches) - 3} more" if len(matches) > 3 else ""
        raise ValueError(
            f"Ambiguous session ID '{partial_id}' matches {len(matches)} sessions: "
            f"{previews}{suffix}"
        )
    return matches[0]


def load_root_resume(
    native_store: "SessionStore", session_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prefer common authority; native data remains only a compatibility fallback."""

    try:
        shared = read_shared_root(session_id)
    except SharedRootStateUnavailableError:
        shared = None
    if shared is not None:
        return shared
    return native_store.load(session_id)


def update_root_metadata(
    native_store: "SessionStore", session_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    """Update root metadata through a fresh held writer, never its projection alone."""

    root = SharedRootSession.acquire(session_id)
    try:
        current = root.read()
        if current is None:
            messages, metadata = native_store.load(session_id)
        else:
            messages, metadata = current
        metadata = {**metadata, **updates, "session_id": session_id}
        root.checkpoint(
            native_store,
            messages,
            bundle=str(metadata.get("bundle", "unknown")),
            metadata=metadata,
        )
        return metadata
    finally:
        root.release()
