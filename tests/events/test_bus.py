"""Tests for EventBus and TurnContext."""

import asyncio
import logging
from unittest.mock import Mock

import pytest
from amplifier_app_cli.events.bus import EventBus
from amplifier_app_cli.events.bus import TurnContext
from amplifier_app_cli.events.schemas import AssistantMessage
from amplifier_app_cli.events.schemas import ToolCall
from amplifier_app_cli.events.schemas import ToolResult
from amplifier_app_cli.events.schemas import UserMessage


class TestEventBus:
    """Test EventBus subscription and publishing."""

    def test_subscribe_single_handler(self):
        """Test subscribing a single handler."""
        bus = EventBus()
        handler = Mock()

        bus.subscribe(handler)
        event = UserMessage(content="test")
        bus.publish(event)

        handler.assert_called_once_with(event, None)

    def test_subscribe_multiple_handlers(self):
        """Test subscribing multiple handlers."""
        bus = EventBus()
        handler1 = Mock()
        handler2 = Mock()
        handler3 = Mock()

        bus.subscribe(handler1)
        bus.subscribe(handler2)
        bus.subscribe(handler3)

        event = UserMessage(content="test")
        bus.publish(event)

        handler1.assert_called_once_with(event, None)
        handler2.assert_called_once_with(event, None)
        handler3.assert_called_once_with(event, None)

    def test_publish_to_multiple_subscribers(self):
        """Test publishing reaches all subscribers."""
        bus = EventBus()
        events_received = []

        def handler1(event, config):
            events_received.append(("handler1", event))

        def handler2(event, config):
            events_received.append(("handler2", event))

        bus.subscribe(handler1)
        bus.subscribe(handler2)

        event1 = UserMessage(content="message1")
        event2 = AssistantMessage(content="message2")

        bus.publish(event1)
        bus.publish(event2)

        assert len(events_received) == 4
        assert events_received[0] == ("handler1", event1)
        assert events_received[1] == ("handler2", event1)
        assert events_received[2] == ("handler1", event2)
        assert events_received[3] == ("handler2", event2)

    def test_error_isolation_handler_exception(self, caplog):
        """Test that handler exceptions don't crash bus or affect other handlers."""
        bus = EventBus()
        handler1 = Mock()

        def failing_handler(event, config):
            raise ValueError("Handler 2 failed")

        handler3 = Mock()

        bus.subscribe(handler1)
        bus.subscribe(failing_handler)
        bus.subscribe(handler3)

        event = UserMessage(content="test")

        with caplog.at_level(logging.ERROR):
            bus.publish(event)

        # All handlers should be called despite handler2 failing
        handler1.assert_called_once_with(event, None)
        handler3.assert_called_once_with(event, None)

        # Error should be logged
        assert "Error in event handler" in caplog.text

    def test_error_isolation_multiple_failures(self, caplog):
        """Test that multiple handler failures are all logged."""
        bus = EventBus()

        def failing_handler1(event, config):
            raise ValueError("First failure")

        def failing_handler2(event, config):
            raise RuntimeError("Second failure")

        handler3 = Mock()

        bus.subscribe(failing_handler1)
        bus.subscribe(failing_handler2)
        bus.subscribe(handler3)

        event = UserMessage(content="test")

        with caplog.at_level(logging.ERROR):
            bus.publish(event)

        # handler3 should still be called
        handler3.assert_called_once()

        # Both errors logged
        assert caplog.text.count("Error in event handler") == 2

    def test_no_subscribers(self):
        """Test publishing with no subscribers doesn't crash."""
        bus = EventBus()
        event = UserMessage(content="test")
        bus.publish(event)  # Should not raise

    def test_handler_receives_all_event_types(self):
        """Test handler receives all message event types."""
        bus = EventBus()
        received = []

        def handler(event, config):
            received.append(event)

        bus.subscribe(handler)

        events = [
            UserMessage(content="user"),
            AssistantMessage(content="assistant"),
            ToolCall(name="tool", id="call_1", arguments={"arg": "value"}),
            ToolResult(id="call_1", name="tool", output="result"),
        ]

        for event in events:
            bus.publish(event)

        assert len(received) == 4
        assert all(r == e for r, e in zip(received, events, strict=False))

    def test_subscribe_same_handler_multiple_times(self):
        """Test subscribing the same handler multiple times."""
        bus = EventBus()
        handler = Mock()

        bus.subscribe(handler)
        bus.subscribe(handler)

        event = UserMessage(content="test")
        bus.publish(event)

        # Handler called twice (once per subscription)
        assert handler.call_count == 2

    def test_handler_modifies_state(self):
        """Test handlers can maintain and modify state."""
        bus = EventBus()
        state = {"count": 0}

        def counting_handler(event, config):
            state["count"] += 1

        bus.subscribe(counting_handler)

        for i in range(5):
            bus.publish(UserMessage(content=f"message {i}"))

        assert state["count"] == 5

    def test_handler_with_complex_logic(self):
        """Test handler with conditional logic."""
        bus = EventBus()
        stats = {"user": 0, "assistant": 0, "tool_call": 0, "tool_result": 0}

        def stats_handler(event, config):
            if event.type == "user_message":
                stats["user"] += 1
            elif event.type == "assistant_message":
                stats["assistant"] += 1
            elif event.type == "tool_call":
                stats["tool_call"] += 1
            elif event.type == "tool_result":
                stats["tool_result"] += 1

        bus.subscribe(stats_handler)

        bus.publish(UserMessage(content="test1"))
        bus.publish(UserMessage(content="test2"))
        bus.publish(AssistantMessage(content="response"))
        bus.publish(ToolCall(name="tool", id="c1", arguments={}))
        bus.publish(ToolResult(id="c1", name="tool", output="out"))

        assert stats["user"] == 2
        assert stats["assistant"] == 1
        assert stats["tool_call"] == 1
        assert stats["tool_result"] == 1


