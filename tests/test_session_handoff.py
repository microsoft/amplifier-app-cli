import asyncio
import sys
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from amplifier_foundation.session import (
    SessionBusyError,
    SharedSessionStore,
    request_release,
)
from rich.console import Console

from amplifier_app_cli.session_handoff import CLIHandoff, acquire_root
from amplifier_app_cli.session_runner import SessionConfig
from amplifier_app_cli.shared_root_state import (
    SharedRootSession,
    SharedRootSessionBusyError,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        sys.platform == "win32", reason="Local shared ownership requires POSIX"
    ),
]


def fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "locks"))
    root = SharedRootSession.acquire("test-session")
    initialized = SimpleNamespace(
        root_state=root,
        cleanup=AsyncMock(),
        session=SimpleNamespace(
            coordinator=SimpleNamespace(
                cancellation=SimpleNamespace(request_graceful=Mock())
            )
        ),
    )
    output = StringIO()
    return root, initialized, CLIHandoff(initialized, Console(file=output)), output


async def test_idle_prompt_wakes_and_release_waits_for_final_save(
    tmp_path, monkeypatch
):
    root, initialized, control, output = fixture(tmp_path, monkeypatch)
    await control.start()
    store = SharedSessionStore(tmp_path, "test-session")
    prompt = asyncio.create_task(control.prompt(lambda: asyncio.sleep(30)))
    request = asyncio.create_task(
        request_release(
            store,
            expected_owner=root.held.owner,
            request_id="takeover",
            requester_app="Amplifier Unified",
        )
    )
    with pytest.raises(EOFError):
        await asyncio.wait_for(prompt, 2)
    with pytest.raises(SessionBusyError):
        store.acquire(app="too-soon")
    stages = []

    async def save():
        root.held.check()
        stages.append("save")

    async def cleanup(**kwargs):
        assert kwargs == {"release_ownership": False}
        root.held.check()
        stages.append("cleanup")

    initialized.cleanup.side_effect = cleanup
    await control.finish(save)
    assert (await request).status == "released"
    assert stages == ["save", "cleanup"]
    assert "Amplifier Unified" in output.getvalue()
    assert "Execution ownership released" in output.getvalue()
    store.acquire(app="successor").release()


async def test_save_failure_reports_failure_and_retains_ownership(
    tmp_path, monkeypatch
):
    root, initialized, control, output = fixture(tmp_path, monkeypatch)
    await control.start()
    store = SharedSessionStore(tmp_path, "test-session")
    request = asyncio.create_task(
        request_release(
            store,
            expected_owner=root.held.owner,
            request_id="takeover",
            requester_app="Unified",
        )
    )
    await asyncio.wait_for(control.requested.wait(), 2)
    with pytest.raises(OSError):
        await control.finish(AsyncMock(side_effect=OSError("full disk")))
    assert (await request).status == "cannot_release"
    assert root.held.active
    assert "released" not in output.getvalue()
    initialized.cleanup.assert_not_called()
    await control.registration.close()
    root.release()


async def test_single_mode_does_not_request_without_explicit_intent(
    tmp_path, monkeypatch
):
    _root, _initialized, control, _output = fixture(tmp_path, monkeypatch)
    await control.start()
    config = SessionConfig(
        {}, [], False, session_id="test-session", invocation_mode="single"
    )
    with pytest.raises(SharedRootSessionBusyError):
        await acquire_root(config, control.console)
    assert not control.requested.is_set()
    await control.finish()


async def test_explicit_cli_takeover_acquires_before_returning(tmp_path, monkeypatch):
    root, _initialized, control, _output = fixture(tmp_path, monkeypatch)
    await control.start()
    config = SessionConfig(
        {},
        [],
        False,
        session_id="test-session",
        invocation_mode="single",
        takeover=True,
    )
    acquisition = asyncio.create_task(acquire_root(config, control.console))
    await asyncio.wait_for(control.requested.wait(), 2)
    await control.finish(AsyncMock())
    successor = await asyncio.wait_for(acquisition, 2)
    assert successor.held.active
    assert successor.held.owner["acquisition_id"] != root.held.owner["acquisition_id"]
    successor.release()


@pytest.mark.parametrize("accept", [False, True])
async def test_interactive_busy_session_prompts_before_requesting(
    tmp_path, monkeypatch, accept
):
    root, _initialized, control, output = fixture(tmp_path, monkeypatch)
    await control.start()
    confirm = Mock(return_value=accept)
    monkeypatch.setattr("amplifier_app_cli.session_handoff.Confirm.ask", confirm)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    config = SessionConfig(
        {}, [], False, session_id="test-session", invocation_mode="chat"
    )
    acquisition = asyncio.create_task(acquire_root(config, control.console))
    if accept:
        await asyncio.wait_for(control.requested.wait(), 2)
        await control.finish(AsyncMock())
        successor = await asyncio.wait_for(acquisition, 2)
        successor.release()
    else:
        with pytest.raises(SharedRootSessionBusyError) as error:
            await asyncio.wait_for(acquisition, 2)
        assert error.value.displayed
        assert not control.requested.is_set()
        assert root.held.active
        await control.finish()
    confirm.assert_called_once()
    assert confirm.call_args.kwargs == {"console": control.console, "default": False}
    assert "Amplifier CLI" in output.getvalue()


