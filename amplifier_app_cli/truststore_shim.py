"""TEMPORARY mitigation: serialize ``truststore.SSLContext.wrap_bio``.

**Delete this module when `truststore` ships the lock upstream.** Everything
here exists only because a third-party package we do not own has a data race
that kills this process with no Python-level error surface.

The defect
----------
``truststore`` re-applies the OS trust store to its wrapped ``ssl.SSLContext``
on every wrap, via ``_configure_context()``, which calls
``ctx.set_default_verify_paths()``. That writes to the context's
``X509_STORE`` and is not safe to call concurrently on one context.

``truststore/_api.py`` knows this. ``wrap_socket`` holds ``self._ctx_lock``
across the ``_configure_context().__enter__()``::

    with contextlib.ExitStack() as stack:
        with self._ctx_lock:
            stack.enter_context(_configure_context(self._ctx))

``wrap_bio``, 25 lines later, does not::

    with _configure_context(self._ctx):
        ssl_obj = self._ctx.wrap_bio(...)

``self._ctx_lock`` exists and is simply not used on that path. Verified
against ``truststore`` 0.10.4 (the current latest release) and against
``sethmlarson/truststore@main`` on 2026-09-05: both still unlocked.

Why it reaches us
-----------------
``wrap_bio`` is the path every async HTTP client in this stack takes --
anyio -> httpcore2 -> httpx2 -> the Anthropic SDK -- and ``anyio`` hands it to
a worker thread (``anyio/streams/tls.py``: "External SSLContext
implementations may do blocking I/O in wrap_bio()"). The CLI resolves the
model role of every agent in the composed bundle concurrently on every session
mount, so tens of those worker threads enter the unlocked
``set_default_verify_paths()`` on one shared context at the same instant and
glibc aborts::

    double free or corruption (!prev)
    Fatal Python error: Aborted

Exit 134, sometimes 139. No traceback, no result envelope, no partial output.
Every abort captured had 20-37 threads inside
``truststore/_openssl.py:38 _configure_context``, **100% via ``wrap_bio``,
0% via ``wrap_socket``**.

Nothing in the amplifier tree constructs a ``truststore.SSLContext``. It is
pulled in by ``httpx2/_config.py:40`` and ``httpcore2/_ssl.py:12`` (pydantic's
httpx fork, which declares ``truststore>=0.10``). So there is no amplifier-owned
call site to fix -- the earliest amplifier-owned point that runs before any TLS
is CLI startup, which is where this shim is applied from.

What this does
--------------
Wraps ``truststore.SSLContext.wrap_bio`` so it runs while holding the very
lock ``wrap_socket`` already takes, mirroring the upstream one-line fix from
the consumer side by monkeypatch.

One honest difference from the upstream diff: upstream holds the lock only
across ``_configure_context().__enter__()``, then releases it for the wrap
itself. From outside the function we cannot interleave like that, so this
holds the lock for the whole call. That is strictly stronger and costs
nothing here: ``SSLContext.wrap_bio`` only builds an ``SSLObject`` over two
memory BIOs -- there is no socket and no I/O in it; the handshake happens
later in ``do_handshake``.

Safety
------
``_ctx_lock`` is a plain ``threading.Lock``, so taking it around a
``wrap_bio`` that *also* took it would deadlock. This shim therefore refuses
to patch unless it can read the installed ``wrap_bio`` source and confirm it
does **not** reference ``_ctx_lock``. Unreadable source means no patch. Every
skip path is a no-op, and any unexpected failure is swallowed and logged --
a mitigation must never be the reason the CLI cannot start.

Removal
-------
When a ``truststore`` release carries the lock, set
:data:`FIRST_FIXED_VERSION` and this becomes a no-op on that version; the
whole module (and its call in ``amplifier_app_cli/__init__.py``) can then be
deleted.

Escape hatch: ``AMPLIFIER_TRUSTSTORE_WRAP_BIO_SHIM=0`` disables it.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# First `truststore` release known to take `self._ctx_lock` inside `wrap_bio`.
# None means "no fixed release exists yet" -- as of 2026-09-05 the latest
# release (0.10.4) and `main` are both still unlocked. Set this when one ships;
# the shim then stands down on its own for anyone on that version or newer.
FIRST_FIXED_VERSION: str | None = None

# Env var that turns the shim off entirely.
DISABLE_ENV_VAR = "AMPLIFIER_TRUSTSTORE_WRAP_BIO_SHIM"

_FALSEY = {"0", "false", "no", "off"}

# Stamped on the replacement function so a second apply() is a no-op.
_MARKER = "_amplifier_wrap_bio_lock_shim"

# Outcome of the most recent apply(). Read this rather than the log: apply()
# runs at import time, before the CLI configures logging, so the log line
# itself lands nowhere useful on a default run.
SHIM_STATUS: str = "not applied"

_apply_lock = threading.Lock()


def _installed_truststore_version() -> str | None:
    """Version of the installed `truststore` distribution, or None."""
    try:
        from importlib.metadata import version

        return version("truststore")
    except Exception:  # pragma: no cover - metadata missing/broken
        return None


def _wrap_bio_takes_lock(func: Any) -> bool | None:
    """Does this `wrap_bio` already take `_ctx_lock`?

    Returns None when the source cannot be read, which is treated as "do not
    patch" -- guessing wrong in that direction is a deadlock.
    """
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):  # pragma: no cover - frozen/zipped installs
        return None
    return "_ctx_lock" in source


def _make_locked_wrap_bio(original: Any) -> Any:
    """Return a `wrap_bio` that holds `self._ctx_lock` across the original."""

    @functools.wraps(original)
    def wrap_bio(self: Any, *args: Any, **kwargs: Any) -> Any:
        # anyio calls this positionally via to_thread.run_sync(), httpcore by
        # keyword -- *args/**kwargs passes both through untouched.
        lock = getattr(self, "_ctx_lock", None)
        if lock is None:
            return original(self, *args, **kwargs)
        with lock:
            return original(self, *args, **kwargs)

    setattr(wrap_bio, _MARKER, True)
    return wrap_bio


def apply_to(ssl_context_cls: type, installed_version: str | None = None) -> str:
    """Apply the lock shim to one `truststore.SSLContext`-shaped class.

    Split out from :func:`apply` so it can be exercised against a fake class
    without importing or mutating the real `truststore`.

    Returns a short status string; only a status starting with "applied" means
    the class was changed.
    """
    original = ssl_context_cls.__dict__.get("wrap_bio")
    if original is None:
        return f"skipped: {ssl_context_cls.__name__} has no own wrap_bio"

    if getattr(original, _MARKER, False):
        return "skipped: already applied"

    if installed_version and FIRST_FIXED_VERSION:
        try:
            from packaging.version import Version

            if Version(installed_version) >= Version(FIRST_FIXED_VERSION):
                return (
                    f"skipped: truststore {installed_version} >= "
                    f"{FIRST_FIXED_VERSION}, fixed upstream"
                )
        except Exception:  # pragma: no cover - unparseable version
            pass

    takes_lock = _wrap_bio_takes_lock(original)
    if takes_lock is None:
        return "skipped: could not read wrap_bio source, refusing to patch blind"
    if takes_lock:
        return "skipped: wrap_bio already takes _ctx_lock"

    setattr(ssl_context_cls, "wrap_bio", _make_locked_wrap_bio(original))
    return f"applied to truststore {installed_version or 'unknown'}"


def apply() -> str:
    """Install the shim on the real `truststore.SSLContext`, if warranted.

    Safe to call more than once and safe to call when `truststore` is not
    installed. Never raises.
    """
    global SHIM_STATUS
    try:
        with _apply_lock:
            status = _apply()
    except Exception as exc:  # pragma: no cover - defensive
        status = f"skipped: shim raised {exc!r}"
    SHIM_STATUS = status
    logger.debug("truststore wrap_bio lock shim: %s", status)
    return status


def _apply() -> str:
    if os.environ.get(DISABLE_ENV_VAR, "").strip().lower() in _FALSEY:
        return f"skipped: disabled by {DISABLE_ENV_VAR}"

    try:
        import truststore
    except Exception:
        # No truststore, no race. httpx2/httpcore2 declare it, but nothing here
        # requires it to be present.
        return "skipped: truststore is not importable"

    return apply_to(truststore.SSLContext, _installed_truststore_version())
