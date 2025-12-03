"""Enhanced hooks system for Amplifier CLI.

Provides an extensible event system that integrates with the kernel's hook
mechanism while adding CLI-specific features:

- Additional event types (session lifecycle, notifications, errors, etc.)
- External command hooks (shell commands as hook handlers)
- LLM-based hooks (AI-powered decision making)
- Inline hooks (pattern-based rules)
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
- Error: Error events
- Checkpoint: Checkpoint events
- ModelSwitch: Model switch events
- MemoryUpdate: Memory file updates

Hook Types:
- Internal: Python async functions
- Command: External shell commands
- LLM: AI-powered decision making
- Inline: Pattern-based rules
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
    # Phase 2 events
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
    # Kernel events (re-exported)
    PROMPT_START,
    PROMPT_COMPLETE,
    TOOL_PRE,
    TOOL_POST,
    # Event data models
    ToolUseEvent,
    NotificationEvent,
    SessionEvent,
    ErrorEvent,
    CheckpointEvent,
    ModelSwitchEvent,
    MemoryUpdateEvent,
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
from .integration import (
    ToolExecutionHooks,
    SessionLifecycleHooks,
    ToolDeniedError,
)

__all__ = [
    # Events
    "PRE_TOOL_USE",
    "POST_TOOL_USE", 
    "NOTIFICATION",
    "STOP",
    "SUBAGENT_STOP",
    "SESSION_START",
    "SESSION_END",
    "ERROR",
    "CHECKPOINT",
    "MODEL_SWITCH",
    "MEMORY_UPDATE",
    "PROMPT_START",
    "PROMPT_COMPLETE",
    "TOOL_PRE",
    "TOOL_POST",
    # Event data
    "ToolUseEvent",
    "NotificationEvent",
    "SessionEvent",
    "ErrorEvent",
    "CheckpointEvent",
    "ModelSwitchEvent",
    "MemoryUpdateEvent",
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
    # Integration
    "ToolExecutionHooks",
    "SessionLifecycleHooks",
    "ToolDeniedError",
]
