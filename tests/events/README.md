# Events Package Test Suite

Comprehensive test coverage for the `amplifier_app_cli.events` package.

## Coverage Summary

**100% code coverage achieved** across all modules:
- `schemas.py`: 100% (21 statements)
- `bus.py`: 100% (32 statements)
- `__init__.py`: 100% (8 statements)

**Total: 54 tests, 0 failures**

## Test Files

### test_schemas.py (33 tests)
Tests for Pydantic MessageEvent models:

**UserMessage Tests (6 tests)**
- Valid message creation
- Empty content handling
- Missing content validation
- Multiline content
- Literal type enforcement
- Invalid type override rejection

**AssistantMessage Tests (5 tests)**
- Valid message creation
- Empty content handling
- Missing content validation
- Long content (10K chars)
- Literal type enforcement

**ToolCall Tests (7 tests)**
- Valid tool call with arguments dict
- Empty arguments dict
- Nested arguments (dicts and lists)
- Missing required fields validation
- Arguments must be dict type
- Various JSON-compatible types in arguments
- Literal type enforcement

**ToolResult Tests (6 tests)**
- Valid tool result
- Empty output handling
- Multiline output
- Missing required fields validation
- Long output (50K chars)
- Literal type enforcement

**MessageEvent Union Tests (4 tests)**
- Discriminated union for UserMessage
- Discriminated union for AssistantMessage
- Discriminated union for ToolCall
- Discriminated union for ToolResult

**Edge Cases (5 tests)**
- Unicode content handling
- Special characters in tool names
- Very long tool IDs
- Empty strings in arguments
- Serialization roundtrip

### test_bus.py (21 tests)
Tests for EventBus and TurnContext:

**EventBus Tests (10 tests)**
- Single handler subscription
- Multiple handler subscription
- Publishing to multiple subscribers
- **Error isolation - single handler exception**
- **Error isolation - multiple handler failures**
- No subscribers (shouldn't crash)
- All event types received by handlers
- Same handler subscribed multiple times
- Handler state modification
- Handler with conditional logic

**TurnContext Tests (9 tests)**
- Context manager lifecycle (`__aenter__`, `__aexit__`)
- Event emission through context
- Multiple events in single turn
- Elapsed time tracking with asyncio.sleep
- Multiple independent contexts
- Exception handling in context
- Event emission outside context
- Elapsed time before entering context
- Nested event emission (re-entrancy)

**Integration Tests (2 tests)**
- Full turn simulation (user → tool call → tool result → assistant)
- Multiple turns sharing same event bus

## Key Testing Patterns

### Error Isolation Testing
Critical requirement: Failed handlers must not crash the bus or prevent other handlers from executing.

```python
def test_error_isolation_handler_exception(self, caplog):
    """Test that handler exceptions don't crash bus or affect other handlers."""
    bus = EventBus()
    handler1 = Mock()

    def failing_handler(event):
        raise ValueError("Handler 2 failed")

    handler3 = Mock()

    bus.subscribe(handler1)
    bus.subscribe(failing_handler)
    bus.subscribe(handler3)

    event = UserMessage(content="test")

    with caplog.at_level(logging.ERROR):
        bus.publish(event)

    # All handlers called despite handler2 failing
    handler1.assert_called_once_with(event)
    handler3.assert_called_once_with(event)

    # Error logged
    assert "Error in event handler" in caplog.text
```

### Async Context Manager Testing
Uses `pytest.mark.asyncio` for async tests:

```python
@pytest.mark.asyncio
async def test_context_manager_lifecycle(self):
    """Test TurnContext __aenter__ and __aexit__."""
    bus = EventBus()
    ctx = TurnContext(bus)

    assert not ctx.is_processing

    async with ctx as entered_ctx:
        assert entered_ctx is ctx
        assert ctx.is_processing
        assert ctx.start_time > 0

    assert not ctx.is_processing
```

### Timing Tests
Validates elapsed time tracking:

```python
@pytest.mark.asyncio
async def test_elapsed_time_tracking(self):
    """Test get_elapsed_time returns accurate timing."""
    bus = EventBus()
    ctx = TurnContext(bus)

    async with ctx:
        elapsed1 = ctx.get_elapsed_time()
        assert 0 <= elapsed1 < 0.01

        await asyncio.sleep(0.05)
        elapsed2 = ctx.get_elapsed_time()
        assert 0.04 <= elapsed2 < 0.1
```

## Edge Cases Covered

1. **Validation Failures**: Missing required fields, invalid types
2. **Empty/Large Content**: Empty strings, 10K-50K character content
3. **Unicode**: Multi-language and emoji content
4. **Special Characters**: Hyphens, dots in tool names
5. **Error Isolation**: Exceptions in handlers don't cascade
6. **Re-entrancy**: Handlers emitting events during processing
7. **Async Behavior**: Context managers, timing, exceptions
8. **Multiple Event Types**: All 4 MessageEvent variants tested

## Test Dependencies

- `pytest`: Test framework
- `pytest-asyncio`: Async test support
- `pydantic`: Data validation (tested module)
- `unittest.mock`: Mock objects for handler testing

## Running Tests

```bash
# Run all events tests
uv run pytest tests/events/ -v

# Run specific test file
uv run pytest tests/events/test_schemas.py -v
uv run pytest tests/events/test_bus.py -v

# Run with coverage
uv run pytest tests/events/ --cov=amplifier_app_cli.events --cov-report=term-missing

# Run specific test
uv run pytest tests/events/test_bus.py::TestEventBus::test_error_isolation_handler_exception -v
```

## Philosophy Alignment

Tests follow project philosophies:
- **Ruthless Simplicity**: Clear, focused test cases
- **Behavior Testing**: Test what matters (error isolation, timing, validation)
- **No Redundant Tests**: Don't test obvious code properties
- **Real Bug Prevention**: Focus on edge cases and failure modes
