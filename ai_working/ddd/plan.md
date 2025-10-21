# DDD Plan: TinkerTasker-Inspired CLI Improvements

**Date**: 2025-01-21
**Branch**: alternate-cli-impl
**Status**: Planning Phase Complete

---

## Problem Statement

### What We're Solving

The current amplifier-app-cli has a **solid foundation** but lacks the **visual polish and clean architecture** demonstrated by TinkerTasker. Specifically:

**User-Facing Issues:**
1. **No markdown rendering** - LLM responses with bold/italic/code blocks display as plain text
2. **No live progress feedback** - Users see "Processing..." with no indication of elapsed time
3. **No output truncation** - Tool outputs can be hundreds of lines, cluttering conversations
4. **Basic visual formatting** - No visual hierarchy or professional styling

**Developer Issues:**
1. **Monolithic main.py** - 75KB, 2500+ lines with mixed responsibilities
2. **No event bus pattern** - Display logic tightly coupled to execution logic
3. **Hard to test** - Display code intermingled with business logic
4. **Difficult to extend** - Adding new display features requires modifying core loop

### Why It Matters

**User Value:**
- **Better readability** - Markdown makes LLM responses much easier to scan and understand
- **Responsive UX** - Live progress feedback eliminates "is it frozen?" concerns
- **Clean conversations** - Truncated tool output keeps focus on important information
- **Professional polish** - Visual hierarchy and formatting make the CLI feel modern

**Developer Value:**
- **Maintainability** - Clean separation of concerns, easier to understand and modify
- **Testability** - Display logic can be tested independently
- **Extensibility** - New display features don't require touching core loop
- **Code quality** - Following proven patterns from TinkerTasker

### Current State Analysis

**From Reconnaissance:**
- ✅ Rich Console already integrated (Console, Panel, Table)
- ✅ Async infrastructure in place
- ✅ Profile system with UIConfig schema
- ✅ Command processing via CommandProcessor
- ❌ No markdown rendering (responses are plain text)
- ❌ No event bus pattern (inline display logic)
- ❌ No live progress feedback
- ❌ No output truncation or formatting

---

## Proposed Solution

### High-Level Approach

**Hybrid Strategy**: Keep Amplifier's strengths (profiles, sessions, agents) while adopting TinkerTasker's clean patterns (event bus, rich formatting, live feedback).

### Core Improvements

1. **Event Bus Architecture**
   - Decouple display logic from execution logic
   - Enable multiple subscribers (display, logging, testing)
   - Clean testable interfaces

2. **Rich Visual Formatting**
   - Markdown rendering for LLM responses
   - Tree-style output (● for messages, ⎿ for hierarchy)
   - Consistent color scheme and styling

3. **Live Progress Feedback**
   - Elapsed time display during LLM calls
   - Smooth updates without terminal corruption
   - Interrupt guidance (ctrl+c)

4. **Configurable Output Control**
   - Tool output truncation (default: 3 lines, configurable)
   - Argument length limiting
   - "... (N more lines)" indicators

5. **Simplified Architecture**
   - Extract event bus to dedicated module
   - Extract display handlers to dedicated module
   - Reduce main.py complexity
   - Maintain clear module boundaries

---

## Alternatives Considered

### Alternative 1: Full Rewrite (Rejected)

**Approach**: Completely rebuild CLI from scratch using TinkerTasker as template

**Pros**:
- Cleanest possible architecture
- No legacy code constraints

**Cons**:
- **HUGE risk** - throws away proven profile/session/agent systems
- **Lost investment** - months of work on existing features
- **Breaking change** - users lose current functionality
- **Time consuming** - 2+ weeks to rebuild everything

**Verdict**: ❌ Too risky, not aligned with modular philosophy

### Alternative 2: Minimal Changes Only (Rejected)

**Approach**: Just add markdown rendering, skip architecture improvements

**Pros**:
- Fastest to implement (< 1 day)
- Minimal risk
- Immediate visual improvement

**Cons**:
- **Misses core problem** - code complexity remains
- **Technical debt** - makes future improvements harder
- **Limited value** - only visual, no architectural benefits
- **Not scalable** - future display features still difficult

