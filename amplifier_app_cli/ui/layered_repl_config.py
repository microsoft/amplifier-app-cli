"""Typed construction contracts for the layered interactive terminal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.input.base import Input
from prompt_toolkit.output.base import Output

from .clipboard import ChatSubmission
from .clipboard_availability import ClipboardImageAvailabilityDetector
from .command_registry import CommandRegistry
from .evidence_links import EvidenceLinkModel
from .interaction_state import NeedsYouQueue
from .interaction_state import SteeringQueue
from .interaction_state import TrustState
from .notices import TransientNoticeState
from .outcome_ledger import OutcomeLedger
from .outcome_ledger import TurnOutcome
from .runtime_status import RuntimeStatusTracker
from .stream_status import StreamStatusTracker
from .task_status import TaskStatusTracker
from .ui_events import UiEventDispatcher


ModelNames = Iterable[str] | Callable[[], Iterable[str]]


@dataclass(frozen=True, slots=True)
class LayeredReplCompletion:
    """Immutable command discovery and dynamic argument-value suppliers."""

    registry: CommandRegistry
    mode_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    model_names: ModelNames | None = None


@dataclass(frozen=True, slots=True)
class LayeredReplConfig:
    """Static identity, terminal, history, and completion configuration."""

    history_path: Path
    completion: LayeredReplCompletion
    bundle_name: str = "unknown"
    session_id: str | None = None
    max_output_lines: int = 260
    output: Output | None = None
    input: Input | None = None


@dataclass(frozen=True, slots=True)
class LayeredReplBindings:
    """Runtime queries and actions owned by the interactive session."""

    on_submit: Callable[[ChatSubmission], Awaitable[None] | None]
    on_interrupt: Callable[[], bool] | None = None
    on_exit: Callable[[], None] | None = None
    get_active_mode: Callable[[], str | None] | None = None
    get_render_profile: Callable[[], str] | None = None
    get_is_running: Callable[[], bool] | None = None
    get_queued_count: Callable[[], int] | None = None
    get_task_title: Callable[[], str | None] | None = None
    on_cycle_mode: Callable[[], object] | None = None
    on_rewind: Callable[[TurnOutcome], object] | None = None


@dataclass(frozen=True, slots=True)
class LayeredReplServices:
    """Live state and adapters observed by the layered terminal."""

    task_tracker: TaskStatusTracker | None = None
    stream_status: StreamStatusTracker | None = None
    runtime_status: RuntimeStatusTracker | None = None
    notice_state: TransientNoticeState | None = None
    trust_state: TrustState | None = None
    outcome_ledger: OutcomeLedger | None = None
    needs_you: NeedsYouQueue | None = None
    steering_queue: SteeringQueue | None = None
    evidence_model: EvidenceLinkModel | None = None
    event_dispatcher: UiEventDispatcher | None = None
    clipboard_detector: ClipboardImageAvailabilityDetector | None = None


__all__ = [
    "LayeredReplBindings",
    "LayeredReplCompletion",
    "LayeredReplConfig",
    "LayeredReplServices",
    "ModelNames",
]
