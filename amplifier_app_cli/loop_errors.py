"""Non-modal, privacy-bounded loop diagnostics owned by interactive CLI sessions.

Only safe snapshots cross into the output worker. Never format an exception,
task, loop context, traceback source, locals, notes, or exception chain.
The drain budget bounds this scope, not a blocked executor thread or subsequent
asyncio.run() executor shutdown.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
from collections import deque
from contextvars import ContextVar
from functools import partial, wraps

logger = logging.getLogger(__name__)
_REPORTING = ContextVar("cli_loop_error_reporting", default=False)
_WORKERS: set[asyncio.Future] = set()  # Retain cancelled workers until observed.
_INTERVAL = 0.5
_DRAIN_TIMEOUT = 0.5
_MAX_COUNT = (1 << 63) - 1


def _token(value: str, limit: int = 64) -> str:
    if type(value) is not str:
        return "unknown"
    return (
        "".join(
            c if c.isascii() and (c.isalnum() or c in "_.-<>") else "_"
            for c in value[:limit]
        )
        or "unknown"
    )


def _snapshot(context: dict) -> tuple[object, str]:
    """Use built-in descriptors, never exception overrides or context repr."""
    error = dict.get(context, "exception") if type(context) is dict else None
    if not isinstance(error, BaseException):
        return object(), "Background loop error; message withheld."
    name = _token(type.__dict__["__name__"].__get__(type(error)))
    message = f"Background loop error: {name}; message withheld."
    if type(error) is AttributeError:
        field = AttributeError.name.__get__(error)
        if type(field) is str and field == "_async_httpx_client":
            message += " SDK cleanup is missing _async_httpx_client."
    tb = BaseException.__traceback__.__get__(error)
    for _ in range(8):
        if tb is None:
            break
        code = tb.tb_frame.f_code
        basename = code.co_filename[-128:].replace("\\", "/").rsplit("/", 1)[-1]
        message += f"\n  at {_token(basename)}:{tb.tb_lineno} in {_token(code.co_name)}"
        tb = tb.tb_next
    if tb is not None:
        message += "\n  additional frames withheld"
    message = message[:1800]  # ASCII snapshot; leave room for a count below.

    # Private equality only. Do not retain raw args or publish their digest.
    # Unsupported/large args deliberately do not coalesce.
    args = BaseException.args.__get__(error)
    if len(args) <= 4 and all(type(a) is str and len(a) <= 256 for a in args):
        digest = hashlib.blake2b(digest_size=16)
        for arg in args:
            data = arg.encode("utf-8", errors="surrogatepass")
            digest.update(len(data).to_bytes(4, "big"))
            digest.update(data)
        return (id(type(error)), message, digest.digest()), message
    return object(), message


def _observe(task: asyncio.Future) -> None:
    _WORKERS.discard(task)
    if not task.cancelled():
        task.exception()  # Observe outcomes without formatting or re-reporting.


class _Reporter:
    def __init__(self, loop):
        self.loop = loop
        self.pending = deque()
        self.overflow = 0
        self.worker = None
        self.next_output = 0.0
        self.closed = False
        self.draining = False
        self.busy = False
        self.fallback_sent = False
        self.last_key = None
        self.handler = self.handle  # Stable identity for ownership restoration.

    def handle(self, loop, context):
        """Snapshot and enqueue only: no terminal/logging I/O in this callback."""
        if self.closed or self.busy or _REPORTING.get():
            return
        self.busy = True
        try:
            try:
                key, message = _snapshot(context)
            except Exception:  # noqa: BLE001 - never expose raw loop context.
                # Fail closed rather than letting asyncio format raw context.
                key, message = object(), "Background loop error; metadata withheld."
            if self.pending and self.last_key == key and self.pending[-1][0] == key:
                self.pending[-1][2] = min(_MAX_COUNT, self.pending[-1][2] + 1)
            elif len(self.pending) < 16:
                self.pending.append([key, message, 1])
            else:
                self.overflow = min(_MAX_COUNT, self.overflow + 1)
            self.last_key = key
            if self.worker is None or self.worker.done():
                coroutine = self.run()
                try:
                    self.worker = self.loop.create_task(coroutine)
                except Exception:  # noqa: BLE001 - task factories are external.
                    coroutine.close()  # No unawaited-coroutine warning or raw context.
                    self.worker = None  # Keep snapshots for a later callback/close.
                    return
                _WORKERS.add(self.worker)
                self.worker.add_done_callback(_observe)
        finally:
            self.busy = False

    def fallback(self):
        """One fixed notice, called only from an output thread."""
        if self.fallback_sent:
            return
        self.fallback_sent = True
        try:
            if sys.__stderr__ is not None:
                sys.__stderr__.write(
                    "Background error reporting failed; diagnostic details withheld.\n"
                )
                sys.__stderr__.flush()
        except Exception:  # noqa: BLE001 - the last sink cannot report recursively.
            return

    def emit(self, batch, overflow):
        token = _REPORTING.set(True)
        try:
            for _, message, count in batch:
                count_text = f">={count}" if count == _MAX_COUNT else f"{count}"
                logger.error("%s", f"{message}\n  count={count_text}", exc_info=None)
            if overflow:
                count_text = (
                    f">={overflow}" if overflow == _MAX_COUNT else f"{overflow}"
                )
                logger.error(
                    "Background loop error queue overflow: %s additional errors; "
                    "details withheld.",
                    count_text,
                    exc_info=None,
                )
        except Exception:  # noqa: BLE001 - sink errors must not escape reporting.
            self.fallback()
        finally:
            _REPORTING.reset(token)

    async def run(self):
        try:
            # Public export; keep import failures inside the safe fallback.
            from prompt_toolkit.application import run_in_terminal

            while self.pending or self.overflow:
                delay = self.next_output - self.loop.time()
                if delay > 0 and not self.draining:
                    await asyncio.sleep(delay)
                batch = list(self.pending)
                self.pending.clear()
                overflow, self.overflow = self.overflow, 0
                await run_in_terminal(
                    partial(self.emit, batch, overflow), in_executor=True
                )
                self.next_output = self.loop.time() + _INTERVAL
        except Exception:  # noqa: BLE001 - terminal/import errors stay private.
            # Terminal rendering failed before reaching the log sink. Do not
            # call the loop handler recursively or emit a raw worker traceback.
            await asyncio.to_thread(self.fallback)

    async def close(self):
        deadline = self.loop.time() + _DRAIN_TIMEOUT
        self.draining = True  # Do not spend the final drain budget on rate delay.
        try:
            while (
                self.pending
                or self.overflow
                or (self.worker is not None and not self.worker.done())
            ):
                if self.loop.time() >= deadline:
                    break
                if self.worker is None or self.worker.done():
                    # Scheduling/output failed with retained snapshots. Report
                    # one fixed notice, within the same absolute drain budget.
                    try:
                        self.worker = self.loop.run_in_executor(None, self.fallback)
                    except Exception:  # noqa: BLE001 - no safe sink remains.
                        return
                    _WORKERS.add(self.worker)
                    self.worker.add_done_callback(_observe)
                    self.pending.clear()
                    self.overflow = 0
                worker = self.worker
                # Unlike wait_for, wait does not await cancellation completion.
                await asyncio.wait({worker}, timeout=max(0, deadline - self.loop.time()))
                # Recheck the current worker/queue: ready callbacks can replace
                # the worker we just waited for before this coroutine resumes.
        finally:
            self.closed = True  # Accept/count loop errors until the drain ends.
            if self.worker is not None and not self.worker.done():
                self.worker.cancel()
            self.pending.clear()


def report_loop_errors(function):
    """Own loop reporting for the entire awaited lifetime, including cleanup."""

    @wraps(function)
    async def wrapped(*args, **kwargs):
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        reporter = _Reporter(loop)
        loop.set_exception_handler(reporter.handler)
        try:
            return await function(*args, **kwargs)
        finally:
            try:
                await reporter.close()
            finally:
                if loop.get_exception_handler() is reporter.handler:
                    loop.set_exception_handler(previous)

    return wrapped