class TestTurnContext:
    """Test TurnContext lifecycle and behavior."""

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

    @pytest.mark.asyncio
    async def test_emit_event(self):
        """Test TurnContext emit_event publishes to bus."""
        bus = EventBus()
        handler = Mock()
        bus.subscribe(handler)

        ctx = TurnContext(bus)

        async with ctx:
            event = UserMessage(content="test")
            ctx.emit_event(event)

        handler.assert_called_once_with(event, None)

    @pytest.mark.asyncio
    async def test_emit_multiple_events(self):
        """Test emitting multiple events in a turn."""
        bus = EventBus()
        received = []
        bus.subscribe(lambda e, c: received.append(e))

        ctx = TurnContext(bus)

        async with ctx:
            ctx.emit_event(UserMessage(content="user"))
            ctx.emit_event(AssistantMessage(content="assistant"))
            ctx.emit_event(ToolCall(name="tool", id="c1", arguments={}))
            ctx.emit_event(ToolResult(id="c1", name="tool", output="result"))

        assert len(received) == 4
        assert received[0].type == "user_message"
        assert received[1].type == "assistant_message"
        assert received[2].type == "tool_call"
        assert received[3].type == "tool_result"

    @pytest.mark.asyncio
    async def test_elapsed_time_tracking(self):
        """Test get_elapsed_time returns accurate timing."""
        bus = EventBus()
        ctx = TurnContext(bus)

        async with ctx:
            # Check initial time (should be near 0)
            elapsed1 = ctx.get_elapsed_time()
            assert 0 <= elapsed1 < 0.01

            # Wait a bit and check again
            await asyncio.sleep(0.05)
            elapsed2 = ctx.get_elapsed_time()
            assert 0.04 <= elapsed2 < 0.1

            # Wait more
            await asyncio.sleep(0.05)
            elapsed3 = ctx.get_elapsed_time()
            assert 0.09 <= elapsed3 < 0.15

        # After exit, elapsed time should still return last value
        final_elapsed = ctx.get_elapsed_time()
        assert final_elapsed >= 0.09

    @pytest.mark.asyncio
    async def test_multiple_contexts_independent(self):
        """Test multiple TurnContext instances are independent."""
        bus = EventBus()
        ctx1 = TurnContext(bus)
        ctx2 = TurnContext(bus)

        async with ctx1:
            await asyncio.sleep(0.05)

            async with ctx2:
                elapsed2_initial = ctx2.get_elapsed_time()
                await asyncio.sleep(0.05)
                elapsed2_final = ctx2.get_elapsed_time()

            elapsed1_final = ctx1.get_elapsed_time()

        # ctx1 ran longer than ctx2
        assert elapsed1_final > elapsed2_final
        # ctx2 started fresh
        assert elapsed2_initial < 0.01
        # ctx2 measured its own time
        assert 0.04 <= elapsed2_final < 0.1

    @pytest.mark.asyncio
    async def test_context_with_exception(self):
        """Test TurnContext handles exceptions gracefully."""
        bus = EventBus()
        ctx = TurnContext(bus)

        with pytest.raises(ValueError, match="Test exception"):
            async with ctx:
                assert ctx.is_processing
                raise ValueError("Test exception")

        # Context should exit cleanly even after exception
        assert not ctx.is_processing

    @pytest.mark.asyncio
    async def test_emit_event_outside_context(self):
        """Test emit_event works outside async context."""
        bus = EventBus()
        handler = Mock()
        bus.subscribe(handler)

        ctx = TurnContext(bus)

        # Emit before entering context
        event1 = UserMessage(content="before")
        ctx.emit_event(event1)

        async with ctx:
            event2 = UserMessage(content="during")
            ctx.emit_event(event2)

        # Emit after exiting context
        event3 = UserMessage(content="after")
        ctx.emit_event(event3)

        assert handler.call_count == 3

    @pytest.mark.asyncio
    async def test_get_elapsed_time_before_enter(self):
        """Test get_elapsed_time before entering context."""
        bus = EventBus()
        ctx = TurnContext(bus)

        # Before entering, start_time is 0 so elapsed is current time
        elapsed = ctx.get_elapsed_time()
        assert elapsed > 0  # time.time() - 0 gives current timestamp

    @pytest.mark.asyncio
    async def test_nested_event_emission(self):
        """Test handler emitting events during event processing."""
        bus = EventBus()
        ctx = TurnContext(bus)
        received = []

        def recursive_handler(event, config):
            received.append(event)
            # Handler emits another event (tests re-entrancy)
            # Only emit on first user message (before any assistant messages)
            if event.type == "user_message" and not any(e.type == "assistant_message" for e in received):
                ctx.emit_event(AssistantMessage(content="recursive"))

        bus.subscribe(recursive_handler)

        async with ctx:
            ctx.emit_event(UserMessage(content="start"))

        # Should receive: UserMessage, then AssistantMessage emitted by handler
        assert len(received) == 2
        assert received[0].type == "user_message"
        assert received[1].type == "assistant_message"


