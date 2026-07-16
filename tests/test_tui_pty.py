"""Real-terminal acceptance tests for the full-screen transcript and input."""

from __future__ import annotations

import errno
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import termios
import textwrap
import time

import pytest


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="PTY support required")
def test_interactive_chat_production_path_acceptance(tmp_path) -> None:
    """Drive the real interactive chat loop through a terminal boundary."""
    result_path = tmp_path / "interactive-chat-result.json"
    script = textwrap.dedent(
        """
        import asyncio
        import importlib
        import inspect
        import json
        from pathlib import Path
        import amplifier_app_cli.incremental_save as incremental_save
        from amplifier_app_cli.ui.approval import CLIApprovalSystem
        from amplifier_app_cli.ui.git_yield import GitDiffSnapshot
        from prompt_toolkit.application.current import set_app
        from prompt_toolkit.layout.mouse_handlers import MouseHandlers
        from prompt_toolkit.layout.screen import Screen, WritePosition
        main_module = importlib.import_module("amplifier_app_cli.main")
        class FakeHooks:
            def __init__(self):
                self._handlers = {}

            def register(self, event, handler, *, priority=0, name=None):
                record = (priority, name, handler)
                self._handlers.setdefault(event, []).append(record)

                def unregister():
                    handlers = self._handlers.get(event, [])
                    if record in handlers:
                        handlers.remove(record)

                return unregister
            def unregister(self, name):
                for handlers in self._handlers.values():
                    handlers[:] = [item for item in handlers if item[1] != name]

            async def emit(self, event, data):
                handlers = sorted(
                    tuple(self._handlers.get(event, ())),
                    key=lambda item: item[0],
                    reverse=True,
                )
                for _, _, handler in handlers:
                    result = handler(event, data)
                    if inspect.isawaitable(result):
                        await result
        class FakeCancellation:
            def __init__(self):
                self.reset()

            def reset(self):
                self.is_cancelled = False
                self.is_immediate = False
                self.running_tool_names = []

            def request_graceful(self):
                self.is_cancelled = True

            def request_immediate(self):
                self.is_cancelled = True
                self.is_immediate = True
        class FakeContext:
            async def get_messages(self):
                return []
        class FakeProvider:
            def __init__(self, hooks, approval_system, events, geometry):
                self.hooks = hooks
                self.approval_system = approval_system
                self.events = events
                self.geometry = geometry

            async def execute(self, prompt, turn):
                self.events.append(f"start:{turn}")
                if turn == 0:
                    pending = asyncio.create_task(
                        self.approval_system.request_approval(
                            "Allow load_skill?",
                            ["Allow once", "Deny"],
                            30,
                            "deny",
                        )
                    )
                    choice = await pending
                    self.events.append(f"approval:{choice}")
                    for child_id, agent, instruction in (
                        ("child-expert", "amplifier-expert", "Review architecture"),
                        ("child-architect", "zen-architect", "Design mission flow"),
                        ("child-critic", "old-engineer", "Challenge risks"),
                    ):
                        await self.hooks.emit(
                            "delegate:agent_spawned",
                            {
                                "sub_session_id": child_id,
                                "parent_session_id": "pty-production",
                                "agent": agent,
                                "instruction": instruction,
                            },
                        )
                    await self.hooks.emit(
                        "tool:pre",
                        {
                            "session_id": "child-expert",
                            "tool_call_id": "child-read",
                            "tool_name": "read",
                            "tool_input": {"description": "Inspecting flagship spec"},
                        },
                    )
                common = {
                    "session_id": "pty-production",
                    "request_id": f"request-{turn}",
                    "block_index": 0,
                    "block_type": "text",
                }
                await self.hooks.emit("llm:stream_block_start", common)
                for chunk in ("provider ", "streaming ", f"turn {turn + 1}"):
                    await self.hooks.emit(
                        "llm:stream_block_delta",
                        {**common, "text": chunk},
                    )
                    await asyncio.sleep(0.04)
                if turn == 0:
                    await asyncio.sleep(1.4)
                    await self.hooks.emit(
                        "tool:post",
                        {
                            "session_id": "child-expert",
                            "tool_call_id": "child-read",
                            "tool_name": "read",
                            "result": {"success": True},
                        },
                    )
                    for child_id, agent in (
                        ("child-expert", "amplifier-expert"),
                        ("child-architect", "zen-architect"),
                        ("child-critic", "old-engineer"),
                    ):
                        await self.hooks.emit(
                            "delegate:agent_completed",
                            {
                                "sub_session_id": child_id,
                                "parent_session_id": "pty-production",
                                "agent": agent,
                                "success": True,
                            },
                        )
                await self.hooks.emit("llm:stream_block_end", common)
                self.events.append(f"end:{turn}")
                return f"FINAL_TYPED_OUTPUT_{turn + 1}"

            async def capture_approval_geometry(self):
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    handler = self.approval_system._handler
                    app = getattr(handler, "__self__", None)
                    if app is None or not app._approval_visible():
                        continue
                    screen = Screen()
                    with set_app(app.application):
                        app.application.layout.container.write_to_screen(
                            screen,
                            MouseHandlers(),
                            WritePosition(0, 0, 200, 36),
                            parent_style="",
                            erase_bg=False,
                            z_index=None,
                        )
                    rows = [
                        "".join(
                            screen.data_buffer[y][x].char
                            for x in range(200)
                        ).rstrip()
                        for y in range(36)
                    ]
                    approval_rows = [
                        index
                        for index, row in enumerate(rows)
                        if "Allow load_skill?" in row
                    ]
                    if not approval_rows:
                        continue
                    self.geometry.update(
                        {
                            "height": 36,
                            "approval_row": approval_rows[-1],
                            "prompt_row": next(
                                (i for i, row in enumerate(rows) if row.startswith("▌")),
                                -1,
                            ),
                            "footer_row": next(
                                (i for i, row in enumerate(rows) if "enter confirm" in row),
                                -1,
                            ),
                        }
                    )
                    return
                raise AssertionError("inline approval was not rendered")
        class FakeCoordinator:
            def __init__(self, hooks, provider, approval_system):
                self.hooks = hooks
                self.provider = provider
                self.approval_system = approval_system
                self.capabilities = {}
                self.session_state = {}
                self.todo_state = None
                self.cancellation = FakeCancellation()

            def get(self, name):
                return {
                    "hooks": self.hooks,
                    "context": FakeContext(),
                    "providers": {"fake": self.provider},
                }.get(name)

            def register_capability(self, name, value):
                self.capabilities[name] = value

            def get_capability(self, name):
                return self.capabilities.get(name)
        class FakeSession:
            def __init__(self, coordinator, provider, prompts):
                self.session_id = "pty-production"
                self.coordinator = coordinator
                self.provider = provider
                self.prompts = prompts
                self.config = {}

            async def execute(self, prompt):
                turn = len(self.prompts)
                self.prompts.append(prompt)
                return await self.provider.execute(prompt, turn)


        class FakeStore:
            def get_metadata(self, session_id):
                return {}

            def save(self, session_id, messages, metadata):
                return None


        async def run():
            prompts = []
            events = []
            geometry = {}
            hooks = FakeHooks()
            approval_system = CLIApprovalSystem()
            provider = FakeProvider(hooks, approval_system, events, geometry)
            coordinator = FakeCoordinator(hooks, provider, approval_system)
            session = FakeSession(coordinator, provider, prompts)

            class Initialized:
                session_id = session.session_id
                configurator = None

                def __init__(self):
                    self.session = session

                async def cleanup(self):
                    Path(%(result_path)r).write_text(
                        json.dumps(
                            {"prompts": prompts, "events": events, "geometry": geometry},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

            async def create_initialized_session(*args, **kwargs):
                return Initialized()

            async def process_mentions(session, text):
                return text

            async def capture_git_diff(cwd):
                return GitDiffSnapshot(True)

            main_module.create_initialized_session = create_initialized_session
            main_module._process_runtime_mentions = process_mentions
            main_module.capture_git_diff = capture_git_diff
            main_module.SessionStore = FakeStore
            incremental_save.register_incremental_save = lambda *args, **kwargs: None

            await main_module.interactive_chat(
                config={},
                search_paths=[Path.cwd()],
                verbose=False,
                bundle_name="pty-acceptance",
            )


        asyncio.run(run())
        """
        % {"result_path": str(result_path)}
    )
    raw_paste = "\n".join(
        f"line {index:03d} · payload {index * 17}" for index in range(430)
    )
    single_line_paste = "single-line " + ("x" * 900) + " TAIL_SENTINEL"
    master, slave = os.openpty()
    size = struct.pack("HHHH", 36, 200, 0, 0)
    import fcntl

    fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "HOME": str(tmp_path),
        "NO_COLOR": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        # Isolated cwd (alongside the isolated HOME above) so this subprocess
        # never sees a real project-scope .amplifier/settings.yaml -- this
        # test asserts the safe chat/chat approval-prompt flow, which a
        # startup_permission=bypass preset in the repo's own settings would
        # silently skip (bypass never surfaces the approval prompt).
        cwd=str(tmp_path),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 20
    try:
        _read_until(master, output, b"shift+tab mode", deadline)
        os.write(
            master,
            b"\x1b[200~" + raw_paste.encode("utf-8") + b"\x1b[201~",
        )
        _read_until(master, output, b"[Pasted #1", deadline)
        os.write(master, b"\r")
        _read_until(master, output, b"esc to interrupt", deadline)
        _read_until(master, output, b"Allow load_skill?", deadline)
        os.write(master, b"\r")
        _read_until(master, output, b"amplifier-expert", deadline)
        _read_until(master, output, b"Inspecting flagship spec", deadline)
        _read_until(master, output, b"Responding...", deadline)

        os.write(master, b"midturn first\x0amidturn second")
        _read_until(master, output, b"midturn first", deadline)
        _read_until(master, output, b"midturn second", deadline)
        os.write(master, b"\r")
        _read_until(master, output, b"steer queued", deadline)
        _read_until(master, output, b"FINAL_TYPED_OUTPUT_1", deadline)
        _read_until(master, output, b"FINAL_TYPED_OUTPUT_2", deadline)
        os.write(
            master,
            b"\x1b[200~" + single_line_paste.encode("utf-8") + b"\x1b[201~",
        )
        _read_until(master, output, b"[Pasted #2", deadline)
        os.write(master, b"\r")
        _read_until(master, output, b"FINAL_TYPED_OUTPUT_3", deadline)
        os.write(master, b"\x04")
        _wait_for_process(master, output, process, deadline)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)

    rendered = output.decode("utf-8", errors="replace")
    assert process.returncode == 0, rendered[-4_000:]
    _assert_alternate_screen_lifecycle(rendered)
    assert "FINAL_TYPED_OUTPUT_1" in rendered
    assert "FINAL_TYPED_OUTPUT_2" in rendered
    assert "FINAL_TYPED_OUTPUT_3" in rendered
    assert "line 429" not in rendered
    assert "TAIL_SENTINEL" not in rendered
    assert "amplifier-expert" in rendered
    assert "Inspecting flagship spec" in rendered
    assert "Your choice" not in rendered
    assert "Hook Approval Required" not in rendered
    assert "bypass permissions on" not in rendered
    assert "[chat]" in rendered
    assert "Allow load_skill?" in rendered

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["prompts"] == [
        raw_paste,
        "midturn first\nmidturn second",
        single_line_paste,
    ]
    assert result["prompts"][0].encode() == raw_paste.encode()
    assert result["prompts"][2].encode() == single_line_paste.encode()
    assert result["events"] == [
        "start:0",
        "approval:Allow once",
        "end:0",
        "start:1",
        "end:1",
        "start:2",
        "end:2",
    ]
    assert result["geometry"] == {}


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="PTY support required")
def test_stable_viewport_and_input_remain_live_during_output(tmp_path) -> None:
    script = textwrap.dedent(
        """
        import asyncio
        from pathlib import Path
        from amplifier_app_cli.ui.command_registry import CommandRegistry
        from amplifier_app_cli.ui.layered_repl import LayeredReplApp
        from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
        from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
        from amplifier_app_cli.ui.layered_repl import LayeredReplConfig

        async def main():
            holder = {}

            async def submit(message):
                await holder["producer"]
                app.append_output(f"SUBMITTED:{message.text}")
                app.exit()

            app = LayeredReplApp(
                config=LayeredReplConfig(
                    history_path=Path(%(history)r),
                    completion=LayeredReplCompletion(
                        CommandRegistry.from_legacy(
                            {"/help": {"description": "help"}}
                        )
                    ),
                    bundle_name="test",
                    session_id="pty-test",
                ),
                bindings=LayeredReplBindings(on_submit=submit),
            )

            async def produce():
                for index in range(500):
                    app.append_output(f"scroll-{index:03d}")
                    await asyncio.sleep(0.001)

            holder["producer"] = asyncio.create_task(produce())
            await app.run_async()
            await holder["producer"]

        asyncio.run(main())
        """
        % {"history": str(tmp_path / "history")}
    )
    master, slave = os.openpty()
    size = struct.pack("HHHH", 30, 100, 0, 0)
    import fcntl

    fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
    env = {**os.environ, "TERM": "xterm-256color"}
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 15
    try:
        _read_until(master, output, b"shift+tab mode", deadline)
        time.sleep(0.05)
        os.write(master, b"typed while streaming\r")
        _read_until(master, output, b"SUBMITTED:typed while streaming", deadline)
        _wait_for_process(master, output, process, deadline)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)

    rendered = output.decode("utf-8", errors="replace")
    assert process.returncode == 0, rendered[-2_000:]
    assert "scroll-000" in rendered
    assert "scroll-499" in rendered
    assert "SUBMITTED:typed while streaming" in rendered
    _assert_alternate_screen_lifecycle(rendered)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
