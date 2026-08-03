"""Shared driver for the integration tests that fork a real pty child process.

Why this exists
~~~~~~~~~~~~~~~
Both ``test_terminal_echo_integration.py`` and
``test_ctrlc_functional_integration.py`` fork a child onto a real pty and then
inspect what the child did to the terminal. Doing that portably requires three
things that are easy to get wrong -- and that were originally gotten wrong in
both files, in a way that only shows up on macOS/BSD:

1. **The pty master must be drained continuously, from the moment of the fork.**
   A pty's output queue is small (~1 KiB on macOS). Once it fills, the child
   blocks in ``write()``. On macOS the damage is far worse than a stalled
   write: if the child is *exiting* when the queue is full, it wedges in the
   kernel's exit path (``ps`` state ``?Es`` -- "exiting", controlling terminal
   already revoked) and **cannot be killed, not even with SIGKILL**. Measured
   on macOS 26.6 / Darwin 25.6.0:

       child wrote     10B, parent_reads=False -> reaped@0.64s
       child wrote   1000B, parent_reads=False -> reaped@0.63s
       child wrote  10000B, parent_reads=False -> NEVER REAPED
                                                  (SIGKILL did NOT make it reapable)

   The same probe on Linux reaps the child every time. Linux tolerates an
   un-drained pty at exit; macOS does not.

   Two distinct symptoms fall out of that single defect, which is why both
   files needed the same fix:

   * The parent's ``os.write(master_fd, ...)`` raises ``OSError(EIO)``, because
     a wedged-in-exit child has already had its pty slave revoked. (On Linux
     the identical write silently succeeds into a pty nobody is reading, so
     the race is invisible.)
   * A blocking ``os.waitpid(pid, 0)`` after the SIGKILL never returns, because
     the child can never be reaped -- an *unbounded* hang, not a slow test.

2. **Every wait must be bounded.** A test may fail; a test may never hang. So
   there is no blocking ``waitpid`` anywhere in this module: everything is a
   ``WNOHANG`` poll against a deadline.

3. **Input must be synchronised with the child, not with the clock.** Fixed
   parent-side sleeps race the child's own timeline; the margin that happens to
   hold on one platform does not hold on another. Callers use
   ``wait_for_marker()`` to block until the child announces it is ready, and
   ``PtyChild.send()`` treats an undeliverable keystroke as a hard failure
   rather than silently testing nothing.

Everything here is POSIX-only (``pty.fork``), matching the tests that use it.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import threading
import time
from collections.abc import Callable

__all__ = ["PtyChild", "fork_pty_child", "wait_for_marker"]

# How long the drain thread waits on the master fd before re-checking whether
# it has been asked to stop.
_DRAIN_POLL_S = 0.05


class PtyChild:
    """A forked child attached to a real pty, with its master fd drained.

    A background thread reads the pty master for the whole lifetime of the
    child, so the child can never block on a full output queue (see the module
    docstring for why that is fatal on macOS). Reads happen on a ``dup()`` of
    the master fd, so the caller's own ``master_fd`` keeps ordinary blocking
    semantics for writes and for ``termios`` inspection.
    """

    def __init__(self, pid: int, master_fd: int) -> None:
        self.pid = pid
        self.master_fd = master_fd
        self.status: int | None = None
        self.exited = False

        self._sink = bytearray()
        self._stop = threading.Event()
        self._closed = False
        self._drain_fd = os.dup(master_fd)
        self._thread = threading.Thread(
            target=self._drain, name=f"pty-drain-{pid}", daemon=True
        )
        self._thread.start()

    # -- output ------------------------------------------------------------

    @property
    def output(self) -> bytes:
        """Everything the child has written to the pty so far."""
        return bytes(self._sink)

    def _drain(self) -> None:
        fd = self._drain_fd
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([fd], [], [], _DRAIN_POLL_S)
            except OSError:
                return  # fd closed underneath us during shutdown
            if not readable:
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                # Linux raises EIO on a master read once the slave side is
                # gone; macOS returns EOF for the same condition (below).
                # Either way nothing more will arrive -- idle until closed so
                # that close() stays the sole owner of the fd's lifetime.
                self._stop.wait(_DRAIN_POLL_S)
                continue
            if not chunk:
                self._stop.wait(_DRAIN_POLL_S)
                continue
            self._sink.extend(chunk)

    # -- input -------------------------------------------------------------

    def send(self, data: str | bytes) -> None:
        """Write ``data`` to the pty as if typed. Undeliverable input fails loudly.

        A failed write means the child is no longer holding the pty slave open
        -- it exited, or wedged in exit, before the input under test could
        reach it. Swallowing that would leave a test that passes while
        exercising nothing, so it is raised as an assertion failure instead.
        """
        payload = data.encode() if isinstance(data, str) else data
        try:
            os.write(self.master_fd, payload)
        except OSError as exc:
            raise AssertionError(self._undeliverable(payload, exc)) from exc

    def signal(self, sig: int) -> None:
        """Send a real OS signal to the child. A missing child fails loudly."""
        try:
            os.kill(self.pid, sig)
        except ProcessLookupError as exc:
            raise AssertionError(self._undeliverable(f"signal {sig}", exc)) from exc

    def _undeliverable(self, what: object, exc: BaseException) -> str:
        return (
            f"could not deliver {what!r} to the pty child (pid={self.pid}): {exc!r}. "
            f"The child is no longer holding the pty slave open, so it exited (or "
            f"wedged while exiting) before the input under test could reach it -- "
            f"this scenario did not actually exercise what it claims to. "
            f"child_reaped={self.poll()} child_output={self.output!r}"
        )

    # -- lifecycle ---------------------------------------------------------

    def poll(self) -> bool:
        """True once the child has been reaped. Never blocks."""
        if self.exited:
            return True
        try:
            wpid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.exited = True
            return True
        if wpid == self.pid:
            self.exited = True
            self.status = status
        return self.exited

    def wait(self, timeout: float) -> bool:
        """Poll for exit until ``timeout``. Returns whether the child exited."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.poll():
                return True
            time.sleep(0.02)
        return self.poll()

    def kill(self, grace: float = 2.0) -> bool:
        """SIGKILL the child and poll (never block) for it to become reapable.

        Returns False if it never does -- which on macOS is a real outcome, not
        a theoretical one, for a child wedged in the kernel's exit path.
        """
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return self.wait(grace)

    def close(self) -> None:
        """Stop draining and release both fds. Safe to call more than once.

        Joins the drain thread so that no extra thread is alive across a
        subsequent ``pty.fork()`` in the same process.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=1.0)
        for fd in (self._drain_fd, self.master_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def fork_pty_child(child_body: Callable[[], None]) -> PtyChild:
    """Fork a child onto a fresh pty and start draining its master fd.

    ``child_body`` runs in the child, whose fds 0/1/2 are the pty slave. The
    child always terminates via ``os._exit`` so it can never re-enter the test
    runner's own teardown.
    """
    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            child_body()
        except BaseException:  # noqa: BLE001 - a fork child must never escape into the test runner
            os._exit(1)
        os._exit(0)
    return PtyChild(pid, master_fd)


def wait_for_marker(path: str, child: PtyChild, timeout: float, what: str) -> None:
    """Block until the child creates ``path``, or fail with a useful message.

    This is the readiness handshake that replaces fixed parent-side sleeps: it
    ties "the parent may now send input" to an event in the child rather than
    to a wall-clock margin that differs per platform.
    """
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() > deadline:
            reaped = child.poll()
            child.kill()
            raise AssertionError(
                f"child never signaled {what} within {timeout}s "
                f"(pid={child.pid}, already_reaped={reaped}, "
                f"output={child.output!r})"
            )
        time.sleep(0.01)