**Verdict**: ❌ Band-aid solution, doesn't address underlying issues

### Alternative 3: Hybrid Approach (SELECTED ✅)

**Approach**: Keep Amplifier features, adopt TinkerTasker patterns incrementally

**Pros**:
- ✅ **Preserves investment** - all existing features stay
- ✅ **Incremental risk** - can validate each phase
- ✅ **Best of both** - clean architecture + rich features
- ✅ **Modular** - changes are self-contained
- ✅ **Testable** - can validate after each phase

**Cons**:
- ⚠️ Slightly longer than minimal (3-5 days vs 1 day)
- ⚠️ Requires careful integration with existing code

**Verdict**: ✅ **SELECTED** - Optimal balance of value, risk, and timeline

---

## Architecture & Design

### Key Interfaces ("Studs")

These are the stable connection points between modules:

#### 1. Event Bus Interface

```python
# events/bus.py
class EventBus:
    """Simple pub-sub for message events."""

    def subscribe(self, handler: Callable[[MessageEvent, Config], None]) -> None:
        """Register an event handler."""

    def publish(self, event: MessageEvent) -> None:
        """Publish event to all subscribers."""

class TurnContext:
    """Context manager for a complete user turn."""

    async def __aenter__(self) -> "TurnContext":
        """Start turn, capture start time."""

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """End turn."""

    def emit_event(self, event: MessageEvent) -> None:
        """Emit event through bus."""
```

#### 2. Message Event Types

```python
# events/schemas.py
MessageEvent = UserMessage | AssistantMessage | ToolCall | ToolResult

class UserMessage(BaseModel):
    content: str

class AssistantMessage(BaseModel):
    content: str

class ToolCall(BaseModel):
    name: str
    id: str
    arguments: dict[str, Any]

class ToolResult(BaseModel):
    id: str  # Links to ToolCall
    name: str
    output: str
```

#### 3. Display Handler Interface

```python
# display/handlers.py
def handle_event(event: MessageEvent, config: UIConfig) -> None:
    """Display event in console with appropriate formatting."""
```

#### 4. UIConfig Extension

```python
# profile_system/schema.py (existing file, add fields)
class UIConfig(BaseModel):
    # Existing fields
    show_thinking_stream: bool = True
    show_tool_lines: int = 5

    # NEW: Add these fields
    tool_output_lines: int = Field(default=3, ge=-1)  # -1 = show all
    max_arg_length: int = Field(default=100, gt=0)
    show_elapsed_time: bool = Field(default=True)
    use_tree_formatting: bool = Field(default=True)
    render_markdown: bool = Field(default=True)
```

### Module Boundaries

Clear separation of concerns:

```
amplifier_app_cli/
├── events/                  # NEW: Event system
│   ├── __init__.py
│   ├── bus.py              # EventBus, TurnContext
│   └── schemas.py          # MessageEvent types
│
├── display/                # NEW: Display logic
│   ├── __init__.py
│   ├── handlers.py         # Event display handlers
│   └── formatters.py       # Rich formatting utilities
│
├── main.py                 # MODIFIED: Simplified main loop
├── profile_system/
│   └── schema.py          # MODIFIED: Extended UIConfig
│
└── data/profiles/
    ├── dev.md             # MODIFIED: Add new UI config
    ├── base.md            # MODIFIED: Add new UI config
    └── ...                # MODIFIED: Other profiles
```

**Key Principle**: Each module is a self-contained "brick" with clear interfaces ("studs")

### Data Models

**No new data models needed** - we leverage existing:
- `Profile` and `UIConfig` (already exist in profile_system/schema.py)
- `ToolResult` (already exists in amplifier_core)

**New event types** defined in events/schemas.py mirror existing structures

---

## Files to Change

### Phase 2: Non-Code Files (Documentation Updates)

#### Profile Documentation

- [ ] `amplifier_app_cli/data/profiles/README.md`
  - Add documentation for new UI config fields
  - Show examples of output truncation settings
  - Document tree formatting and markdown rendering
  - Add session configuration section documenting max_iterations

