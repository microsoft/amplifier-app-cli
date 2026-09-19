"""CLI policy around Foundation's shared root-session checkpoint.

Foundation owns the lock, checkpoint format, identity validation, and durable
I/O.  This module intentionally contains no compatibility implementation of
those mechanisms: when the required Foundation API is absent, opening a root
fails loudly instead of silently falling back to an unlocked writer.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amplifier_core.utils.truncate import redact_secrets

from .session_store import BUNDLE_PREFIX

if TYPE_CHECKING:
    from .session_store import SessionStore


class SharedRootStateUnavailableError(RuntimeError):
    """The installed Foundation does not implement the WARM shared-state API."""


class SharedRootSessionBusyError(RuntimeError):
    """A shared root is held by another process, with bounded owner advice."""

    def __init__(self, owner: object, state_root: object) -> None:
        self.owner = owner if isinstance(owner, dict) else None
        self.state_root = _bounded_value(state_root, limit=240)
        details = _format_owner_details(self.owner)
        root_detail = f" Shared state root: {self.state_root}." if self.state_root else ""
        super().__init__(
            "Shared root session is busy."
            f"{root_detail}"
            f"{details} Finish/exit that owner then retry."
        )


_WINDOWS_NATIVE_NOTICE_EMITTED = False
_SHARED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_FOUNDATION_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "credential",
        "credentials",
        "secret",
        "secrets",
        "token",
        "password",
        "api_key",
        "apikey",
        "env",
        "environment",
    }
)


def _shared_root_platform_supported() -> bool:
    """Return whether this runtime can use Foundation's POSIX lock contract.

    ``sys.platform`` is deliberately the seam: tests can simulate Windows
    without replacing ``os.name`` after pathlib has selected its path class.
    """

    return sys.platform != "win32"


def _require_shared_root_platform() -> None:
    if not _shared_root_platform_supported():
        return
    try:
        import fcntl  # noqa: F401 - capability probe only
    except ImportError as exc:
        raise SharedRootStateUnavailableError(
            "Root session sharing requires POSIX fcntl locking on this platform."
        ) from exc


def shared_root_id_supported(session_id: str) -> bool:
    """Return whether Foundation can safely address this existing root ID."""

    return isinstance(session_id, str) and bool(_SHARED_ID_RE.fullmatch(session_id))


def warn_windows_native_persistence() -> None:
    """Explain the Windows policy once per CLI process, on stderr only."""

    global _WINDOWS_NATIVE_NOTICE_EMITTED
    if _shared_root_platform_supported() or _WINDOWS_NATIVE_NOTICE_EMITTED:
        return
    _WINDOWS_NATIVE_NOTICE_EMITTED = True
    print(
        "Shared root sessions are unavailable on Windows; using native session persistence.",
        file=sys.stderr,
    )


def _bounded_value(value: object, *, limit: int = 120) -> str:
    """Render untrusted lock diagnostics as one bounded, non-control line."""

    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, (str, int, float, bool)):
        text = "".join(char if char.isprintable() else " " for char in str(value))
        text = " ".join(text.split())
        return text[:limit]
    return ""


def _format_owner_details(owner: dict[str, Any] | None) -> str:
    """Allow-list advisory owner fields; corrupt owner data remains merely busy."""

    if not owner:
        return ""
    labels = (
        ("app", "app"),
        ("hostname", "host"),
        ("user", "user"),
        ("pid", "pid"),
        ("process_start_identity", "process"),
        ("acquired_at", "acquired"),
        ("tty", "tty"),
        ("service", "service"),
    )
    parts = [
        f"{label}={value}"
        for key, label in labels
        if (value := _bounded_value(owner.get(key)))
    ]
    return f" Owner: {', '.join(parts)}." if parts else ""


def shared_state_available() -> bool:
    """Probe the required Foundation API without creating a session directory."""

    if not _shared_root_platform_supported():
        return False
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
    if not callable(getattr(HeldSession, "delete_checkpoint", None)):
        raise SharedRootStateUnavailableError(
            "Root session sharing requires amplifier-foundation "
            "HeldSession.delete_checkpoint(); install the compatible Foundation release."
        )
    return SharedSessionStore, HeldSession


def _session_busy_type() -> type[BaseException] | None:
    """Import Foundation's exact busy type lazily with the shared API."""

    try:
        from amplifier_foundation.session.shared_state import SessionBusyError
    except ImportError:
        return None
    return SessionBusyError


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


