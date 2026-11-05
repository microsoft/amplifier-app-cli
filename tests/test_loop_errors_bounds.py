"""Additional privacy, overload, and shutdown bounds not in the frozen RED set."""

import asyncio
import logging

import pytest

from amplifier_app_cli.loop_errors import report_loop_errors

_LOGGER = "amplifier_app_cli.loop_errors"


@pytest.mark.asyncio
async def test_exception_metadata_cannot_invoke_hostile_overrides(caplog):
    calls = []

    def forbidden(*_args):
        calls.append(True)
        raise AssertionError("Do not invoke exception-controlled accessors")

    class HostileMeta(type):
        __name__ = property(forbidden)

    class HostileError(RuntimeError, metaclass=HostileMeta):
        __str__ = forbidden
        __repr__ = forbidden
        __getattribute__ = forbidden

    error = HostileError("SYNTHETIC_PRIVATE_ARGUMENT")

    @report_loop_errors
    async def operation():
        asyncio.get_running_loop().call_exception_handler({"exception": error})

    caplog.set_level(logging.DEBUG)
    await operation()
    assert not calls
    assert "HostileError" in caplog.text
    assert "SYNTHETIC_PRIVATE_ARGUMENT" not in caplog.text


@pytest.mark.asyncio
async def test_distinct_private_arguments_fill_bounded_queue_and_count_overflow(caplog):
    @report_loop_errors
    async def operation():
        loop = asyncio.get_running_loop()
        for i in range(100):
            loop.call_exception_handler(
                {"exception": RuntimeError(f"SYNTHETIC_PRIVATE_ARGUMENT_{i}")}
            )

    caplog.set_level(logging.DEBUG)
    await operation()
    records = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert len(records) == 17  # 16 snapshots plus one counted overflow notice.
    assert all("count=1" in message for message in records[:16])
    assert "84 additional errors" in records[-1]
    assert "SYNTHETIC_PRIVATE_ARGUMENT" not in caplog.text
    assert all(len(message.encode("utf-8")) <= 2048 for message in records)


@pytest.mark.asyncio
async def test_batches_use_terminal_executor_and_are_rate_limited(monkeypatch, caplog):
    emitted = asyncio.Queue()
    times = []

    async def terminal(function, *, in_executor):
        assert in_executor is True
        times.append(asyncio.get_running_loop().time())
        await asyncio.to_thread(function)
        emitted.put_nowait(None)

    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", terminal)

    @report_loop_errors
    async def operation():
        loop = asyncio.get_running_loop()
        for _ in range(3):
            loop.call_exception_handler({"exception": RuntimeError("synthetic")})
            await asyncio.wait_for(emitted.get(), timeout=2)

    caplog.set_level(logging.DEBUG)
    await operation()
    assert len(times) == 3
    assert all(later - earlier >= 0.5 for earlier, later in zip(times, times[1:]))


@pytest.mark.asyncio
async def test_stalled_reporter_drain_restores_owner_without_awaiting_cancel(
    monkeypatch,
):
    """A cancellation-resistant renderer must not hold the CLI's finally open."""
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()
    workers = []

    async def terminal(function, *, in_executor):
        workers.append(asyncio.current_task())
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            finished.set()

    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", terminal)

    @report_loop_errors
    async def operation():
        loop.call_exception_handler({"exception": RuntimeError("synthetic")})
        await started.wait()

    try:
        begin = loop.time()
        await asyncio.wait_for(operation(), timeout=1.5)
        assert loop.time() - begin < 1.0
        assert loop.get_exception_handler() is original
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        await asyncio.wait_for(asyncio.gather(*workers), timeout=2)


@pytest.mark.asyncio
async def test_close_drains_replacement_worker_from_ready_callback(monkeypatch, caplog):
    """A ready callback can replace the completed worker before close resumes."""
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()
    workers = []
    late_callback_ran = []

    def report_late(_completed):
        late_callback_ran.append(True)
        loop.call_exception_handler({"exception": ValueError("late private detail")})

    async def terminal(function, *, in_executor):
        assert in_executor is True
        worker = asyncio.current_task()
        workers.append(worker)
        if len(workers) == 1:
            # close's asyncio.wait completion callback is already registered.
            # This callback runs after it, but before the waiting parent resumes.
            worker.add_done_callback(report_late)
        function()  # Test-only sink: no terminal or executor I/O.

    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", terminal)

    @report_loop_errors
    async def operation():
        loop.call_exception_handler({"exception": RuntimeError("first private detail")})
        # No yield: the decorator enters close before the worker starts.

    caplog.set_level(logging.DEBUG)
    begin = loop.time()
    try:
        await asyncio.wait_for(operation(), timeout=1.5)
        assert late_callback_ran == [True], "Exercise the accepted late error"
        messages = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
        assert any("RuntimeError" in message for message in messages)
        assert any("ValueError" in message for message in messages), (
            "close cancelled a replacement worker before draining its accepted error"
        )
        assert len(workers) == 2
        assert all(worker.done() and not worker.cancelled() for worker in workers)
        assert loop.time() - begin < 1.0, "Do not restart the 500 ms drain budget"
        assert loop.get_exception_handler() is original
        assert "private detail" not in caplog.text
    finally:
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), timeout=2)