- [ ] `amplifier_app_cli/data/profiles/dev.md`
  - Update `session.orchestrator.config` to add `max_iterations: 50`
  - Update `ui:` section with new fields:
    - `tool_output_lines: 3`
    - `max_arg_length: 100`
    - `show_elapsed_time: true`
    - `use_tree_formatting: true`
    - `render_markdown: true`

- [ ] `amplifier_app_cli/data/profiles/base.md`
  - Add `session.orchestrator.config.max_iterations: 30`
  - Add `ui:` section with conservative defaults

- [ ] `amplifier_app_cli/data/profiles/production.md`
  - Add `session.orchestrator.config.max_iterations: 100`
  - Add `ui:` section optimized for production use

- [ ] `amplifier_app_cli/data/profiles/test.md`
  - Add `session.orchestrator.config.max_iterations: 20`
  - Add `ui:` section for testing (verbose output)

- [ ] `amplifier_app_cli/data/profiles/full.md`
  - Add `session.orchestrator.config.max_iterations: 100`
  - Add `ui:` section with all features enabled

- [ ] `amplifier_app_cli/data/profiles/foundation.md`
  - Add `session.orchestrator.config.max_iterations: 30`

#### Main Documentation

- [ ] `README.md`
  - Add section showcasing new visual improvements
  - Show before/after examples of markdown rendering
  - Mention configurable output truncation

- [ ] `docs/INTERACTIVE_MODE.md`
  - Document live progress feedback feature
  - Explain output truncation behavior
  - Show UI configuration options

- [ ] `CHANGELOG.md` (if exists) or create it
  - Document all new features
  - Note backwards compatibility

#### Configuration Examples

- [ ] `docs/examples/custom-profile-with-ui.md` (create if needed)
  - Show how to customize UI settings
  - Provide example profiles with different display preferences

### Phase 4: Code Files (Implementation)

#### New Files to Create

- [ ] `amplifier_app_cli/events/__init__.py`
  - Export EventBus, TurnContext, MessageEvent types

- [ ] `amplifier_app_cli/events/bus.py`
  - Implement EventBus class
  - Implement TurnContext async context manager
  - ~80 lines

- [ ] `amplifier_app_cli/events/schemas.py`
  - Define MessageEvent types (UserMessage, AssistantMessage, ToolCall, ToolResult)
  - Pydantic models for type safety
  - ~50 lines

- [ ] `amplifier_app_cli/display/__init__.py`
  - Export display_event handler and formatters

- [ ] `amplifier_app_cli/display/handlers.py`
  - Implement handle_event() dispatcher
  - Implement display_user_message()
  - Implement display_assistant_message() with markdown
  - Implement display_tool_call()
  - Implement display_tool_result() with truncation
  - ~150 lines

- [ ] `amplifier_app_cli/display/formatters.py`
  - Implement format_tool_arguments() with length limiting
  - Implement truncate_output() with line counting
  - Implement format_tree_output() with ● and ⎿ characters
  - ~100 lines

#### Files to Modify

- [ ] `amplifier_app_cli/profile_system/schema.py`
  - Extend UIConfig class with new fields
  - Add field validators if needed
  - ~10 lines added

- [ ] `amplifier_app_cli/main.py`
  - Import event bus and display modules
  - Initialize EventBus in main()
  - Subscribe display handlers
  - Modify interactive_chat() to emit events
  - Modify execute_single() to emit events
  - Add live elapsed time display
  - Simplify response handling (delegate to event handlers)
  - ~50 lines changed, ~30 lines added

#### Tests to Create

- [ ] `tests/events/test_bus.py`
  - Test EventBus subscription
  - Test event publishing
  - Test TurnContext lifecycle

- [ ] `tests/events/test_schemas.py`
  - Test MessageEvent types validation

- [ ] `tests/display/test_handlers.py`
  - Test each display handler function
  - Mock console to capture output
  - Verify markdown rendering
  - Verify truncation logic

- [ ] `tests/display/test_formatters.py`
  - Test argument formatting
  - Test output truncation
  - Test tree formatting