class TestEventBusAndTurnContextIntegration:
    """Test EventBus and TurnContext working together."""

    @pytest.mark.asyncio
    async def test_full_turn_simulation(self):
        """Test complete turn with events flowing through system."""
        bus = EventBus()
        events_log = []

        def logger_handler(event, config):
            events_log.append((event.type, event))

        bus.subscribe(logger_handler)

        ctx = TurnContext(bus)

        async with ctx as turn:
            # User sends message
            turn.emit_event(UserMessage(content="What is 2+2?"))

            # Assistant responds with tool call
            turn.emit_event(
                ToolCall(
                    name="calculator",
                    id="call_calc_1",
                    arguments={"expression": "2+2"},
                )
            )

            # Tool returns result
            turn.emit_event(
                ToolResult(
                    id="call_calc_1",
                    name="calculator",
                    output="4",
                )
            )

            # Assistant gives final response
            turn.emit_event(AssistantMessage(content="The answer is 4"))

            elapsed = turn.get_elapsed_time()
            assert elapsed >= 0

        assert len(events_log) == 4
        assert events_log[0][0] == "user_message"
        assert events_log[1][0] == "tool_call"
        assert events_log[2][0] == "tool_result"
        assert events_log[3][0] == "assistant_message"

    @pytest.mark.asyncio
    async def test_multiple_turns_with_same_bus(self):
        """Test multiple turns sharing the same event bus."""
        bus = EventBus()
        all_events = []
        bus.subscribe(lambda e, c: all_events.append(e))

        # First turn
        async with TurnContext(bus) as turn1:
            turn1.emit_event(UserMessage(content="turn 1"))

        # Second turn
        async with TurnContext(bus) as turn2:
            turn2.emit_event(UserMessage(content="turn 2"))

        # Third turn
        async with TurnContext(bus) as turn3:
            turn3.emit_event(UserMessage(content="turn 3"))

        assert len(all_events) == 3
        assert all_events[0].content == "turn 1"
        assert all_events[1].content == "turn 2"
        assert all_events[2].content == "turn 3"