def test_live_transcript_scroll_keeps_composer_and_footer_pinned(tmp_path) -> None:
    """Browse live history without moving bottom chrome or losing a draft."""
    script_path = tmp_path / "pinned_transcript_probe.py"
    append_trigger = tmp_path / "append-live-output"
    append_complete = tmp_path / "append-live-output-complete"
    submitted_path = tmp_path / "submitted.json"
    script_path.write_text(
        textwrap.dedent(
            f"""
            import asyncio
            import json
            from pathlib import Path
            from amplifier_app_cli.ui.command_registry import CommandRegistry
            from amplifier_app_cli.ui.layered_repl import LayeredReplApp
            from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
            from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
            from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
            from amplifier_app_cli.ui.transcript_blocks import AnswerBlock
            from amplifier_app_cli.ui.transcript_blocks import NarrationBlock
            from amplifier_app_cli.ui.transcript_blocks import ToolBlock, ToolStatus
            from amplifier_app_cli.ui.transcript_blocks import UserBlock

            async def main():
                holder = {{}}

                async def submit(message):
                    Path({str(submitted_path)!r}).write_text(
                        json.dumps({{"text": message.text}}),
                        encoding="utf-8",
                    )
                    holder["app"].append_output(f"SUBMITTED:{{message.text}}")

                app = LayeredReplApp(
                    config=LayeredReplConfig(
                        history_path=Path({str(tmp_path / "history")!r}),
                        completion=LayeredReplCompletion(
                            CommandRegistry.from_legacy(
                                {{"/help": {{"description": "help"}}}}
                            )
                        ),
                        bundle_name="scroll-test",
                        session_id="pinned-scroll",
                    ),
                    bindings=LayeredReplBindings(on_submit=submit),
                )
                holder["app"] = app

                async def produce():
                    await asyncio.sleep(0.2)
                    app._emit_ui_event(UserBlock("EXACT_USER_ROW", mode="chat"))
                    app._emit_ui_event(NarrationBlock("EXACT_NARRATION_ROW"))
                    app._emit_ui_event(
                        ToolBlock("EXACT_TOOL_ROW", ToolStatus.COMPLETED)
                    )
                    app._emit_ui_event(AnswerBlock("EXACT_ANSWER_ROW"))
                    for index in range(160):
                        app.append_output(f"ROW-{{index:03d}}")
                    trigger = Path({str(append_trigger)!r})
                    while not trigger.exists():
                        await asyncio.sleep(0.02)
                    app.append_output("LIVE-WHILE-SCROLLED")
                    # Let prompt-toolkit paint the append before acknowledging it.
                    await asyncio.sleep(0.4)
                    Path({str(append_complete)!r}).write_text(
                        "done", encoding="utf-8"
                    )
                    await asyncio.Event().wait()

                producer = asyncio.create_task(produce())
                try:
                    await app.run_async()
                finally:
                    producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)

            asyncio.run(main())
            """
        ),
        encoding="utf-8",
    )
    session_name = f"amp-pinned-scroll-{os.getpid()}-{time.time_ns()}"
    env = {**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"}
    try:
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-x",
                "100",
                "-y",
                "30",
                "-s",
                session_name,
                sys.executable,
                str(script_path),
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
        )
        deadline = time.monotonic() + 10
        visible = ""
        while time.monotonic() < deadline:
            visible = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            rows = visible.splitlines()
            if (
                len(rows) >= 30
                and "ROW-159" in visible
                and rows[-2].startswith("▌")
                and "ctrl-t" in rows[-1]
            ):
                break
            time.sleep(0.1)
        else:
            raise AssertionError(f"full-screen transcript did not settle:\n{visible}")

        state = subprocess.check_output(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                session_name,
                "#{alternate_on}:#{pane_in_mode}",
            ],
            env=env,
            text=True,
        ).strip()
        assert state == "1:0"

        subprocess.run(
            [
                "tmux",
                "send-keys",
                "-t",
                session_name,
                "-l",
                "DRAFT_SENTINEL",
            ],
            env=env,
            check=True,
        )
        draft_deadline = time.monotonic() + 3
        while time.monotonic() < draft_deadline:
            tail = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            tail_rows = tail.splitlines()
            if len(tail_rows) >= 30 and "DRAFT_SENTINEL" in tail_rows[-2]:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"draft was not rendered:\n{tail}")
        assert "ROW-159" in tail
        assert tail_rows[-2].startswith("▌")
        assert "ctrl-t" in tail_rows[-1]
        tail_chrome = tail_rows[-2:]

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "PageUp"],
            env=env,
            check=True,
        )
        scroll_deadline = time.monotonic() + 3
        while time.monotonic() < scroll_deadline:
            scrolled = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            scrolled_rows = scrolled.splitlines()
            if "ROW-159" not in scrolled and any(
                f"ROW-{index:03d}" in scrolled for index in range(90, 150)
            ):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"PageUp did not move the transcript:\n{scrolled}")

        assert scrolled_rows[-2:] == tail_chrome
        assert "DRAFT_SENTINEL" in scrolled_rows[-2]
        pane_mode = subprocess.check_output(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                session_name,
                "#{pane_in_mode}",
            ],
            env=env,
            text=True,
        ).strip()
        assert pane_mode == "0"

        append_trigger.write_text("append", encoding="utf-8")
        append_deadline = time.monotonic() + 3
        while time.monotonic() < append_deadline and not append_complete.exists():
            time.sleep(0.05)
        assert append_complete.exists()
        after_live = subprocess.check_output(
            ["tmux", "capture-pane", "-p", "-t", session_name],
            env=env,
            text=True,
        )
        after_live_rows = after_live.splitlines()
        assert after_live_rows == scrolled_rows
        assert "LIVE-WHILE-SCROLLED" not in after_live
        assert "DRAFT_SENTINEL" in after_live_rows[-2]

        subprocess.run(
            ["tmux", "resize-window", "-t", session_name, "-x", "120", "-y", "40"],
            env=env,
            check=True,
        )
        resized = ""
        resize_deadline = time.monotonic() + 3
        while time.monotonic() < resize_deadline:
            resized = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            rows = resized.splitlines()
            if (
                len(rows) >= 40
                and rows[-2].startswith("▌")
                and "DRAFT_SENTINEL" in rows[-2]
            ):
                break
            time.sleep(0.1)
        assert rows[-2].startswith("▌")
        assert "DRAFT_SENTINEL" in rows[-2]
        assert "ctrl-t" in rows[-1]
        assert "ROW-159" not in resized
        assert "LIVE-WHILE-SCROLLED" not in resized

        subprocess.run(
            [
                "tmux",
                "send-keys",
                "-t",
                session_name,
                "PageDown",
                "PageDown",
            ],
            env=env,
            check=True,
        )
        tail_deadline = time.monotonic() + 3
        while time.monotonic() < tail_deadline:
            returned = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            returned_rows = returned.splitlines()
            if "LIVE-WHILE-SCROLLED" in returned:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"PageDown did not restore the tail:\n{returned}")
        assert "ROW-159" in returned
        assert "DRAFT_SENTINEL" in returned_rows[-2]
        assert "ctrl-t" in returned_rows[-1]

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Enter"],
            env=env,
            check=True,
        )
        submit_deadline = time.monotonic() + 3
        while time.monotonic() < submit_deadline and not submitted_path.exists():
            time.sleep(0.05)
        assert json.loads(submitted_path.read_text(encoding="utf-8")) == {
            "text": "DRAFT_SENTINEL"
        }

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "C-d"],
            env=env,
            check=True,
        )
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
def test_inline_approval_owns_bottom_rows_and_preserves_hidden_draft(tmp_path) -> None:
    script_path = tmp_path / "approval_probe.py"
    script_path.write_text(
        textwrap.dedent(
            f"""
            import asyncio
            from pathlib import Path
            from amplifier_app_cli.ui.command_registry import CommandRegistry
            from amplifier_app_cli.ui.layered_repl import LayeredReplApp
            from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
            from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
            from amplifier_app_cli.ui.layered_repl import LayeredReplConfig

            async def main():
                app = LayeredReplApp(
                    config=LayeredReplConfig(
                        history_path=Path({str(tmp_path / "approval-history")!r}),
                        completion=LayeredReplCompletion(
                            CommandRegistry.from_legacy(
                                {{"/help": {{"description": "help"}}}}
                            )
                        ),
                        bundle_name="approval-test",
                        session_id="approval-session",
                    ),
                    bindings=LayeredReplBindings(
                        on_submit=lambda message: None,
                    ),
                )

                async def ask():
                    await asyncio.sleep(0.3)
                    choice = await app.request_approval(
                        "Allow write?", ("Allow once", "Deny"), 30, "deny"
                    )
                    app.append_output(
                        f"APPROVAL={{choice}} DRAFT={{app.input_buffer.text!r}}"
                    )
                    await asyncio.sleep(10)

                task = asyncio.create_task(ask())
                try:
                    await app.run_async()
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            asyncio.run(main())
            """
        ),
        encoding="utf-8",
    )
    session_name = f"amp-approval-{os.getpid()}-{time.time_ns()}"
    env = {**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"}
    try:
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-x",
                "100",
                "-y",
                "30",
                "-s",
                session_name,
                sys.executable,
                str(script_path),
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
        )
        deadline = time.monotonic() + 8
        visible = ""
        while time.monotonic() < deadline:
            visible = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            rows = visible.splitlines()
            if len(rows) >= 30 and "Allow write?" in rows[-2]:
                break
            time.sleep(0.1)
        assert "Allow write?" in rows[-2]
        assert "enter confirm" in rows[-1]
        assert not any(row.startswith(("▌", "❯")) for row in rows[-2:])

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "x", "Enter"],
            env=env,
            check=True,
        )
        full = ""
        while time.monotonic() < deadline:
            full = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name, "-S", "-80"],
                env=env,
                text=True,
            )
            if "APPROVAL=Allow once" in full:
                break
            time.sleep(0.1)
        assert "APPROVAL=Allow once DRAFT=''" in full
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
def test_transcript_mouse_drag_copies_without_stealing_draft(tmp_path) -> None:
    """Exercise the xterm SGR drag protocol against the real full-screen app."""
    copied_path = tmp_path / "copied.txt"
    script_path = tmp_path / "mouse_copy_probe.py"
    script_path.write_text(
        textwrap.dedent(
            f"""
            import asyncio
            from pathlib import Path
            from amplifier_app_cli.ui.command_registry import CommandRegistry
            import amplifier_app_cli.ui.layered_repl as layered_repl

            def capture_copy(text, **kwargs):
                Path({str(copied_path)!r}).write_text(text, encoding="utf-8")
                return True

            layered_repl.copy_text_to_clipboard = capture_copy

            async def main():
                app = layered_repl.LayeredReplApp(
                    config=layered_repl.LayeredReplConfig(
                        history_path=Path({str(tmp_path / "mouse-copy-history")!r}),
                        completion=layered_repl.LayeredReplCompletion(
                            CommandRegistry.from_legacy(
                                {{"/help": {{"description": "help"}}}}
                            )
                        ),
                        bundle_name="copy-test",
                        session_id="copy-session",
                    ),
                    bindings=layered_repl.LayeredReplBindings(
                        on_submit=lambda message: None,
                    ),
                )

                async def produce():
                    await asyncio.sleep(0.2)
                    app.append_output("COPY_TARGET_SENTINEL")
                    await asyncio.Event().wait()

                producer = asyncio.create_task(produce())
                try:
                    await app.run_async()
                finally:
                    producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)

            asyncio.run(main())
            """
        ),
        encoding="utf-8",
    )
    session_name = f"amp-mouse-copy-{os.getpid()}-{time.time_ns()}"
    env = {**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"}
    try:
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-x",
                "80",
                "-y",
                "20",
                "-s",
                session_name,
                sys.executable,
                str(script_path),
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
        )
        deadline = time.monotonic() + 8
        visible = ""
        while time.monotonic() < deadline:
            visible = subprocess.check_output(
                ["tmux", "capture-pane", "-p", "-t", session_name],
                env=env,
                text=True,
            )
            rows = visible.splitlines()
            if "COPY_TARGET_SENTINEL" in visible and len(rows) >= 20:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"copy target did not render:\n{visible}")

        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "-l", "DRAFT_SENTINEL"],
            env=env,
            check=True,
        )
        target_row = next(
            index for index, row in enumerate(rows) if "COPY_TARGET_SENTINEL" in row
        )
        target_column = rows[target_row].index("COPY_TARGET_SENTINEL")

        # SGR coordinates are one-based. Motion code 32 means button 1 drag.
        for sequence in (
            f"\x1b[<0;{target_column + 1};{target_row + 1}M",
            f"\x1b[<32;{target_column + 12};{target_row + 1}M",
            f"\x1b[<0;{target_column + 12};{target_row + 1}m",
        ):
            subprocess.run(
                [
                    "tmux",
                    "send-keys",
                    "-H",
                    "-t",
                    session_name,
                    *[f"{byte:02x}" for byte in sequence.encode()],
                ],
                env=env,
                check=True,
            )

        while time.monotonic() < deadline and not copied_path.exists():
            time.sleep(0.05)
        assert copied_path.read_text(encoding="utf-8") == "COPY_TARGET"
        after = subprocess.check_output(
            ["tmux", "capture-pane", "-p", "-t", session_name],
            env=env,
            text=True,
        ).splitlines()
        assert "DRAFT_SENTINEL" in after[-2]
        assert "ctrl-t" in after[-1]
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _assert_alternate_screen_lifecycle(rendered: str) -> None:
    enter = "\x1b[?1049h"
    restore = "\x1b[?1049l"

    assert rendered.count(enter) == 1
    assert rendered.count(restore) == 1
    assert rendered.index(enter) < rendered.index(restore)


def _read_until(
    master: int,
    output: bytearray,
    needle: bytes,
    deadline: float,
) -> None:
    while needle not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"timed out waiting for {needle!r}; tail={bytes(output[-2000:])!r}"
            )
        readable, _, _ = select.select([master], [], [], min(0.2, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(master, 65_536)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    if needle not in output:
        raise AssertionError(f"PTY closed before {needle!r}")


def _drain(master: int, output: bytearray) -> None:
    while True:
        readable, _, _ = select.select([master], [], [], 0)
        if not readable:
            return
        try:
            chunk = os.read(master, 65_536)
        except OSError as error:
            if error.errno == errno.EIO:
                return
            raise
        if not chunk:
            return
        output.extend(chunk)


def _wait_for_process(
    master: int,
    output: bytearray,
    process: subprocess.Popen,
    deadline: float,
) -> None:
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"timed out waiting for PTY process; tail={bytes(output[-2000:])!r}"
            )
        readable, _, _ = select.select([master], [], [], min(0.1, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(master, 65_536)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    process.wait(timeout=max(1, deadline - time.monotonic()))
    _drain(master, output)
