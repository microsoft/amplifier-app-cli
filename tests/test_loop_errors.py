"""Contract tests for the planned CLI-owned loop reporter.

The planned module is imported only during test execution. Its absence is an
explicit ExpectedMissingModule failure, not collection failure or a skip.
Actual pre-existing prompt regressions live in test_steering.py and
test_interactive_chat_tty_teardown.py; missing-module failures are not that RED.
"""

from __future__ import annotations

import asyncio
import gc
import importlib
import logging
import re
from unittest.mock import MagicMock

import pytest

_MODULE = "amplifier_app_cli.loop_errors"


@pytest.fixture
def report_loop_errors():
    """Public API assumption: @report_loop_errors wraps an async callable."""
    try:
        module = importlib.import_module(_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _MODULE:
            raise
        pytest.fail(
            f"ExpectedMissingModule: {_MODULE}; reporter is not implemented yet",
            pytrace=False,
        )
    reporter = getattr(module, "report_loop_errors", None)
    assert callable(reporter), "loop_errors must export report_loop_errors"
    return reporter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["return", "failure", "eof", "cancel", "new_owner"]
)
async def test_reporter_restores_only_its_own_handler(report_loop_errors, outcome):
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()
    previous = MagicMock()
    newer = MagicMock()
    value = object()
    failure = (
        EOFError("synthetic EOF")
        if outcome == "eof"
        else RuntimeError("synthetic failure")
    )
    entered = asyncio.Event()

    @report_loop_errors
    async def operation(*, result):
        assert loop.get_exception_handler() is not None
        assert loop.get_exception_handler() is not previous
        entered.set()
        if outcome in {"failure", "eof"}:
            raise failure
        if outcome == "cancel":
            await asyncio.Future()
        if outcome == "new_owner":
            loop.set_exception_handler(newer)
        return result

    loop.set_exception_handler(previous)
    task = asyncio.create_task(operation(result=value))
    try:
        if outcome == "cancel":
            await asyncio.wait_for(entered.wait(), timeout=3)
            task.cancel("synthetic cancellation")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
        elif outcome in {"failure", "eof"}:
            with pytest.raises(type(failure)) as caught:
                await asyncio.wait_for(task, timeout=3)
            assert caught.value is failure
        else:
            assert await asyncio.wait_for(task, timeout=3) is value
        assert loop.get_exception_handler() is (
            newer if outcome == "new_owner" else previous
        )
    finally:
        if not task.done():
            task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
        finally:
            loop.set_exception_handler(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("known_attribute", [False, True])
@pytest.mark.parametrize("strip_exc_info", [False, True])
async def test_reporter_logs_safe_type_and_origin_without_raw_context(
    report_loop_errors, caplog, known_attribute, strip_exc_info
):
    """Safe diagnostics must not depend on a traceback surviving log filters."""
    payload = "SYNTHETIC_PRIVATE_PAYLOAD\x1b[2J\r\n[bold]not markup[/bold]"
    repr_calls = []

    class PrivateContext:
        def __repr__(self):
            repr_calls.append(True)
            raise AssertionError("raw loop context must never be rendered")

    class StripTraceback(logging.Filter):
        def filter(self, record):
            record.exc_info = None
            record.exc_text = None
            return True

    error = (
        AttributeError(payload, name="_async_httpx_client")
        if known_attribute
        else RuntimeError(payload)
    )

    async def aclose():
        raise error

    @report_loop_errors
    async def operation():
        task = asyncio.create_task(aclose(), name=payload)
        await asyncio.gather(task, return_exceptions=True)
        asyncio.get_running_loop().call_exception_handler(
            {
                "message": payload,
                "exception": error,
                "future": task,
                "transport": PrivateContext(),
            }
        )

    caplog.set_level(logging.DEBUG)
    log_filter = StripTraceback()
    if strip_exc_info:
        caplog.handler.addFilter(log_filter)
    try:
        await asyncio.wait_for(operation(), timeout=3)
    finally:
        if strip_exc_info:
            caplog.handler.removeFilter(log_filter)

    records = [record for record in caplog.records if record.name == _MODULE]
    assert records, "A background cleanup error must produce a diagnostic"
    text = "\n".join(record.getMessage() for record in records)
    assert type(error).__name__ in text
    assert "aclose" in text, "Retain the safe coroutine origin"
    if known_attribute:
        assert "_async_httpx_client" in text, "Recognize the fixed safe SDK symptom"
    assert "SYNTHETIC_PRIVATE_PAYLOAD" not in caplog.text
    assert "\x1b" not in text and "\r" not in text
    assert not repr_calls
    assert all(record.exc_info is None for record in records)
    assert all(record.exc_text is None for record in records)


@pytest.mark.asyncio
async def test_reporter_coalesces_a_repeat_flood_with_bounded_workers(
    report_loop_errors, caplog
):
    """A flood must retain a count, not schedule one worker/log per error."""
    error = RuntimeError("synthetic private detail")

    @report_loop_errors
    async def operation():
        loop = asyncio.get_running_loop()
        before = asyncio.all_tasks()
        for _ in range(1000):
            loop.call_exception_handler(
                {"message": "synthetic private detail", "exception": error}
            )
        # At most one reporter worker, before any scheduled worker can run.
        assert len(asyncio.all_tasks() - before) <= 1

    caplog.set_level(logging.DEBUG)
    await asyncio.wait_for(operation(), timeout=3)
    records = [record for record in caplog.records if record.name == _MODULE]
    assert records, "Repeated failures must remain visible"
    assert len(records) < 1000, "Repeated errors must be coalesced"
    text = "\n".join(record.getMessage() for record in records)
    assert re.search(r"\b(?:999|1000)\b", text), (
        "Report either the repeated count or the total count when the scope drains"
    )
    assert "synthetic private detail" not in caplog.text


@pytest.mark.asyncio
async def test_reporter_observes_worker_failure_when_log_sink_raises(
    report_loop_errors, monkeypatch, caplog
):
    """A broken sink must not leave an unobserved failed reporting task."""
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()
    observer = MagicMock()
    workers = set()
    sink_calls = []

    class BrokenSink(logging.Handler):
        def emit(self, record):
            sink_calls.append(True)
            raise RuntimeError("synthetic reporting sink failure")

    logger = logging.getLogger(_MODULE)
    monkeypatch.setattr(logger, "handlers", [BrokenSink()])
    monkeypatch.setattr(logger, "propagate", False)
    caplog.set_level(logging.DEBUG, logger=_MODULE)

    @report_loop_errors
    async def operation():
        before = asyncio.all_tasks()
        handler = loop.get_exception_handler()
        assert handler is not None and handler is not observer
        # Call the installed callback directly so asyncio cannot swallow a
        # synchronous callback failure and hide it behind its default handler.
        handler(loop, {"exception": RuntimeError("synthetic private detail")})
        workers.update(asyncio.all_tasks() - before)
        assert len(workers) <= 1

    loop.set_exception_handler(observer)
    try:
        await asyncio.wait_for(operation(), timeout=3)
        assert sink_calls, "Exercise the failing reporting sink"
        assert all(task.done() for task in workers), "Drain reporter work on exit"
        assert loop.get_exception_handler() is observer
        # Drop our last references, without retrieving exceptions on the
        # reporter's behalf. CPython then reports any unobserved task failure.
        workers.clear()
        gc.collect()
        await asyncio.sleep(0)  # Dispatch already-ready task finalizers.
        for call in observer.call_args_list:
            context = call.args[1]
            assert context.get("message") != "Task exception was never retrieved"
    finally:
        loop.set_exception_handler(original)