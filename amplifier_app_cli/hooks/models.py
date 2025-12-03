"""Hook system data models.

Defines the core types for the enhanced hooks system:
- HookType: Types of hook handlers
- HookConfig: Configuration for a hook
- HookMatcher: Conditions for when a hook should fire
- HookResult: Result from hook execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


class HookType(str, Enum):
    """Type of hook handler.

    Types:
    - INTERNAL: Python async function
    - COMMAND: External shell command
    - LLM: AI-powered hook using an LLM
    """

    INTERNAL = "internal"
    COMMAND = "command"
    LLM = "llm"


@dataclass
class HookMatcher:
    """Conditions for when a hook should fire.

    All specified conditions must match (AND logic).
    Empty matcher matches everything.

    Attributes:
        tools: List of tool names to match (empty = all tools)
        events: List of event names to match (empty = all events)
        path_patterns: Glob patterns for file paths
        command_patterns: Patterns for bash commands
        session_types: Session types to match (root, subagent)
    """

    tools: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    path_patterns: list[str] = field(default_factory=list)
    command_patterns: list[str] = field(default_factory=list)
    session_types: list[str] = field(default_factory=list)

    def matches(self, event: str, data: dict[str, Any]) -> bool:
        """Check if this matcher matches the event and data.

        Args:
            event: Event name
            data: Event data

        Returns:
            True if all conditions match
        """
        # Check event
        if self.events and event not in self.events:
            return False

        # Check tool
        tool = data.get("tool")
        if self.tools and tool and tool not in self.tools:
            return False

        # Check path patterns
        if self.path_patterns:
            path = data.get("path") or data.get("args", {}).get("path")
            if path:
                import fnmatch
                if not any(fnmatch.fnmatch(path, p) for p in self.path_patterns):
                    return False
            else:
                return False  # Pattern specified but no path

        # Check command patterns
        if self.command_patterns:
            command = data.get("command") or data.get("args", {}).get("command")
            if command:
                import fnmatch
                if not any(fnmatch.fnmatch(command, p) for p in self.command_patterns):
                    return False
            else:
                return False  # Pattern specified but no command

        # Check session type
        if self.session_types:
            parent_id = data.get("parent_id") or data.get("parent_session_id")
            session_type = "subagent" if parent_id else "root"
            if session_type not in self.session_types:
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        result = {}
        if self.tools:
            result["tools"] = self.tools
        if self.events:
            result["events"] = self.events
        if self.path_patterns:
            result["path_patterns"] = self.path_patterns
        if self.command_patterns:
            result["command_patterns"] = self.command_patterns
        if self.session_types:
            result["session_types"] = self.session_types
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookMatcher:
        """Create from dictionary."""
        return cls(
            tools=data.get("tools", []),
            events=data.get("events", []),
            path_patterns=data.get("path_patterns", []),
            command_patterns=data.get("command_patterns", []),
            session_types=data.get("session_types", []),
        )


@dataclass
class HookConfig:
    """Configuration for a hook.

    Attributes:
        name: Unique name for the hook
        type: Type of hook (internal, command, llm)
        matcher: Conditions for when to fire
        command: Shell command (for command hooks)
        script: Path to script file (for command hooks)
        prompt: LLM prompt template (for llm hooks)
        timeout: Timeout in seconds
        priority: Execution priority (lower = earlier)
        enabled: Whether hook is active
        description: Human-readable description
    """

    name: str
    type: HookType
    matcher: HookMatcher = field(default_factory=HookMatcher)
    command: str | None = None
    script: str | None = None
    prompt: str | None = None
    timeout: float = 30.0
    priority: int = 100
    enabled: bool = True
    description: str | None = None

    def __post_init__(self):
        """Validate configuration."""
        if self.type == HookType.COMMAND and not (self.command or self.script):
            raise ValueError("Command hook requires 'command' or 'script'")
        if self.type == HookType.LLM and not self.prompt:
            raise ValueError("LLM hook requires 'prompt'")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.type.value,
            "timeout": self.timeout,
            "priority": self.priority,
            "enabled": self.enabled,
        }
        if self.matcher.to_dict():
            result["matcher"] = self.matcher.to_dict()
        if self.command:
            result["command"] = self.command
        if self.script:
            result["script"] = self.script
        if self.prompt:
            result["prompt"] = self.prompt
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookConfig:
        """Create from dictionary."""
        return cls(
            name=data["name"],
            type=HookType(data.get("type", "internal")),
            matcher=HookMatcher.from_dict(data.get("matcher", {})),
            command=data.get("command"),
            script=data.get("script"),
            prompt=data.get("prompt"),
            timeout=data.get("timeout", 30.0),
            priority=data.get("priority", 100),
            enabled=data.get("enabled", True),
            description=data.get("description"),
        )


@dataclass
class HookResult:
    """Result from hook execution.

    Attributes:
        action: Action to take (continue, deny, modify)
        reason: Explanation for the action
        modified_data: Modified event data (for modify action)
        output: Any output from the hook
        error: Error message if hook failed
        duration_ms: Execution time
    """

    action: str = "continue"
    reason: str | None = None
    modified_data: dict[str, Any] | None = None
    output: Any = None
    error: str | None = None
    duration_ms: float | None = None

    @classmethod
    def continue_(cls, reason: str | None = None) -> HookResult:
        """Create a continue result."""
        return cls(action="continue", reason=reason)

    @classmethod
    def deny(cls, reason: str) -> HookResult:
        """Create a deny result."""
        return cls(action="deny", reason=reason)

    @classmethod
    def modify(cls, data: dict[str, Any], reason: str | None = None) -> HookResult:
        """Create a modify result."""
        return cls(action="modify", modified_data=data, reason=reason)

    @classmethod
    def error(cls, message: str) -> HookResult:
        """Create an error result."""
        return cls(action="continue", error=message)


# Type alias for hook handlers
HookHandler = Callable[[str, dict[str, Any]], Awaitable[HookResult]]
