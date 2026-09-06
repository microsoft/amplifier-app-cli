"""The truststore `wrap_bio` lock shim actually serializes concurrent calls.

The bug being mitigated is a data race in a third-party package: `truststore`
0.10.4's `wrap_bio` mutates a shared OpenSSL context without the `_ctx_lock`
its own `wrap_socket` takes, and the CLI drives ~20 threads into it at once.
The failure mode is a glibc abort (exit 134), so it cannot be asserted on
directly -- these tests assert the property that prevents it instead: after the
shim, no two threads are inside `wrap_bio` at the same time.

Every test drives a fake `truststore.SSLContext`-shaped class, so the real
`truststore` is never mutated here.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from amplifier_app_cli import truststore_shim

THREADS = 20


class _Recorder:
    """Tracks how many threads were inside `wrap_bio` simultaneously."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.calls = 0


def _make_unlocked_context_class() -> type:
    """A class shaped like `truststore.SSLContext` 0.10.4: wrap_bio unlocked."""

    class FakeUnlockedSSLContext:
        def __init__(self) -> None:
            self._ctx_lock = threading.Lock()
            self.recorder = _Recorder()
            self.barrier: threading.Barrier | None = None

        def wrap_bio(
            self,
            incoming,
            outgoing,
            server_side=False,
            server_hostname=None,
            session=None,
        ):
            rec = self.recorder
            with rec.lock:
                rec.live += 1
                rec.peak = max(rec.peak, rec.live)
                rec.calls += 1
            try:
                if self.barrier is not None:
                    # Only the control test sets this: it proves the fake really
                    # can run THREADS-way concurrent, so a peak of 1 later is
                    # the shim's doing and not an artifact of the harness.
                    self.barrier.wait()
                else:
                    time.sleep(0.01)
            finally:
                with rec.lock:
                    rec.live -= 1
            return ("sslobj", incoming, outgoing, server_side, server_hostname, session)

    return FakeUnlockedSSLContext


def _make_locked_context_class() -> type:
    """A class shaped like a future, already-fixed truststore."""

    class FakeLockedSSLContext:
        def __init__(self) -> None:
            self._ctx_lock = threading.Lock()

        def wrap_bio(
            self,
            incoming,
            outgoing,
            server_side=False,
            server_hostname=None,
            session=None,
        ):
            with self._ctx_lock:
                return "sslobj"

    return FakeLockedSSLContext


def _hammer(ctx, threads: int = THREADS) -> None:
    """Call `wrap_bio` from `threads` threads at once, positionally.

    Positional on purpose: anyio invokes it as
    `to_thread.run_sync(ssl_context.wrap_bio, bio_in, bio_out, server_side,
    server_hostname, None)`.
    """
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [
            pool.submit(ctx.wrap_bio, "in", "out", False, "example.invalid", None)
            for _ in range(threads)
        ]
        for future in futures:
            future.result(timeout=30)


def test_control_unpatched_wrap_bio_runs_concurrently():
    """Positive control: without the shim the fake really does run in parallel."""
    cls = _make_unlocked_context_class()
    ctx = cls()
    ctx.barrier = threading.Barrier(THREADS, timeout=30)

    _hammer(ctx)

    assert ctx.recorder.calls == THREADS
    assert ctx.recorder.peak == THREADS


def test_shim_serializes_concurrent_wrap_bio():
    """The point of the whole exercise."""
    cls = _make_unlocked_context_class()
    status = truststore_shim.apply_to(cls, "0.10.4")
    assert status.startswith("applied"), status

    ctx = cls()
    _hammer(ctx)

    assert ctx.recorder.calls == THREADS
    assert ctx.recorder.peak == 1, (
        f"{ctx.recorder.peak} threads were inside wrap_bio at once; "
        "the shim did not serialize them"
    )


def test_shim_applies_to_instances_created_before_patching():
    """Patching the class covers contexts httpx2/httpcore2 already built."""
    cls = _make_unlocked_context_class()
    ctx = cls()  # built first, patched after -- the startup-shim ordering case

    assert truststore_shim.apply_to(cls, "0.10.4").startswith("applied")

    _hammer(ctx)
    assert ctx.recorder.peak == 1