def _shared_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove metadata Foundation refuses rather than persisting redacted keys."""

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: scrub(nested)
                for key, nested in value.items()
                if isinstance(key, str)
                and key.lower() not in _FOUNDATION_FORBIDDEN_METADATA_KEYS
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(redact_secrets(metadata))


@dataclass
class SharedRootSession:
    """One process-bound Foundation writer handle for a top-level CLI root."""

    session_id: str
    workspace: Path
    held: Any

    @classmethod
    def acquire(cls, session_id: str) -> "SharedRootSession":
        """Acquire before constructing provider context or writing a projection."""

        _require_shared_root_platform()
        if not shared_root_id_supported(session_id):
            raise SharedRootStateUnavailableError(
                "Root session sharing requires a 1-128 character ASCII letter, digit, or hyphen session ID."
            )
        SharedSessionStore, _ = _shared_api()
        workspace = _workspace()
        store = SharedSessionStore(workspace, session_id)
        try:
            held = store.acquire(app="amplifier-cli")
        except BaseException as exc:
            busy_type = _session_busy_type()
            if busy_type is not None and isinstance(exc, busy_type):
                raise SharedRootSessionBusyError(
                    getattr(exc, "owner", None), getattr(store, "root", None)
                ) from None
            raise
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
            metadata=_shared_metadata(metadata),
        )
        # Projections never feed back into authority.  The Foundation write
        # above either succeeded completely or this native write is not reached.
        native_store.save(self.session_id, messages, metadata)

    def delete_checkpoint(self) -> None:
        """Delete authority through Foundation's held capability only."""

        self.held.delete_checkpoint()

    def release(self) -> None:
        """Release only after the caller's final checkpoint and cleanup."""

        self.held.release()


def read_shared_root(session_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Read a root without taking a writer lock (history/resume discovery only)."""

    if not _shared_root_platform_supported():
        return None
    if not shared_root_id_supported(session_id):
        return None
    _require_shared_root_platform()
    SharedSessionStore, _ = _shared_api()
    checkpoint = SharedSessionStore(_workspace(), session_id).read()
    return _checkpoint_resume(checkpoint) if checkpoint is not None else None


def list_shared_root_ids() -> list[str]:
    """Return metadata-only shared root IDs for this exact canonical workspace."""

    if not _shared_root_platform_supported():
        return []
    _require_shared_root_platform()
    SharedSessionStore, _ = _shared_api()
    return SharedSessionStore.list_ids(_workspace())


def is_shared_root(session_id: str) -> bool:
    """Return whether the exact root has common authority without taking it."""

    return read_shared_root(session_id) is not None


def resolve_root_session_id(native_store: "SessionStore", partial_id: str) -> str:
    """Resolve one root ID from native or shared state without copying either."""

    native_ids = native_store.list_sessions()
    shared_ids = list_shared_root_ids()
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

    if not _shared_root_platform_supported() or not shared_root_id_supported(session_id):
        return native_store.load(session_id)
    shared = read_shared_root(session_id)
    if shared is not None:
        return shared
    return native_store.load(session_id)


def update_root_metadata(
    native_store: "SessionStore", session_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    """Update root metadata through a fresh held writer, never its projection alone."""

    if not _shared_root_platform_supported() or not shared_root_id_supported(session_id):
        native_store.update_metadata(session_id, updates)
        return native_store.get_metadata(session_id)
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
