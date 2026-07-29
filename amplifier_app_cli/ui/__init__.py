"""UI implementations for CLI environment."""

from .approval import CLIApprovalSystem
from .display import CLIDisplaySystem
from .message_renderer import render_message
from .ui_events import UiEvent
from .ui_events import UiEventDispatcher
from .scope import (
    is_scope_change_available,
    print_scope_indicator,
    prompt_scope_change,
    validate_scope_cli,
)

__all__ = [
    "CLIApprovalSystem",
    "CLIDisplaySystem",
    "render_message",
    "UiEvent",
    "UiEventDispatcher",
    "is_scope_change_available",
    "print_scope_indicator",
    "prompt_scope_change",
    "validate_scope_cli",
]
