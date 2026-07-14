"""Application lifecycle and transcript output ownership for the layered REPL."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.application.current import create_app_session

from .transcript_blocks import DebugBlock
from .ui_events import UiEvent

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from .agent_lanes import AgentLaneViewModel
    from .bottom_stdout import TranscriptOutput
    from .bottom_stdout import TranscriptOutputBridge
    from .clipboard import LosslessTextPasteState
    from .clipboard import TextPasteReference
    from .clipboard_availability import ClipboardImageAvailabilityDetector
    from .inline_approval import InlineApprovalState
    from .layered_transcript import LayeredTranscriptView
    from .terminal_transcript import TerminalTranscript
    from .ui_events import UiEventDispatcher

    class _LayeredReplLifecycleOwner(Protocol):
        application: Application[Any]
        transcript_window: Any
        _agent_lanes: AgentLaneViewModel | None
        _approval_state: InlineApprovalState
        _background_process: asyncio.subprocess.Process | None
        _background_shell_task: asyncio.Task[None] | None
        _clipboard_detector: ClipboardImageAvailabilityDetector
        _exit_transcript: TerminalTranscript
        _exit_when_submitted: bool
        _on_exit: Callable[[], None] | None
        _output_bridge: TranscriptOutputBridge
        _owner_loop: asyncio.AbstractEventLoop | None
        _paste_tokens: dict[str, TextPasteReference]
        _remove_clipboard_listener: Callable[[], None] | None
        _remove_lane_listener: Callable[[], None] | None
        _remove_notice_listener: Callable[[], None] | None
        _remove_runtime_listener: Callable[[], None] | None
        _remove_steering_listener: Callable[[], None] | None
        _remove_stream_listener: Callable[[], None] | None
        _remove_task_listener: Callable[[], None] | None
        _submit_tasks: set[asyncio.Task[Any]]
        _terminal_file: Any
        _text_pastes: LosslessTextPasteState
        _transcript_flushed_on_exit: bool
        _transcript_view: LayeredTranscriptView
        _typed_output: TranscriptOutput
        _ui_events: UiEventDispatcher

        def _append_transcript_output(self, text: str) -> None: ...

        async def _await_background_shell_shutdown(self) -> None: ...

        def _flush_transcript_on_exit(self) -> None: ...

        def _stop_focused_transcript_follow(self) -> None: ...

        def _terminal_size(self) -> tuple[int, int]: ...

        def commit_plan_state(self, lifecycle: str) -> bool: ...

        def exit(self) -> None: ...


class LayeredReplLifecycleMixin:
    """Run, stop, and capture output for the full-screen application."""

    async def run_async(self: _LayeredReplLifecycleOwner) -> None:
        owner_loop = asyncio.get_running_loop()
        self._owner_loop = owner_loop
        self._clipboard_detector.start()
        try:
            with create_app_session(
                input=self.application.input,
                output=self.application.output,
            ):
                try:
                    with self._output_bridge.patch():
                        await self.application.run_async()
                        if self._submit_tasks:
                            await asyncio.gather(
                                *tuple(self._submit_tasks), return_exceptions=True
                            )
                finally:
                    await self._await_background_shell_shutdown()
                    self._flush_transcript_on_exit()
        finally:
            try:
                await self._clipboard_detector.stop()
            finally:
                if self._owner_loop is owner_loop:
                    self._owner_loop = None

    def _flush_transcript_on_exit(self: _LayeredReplLifecycleOwner) -> None:
        """Restore terminal state and retain the completed chat in shell scrollback."""
        if self._transcript_flushed_on_exit:
            return
        self._transcript_flushed_on_exit = True
        output = self.application.output
        try:
            output.enable_autowrap()
            output.reset_attributes()
            output.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

        transcript = self._exit_transcript.plain_text.rstrip("\n")
        self._exit_transcript.clear()
        if transcript:
            try:
                self._terminal_file.write(transcript + "\n")
                self._terminal_file.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def batch_transcript_output(self: _LayeredReplLifecycleOwner):
        """Batch typed UI events into one transcript append."""
        return self._typed_output.batch()

    def mark_exit_flush_boundary(self: _LayeredReplLifecycleOwner) -> None:
        """Exclude transcript history already present in primary scrollback."""
        self._exit_transcript.clear()

    async def _await_background_shell_shutdown(
        self: _LayeredReplLifecycleOwner,
    ) -> None:
        """Let a suspended shell restore prompt-toolkit before final output."""
        task = self._background_shell_task
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
        finally:
            if self._background_shell_task is task:
                self._background_shell_task = None

    def request_exit(self: _LayeredReplLifecycleOwner) -> None:
        if self._submit_tasks:
            self._exit_when_submitted = True
            return
        if self._on_exit:
            self._on_exit()
        else:
            self.exit()

    def exit(self: _LayeredReplLifecycleOwner) -> None:
        self.commit_plan_state("incomplete")
        self._stop_focused_transcript_follow()
        self._clipboard_detector.request_stop()
        self._approval_state.close()
        if self._remove_task_listener is not None:
            self._remove_task_listener()
            self._remove_task_listener = None
        if self._remove_stream_listener is not None:
            self._remove_stream_listener()
            self._remove_stream_listener = None
        if self._remove_runtime_listener is not None:
            self._remove_runtime_listener()
            self._remove_runtime_listener = None
        if self._remove_notice_listener is not None:
            self._remove_notice_listener()
            self._remove_notice_listener = None
        if self._remove_steering_listener is not None:
            self._remove_steering_listener()
            self._remove_steering_listener = None
        if self._remove_lane_listener is not None:
            self._remove_lane_listener()
            self._remove_lane_listener = None
        if self._remove_clipboard_listener is not None:
            self._remove_clipboard_listener()
            self._remove_clipboard_listener = None
        if self._agent_lanes is not None:
            self._agent_lanes.close()
        if self._background_shell_task is not None:
            self._background_shell_task.cancel()
        if (
            self._background_process is not None
            and self._background_process.returncode is None
        ):
            self._background_process.terminate()
        self._text_pastes.clear()
        self._paste_tokens.clear()
        if self.application.is_running and not self.application.is_done:
            self.application.exit()

    def append_output(self: _LayeredReplLifecycleOwner, text: str) -> None:
        value = str(text)
        if not value:
            return
        if not value.endswith("\n"):
            value += "\n"
        self._append_transcript_output(value)

    def _append_transcript_output(self: _LayeredReplLifecycleOwner, text: str) -> None:
        owner_loop = self._owner_loop
        if owner_loop is not None and not owner_loop.is_closed():
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if current_loop is not owner_loop:
                try:
                    owner_loop.call_soon_threadsafe(
                        self._append_transcript_output, text
                    )
                except RuntimeError:
                    pass
                else:
                    return
        self._transcript_view.append_output(text)
        self._exit_transcript.write(text)

    def _capture_untyped_output(self: _LayeredReplLifecycleOwner, text: str) -> None:
        lines = tuple(line for line in str(text).splitlines() if line.strip())
        if not lines:
            return
        self._ui_events.emit(
            DebugBlock(
                lines[:200],
                label="Internal output",
                expanded=False,
                total_lines=len(lines),
            )
        )

    async def flush_output(self: _LayeredReplLifecycleOwner) -> None:
        """Yield until queued cross-thread output is visible to the layout."""
        await asyncio.sleep(0)
        self.application.invalidate()

    def _transcript_page_rows(self: _LayeredReplLifecycleOwner) -> int:
        rows = self._terminal_size()[0]
        render_info = getattr(self.transcript_window, "render_info", None)
        height = getattr(render_info, "window_height", None)
        if isinstance(height, int) and height > 0:
            return max(1, height - 1)
        return max(1, rows - 8)

    def _emit_ui_event(self: _LayeredReplLifecycleOwner, event: UiEvent) -> None:
        self._ui_events.emit(event)

    def capture_output(self: _LayeredReplLifecycleOwner, console: Any):
        """Capture Rich/default stdout before and during the application run."""
        return self._output_bridge.patch()


__all__ = ["LayeredReplLifecycleMixin"]
