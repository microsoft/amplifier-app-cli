# Phase 2 Architecture: Enhanced Hooks System

## Executive Summary

Phase 2 completes the Enhanced Hooks System (Issue #18) by adding:
1. **4 Missing Event Types**: Error, Checkpoint, ModelSwitch, MemoryUpdate
2. **LLM Hooks**: AI-powered decision making for hook actions
3. **Inline Matcher Hooks**: Simple pattern-based hooks without external commands
4. **Integration Points**: Hooks wired into tool execution and session lifecycle
5. **Integration Tests**: End-to-end validation

**Design Philosophy**: Ruthless simplicity. Each component does one thing well. Clear contracts. Easy to regenerate.

---

## 1. Missing Event Types Design

### 1.1 Event Type Specifications

#### Error Event
**Purpose**: Fire when errors occur during tool execution or session operations

**Event Name**: `ERROR` / `Error`

**Data Model**:
```python
@dataclass
class ErrorEvent:
    """Event data for error events.
    
    Fired when exceptions or errors occur during operations.
    Allows hooks to log, notify, or block operations based on errors.
    """
    error_type: str              # Exception type name
    error_message: str           # Error message
    tool: str | None = None      # Tool that caused error (if applicable)
    session_id: str | None = None
    stack_trace: str | None = None
    severity: Literal["warning", "error", "critical"] = "error"
    timestamp: datetime = field(default_factory=datetime.now)
```

**Matcher Fields**:
- `events: ["Error"]`
- `tools: [tool_name]` - Match errors from specific tools
- No new matcher fields needed

**Use Cases**:
- Log all errors to external monitoring
- Notify on critical errors
- Auto-retry on specific error types
- Block operations after repeated errors

---

#### Checkpoint Event
**Purpose**: Fire when session checkpoints are created

**Event Name**: `CHECKPOINT` / `Checkpoint`

**Data Model**:
```python
@dataclass
class CheckpointEvent:
    """Event data for checkpoint events.
    
    Fired when session state is checkpointed.
    Allows hooks to backup, sync, or validate checkpoints.
    """
    checkpoint_id: str           # Unique checkpoint identifier
    session_id: str
    checkpoint_type: Literal["auto", "manual", "periodic"] = "auto"
    message_count: int = 0       # Messages since last checkpoint
    duration_since_last_ms: float | None = None
    storage_path: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
```

**Matcher Fields**:
- `events: ["Checkpoint"]`
- No new matcher fields needed

**Use Cases**:
- Sync checkpoints to cloud storage
- Create backups of important sessions
- Notify on checkpoint frequency (too many/few)
- Validate checkpoint integrity

---

#### ModelSwitch Event
**Purpose**: Fire when LLM model changes during session

**Event Name**: `MODEL_SWITCH` / `ModelSwitch`

**Data Model**:
```python
@dataclass
class ModelSwitchEvent:
    """Event data for model switch events.
    
    Fired when the active LLM model changes.
    Allows hooks to log usage, enforce policies, or notify.
    """
    old_model: str | None        # Previous model name
    new_model: str               # New model name
    reason: str | None = None    # Why model switched
    session_id: str | None = None
    profile: str | None = None   # Active profile
    triggered_by: Literal["user", "automatic", "fallback"] = "user"
    timestamp: datetime = field(default_factory=datetime.now)
```

**Matcher Fields**:
- `events: ["ModelSwitch"]`
- Add new matcher field: `model_patterns: list[str]` - Match specific models

**Use Cases**:
- Log model usage for cost tracking
- Enforce model policies (e.g., no expensive models in CI)
- Notify on expensive model switches
- Block specific model combinations

---

#### MemoryUpdate Event
**Purpose**: Fire when memory files (AGENTS.md, etc.) are modified

**Event Name**: `MEMORY_UPDATE` / `MemoryUpdate`

**Data Model**:
```python
@dataclass
class MemoryUpdateEvent:
    """Event data for memory update events.
    
    Fired when memory files are modified.
    Allows hooks to sync, backup, or validate memory changes.
    """
    file_path: str               # Path to memory file
    update_type: Literal["created", "modified", "deleted"] = "modified"
    session_id: str | None = None
    content_size: int | None = None
    previous_hash: str | None = None
    new_hash: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
```

**Matcher Fields**:
- `events: ["MemoryUpdate"]`
- `path_patterns: ["*.md", "**/AGENTS.md"]` - Match specific files

**Use Cases**:
- Sync AGENTS.md across projects
- Backup memory files before changes
- Validate memory file structure
- Notify team on memory updates

---

### 1.2 Module Structure for Event Types

**Module**: `amplifier_app_cli/hooks/events.py` (MODIFY EXISTING)

**Changes**:
1. Add 4 new event constants
2. Add 4 new dataclass definitions
3. Update EVENT_DATA_TYPES mapping
4. No breaking changes to existing code

**Interface Contract**:
```python
# New event constants
ERROR = "Error"
CHECKPOINT = "Checkpoint"
MODEL_SWITCH = "ModelSwitch"
MEMORY_UPDATE = "MemoryUpdate"

# New data classes
class ErrorEvent(...)
class CheckpointEvent(...)
class ModelSwitchEvent(...)
class MemoryUpdateEvent(...)

# Updated mapping
EVENT_DATA_TYPES = {
    # ... existing ...
    ERROR: ErrorEvent,
    CHECKPOINT: CheckpointEvent,
    MODEL_SWITCH: ModelSwitchEvent,
    MEMORY_UPDATE: MemoryUpdateEvent,
}
```

---

## 2. LLM Hooks Implementation

### 2.1 Design Philosophy

**Simple, Optional, Fast**:
- LLM hooks are OPTIONAL - don't require LLM for basic functionality
- Use fast models (claude-3-haiku) by default
- Simple prompt templating (no complex engines)
- Basic caching for identical requests
- Clear error handling when LLM unavailable

### 2.2 Architecture

**Module**: `amplifier_app_cli/hooks/llm.py` (NEW)

**Purpose**: Execute hooks using LLM to make decisions

**Key Components**:

1. **LLMHookExecutor** - Main handler class
2. **PromptTemplate** - Simple template processor
3. **ResponseCache** - Hash-based caching
4. **LLMHookConfig** - Extended configuration

### 2.3 Class Structure

```python
class PromptTemplate:
    """Simple template processor for LLM prompts.
    
    Supports {{variable}} syntax for substitution.
    No complex logic - just string replacement.
    """
    
    def __init__(self, template: str):
        self.template = template
    
    def render(self, context: dict[str, Any]) -> str:
        """Render template with context variables."""
        # Simple {{var}} replacement
        pass

class ResponseCache:
    """Hash-based cache for LLM responses.
    
    Caches based on (prompt_hash, event_hash).
    Simple TTL-based expiration.
    """
    
    def __init__(self, ttl_seconds: int = 300):
        self.cache: dict[str, tuple[HookResult, float]] = {}
        self.ttl_seconds = ttl_seconds
    
    def get(self, prompt: str, data: dict) -> HookResult | None:
        """Get cached result if available and fresh."""
        pass
    
    def put(self, prompt: str, data: dict, result: HookResult):
        """Cache result with timestamp."""
        pass
    
    def cleanup(self):
        """Remove expired entries."""
        pass

class LLMHookExecutor:
    """Execute LLM hooks using PydanticAI.
    
    Renders prompt template, calls LLM, parses response.
    Handles caching and errors gracefully.
    """
    
    def __init__(
        self,
        config: HookConfig,
        model_name: str = "claude-3-5-haiku-20241022",
        cache_enabled: bool = True,
    ):
        self.config = config
        self.model_name = model_name
        self.template = PromptTemplate(config.prompt)
        self.cache = ResponseCache() if cache_enabled else None
    
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Execute LLM hook.
        
        1. Check cache
        2. Render prompt
        3. Call LLM
        4. Parse response
        5. Cache result
        """
        pass
    
    async def _call_llm(self, prompt: str) -> str:
        """Call LLM with prompt using PydanticAI."""
        # Use pydantic_ai.Agent with structured output
        pass
    
    def _parse_response(self, response: str) -> HookResult:
        """Parse LLM response into HookResult.
        
        Expected format:
        {
            "action": "continue" | "deny" | "modify",
            "reason": "explanation",
            "modified_data": {...}  // optional
        }
        """
        pass
```

### 2.4 Prompt Template Format

**System Prompt** (hardcoded for security):
```
You are a security and policy enforcement assistant for the Amplifier CLI.
Analyze the tool call or event and decide whether to:
- "continue": Allow the action
- "deny": Block the action with reason
- "modify": Modify the action data

Respond with JSON only: {"action": "...", "reason": "...", "modified_data": {...}}
```

**User Prompt** (from config):
```yaml
prompt: |
  Event: {{event}}
  Tool: {{tool}}
  Arguments: {{args}}
  
  Check if this bash command is safe:
  {{args.command}}
  
  Deny if it contains destructive operations like rm -rf or dd.
```

**Template Variables Available**:
- `{{event}}` - Event name
- `{{tool}}` - Tool name
- `{{args}}` - Tool arguments (as JSON)
- `{{session_id}}` - Session ID
- Any field from event data

### 2.5 Integration with Manager

**Module**: `amplifier_app_cli/hooks/manager.py` (MODIFY)

**Changes**:
```python
def _create_handler(self, config: HookConfig) -> Callable | None:
    # ... existing command handler ...
    
    elif config.type == HookType.LLM:
        # Try to create LLM hook
        try:
            from .llm import LLMHookExecutor
            model = self._get_llm_model()  # From settings or default
            return LLMHookExecutor(config, model_name=model)
        except ImportError:
            logger.warning(f"LLM dependencies not available for {config.name}")
            return None
        except Exception as e:
            logger.error(f"Failed to create LLM hook {config.name}: {e}")
            return None
```

### 2.6 Configuration Example

```yaml
hooks:
  definitions:
    - name: llm-bash-safety
      type: llm
      prompt: |
        Analyze this bash command for safety:
        Command: {{args.command}}
        
        Deny if destructive (rm -rf, dd, mkfs, etc.)
      matcher:
        events: [PreToolUse]
        tools: [bash]
      timeout: 10
      priority: 10  # Run early
```

---

## 3. Inline Matcher Hooks

### 3.1 Design Philosophy

**Simple Pattern Matching**:
- No external commands needed
- Direct pattern matching on event data
- Simpler than external hooks, more powerful than basic matchers
- Inline rules in configuration

### 3.2 Architecture

**Module**: `amplifier_app_cli/hooks/inline.py` (NEW)

**Purpose**: Execute hooks with inline matching rules and actions

**Key Components**:

1. **InlineRule** - Pattern and action pair
2. **InlineMatcher** - Pattern matching engine
3. **InlineHookExecutor** - Execute inline rules

### 3.3 Class Structure

```python
@dataclass
class InlineRule:
    """Inline rule for pattern matching and action.
    
    Example:
        tool=bash args.command=rm * -> deny("Dangerous command")
    """
    
    # Pattern matching
    field: str                    # e.g., "args.command", "tool", "path"
    operator: str                 # e.g., "equals", "contains", "matches", "glob"
    value: str                    # Pattern value
    
    # Action
    action: str                   # "continue", "deny", "modify"
    reason: str | None = None
    modify_field: str | None = None
    modify_value: Any = None

class InlineMatcher:
    """Pattern matching engine for inline rules.
    
    Supports:
    - Equality: field equals value
    - Contains: field contains substring
    - Glob: field matches glob pattern
    - Regex: field matches regex
    """
    
    def matches(self, rule: InlineRule, data: dict[str, Any]) -> bool:
        """Check if rule matches data."""
        pass
    
    def _get_field_value(self, field: str, data: dict) -> Any:
        """Get nested field value (e.g., 'args.command')."""
        pass

class InlineHookExecutor:
    """Execute inline matcher hooks.
    
    Evaluates rules in order until one matches.
    Returns action from first matching rule.
    """
    
    def __init__(self, config: HookConfig):
        self.config = config
        self.rules: list[InlineRule] = []
        self._parse_rules()
    
    def _parse_rules(self):
        """Parse rules from config.inline_rules."""
        pass
    
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Execute inline rules.
        
        1. Check each rule in order
        2. Return action from first match
        3. Return continue if no match
        """
        pass
```

### 3.4 Configuration Format

**Option 1: Simple Rules** (Recommended)
```yaml
hooks:
  definitions:
    - name: block-dangerous-bash
      type: inline
      matcher:
        events: [PreToolUse]
        tools: [bash]
      inline_rules:
        - field: args.command
          operator: contains
          value: "rm -rf"
          action: deny
          reason: "Dangerous rm -rf command blocked"
        
        - field: args.command
          operator: glob
          value: "rm *"
          action: deny
          reason: "Bulk delete blocked"
```

**Option 2: DSL Syntax** (Future enhancement)
```yaml
hooks:
  definitions:
    - name: block-dangerous-bash
      type: inline
      matcher:
        events: [PreToolUse]
        tools: [bash]
      rules: |
        args.command contains "rm -rf" -> deny("Dangerous command")
        args.command glob "rm *" -> deny("Bulk delete")
        tool equals "write_file" and path glob "*.py" -> continue()
```

### 3.5 Integration with Models

**Module**: `amplifier_app_cli/hooks/models.py` (MODIFY)

**Changes**:
```python
@dataclass
class HookConfig:
    # ... existing fields ...
    
    # New field for inline rules
    inline_rules: list[dict[str, Any]] = field(default_factory=list)
    
    def __post_init__(self):
        # ... existing validation ...
        
        if self.type == HookType.INLINE and not self.inline_rules:
            raise ValueError("Inline hook requires 'inline_rules'")

class HookType(str, Enum):
    INTERNAL = "internal"
    COMMAND = "command"
    LLM = "llm"
    INLINE = "inline"  # NEW
```

---

## 4. Integration Points

### 4.1 Integration Strategy

**Minimal, Non-Invasive**:
- Single integration point per layer
- Wrapper functions that call hooks
- Existing code calls wrappers instead of direct calls
- No deep coupling to hooks system

### 4.2 Tool Execution Integration

**Module**: `amplifier_app_cli/hooks/integration.py` (NEW)

**Purpose**: Provide wrapper functions for integrating hooks into tool execution

```python
class ToolExecutionHooks:
    """Wrapper for tool execution with hooks.
    
    Integrates PreToolUse and PostToolUse hooks into tool calls.
    """
    
    def __init__(self, hooks_manager: HooksManager | None = None):
        self.hooks_manager = hooks_manager
    
    async def wrap_tool_call(
        self,
        session,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_fn: Callable,
    ) -> Any:
        """Wrap tool call with pre/post hooks.
        
        Flow:
        1. Emit PreToolUse event
        2. Check results for deny action
        3. Execute tool with potentially modified args
        4. Emit PostToolUse event
        5. Return result
        """
        if not self.hooks_manager:
            return await tool_fn(**tool_args)
        
        # Pre-tool hooks
        pre_event_data = {
            "tool": tool_name,
            "args": tool_args,
            "session_id": session.session_id,
        }
        
        pre_results = await self.hooks_manager.emit(
            session, PRE_TOOL_USE, pre_event_data
        )
        
        # Check for deny
        for result in pre_results:
            if result.action == "deny":
                raise ToolDeniedError(result.reason or "Tool call denied by hook")
            elif result.action == "modify" and result.modified_data:
                tool_args = result.modified_data.get("args", tool_args)
        
        # Execute tool
        error = None
        result = None
        start_time = time.time()
        
        try:
            result = await tool_fn(**tool_args)
        except Exception as e:
            error = e
            raise
        finally:
            # Post-tool hooks
            duration_ms = (time.time() - start_time) * 1000
            post_event_data = {
                "tool": tool_name,
                "args": tool_args,
                "result": result,
                "error": str(error) if error else None,
                "duration_ms": duration_ms,
                "session_id": session.session_id,
            }
            
            await self.hooks_manager.emit(
                session, POST_TOOL_USE, post_event_data
            )
        
        return result

class ToolDeniedError(Exception):
    """Raised when a hook denies a tool call."""
    pass
```

**Integration Points**:
- Kernel tool executor (if accessible)
- OR CLI tool command wrapper
- Wrap all tool.call() invocations

### 4.3 Session Lifecycle Integration

**Module**: `amplifier_app_cli/hooks/integration.py` (EXTEND)

```python
class SessionLifecycleHooks:
    """Wrapper for session lifecycle with hooks."""
    
    def __init__(self, hooks_manager: HooksManager | None = None):
        self.hooks_manager = hooks_manager
    
    async def on_session_start(
        self,
        session,
        profile: str | None = None,
        config: dict | None = None,
    ):
        """Fire SessionStart event."""
        if not self.hooks_manager:
            return
        
        event_data = {
            "session_id": session.session_id,
            "event_type": "start",
            "profile": profile,
            "config": config or {},
        }
        
        await self.hooks_manager.emit(session, SESSION_START, event_data)
    
    async def on_session_end(
        self,
        session,
        duration_ms: float | None = None,
        exit_reason: str | None = None,
    ):
        """Fire SessionEnd event."""
        if not self.hooks_manager:
            return
        
        event_data = {
            "session_id": session.session_id,
            "event_type": "end",
            "duration_ms": duration_ms,
            "exit_reason": exit_reason,
        }
        
        await self.hooks_manager.emit(session, SESSION_END, event_data)
    
    # Similar methods for other events...
    async def on_error(self, session, error: Exception, tool: str | None = None):
        """Fire Error event."""
        pass
    
    async def on_checkpoint(self, session, checkpoint_id: str, **kwargs):
        """Fire Checkpoint event."""
        pass
    
    async def on_model_switch(self, session, old_model: str, new_model: str, **kwargs):
        """Fire ModelSwitch event."""
        pass
    
    async def on_memory_update(self, session, file_path: str, update_type: str):
        """Fire MemoryUpdate event."""
        pass
```

**Integration Points**:
1. **main.py** - Session start/end in CLI entrypoint
2. **session_spawner.py** - Subagent lifecycle
3. **Tool execution** - Error events on exceptions
4. **Session checkpointing** - Checkpoint events
5. **Model switching** - ModelSwitch events
6. **File monitoring** - MemoryUpdate events (simple)

### 4.4 Integration Implementation Strategy

**Phase 2A: Core Integration**
1. Add ToolExecutionHooks wrapper
2. Add SessionLifecycleHooks wrapper
3. Wire into main.py for session lifecycle
4. Wire into one tool as proof of concept

**Phase 2B: Full Integration**
5. Wire into all tool execution paths
6. Add error event firing
7. Add checkpoint event firing
8. Add model switch event firing

**Phase 2C: Memory Updates** (Optional)
9. Add file watcher for memory files
10. Fire MemoryUpdate events

---

## 5. Module Boundaries and Contracts

### 5.1 Module Map

```
amplifier_app_cli/hooks/
├── __init__.py              # Public API exports
├── models.py                # MODIFY: Add inline_rules field, INLINE type
├── events.py                # MODIFY: Add 4 new events and data classes
├── config.py                # No changes needed
├── manager.py               # MODIFY: Add LLM and inline handler creation
├── external.py              # No changes needed
├── commands.py              # No changes needed
├── llm.py                   # NEW: LLM hook implementation
├── inline.py                # NEW: Inline matcher implementation
└── integration.py           # NEW: Integration wrappers
```

### 5.2 Public API Contract

**amplifier_app_cli/hooks/__init__.py**:
```python
# Core models
from .models import (
    HookType,
    HookConfig,
    HookMatcher,
    HookResult,
)

# Events
from .events import (
    # Existing
    PRE_TOOL_USE,
    POST_TOOL_USE,
    SESSION_START,
    SESSION_END,
    NOTIFICATION,
    STOP,
    SUBAGENT_STOP,
    # NEW
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
    # Data classes
    ToolUseEvent,
    SessionEvent,
    NotificationEvent,
    ErrorEvent,          # NEW
    CheckpointEvent,     # NEW
    ModelSwitchEvent,    # NEW
    MemoryUpdateEvent,   # NEW
)

# Manager
from .manager import HooksManager

# Integration helpers
from .integration import (
    ToolExecutionHooks,
    SessionLifecycleHooks,
    ToolDeniedError,
)

__all__ = [
    # Models
    "HookType",
    "HookConfig",
    "HookMatcher",
    "HookResult",
    # Events
    "PRE_TOOL_USE",
    "POST_TOOL_USE",
    "SESSION_START",
    "SESSION_END",
    "ERROR",
    "CHECKPOINT",
    "MODEL_SWITCH",
    "MEMORY_UPDATE",
    # Manager
    "HooksManager",
    # Integration
    "ToolExecutionHooks",
    "SessionLifecycleHooks",
    "ToolDeniedError",
]
```

### 5.3 Interface Contracts

**HooksManager** (EXISTING - NO BREAKING CHANGES):
- `load()` - Load hooks from config
- `register_with_session(session)` - Register with session
- `emit(session, event, data)` - Emit event
- `get_stats()` - Get statistics
- `enable_hook(name)` / `disable_hook(name)` - Control hooks

**ToolExecutionHooks** (NEW):
- `__init__(hooks_manager)` - Initialize with manager
- `wrap_tool_call(session, tool_name, tool_args, tool_fn)` - Wrap tool execution

**SessionLifecycleHooks** (NEW):
- `__init__(hooks_manager)` - Initialize with manager
- `on_session_start(session, profile, config)` - Session start
- `on_session_end(session, duration_ms, exit_reason)` - Session end
- `on_error(session, error, tool)` - Error occurred
- `on_checkpoint(session, checkpoint_id, **kwargs)` - Checkpoint created
- `on_model_switch(session, old_model, new_model, **kwargs)` - Model switched
- `on_memory_update(session, file_path, update_type)` - Memory file updated

---

## 6. Testing Strategy

### 6.1 Test Structure

```
tests/hooks/
├── test_models.py           # EXISTING - No changes needed
├── test_events.py           # MODIFY: Add tests for 4 new events
├── test_config.py           # EXISTING - No changes needed
├── test_standalone.py       # EXISTING - No changes needed
├── test_llm.py              # NEW: LLM hook tests
├── test_inline.py           # NEW: Inline matcher tests
└── test_integration.py      # NEW: Integration tests
```

### 6.2 Test Requirements

**test_events.py** additions:
- Test ErrorEvent creation and serialization
- Test CheckpointEvent creation and serialization
- Test ModelSwitchEvent creation and serialization
- Test MemoryUpdateEvent creation and serialization
- Test EVENT_DATA_TYPES mapping

**test_llm.py** (NEW):
- Test PromptTemplate rendering
- Test ResponseCache get/put/cleanup
- Test LLMHookExecutor with mocked LLM
- Test LLM error handling
- Test cache hit/miss behavior
- Test prompt variable substitution

**test_inline.py** (NEW):
- Test InlineRule matching
- Test InlineMatcher with different operators
- Test InlineHookExecutor rule evaluation
- Test rule priority ordering
- Test field path resolution (args.command)

**test_integration.py** (NEW):
- Test ToolExecutionHooks.wrap_tool_call
  - Continue action
  - Deny action
  - Modify action
  - Error handling
- Test SessionLifecycleHooks events
  - Session start/end
  - Error events
  - All lifecycle events
- Test end-to-end flow:
  - Hook registration
  - Tool execution with hooks
  - Event firing and handling

### 6.3 Integration Test Example

```python
async def test_tool_execution_with_deny_hook():
    """Test that hook can deny tool execution."""
    
    # Setup hooks manager with deny hook
    hook_config = HookConfig(
        name="test-deny",
        type=HookType.INLINE,
        matcher=HookMatcher(events=[PRE_TOOL_USE], tools=["bash"]),
        inline_rules=[{
            "field": "args.command",
            "operator": "contains",
            "value": "rm",
            "action": "deny",
            "reason": "Destructive command blocked",
        }],
    )
    
    hooks_manager = HooksManager()
    hooks_manager.hooks_config = HooksConfig(hooks=[hook_config])
    hooks_manager.load()
    
    # Create session and register hooks
    session = create_test_session()
    await hooks_manager.register_with_session(session)
    
    # Create tool executor wrapper
    tool_hooks = ToolExecutionHooks(hooks_manager)
    
    # Try to execute denied command
    async def mock_bash_tool(command: str):
        return f"executed: {command}"
    
    with pytest.raises(ToolDeniedError) as exc_info:
        await tool_hooks.wrap_tool_call(
            session,
            "bash",
            {"command": "rm -rf /"},
            mock_bash_tool,
        )
    
    assert "Destructive command blocked" in str(exc_info.value)
```

---

## 7. Implementation Specifications

### 7.1 Implementation Order

**Priority 1: Events and Models** (Foundation)
1. Modify `events.py` - Add 4 new events
2. Modify `models.py` - Add inline_rules and INLINE type
3. Add tests for new events

**Priority 2: Inline Hooks** (High Value, Low Complexity)
4. Create `inline.py` - Inline matcher implementation
5. Modify `manager.py` - Add inline handler creation
6. Add tests for inline hooks

**Priority 3: Integration** (Core Value)
7. Create `integration.py` - Integration wrappers
8. Wire into main.py for session lifecycle
9. Add integration tests

**Priority 4: LLM Hooks** (Optional Enhancement)
10. Create `llm.py` - LLM hook implementation
11. Modify `manager.py` - Add LLM handler creation
12. Add tests for LLM hooks

### 7.2 Dependencies

- **Events**: No dependencies
- **Inline Hooks**: Depends on events
- **Integration**: Depends on events and manager
- **LLM Hooks**: Depends on events, optional dependency on pydantic-ai

### 7.3 Backward Compatibility

**No Breaking Changes**:
- All existing hooks continue to work
- New event types are additive
- New hook types are optional
- Existing configuration format still valid
- Integration wrappers are opt-in

---

## 8. Future Enhancements (Out of Scope for Phase 2)

1. **Hook Composition**: Chain multiple hooks together
2. **Async Hooks**: Fire hooks asynchronously without blocking
3. **Hook Marketplace**: Share hooks across projects
4. **Visual Hook Editor**: GUI for creating hooks
5. **Hook Testing Framework**: Test hooks in isolation
6. **Hook Analytics**: Visualize hook execution patterns
7. **Memory File Watcher**: Real-time monitoring of memory files

---

## Appendix A: Configuration Examples

### Example 1: Comprehensive Safety Hook
```yaml
hooks:
  definitions:
    # Block dangerous bash commands
    - name: bash-safety-guard
      type: inline
      matcher:
        events: [PreToolUse]
        tools: [bash]
      inline_rules:
        - field: args.command
          operator: contains
          value: "rm -rf /"
          action: deny
          reason: "Dangerous system-wide delete blocked"
        
        - field: args.command
          operator: matches
          value: "^rm\\s+-rf\\s+/"
          action: deny
          reason: "Regex matched dangerous delete"
      priority: 10  # Run early
    
    # LLM review for complex decisions
    - name: llm-code-reviewer
      type: llm
      prompt: |
        Review this code modification:
        Tool: {{tool}}
        File: {{args.file_path}}
        Changes size: {{args.content | length}}
        
        Check for:
        - Security vulnerabilities
        - Hardcoded secrets
        - SQL injection risks
        
        Respond with JSON: {"action": "continue|deny", "reason": "..."}
      matcher:
        events: [PreToolUse]
        tools: [write_file, edit_file]
        path_patterns: ["*.py", "*.js", "*.ts"]
      timeout: 15
      priority: 50
    
    # Log all errors
    - name: error-logger
      type: command
      command: python scripts/log_error.py
      matcher:
        events: [Error]
      priority: 100
    
    # Backup on checkpoint
    - name: checkpoint-backup
      type: command
      script: scripts/backup_checkpoint.sh
      matcher:
        events: [Checkpoint]
      priority: 100
```

### Example 2: Model Usage Tracking
```yaml
hooks:
  definitions:
    - name: model-usage-tracker
      type: command
      command: python scripts/track_model_usage.py
      matcher:
        events: [ModelSwitch]
      priority: 100
    
    - name: expensive-model-blocker
      type: inline
      matcher:
        events: [ModelSwitch]
      inline_rules:
        - field: new_model
          operator: contains
          value: "opus"
          action: deny
          reason: "Expensive model blocked in CI environment"
```

---

## Appendix B: Decision Log

**Why inline hooks?**
- Simpler than external commands for basic patterns
- Faster execution (no process spawning)
- Easier to configure and test
- Fills gap between internal and external hooks

**Why optional LLM hooks?**
- Not everyone has LLM access
- Don't want to require API keys for basic usage
- Expensive to run on every event
- Should be opt-in enhancement

**Why simple caching for LLM?**
- Hash-based caching is sufficient
- TTL prevents stale decisions
- No need for complex cache invalidation
- Can enhance later if needed

**Why minimal integration points?**
- Easier to maintain
- Less coupling to hooks system
- Can remove hooks without breaking system
- Follows ruthless simplicity principle

**Why not file watcher for memory updates?**
- File watching is complex (cross-platform issues)
- Simple post-write event is sufficient
- Can enhance later with proper watcher
- Keeps Phase 2 focused

---

## Summary

Phase 2 completes the Enhanced Hooks System with:

1. **4 New Events**: Error, Checkpoint, ModelSwitch, MemoryUpdate
2. **LLM Hooks**: Optional AI-powered decision making
3. **Inline Hooks**: Simple pattern-based rules
4. **Integration**: Wired into tool execution and session lifecycle
5. **Tests**: Comprehensive coverage of new functionality

**Total New Files**: 3
- `hooks/llm.py`
- `hooks/inline.py`
- `hooks/integration.py`

**Modified Files**: 3
- `hooks/events.py` (add 4 events)
- `hooks/models.py` (add inline support)
- `hooks/manager.py` (add LLM/inline handlers)

**Design Philosophy Maintained**: Ruthless simplicity, clear contracts, modular design, easy regeneration.