- [ ] `tests/test_main_integration.py`
  - Integration test with event bus
  - Verify events flow end-to-end

---

## Philosophy Alignment

### Ruthless Simplicity ✅

**Start Minimal:**
- Event bus is <100 lines, just pub-sub
- Display handlers are single-purpose functions
- No abstractions unless justified
- Direct use of Rich, no custom wrappers

**Avoid Future-Proofing:**
- NOT building: complex event filtering, prioritization, async subscribers
- NOT building: plugin system for display handlers
- NOT building: themeing system or skins
- Building ONLY: what's needed for the 5 core improvements

**Clear Over Clever:**
- Event types are explicit Pydantic models (no unions or dynamic dispatch)
- Display handlers are named functions (not class hierarchies)
- Configuration is declarative YAML (not code-based)

### Modular Design ✅

**Bricks (Self-Contained Modules):**
1. **events/** - Event bus and schemas (can be regenerated independently)
2. **display/** - Display logic (can be regenerated from spec)
3. Profile YAML files (declarative config)

**Studs (Clear Interfaces):**
1. **EventBus.subscribe/publish** - Standard observer pattern
2. **handle_event(MessageEvent, UIConfig)** - Single dispatcher function
3. **UIConfig** - Pydantic model with field validators

**Regeneratable:**
- Each module has clear spec (this document)
- Interfaces are stable (won't change)
- Implementations can be rebuilt from scratch without breaking system
- Tests define behavior contract

### Analysis-First Development ✅

**We Did the Analysis:**
1. ✅ Explored current architecture (3 agents)
2. ✅ Identified patterns and anti-patterns
3. ✅ Evaluated alternatives (3 options)
4. ✅ Created detailed design before coding
5. ✅ Defined success criteria

---

## Test Strategy

### Unit Tests

**events/bus.py:**
```python
def test_event_bus_subscription():
    """Test subscribing and receiving events."""
    bus = EventBus()
    events_received = []

    def handler(event, config):
        events_received.append(event)

    bus.subscribe(handler)
    bus.publish(UserMessage(content="test"))

    assert len(events_received) == 1
    assert events_received[0].content == "test"

async def test_turn_context_lifecycle():
    """Test TurnContext emits events."""
    bus = EventBus()
    events = []
    bus.subscribe(lambda e, c: events.append(e))

    async with TurnContext(bus) as turn:
        turn.emit_event(UserMessage(content="test"))

    assert len(events) == 1
```

**display/handlers.py:**
```python
def test_display_assistant_message_with_markdown():
    """Test markdown rendering in assistant messages."""
    from io import StringIO
    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, width=80, legacy_windows=False)

    event = AssistantMessage(content="**bold** and *italic*")
    config = UIConfig(render_markdown=True)

    # Inject console for testing
    with patch('amplifier_app_cli.display.handlers.console', console):
        display_assistant_message(event, config)

    output = buffer.getvalue()
    assert "bold" in output  # Rich renders markdown

def test_tool_output_truncation():
    """Test tool output respects line limits."""
    event = ToolResult(
        id="1",
        name="bash",
        output="line1\nline2\nline3\nline4\nline5"
    )
    config = UIConfig(tool_output_lines=2)

    buffer = StringIO()
    console = Console(file=buffer)

    with patch('amplifier_app_cli.display.handlers.console', console):
        display_tool_result(event, config)

    output = buffer.getvalue()
    assert "line1" in output
    assert "line2" in output
    assert "... (3 more lines)" in output
```

**display/formatters.py:**
```python
def test_format_tool_arguments_truncation():
    """Test argument value truncation."""
    args = {"path": "/very/long/path/to/file/that/exceeds/limit.txt"}
    config = UIConfig(max_arg_length=20)

    result = format_tool_arguments(args, config)
    assert len(result) < 50  # Includes arg name + truncation
    assert "..." in result

def test_truncate_output_shows_count():
    """Test truncated output shows remaining line count."""
    output = "\n".join([f"line{i}" for i in range(10)])
    config = UIConfig(tool_output_lines=3)

    result = truncate_output(output, config)
    assert result.count("\n") <= 3
    assert "(7 more lines)" in result or result.endswith("...")
```

### Integration Tests

**Full flow test:**
```python
async def test_event_bus_integration():
    """Test event bus integrates with session execution."""
    bus = EventBus()
    events = []
    bus.subscribe(lambda e, c: events.append(e))

    # Mock session
    session = create_test_session()

    # Execute with event bus
    async with TurnContext(bus) as turn:
        response = await session.execute("test prompt")
        turn.emit_event(AssistantMessage(content=response))

    # Verify events were emitted
    assert len(events) > 0
    assert any(isinstance(e, AssistantMessage) for e in events)
```

### User Testing

**Manual verification checklist:**
- [ ] Start amplifier in chat mode
- [ ] Send prompt with markdown response (ask for bold/italic/code)
- [ ] Verify markdown renders correctly
- [ ] Send prompt that uses tools
- [ ] Verify tool output is truncated to configured lines
- [ ] Verify "... (N more lines)" indicator appears
- [ ] Verify elapsed time updates during LLM call
- [ ] Verify tree formatting (● and ⎿) is applied
- [ ] Test all slash commands still work
- [ ] Test profile loading with new UI config
- [ ] Test session save/resume preserves formatting
- [ ] Verify ctrl+c interrupts cleanly

---

## Implementation Approach

### Phase 2: Documentation Updates (Day 1 morning)

**Goal**: Update all non-code files to reflect future state

**Order**:
1. Profile YAML files (dev.md, base.md, etc.) - Add UI config sections
2. Profile README - Document new UI fields
3. Main README - Show new features
4. INTERACTIVE_MODE.md - Explain new behaviors

**Technique**: File crawling with checklist tracking

**Output**: All documentation matches final implementation

### Phase 4: Code Implementation (Day 1 afternoon - Day 3)

**Day 1 Afternoon: Event Bus**
1. Create events/bus.py (EventBus, TurnContext)
2. Create events/schemas.py (MessageEvent types)
3. Write unit tests for event bus
4. Verify: Tests pass, event bus works in isolation

**Day 2 Morning: Display Handlers**
1. Create display/handlers.py (display functions)
2. Create display/formatters.py (formatting utilities)
3. Implement markdown rendering
4. Implement tree formatting
5. Implement truncation logic
6. Write unit tests for display
7. Verify: Display functions work with mocked console

**Day 2 Afternoon: Main Loop Integration**
1. Extend UIConfig in schema.py
2. Wire event bus into main.py
3. Subscribe display handlers
4. Emit events during execution
5. Add live elapsed time display
6. Remove old inline display code
7. Verify: Integration tests pass

**Day 3: Polish and Testing**
1. Update profile YAML files with defaults
2. Manual testing in chat mode
3. Test tool output truncation
4. Test markdown rendering
5. Test elapsed time display
6. Fix any issues discovered
7. Final verification

### Incremental Validation

**After each chunk:**
- Run tests
- Verify no regressions
- Manual smoke test
- Git commit with clear message

**Can abort/rollback at any phase** if issues arise

---

## Success Criteria

### Functional

- [  ] Markdown rendering works (bold, italic, code blocks, lists)
- [ ] Live elapsed time displays during LLM calls
- [ ] Tool output truncates to configured lines
- [ ] "... (N more lines)" indicator shows when truncated
- [ ] Tree formatting (● and ⎿) displays correctly
- [ ] All slash commands work unchanged
- [ ] Profile system loads new UI config
- [ ] Session save/resume preserves context

### Technical

- [ ] Event bus has >90% test coverage
- [ ] Display handlers have >80% test coverage
- [ ] No regressions in existing features
- [ ] main.py reduced by >20% lines of code
- [ ] Clear module boundaries maintained

### User Experience

- [ ] Responses are easier to read (markdown)
- [ ] User knows system is working (elapsed time)
- [ ] Conversations are cleaner (truncation)
- [ ] Visual hierarchy is clear (tree formatting)
- [ ] No terminal corruption or glitches

### Philosophy

- [ ] Ruthless simplicity maintained
- [ ] Modules are self-contained (bricks)
- [ ] Interfaces are stable (studs)
- [ ] Code is regeneratable from spec

---

## Risks & Mitigation

### Risk 1: Event Bus Complexity Creep ⚠️

**Risk**: Event bus grows beyond simple pub-sub

**Mitigation**:
- Strict scope: ONLY publish/subscribe, no filtering/prioritization
- Review against TinkerTasker's 34-line EventBus
- Reject any feature requests beyond basic pub-sub
- Delete code that isn't actively used

### Risk 2: Display Handler Coupling ⚠️

**Risk**: Display handlers become tightly coupled to amplifier_core internals

**Mitigation**:
- Handlers ONLY receive MessageEvent types (not core models)
- Conversion layer (events/schemas.py) adapts core models to events
- Handlers can be tested with mock events
- No imports of amplifier_core in display/

### Risk 3: Terminal Corruption with Rich ⚠️

**Risk**: Rich live updates cause terminal glitches

**Mitigation**:
- Use Rich's proven patterns (console.status, live)
- Test on multiple terminals (iTerm, Terminal.app, WSL)
- Add fallback to static display if live fails
- No custom ANSI codes, rely on Rich

### Risk 4: Configuration Confusion ⚠️

**Risk**: Users unsure where to configure UI settings

**Mitigation**:
- Comprehensive documentation in Profile README
- Examples in bundled profiles (dev.md, base.md)
- Sensible defaults (most users won't need to configure)
- Error messages point to documentation

---

## Alternative Implementation Notes

### If We Need to Simplify

**Minimum viable changes** (if timeline too aggressive):
1. Add markdown rendering ONLY (1 line change: `Markdown(response)`)
2. Add live elapsed time (copy TinkerTasker's 20 lines)
3. Skip event bus, skip truncation

**Saves**: 2 days of work
**Loses**: Architecture improvements, testability, extensibility

### If We Want to Go Further

**Additional enhancements** (if we have extra time):
1. Syntax highlighting for code blocks
2. Collapsible tool output (show/hide on demand)
3. Progress bars for multi-step operations
4. Diff display for file edits

**Requires**: Additional 2-3 days
**Benefits**: Even more polished UX

---

## Next Steps

### Immediate (After Plan Approval)

✅ Plan complete and approved
➡️ Ready for `/ddd:2-docs` - Update all non-code files

### Phase 2 (Documentation)

1. Update profile YAML files
2. Update Profile README with UI config docs
3. Update main README with new features
4. Update INTERACTIVE_MODE.md

### Phase 3 (Code Implementation)

1. Create event bus module
2. Create display module
3. Wire into main loop
4. Test and validate

### Phase 4 (Verification)

1. Run all tests
2. Manual UX testing
3. Document any issues
4. Final polish

---

## Open Questions for User

1. **UI Config Defaults** - Are these reasonable defaults?
   - `tool_output_lines: 3` (show first 3 lines)
   - `max_arg_length: 100` (truncate args at 100 chars)
   - `show_elapsed_time: true`
   - `render_markdown: true`

2. **Profile Updates** - Should all 6 profiles get UI config, or just dev?

3. **Backwards Compatibility** - Since you said "no backcompat requirements", can we:
   - Change output format immediately?
   - Remove old response display code?
   - Update all profiles without migration path?

4. **Timeline** - Prefer conservative (5 days) or aggressive (3 days)?

5. **Scope** - Just the 5 core improvements, or add bonus features if time permits?

---

## References

- **TinkerTasker Analysis**: `/Users/robotdad/Source/dev/amplifier.cli/amplifier-app-textual/docs/TINKERTASKER_ANALYSIS.md`
- **TinkerTasker Implementation**: `~/Source/TinkerTasker/cli-ux/`
- **Current CLI**: `./amplifier_app_cli/main.py`
- **Philosophy Docs**: `@AGENTS.md`, `@IMPLEMENTATION_PHILOSOPHY.md`, `@MODULAR_DESIGN_PHILOSOPHY.md`

---

**Plan Status**: ✅ Ready for Review

**Next Command**: `/ddd:2-docs` (after user approval)
