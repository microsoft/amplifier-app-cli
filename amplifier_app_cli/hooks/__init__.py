"""Enhanced hooks system for Amplifier CLI.

Provides an extensible event system that integrates with the kernel's hook
mechanism while adding CLI-specific features:

- Additional event types (session lifecycle, notifications, etc.)
- External command hooks (shell commands as hook handlers)
- LLM-based hooks (Claude as hook handler)
- Hook configuration loading from settings.yaml
- Session-scoped hook state management

Event Types:
- PreToolUse: Before tool execution
- PostToolUse: After tool execution  
- Notification: For displaying messages to user
- Stop: Session termination
- SubagentStop: Subagent session termination
- SessionStart: Session initialization
- SessionEnd: Session cleanup

Hook Types:
- Internal: Python async functions
- Command: External shell commands
- LLM: AI-powered decision making
"""

from .events import (
    # Event names
    PRE_TOOL_USE,
    POST_TOOL_USE,
    NOTIFICATION,
    STOP,
    SUBAGENT_STOP,
    SESSION_START,
    SESSION_END,
    # Kernel events (re-exported)
    PROMPT_START,
    PROMPT_COMPLETE,
    TOOL_PRE,
    TOOL_POST,
    # Event data models
    ToolUseEvent,
    NotificationEvent,
    SessionEvent,
)
from .models import (
    HookType,
    HookConfig,
    HookMatcher,
    HookResult,
)
from .external import (
    ExternalCommandHook,
    create_external_hook,
)
from .config import (
    HooksConfig,
    load_hooks_config,
)
from .manager import HooksManager

__all__ = [
    # Events
    "PRE_TOOL_USE",
    "POST_TOOL_USE", 
    "NOTIFICATION",
    "STOP",
    "SUBAGENT_STOP",
    "SESSION_START",
    "SESSION_END",
    "PROMPT_START",
    "PROMPT_COMPLETE",
    "TOOL_PRE",
    "TOOL_POST",
    # Event data
    "ToolUseEvent",
    "NotificationEvent",
    "SessionEvent",
    # Models
    "HookType",
    "HookConfig",
    "HookMatcher",
    "HookResult",
    # External hooks
    "ExternalCommandHook",
    "create_external_hook",
    # Config
    "HooksConfig",
    "load_hooks_config",
    # Manager
    "HooksManager",
]
