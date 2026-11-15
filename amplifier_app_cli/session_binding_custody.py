"""One process-local CLI cleanup owner per actual Foundation binding identity.

This is lifecycle bookkeeping, not a writer, filesystem lease or enrollment
protocol. Foundation remains the sole persistent owner. Root and child entry
points claim synchronously before accepting cleanup responsibility; retained
failures keep strong references and cannot be adopted by another lifecycle.
"""

from dataclasses import dataclass
import threading
from typing import Any


@dataclass(eq=False)
class BindingCustody:
    binding: Any
    owner: Any
    session: Any = None
    error: BaseException | None = None
    closing: bool = False


_CUSTODY: dict[int, BindingCustody] = {}
_LOCK = threading.RLock()


def binding_custody(binding) -> BindingCustody | None:
    """Inspect only this exact object, never a session ID or serialized token."""
    with _LOCK:
        current = _CUSTODY.get(id(binding))
        return current if current is not None and current.binding is binding else None


def require_binding_available(binding) -> None:
    with _LOCK:
        current = binding_custody(binding)
        if current is not None:
            if current.error is not None:
                raise RuntimeError("Versioned cleanup requires explicit owner settlement before reuse (custody)")
            raise RuntimeError("Versioned source already has a live cleanup owner (custody)")


def claim_binding(binding, owner) -> None:
    """Atomically accept one real owner; no await or implicit ownership transfer."""
    if owner is None:
        raise ValueError("Binding custody requires an actual cleanup owner")
    with _LOCK:
        require_binding_available(binding)
        binding.check()
        _CUSTODY[id(binding)] = BindingCustody(binding, owner)


def _owned(binding, owner) -> BindingCustody:
    current = binding_custody(binding)
    if current is None or current.owner is not owner:
        raise RuntimeError("Versioned binding custody belongs to a different cleanup owner")
    return current


def retain_binding(binding, owner, error, *, session=None) -> None:
    """Keep the original cleanup failure and actual resources, without replay."""
    with _LOCK:
        current = _owned(binding, owner)
        if current.error is None:
            current.error = error
        if session is not None:
            current.session = session


def close_binding(binding, owner) -> None:
    """Release only this owner after it has verified successful session cleanup.

    Callers must check the actual media settlement witness first. Failed or
    cancelled cleanup never reaches this function, and retained custody cannot
    be cleared here. Close failure likewise leaves the identity claimed.
    """
    with _LOCK:
        current = _owned(binding, owner)
        if current.error is not None:
            raise RuntimeError("Versioned cleanup requires explicit owner settlement before release")
        if current.closing:
            raise RuntimeError("Versioned binding custody release was already attempted")
        current.closing = True
    # Foundation close may join physical I/O. Keep custody claimed, but do not
    # hold the registry lock while waiting on another subsystem's mutex.
    try:
        binding.close()
    except BaseException as error:
        # Retain at the actual release boundary as well as in host cleanup.
        # Even an interrupted caller cannot leave an unrecorded close failure.
        retain_binding(binding, owner, error)
        raise
    with _LOCK:
        _owned(binding, owner)
        del _CUSTODY[id(binding)]