async def test_interactive_unsupported_takeover_keeps_owner_and_reports_failure(
    tmp_path, monkeypatch
):
    root, _initialized, control, output = fixture(tmp_path, monkeypatch)
    # A real lock without a registered handoff handler represents an older app.
    monkeypatch.setattr(
        "amplifier_app_cli.session_handoff.Confirm.ask", lambda *a, **k: True
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    config = SessionConfig(
        {}, [], False, session_id="test-session", invocation_mode="chat"
    )
    try:
        with pytest.raises(SharedRootSessionBusyError) as error:
            await acquire_root(config, control.console)
        assert error.value.displayed  # The startup boundary must not print it again.
        assert root.held.active
        assert "does not support takeover" in output.getvalue()
        assert "Session acquired" not in output.getvalue()
    finally:
        root.release()


@pytest.mark.parametrize("release_wins", [False, True])
async def test_prompt_keyboard_interrupt_is_caught_in_parent_task(tmp_path, release_wins):
    """Run in a child process: the pre-fix Task re-raise would abort pytest."""
    source = """
import asyncio
import sys
from types import SimpleNamespace
from amplifier_app_cli.session_handoff import CLIHandoff

async def main():
    release_wins = sys.argv[1] == 'True'
    control = CLIHandoff(SimpleNamespace(), SimpleNamespace())
    before = asyncio.all_tasks()
    async def factory():
        if release_wins:
            control.requested.set()
        raise KeyboardInterrupt
    try:
        await control.prompt(factory)
    except KeyboardInterrupt:
        assert not release_wins
        print('PARENT_CAUGHT')
    except EOFError:
        assert release_wins
        print('RELEASE_WON')
    else:
        raise AssertionError('Prompt did not propagate its outcome')
    assert not (asyncio.all_tasks() - before), 'Handoff left pending children'

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-c",
        source,
        str(release_wins),
        cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3)
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=3)

    assert process.returncode == 0, stderr.decode(errors="replace")
    assert stdout.decode().strip() == ("RELEASE_WON" if release_wins else "PARENT_CAUGHT")
    assert not stderr, stderr.decode(errors="replace")


@pytest.mark.parametrize("outcome", ["value", "eof", "cancel"])
async def test_prompt_preserves_value_eof_and_parent_cancellation(outcome):
    control = CLIHandoff(SimpleNamespace(), Mock())
    entered = asyncio.Event()
    prompt_children = []
    value = object()

    async def factory():
        prompt_children.append(asyncio.current_task())
        entered.set()
        if outcome == "cancel":
            await asyncio.Future()
        if outcome == "eof":
            raise EOFError
        return value

    before = asyncio.all_tasks()
    task = asyncio.create_task(control.prompt(factory))
    if outcome == "cancel":
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert prompt_children[0].cancelled()
    elif outcome == "eof":
        with pytest.raises(EOFError):
            await asyncio.wait_for(task, timeout=1)
    else:
        assert await asyncio.wait_for(task, timeout=1) is value
    assert not (asyncio.all_tasks() - before), "Handoff left pending children"


@pytest.mark.parametrize("timing", ["ready_callback", "child_cleanup"])
@pytest.mark.parametrize("outcome", ["interrupt", "value", "error"])
async def test_late_release_wins_after_prompt_completion(timing, outcome):
    """Release takes priority after the wait snapshot and during child cleanup."""
    control = CLIHandoff(SimpleNamespace(), Mock())
    release_points = []
    children = []

    class ReleaseDuringCleanup(asyncio.Event):
        async def wait(self):
            children.append(asyncio.current_task())
            try:
                return await super().wait()
            except asyncio.CancelledError:
                release_points.append("child_cleanup")
                self.set()
                await asyncio.sleep(0)  # Keep the parent suspended in its gather.
                raise

    if timing == "child_cleanup":
        control.requested = ReleaseDuringCleanup()

    def request_release(completed):
        assert completed.done()
        release_points.append("ready_callback")
        control.requested.set()

    async def factory():
        child = asyncio.current_task()
        children.append(child)
        if timing == "ready_callback":
            # asyncio.wait has registered its completion callback first. That
            # wakes the parent; this callback sets the event before it resumes,
            # but the release-wait child is still in the ready queue.
            child.add_done_callback(request_release)
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "error":
            raise RuntimeError("synthetic prompt failure")
        return "must not be delivered after release"

    before = asyncio.all_tasks()
    try:
        await control.prompt(factory)
    except EOFError:
        assert control.requested.is_set()
    except KeyboardInterrupt:
        pytest.fail("Late release must prevent parent interrupt/exit confirmation")
    except RuntimeError:
        pytest.fail("Late release must take priority over the prompt error")
    else:
        pytest.fail("Late release must take priority over the prompt value")
    assert release_points == [timing]
    assert all(child.done() for child in children)
    assert not (asyncio.all_tasks() - before), "Handoff left pending children"
