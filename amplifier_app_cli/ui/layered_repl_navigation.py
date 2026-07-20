"""Inline palette, rewind picker, and transcript navigation surfaces."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth

from .layered_repl_style import TOKENS
from .repl import summarize_cell_text
from .transcript_blocks import AnswerBlock
from .transcript_blocks import tool_block_from_activity

if TYPE_CHECKING:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer

    from .command_palette import CommandPalette
    from .command_palette import PaletteSnapshot
    from .evidence_links import EvidenceLink
    from .evidence_links import EvidenceLinkModel
    from .interaction_state import NeedsYouQueue
    from .notices import TransientNoticeState
    from .outcome_ledger import OutcomeLedger
    from .outcome_ledger import TurnOutcome
    from .ui_events import UiEvent

    class _LayeredReplNavigationOwner(Protocol):
        input_buffer: Buffer
        application: Application[Any]
        _palette: CommandPalette
        _palette_dismissed_text: str | None
        _palette_selected_index: int
        _tasks_visible: bool
        _outcome_ledger: OutcomeLedger | None
        _notices: TransientNoticeState
        _rewind_visible_state: bool
        _rewind_selected_index: int
        _on_rewind: Callable[[Any], Any] | None
        _submit_tasks: set[asyncio.Task[Any]]
        _evidence_model: EvidenceLinkModel | None
        _evidence_answer_id: str | None
        _evidence_selected_index: int
        _evidence_visible_state: bool
        _needs_you: NeedsYouQueue | None

        def _terminal_size(self) -> tuple[int, int]: ...

        def _palette_snapshot(self) -> PaletteSnapshot: ...

        def _rewind_entries(self) -> tuple[TurnOutcome, ...]: ...

        def _dismiss_rewind(self) -> None: ...

        def _evidence_links(self) -> tuple[EvidenceLink, ...]: ...

        def _dismiss_evidence(self) -> None: ...

        def submit_current_input(self) -> None: ...

        def _submission_done(self, task: asyncio.Task[object]) -> None: ...

        def _emit_ui_event(self, event: UiEvent) -> None: ...


class LayeredReplNavigationMixin:
    def show_shortcut_help(self: _LayeredReplNavigationOwner) -> None:
        self._notices.show(
            "drag copy · shift-drag native select · ctrl-j newline · "
            "shift-tab mode · ctrl-p permission · ctrl-t tasks · ctrl-o tool · "
            "ctrl-l ledger · ctrl-r rewind · ctrl-y decisions · ctrl-e evidence · "
            "ctrl-d exit"
        )

    def _palette_snapshot(self: _LayeredReplNavigationOwner):
        text = self.input_buffer.text
        if self._palette_dismissed_text is not None:
            if text == self._palette_dismissed_text:
                return self._palette.query("")
            self._palette_dismissed_text = None
            self._palette_selected_index = 0
        if not text.startswith("/") or any(character.isspace() for character in text):
            return self._palette.query("")
        return self._palette.query(text, selected_index=self._palette_selected_index)

    def _palette_visible(self: _LayeredReplNavigationOwner) -> bool:
        return not self._tasks_visible and bool(self._palette_snapshot().commands)

    def _palette_height(self: _LayeredReplNavigationOwner) -> Dimension:
        snapshot = self._palette_snapshot()
        lines = len(snapshot.commands)
        if snapshot.query == "/":
            lines += len({command.phase for command in snapshot.commands})
        return Dimension.exact(lines)

    def _palette_text(self: _LayeredReplNavigationOwner) -> FormattedText:
        snapshot = self._palette_snapshot()
        width = max(1, self._terminal_size()[1])
        show_headers = snapshot.query == "/"
        name_cells = min(24, max(12, width // 4))
        fragments: list[tuple[str, str]] = []
        current_phase = None
        for index, command in enumerate(snapshot.commands):
            if fragments:
                fragments.append(("", "\n"))
            if show_headers and command.phase is not current_phase:
                current_phase = command.phase
                fragments.append(
                    ("class:palette.phase", f"  {command.phase.value.upper()}")
                )
                fragments.append(("", "\n"))
            selected = index == snapshot.selected_index
            row = "class:palette.selected" if selected else "class:palette"
            marker = "›" if selected else " "
            name = summarize_cell_text(command.name, max_cells=name_cells)
            name += " " * max(0, name_cells - get_cwidth(name))
            source = f"[{command.source.value}]"
            prefix = f"{marker} {name}  "
            budget = max(0, width - get_cwidth(prefix) - get_cwidth(source) - 2)
            description = (
                summarize_cell_text(command.description, max_cells=budget)
                if budget
                else ""
            )
            pad = " " * max(
                1, width - get_cwidth(prefix + description) - get_cwidth(source)
            )
            fragments.append((row, f"{marker} "))
            fragments.append((f"{row} class:palette.command", name))
            fragments.append((row if selected else "class:palette", f"  {description}"))
            fragments.append((row, pad))
            fragments.append((f"{row} class:palette.source", source))
        return FormattedText(fragments)

    def _move_palette(self: _LayeredReplNavigationOwner, delta: int) -> None:
        snapshot = self._palette.move(self._palette_snapshot(), delta)
        self._palette_selected_index = snapshot.selected_index
        self.application.invalidate()

    def _accept_palette_selection(self: _LayeredReplNavigationOwner) -> None:
        selected = self._palette_snapshot().selected
        if selected is None:
            return
        self.input_buffer.set_document(
            Document(selected.name, cursor_position=len(selected.name))
        )
        self._palette_selected_index = 0
        self._palette_dismissed_text = selected.name
        self.submit_current_input()

    def _dismiss_palette(self: _LayeredReplNavigationOwner) -> None:
        self._palette_dismissed_text = self.input_buffer.text
        self.application.invalidate()

    def open_rewind_picker(self: _LayeredReplNavigationOwner) -> bool:
        if self._outcome_ledger is None or not self._outcome_ledger.entries:
            self._notices.show("no rewind checkpoints yet")
            return False
        self._rewind_visible_state = True
        self._rewind_selected_index = len(self._rewind_entries()) - 1
        self.application.invalidate()
        return True

    def _rewind_entries(self: _LayeredReplNavigationOwner):
        if self._outcome_ledger is None:
            return ()
        return self._outcome_ledger.entries[-8:]

    def _rewind_visible(self: _LayeredReplNavigationOwner) -> bool:
        return self._rewind_visible_state and bool(self._rewind_entries())

    def _rewind_text(self: _LayeredReplNavigationOwner) -> FormattedText:
        entries = self._rewind_entries()
        if not entries:
            return FormattedText()
        entry = entries[self._rewind_selected_index]
        outcome = entry.yield_summary or "no recorded yield"
        dimmer = f"fg:{TOKENS['dimmer']}"
        tail: list[tuple[str, str]] = [
            (dimmer, " · ‹ › move · "),
            ("class:selected", " enter fork "),
            (dimmer, " · esc close"),
        ]
        tail_cells = sum(get_cwidth(text) for _, text in tail)
        head = summarize_cell_text(
            f"  rewind › {entry.checkpoint_id} · ${entry.cost:.2f} · {outcome}",
            max_cells=max(1, self._terminal_size()[1] - tail_cells),
        )
        return FormattedText([("class:rewind", head), *tail])

    def _move_rewind(self: _LayeredReplNavigationOwner, delta: int) -> None:
        entries = self._rewind_entries()
        if not entries:
            return
        self._rewind_selected_index = (self._rewind_selected_index + delta) % len(
            entries
        )
        self.application.invalidate()

    def _dismiss_rewind(self: _LayeredReplNavigationOwner) -> None:
        self._rewind_visible_state = False
        self.application.invalidate()

    def _accept_rewind(self: _LayeredReplNavigationOwner) -> None:
        entries = self._rewind_entries()
        if not entries:
            return
        outcome = entries[self._rewind_selected_index]
        self._dismiss_rewind()
        if self._on_rewind is None:
            self._notices.show("rewind callback is unavailable")
            return
        result = self._on_rewind(outcome)
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            self._submit_tasks.add(task)
            task.add_done_callback(self._submission_done)

    def open_evidence_picker(self: _LayeredReplNavigationOwner) -> bool:
        if self._evidence_model is None or not self._evidence_model.answer_ids:
            self._notices.show("no answer evidence yet")
            return False
        answer_id = self._evidence_model.answer_ids[-1]
        snapshot = self._evidence_model.reveal(answer_id)
        if snapshot is None or not snapshot.links:
            self._notices.show("latest answer has no supported evidence claims")
            return False
        claims = {claim.claim_id: claim for claim in snapshot.claims}
        evidence_lines = []
        for link in snapshot.links:
            claim = claims.get(link.claim_id)
            tool = self._evidence_model.resolve(answer_id, link.number)
            claim_text = " ".join(claim.text.split()) if claim is not None else "claim"
            summary = tool.summary if tool is not None else link.tool_call_id
            evidence_lines.append(f"{link.marker} {claim_text} -> {summary}")
        self._emit_ui_event(AnswerBlock("\n".join(evidence_lines), label="Evidence"))
        self._evidence_answer_id = answer_id
        self._evidence_selected_index = 0
        self._evidence_visible_state = True
        self.application.invalidate()
        return True

    def _evidence_links(self: _LayeredReplNavigationOwner):
        if self._evidence_model is None or self._evidence_answer_id is None:
            return ()
        snapshot = self._evidence_model.reveal(self._evidence_answer_id)
        return snapshot.links if snapshot is not None else ()

    def _evidence_visible(self: _LayeredReplNavigationOwner) -> bool:
        return self._evidence_visible_state and bool(self._evidence_links())

    def _evidence_text(self: _LayeredReplNavigationOwner) -> FormattedText:
        links = self._evidence_links()
        model = self._evidence_model
        answer_id = self._evidence_answer_id
        if not links or model is None or answer_id is None:
            return FormattedText()
        link = links[self._evidence_selected_index]
        tool = model.resolve(answer_id, link.number)
        summary = tool.summary if tool is not None else link.tool_call_id
        text = (
            f"  evidence {self._evidence_selected_index + 1}/{len(links)} · "
            f"{link.marker} {summary} · ←/→ select · enter expand · esc close"
        )
        return FormattedText(
            [
                (
                    "class:evidence",
                    summarize_cell_text(text, max_cells=self._terminal_size()[1]),
                )
            ]
        )

    def _move_evidence(self: _LayeredReplNavigationOwner, delta: int) -> None:
        links = self._evidence_links()
        if not links:
            return
        self._evidence_selected_index = (self._evidence_selected_index + delta) % len(
            links
        )
        self.application.invalidate()

    def _dismiss_evidence(self: _LayeredReplNavigationOwner) -> None:
        self._evidence_visible_state = False
        self.application.invalidate()

    def _accept_evidence(self: _LayeredReplNavigationOwner) -> None:
        links = self._evidence_links()
        model = self._evidence_model
        answer_id = self._evidence_answer_id
        if not links or model is None or answer_id is None:
            return
        link = links[self._evidence_selected_index]
        tool = model.resolve(answer_id, link.number)
        self._dismiss_evidence()
        if tool is None:
            self._notices.show("evidence tool is no longer available")
            return
        self._emit_ui_event(tool_block_from_activity(tool, expanded=True))

    def show_ledger(self: _LayeredReplNavigationOwner) -> None:
        if self._outcome_ledger is None or not self._outcome_ledger.entries:
            self._notices.show("session ledger is empty")
            return
        summary = self._outcome_ledger.summary()
        headline = (
            f"{summary.turns} turns · ${summary.session_cost:.2f} · "
            f"{summary.shipped_turns} shipped · "
            f"{summary.answer_only_turns} answer-only · "
            f"{summary.interrupted_turns} interrupted"
        )
        details: list[str] = []
        if summary.cheapest_shipped_cost is not None:
            details.append(
                f"cheapest shipped diff ${summary.cheapest_shipped_cost:.2f}"
            )
        if summary.dearest_shipped_cost is not None:
            details.append(f"dearest ${summary.dearest_shipped_cost:.2f}")
        if summary.cache_hit_percent is not None:
            details.append(f"cache hit {summary.cache_hit_percent}%")
        markdown = headline
        if details:
            markdown += "\n" + " · ".join(details)
        self._emit_ui_event(AnswerBlock(markdown, label="Session ledger"))

    def show_needs_you(self: _LayeredReplNavigationOwner) -> None:
        if self._needs_you is None or not self._needs_you.pending:
            self._notices.show("no decisions waiting")
            return
        lines = [
            f"{decision.decision_id}. {decision.question} ({decision.reason})"
            for index, decision in enumerate(self._needs_you.pending, start=1)
        ]
        lines.append("/answer decision-1=yes; decision-2=not yet")
        self._emit_ui_event(AnswerBlock("\n".join(lines), label="Needs you"))


__all__ = ["LayeredReplNavigationMixin"]
