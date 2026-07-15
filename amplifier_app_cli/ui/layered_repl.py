"""Layered prompt-toolkit application for interactive Amplifier sessions."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from typing import Any
from typing import TextIO
from typing import cast

from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout.containers import Window
from rich.console import Console

from amplifier_app_cli.session_store import SessionStore

from .agent_lanes import AgentLaneViewModel
from .block_render_cache import BlockRenderCache
from .bottom_stdout import TranscriptOutput
from .bottom_stdout import TranscriptOutputBridge
from .clipboard import ImageAttachment
from .clipboard import LosslessTextPasteState
from .clipboard import TextPasteReference
from .clipboard import read_clipboard_image
from .clipboard_availability import ClipboardImageAvailabilityDetector
from .inline_approval import InlineApprovalState
from .layered_repl_agents import LayeredReplAgentMixin
from .layered_repl_approval import LayeredReplApprovalMixin
from .layered_repl_config import LayeredReplBindings
from .layered_repl_config import LayeredReplCompletion
from .layered_repl_config import LayeredReplConfig
from .layered_repl_config import LayeredReplServices
from .layered_repl_input import LayeredReplInputMixin
from .layered_repl_input import load_history
from .layered_repl_layout import build_layered_application
from .layered_repl_lifecycle import LayeredReplLifecycleMixin
from .layered_repl_navigation import LayeredReplNavigationMixin
from .layered_repl_status import LayeredReplStatusMixin
from .layered_repl_surfaces import LayeredReplSurfaceMixin
from .layered_repl_terminal import LayeredReplTerminalMixin
from .layered_transcript import LayeredTranscriptView
from .notices import TransientNoticeState
from .repl import SlashCommandCompleter
from .terminal_transcript import TerminalTranscript
from .text_clipboard import copy_text_to_clipboard
from .transcript_blocks import AnswerBlock
from .transcript_blocks import ToolBlock
from .transcript_blocks import tool_block_from_activity
from .transcript_reflow import TranscriptReflowController
from .ui_events import TranscriptClickAction
from .ui_events import UiEventDispatcher


class LayeredReplApp(
    LayeredReplInputMixin,
    LayeredReplNavigationMixin,
    LayeredReplApprovalMixin,
    LayeredReplAgentMixin,
    LayeredReplLifecycleMixin,
    LayeredReplTerminalMixin,
    LayeredReplStatusMixin,
    LayeredReplSurfaceMixin,
):
    """Own the full-screen transcript, composer, and persistent status chrome."""

    # The layout builder assigns this window before the application is exposed.
    transcript_window: Window

    def __init__(
        self,
        *,
        config: LayeredReplConfig,
        bindings: LayeredReplBindings,
        services: LayeredReplServices | None = None,
    ):
        services = services or LayeredReplServices()
        completion = config.completion
        self._on_submit = bindings.on_submit
        self._on_interrupt = bindings.on_interrupt
        self._on_exit = bindings.on_exit
        self._get_active_mode = bindings.get_active_mode
        self._get_render_profile = bindings.get_render_profile
        self._get_is_running = bindings.get_is_running
        self._get_queued_count = bindings.get_queued_count
        self._get_queued_preview = bindings.get_queued_preview
        self._pop_last_queued = bindings.pop_last_queued
        self._bundle_name = config.bundle_name
        self._session_id = config.session_id
        self._task_tracker = services.task_tracker
        self._stream_status = services.stream_status
        self._runtime_status = services.runtime_status
        self._agent_lanes = (
            AgentLaneViewModel(self._task_tracker, self._runtime_status)
            if self._task_tracker is not None
            else None
        )
        self._notices = services.notice_state or TransientNoticeState()
        self._trust_state = services.trust_state
        self._outcome_ledger = services.outcome_ledger
        self._needs_you = services.needs_you
        self._steering_queue = services.steering_queue
        self._get_task_title = bindings.get_task_title
        self._on_cycle_mode = bindings.on_cycle_mode
        self._on_cycle_permission = bindings.on_cycle_permission
        self._on_rewind = bindings.on_rewind
        self._evidence_model = services.evidence_model
        self._clipboard_detector = (
            services.clipboard_detector or ClipboardImageAvailabilityDetector()
        )
        self._tasks_visible = False
        self._attachments: list[ImageAttachment] = []
        self._text_pastes = LosslessTextPasteState()
        self._paste_tokens: dict[str, TextPasteReference] = {}
        self._running_started_at: float | None = None
        self._rendered_terminal_tools: set[tuple[str, str]] = set()
        self._expanded_terminal_tools: set[tuple[str, str]] = set()
        self._committed_plan_signature: tuple[tuple[str, str], ...] | None = None
        self._committed_plan_lifecycle: (
            tuple[tuple[tuple[str, str], ...], str] | None
        ) = None
        self._last_task_counts = (
            self._task_tracker.counts() if self._task_tracker else None
        )
        self._remove_task_listener: Callable[[], None] | None = None
        self._remove_stream_listener: Callable[[], None] | None = None
        self._remove_runtime_listener: Callable[[], None] | None = None
        self._remove_notice_listener: Callable[[], None] | None = None
        self._remove_steering_listener: Callable[[], None] | None = None
        self._remove_lane_listener: Callable[[], None] | None = None
        self._remove_clipboard_listener: Callable[[], None] | None = None
        self._submit_tasks: set[asyncio.Task[Any]] = set()
        self._focused_transcript_signatures: dict[str, tuple[str, ...]] = {}
        self._focused_transcript_revisions: dict[str, tuple[int, int]] = {}
        self._focused_transcript_task: asyncio.Task[None] | None = None
        self._session_store = SessionStore()
        self._exit_when_submitted = False
        self._approval_state = InlineApprovalState(self._approval_state_changed)
        self._transcript_view = LayeredTranscriptView(
            stream_status=self._stream_status,
            render_width=lambda: self._terminal_size()[1],
            copy_selection=self._copy_transcript_selection,
            max_lines=config.max_output_lines,
        )
        self._transcript_flushed_on_exit = False
        self._exit_transcript = TerminalTranscript(max_lines=None)
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._terminal_file = sys.stdout
        self._typed_output = TranscriptOutput(
            self._append_typed_transcript_output, stream=self._terminal_file
        )
        typed_console = Console(
            file=cast(TextIO, self._typed_output),
            force_terminal=True,
        )
        self._ui_events = services.event_dispatcher or UiEventDispatcher(
            typed_console,
            self._render_profile,
        )
        if services.event_dispatcher is not None:
            services.event_dispatcher.bind_console(typed_console)
        self._ui_events.set_click_ref_resolver(self._resolve_click_ref)
        self._transcript_view.set_click_action_handler(self._activate_transcript_click)
        self._block_render_cache = BlockRenderCache()
        self._transcript_view.set_block_renderer(self._render_block_for_reflow)
        self._transcript_reflow = TranscriptReflowController(
            observe_width=self._transcript_view.current_render_width,
            reflow=self._transcript_view.reflow_to_width,
            stream_active=self._reflow_stream_active,
        )
        self._output_bridge = TranscriptOutputBridge(self._capture_untyped_output)

        completer = SlashCommandCompleter(
            completion.registry,
            mode_names=list(completion.mode_names),
            skill_names=list(completion.skill_names),
            model_names=completion.model_names,
        )
        self._palette = completer.palette
        self._palette_selected_index = 0
        self._palette_dismissed_text: str | None = None
        self._rewind_visible_state = False
        self._rewind_selected_index = 0
        self._evidence_visible_state = False
        self._evidence_answer_id: str | None = None
        self._evidence_selected_index = 0
        self._ambient_state = "idle"
        self._backgrounded = False
        self._background_terminal_active = False
        self._background_shell_task: asyncio.Task[None] | None = None
        self._background_process: asyncio.subprocess.Process | None = None
        self._pending_terminal_sequences: list[str] = []
        self.input_buffer = Buffer(
            completer=completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=load_history(config.history_path),
            multiline=True,
            enable_history_search=True,
        )
        self.application = build_layered_application(
            self,
            output=config.output,
            input=config.input,
        )
        self.application.after_render += self._flush_terminal_sequences
        self.application.after_render += self._transcript_reflow.observe
        self._transcript_view.set_invalidate(self.application.invalidate)
        if self._task_tracker is not None:
            self._remove_task_listener = self._task_tracker.add_listener(
                self._task_state_changed
            )
        if self._stream_status is not None:
            self._remove_stream_listener = self._stream_status.add_listener(
                self._stream_state_changed
            )
        if self._runtime_status is not None:
            self._remove_runtime_listener = self._runtime_status.add_listener(
                self._runtime_state_changed
            )
        self._remove_notice_listener = self._notices.add_listener(
            self.application.invalidate
        )
        if self._steering_queue is not None:
            self._remove_steering_listener = self._steering_queue.add_listener(
                self.application.invalidate
            )
        if self._agent_lanes is not None:
            self._remove_lane_listener = self._agent_lanes.add_listener(
                self.application.invalidate
            )
        self._remove_clipboard_listener = self._clipboard_detector.add_listener(
            self._clipboard_availability_changed
        )

    def _render_profile(self) -> str:
        return (
            self._get_render_profile() if self._get_render_profile else "conversational"
        )

    def _append_typed_transcript_output(self, text: str) -> None:
        """Commit one typed block chunk with its click identity and source."""
        self._append_click_transcript_output(
            text,
            self._ui_events.active_click_action,
            self._ui_events.active_block,
        )

    def _append_click_transcript_output(
        self, text: str, action: object | None, block: object | None = None
    ) -> None:
        owner_loop = self._owner_loop
        if owner_loop is not None and not owner_loop.is_closed():
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if current_loop is not owner_loop:
                try:
                    owner_loop.call_soon_threadsafe(
                        self._append_click_transcript_output, text, action, block
                    )
                except RuntimeError:
                    pass
                else:
                    return
        self._transcript_view.append_output(text, action=action, block=block)
        self._exit_transcript.write(text)

    def _render_block_for_reflow(self, block: object, width: int) -> str:
        """Re-render one retained block at a reflow width through the cache."""
        return self._block_render_cache.render(
            block,
            width,
            lambda source, target_width: self._ui_events.render_to_ansi(
                cast(Any, source), width=target_width
            ),
        )

    def _reflow_stream_active(self) -> bool:
        """Report whether a reflow must wait for actively streamed output.

        A turn can be "running" for a long stretch without appending anything
        new to the transcript yet (e.g. mid-tool-call, waiting on a shell
        command) -- that idle window is safe to reflow immediately. Only a
        live stream preview (text genuinely being painted) needs to hold the
        rebuild, so this checks the preview alone rather than the turn's
        overall running flag.
        """
        if self._stream_status is None:
            return False
        try:
            return self._stream_status.preview is not None
        except Exception:
            return True

    def _resolve_click_ref(
        self, action: TranscriptClickAction
    ) -> TranscriptClickAction | None:
        """Stamp emit-time identity onto a clickable block span."""
        kind, ref = action
        if kind == "terminator":
            latest = (
                self._outcome_ledger.latest
                if self._outcome_ledger is not None
                else None
            )
            return None if latest is None else ("terminator", latest.checkpoint_id)
        if kind == "answer":
            answer_id = self._recorded_answer_id(ref)
            return None if answer_id is None else ("answer", answer_id)
        return action

    def _recorded_answer_id(self, ref: object) -> str | None:
        """Match one rendered answer against the latest evidence record."""
        model = self._evidence_model
        if model is None or not model.answer_ids or not isinstance(ref, AnswerBlock):
            return None
        answer_id = model.answer_ids[-1]
        snapshot = model.snapshot(answer_id)
        if snapshot is None:
            return None
        recorded = " ".join(snapshot.answer.split())
        rendered = " ".join(ref.markdown.split())
        if not recorded or not rendered:
            return None
        if recorded == rendered:
            return answer_id
        if snapshot.truncated and rendered.startswith(recorded):
            return answer_id
        return None

    def _activate_transcript_click(self, action: object) -> bool:
        """Dispatch a transcript click to its keyboard-equivalent path."""
        if not isinstance(action, tuple) or len(action) != 2:
            return False
        kind, ref = action
        if kind == "tool" and isinstance(ref, ToolBlock):
            return self._expand_clicked_tool(ref)
        if kind == "terminator" and isinstance(ref, str):
            return self.open_rewind_at_checkpoint(ref)
        if kind == "answer" and isinstance(ref, str):
            return self.open_evidence_for_answer(ref)
        return False

    def _expand_clicked_tool(self, block: ToolBlock) -> bool:
        if block.expanded or not block.output:
            return False
        key = self._clicked_tool_key(block)
        if key is not None:
            if key in self._expanded_terminal_tools:
                return False
            self._expanded_terminal_tools.add(key)
        self._emit_ui_event(replace(block, expanded=True))
        self._notices.show(f"expanded {block.summary}")
        return True

    def _clicked_tool_key(self, block: ToolBlock) -> tuple[str, str] | None:
        """Keep ctrl-o from re-expanding a tool a click already expanded."""
        if self._runtime_status is None:
            return None
        for tool in reversed(self._runtime_status.tool_snapshot()):
            if not tool.terminal or tool.result is None:
                continue
            rendered = tool_block_from_activity(tool)
            if rendered.summary == block.summary and rendered.command == block.command:
                return (tool.session_id, tool.tool_call_id)
        return None

    def open_rewind_at_checkpoint(self, checkpoint_id: str) -> bool:
        """Open the rewind bar with one clicked turn rule preselected."""
        if not self.open_rewind_picker():
            return False
        for index, entry in enumerate(self._rewind_entries()):
            if entry.checkpoint_id == checkpoint_id:
                self._rewind_selected_index = index
                self.application.invalidate()
                break
        return True

    def open_evidence_for_answer(self, answer_id: str) -> bool:
        """Reveal evidence for one clicked answer, mirroring ctrl-e."""
        model = self._evidence_model
        if model is None or not model.answer_ids:
            self._notices.show("no answer evidence yet")
            return False
        if answer_id not in model.answer_ids or answer_id == model.answer_ids[-1]:
            return self.open_evidence_picker()
        snapshot = model.reveal(answer_id)
        if snapshot is None or not snapshot.links:
            self._notices.show("this answer has no supported evidence claims")
            return False
        claims = {claim.claim_id: claim for claim in snapshot.claims}
        evidence_lines = []
        for link in snapshot.links:
            claim = claims.get(link.claim_id)
            tool = model.resolve(answer_id, link.number)
            claim_text = " ".join(claim.text.split()) if claim is not None else "claim"
            summary = tool.summary if tool is not None else link.tool_call_id
            evidence_lines.append(f"{link.marker} {claim_text} -> {summary}")
        self._emit_ui_event(AnswerBlock("\n".join(evidence_lines), label="Evidence"))
        self._evidence_answer_id = answer_id
        self._evidence_selected_index = 0
        self._evidence_visible_state = True
        self.application.invalidate()
        return True

    def _clock(self) -> float:
        """Keep the established main-module clock monkeypatch seam."""
        return monotonic()

    def _read_clipboard_image(self) -> ImageAttachment | None:
        """Keep the established main-module clipboard monkeypatch seam."""
        return read_clipboard_image()

    def _copy_text(self, text: str) -> bool:
        """Keep transcript copy tests and embedders on the public module seam."""
        return copy_text_to_clipboard(text, terminal=self._terminal_file)


__all__ = [
    "LayeredReplApp",
    "LayeredReplBindings",
    "LayeredReplCompletion",
    "LayeredReplConfig",
    "LayeredReplServices",
]
