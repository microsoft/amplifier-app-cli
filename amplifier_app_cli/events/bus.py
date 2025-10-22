"""Event bus and turn context for conversation events."""

import logging
import time
from collections.abc import Callable
from typing import Any

from amplifier_app_cli.events.schemas import MessageEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Simple event bus for publishing and subscribing to message events.

    Subscribers are called synchronously. Errors in handlers are isolated
    and logged to prevent one failing handler from breaking others.
    """

    def __init__(self, config: Any = None) -> None:
        self._subscribers: list[Callable[[MessageEvent, Any], None]] = []
        self._config = config

    def subscribe(self, handler: Callable[[MessageEvent, Any], None]) -> None:
        """Subscribe a handler to receive all message events.

        Args:
            handler: Callable that takes a MessageEvent and config
        """
        self._subscribers.append(handler)

    def publish(self, event: MessageEvent) -> None:
        """Publish an event to all subscribers.

        Errors in handlers are caught and logged to prevent cascading failures.

        Args:
            event: MessageEvent to publish
        """
        for handler in self._subscribers:
            try:
                handler(event, self._config)
            except Exception:
                logger.exception(f"Error in event handler {handler.__name__}")


class TurnContext:
    """Context manager for a complete user turn with timing and event emission.

    Tracks elapsed time from context entry and provides event emission.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.start_time: float = 0
        self.is_processing = False

    async def __aenter__(self) -> "TurnContext":
        """Enter turn context and start timing."""
        self.start_time = time.time()
        self.is_processing = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit turn context."""
        self.is_processing = False

    def emit_event(self, event: MessageEvent) -> None:
        """Emit an event to all subscribers.

        Args:
            event: MessageEvent to emit
        """
        self.event_bus.publish(event)

    def get_elapsed_time(self) -> float:
        """Get elapsed time since turn start in seconds.

        Returns:
            Seconds elapsed since __aenter__
        """
        return time.time() - self.start_time