def test_shim_preserves_arguments_and_return_value():
    cls = _make_unlocked_context_class()
    truststore_shim.apply_to(cls, "0.10.4")
    ctx = cls()

    positional = ctx.wrap_bio("in", "out", False, "example.invalid", None)
    keyword = ctx.wrap_bio(
        "in", "out", server_side=True, server_hostname="other.invalid", session=None
    )

    assert positional == ("sslobj", "in", "out", False, "example.invalid", None)
    assert keyword == ("sslobj", "in", "out", True, "other.invalid", None)


def test_shim_is_idempotent():
    cls = _make_unlocked_context_class()
    assert truststore_shim.apply_to(cls, "0.10.4").startswith("applied")
    patched = cls.__dict__["wrap_bio"]

    assert truststore_shim.apply_to(cls, "0.10.4") == "skipped: already applied"
    assert cls.__dict__["wrap_bio"] is patched


def test_shim_refuses_when_wrap_bio_already_takes_the_lock():
    """`_ctx_lock` is not reentrant -- double-locking would deadlock."""
    cls = _make_locked_context_class()
    original = cls.__dict__["wrap_bio"]

    assert truststore_shim.apply_to(cls, "9.9.9") == (
        "skipped: wrap_bio already takes _ctx_lock"
    )
    assert cls.__dict__["wrap_bio"] is original

    # And it still works -- no deadlock, because nothing was wrapped.
    assert cls().wrap_bio("in", "out") == "sslobj"


def test_shim_refuses_when_source_is_unreadable():
    """Unreadable source means we cannot rule out a deadlock, so: no patch."""
    cls = _make_unlocked_context_class()
    original = cls.__dict__["wrap_bio"]

    exec_globals: dict = {}
    exec(  # noqa: S102 - deliberately source-less, mimics a frozen install
        "def wrap_bio(self, *a, **k):\n    return 'sslobj'\n", exec_globals
    )
    cls.wrap_bio = exec_globals["wrap_bio"]

    assert truststore_shim.apply_to(cls, "0.10.4") == (
        "skipped: could not read wrap_bio source, refusing to patch blind"
    )
    assert cls.__dict__["wrap_bio"] is not original
    assert not hasattr(cls.__dict__["wrap_bio"], "_amplifier_wrap_bio_lock_shim")


def test_shim_stands_down_once_upstream_ships_the_fix(monkeypatch):
    monkeypatch.setattr(truststore_shim, "FIRST_FIXED_VERSION", "0.11.0")
    cls = _make_unlocked_context_class()
    original = cls.__dict__["wrap_bio"]

    status = truststore_shim.apply_to(cls, "0.11.0")

    assert status == "skipped: truststore 0.11.0 >= 0.11.0, fixed upstream"
    assert cls.__dict__["wrap_bio"] is original


def test_shim_honours_the_disable_env_var(monkeypatch):
    monkeypatch.setenv(truststore_shim.DISABLE_ENV_VAR, "0")
    assert truststore_shim.apply() == (
        f"skipped: disabled by {truststore_shim.DISABLE_ENV_VAR}"
    )


def test_apply_is_safe_without_truststore(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "truststore":
            raise ImportError("no truststore here")
        return real_import(name, *args, **kwargs)

    # Patch __import__ rather than evicting sys.modules: the `import truststore`
    # statement always routes through __import__, and evicting the real module
    # would hand a fresh, unpatched truststore to every later test.
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert truststore_shim.apply() == "skipped: truststore is not importable"


def test_real_truststore_is_covered_at_cli_import():
    """Importing the CLI package must leave the real truststore patched."""
    truststore = pytest.importorskip("truststore")

    marked = getattr(
        truststore.SSLContext.__dict__.get("wrap_bio"),
        "_amplifier_wrap_bio_lock_shim",
        False,
    )
    already_locked = truststore_shim._wrap_bio_takes_lock(
        truststore.SSLContext.__dict__.get("wrap_bio")
    )

    assert marked or already_locked, (
        "amplifier_app_cli import did not leave truststore.SSLContext.wrap_bio "
        f"serialized (status was: {truststore_shim.SHIM_STATUS!r})"
    )
