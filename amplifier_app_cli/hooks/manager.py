"""Hooks manager for CLI integration.

Provides high-level management of hooks including:
- Loading hooks from configuration
- Discovering hook scripts
- Registering hooks with the kernel
- Emitting CLI-specific events
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

from .config import HooksConfig, load_hooks_config, discover_hook_scripts
from .events import (
    PRE_TOOL_USE,
    POST_TOOL_USE,
    SESSION_START,
    SESSION_END,
    NOTIFICATION,
    TOOL_PRE,
    TOOL_POST,
)
from .external import ExternalCommandHook
from .models import HookConfig, HookResult, HookType

logger = logging.getLogger(__name__)


class HooksManager:
    """Manages CLI hooks lifecycle and integration.

    Responsibilities:
    - Load hooks from configuration
    - Discover hook scripts from filesystem
    - Create hook handlers from configurations
    - Register hooks with kernel's hook system
    - Emit CLI-specific events
    - Track hook execution statistics

    Attributes:
        hooks_config: Loaded hooks configuration
        handlers: Map of hook name to handler
        stats: Execution statistics
    """

    def __init__(
        self,
        config_manager=None,
        search_paths: list[Path] | None = None,
    ):
        """Initialize hooks manager.

        Args:
            config_manager: Optional config manager for loading settings
            search_paths: Paths to search for hook scripts
        """
        self.config_manager = config_manager
        self.search_paths = search_paths or []
        self.hooks_config: HooksConfig | None = None
        self.handlers: dict[str, Callable] = {}
        self.stats: dict[str, dict[str, Any]] = {}
        self._registered_unregister_fns: list[Callable] = []

    def load(self) -> None:
        """Load hooks from configuration and discover scripts."""
        # Load from settings
        if self.config_manager:
            self.hooks_config = load_hooks_config(self.config_manager)
        else:
            self.hooks_config = HooksConfig()

        # Discover hook scripts
        discovered = discover_hook_scripts(self.search_paths)
        for hook_config in discovered:
            # Don't override configured hooks
            if not any(h.name == hook_config.name for h in self.hooks_config.hooks):
                self.hooks_config.hooks.append(hook_config)
                logger.debug(f"Discovered hook script: {hook_config.name}")

        # Create handlers
        for hook_config in self.hooks_config.hooks:
            handler = self._create_handler(hook_config)
            if handler:
                self.handlers[hook_config.name] = handler
                self.stats[hook_config.name] = {
                    "calls": 0,
                    "errors": 0,
                    "total_duration_ms": 0,
                }

        logger.info(f"Loaded {len(self.handlers)} hooks")

    def _create_handler(self, config: HookConfig) -> Callable | None:
        """Create a handler from hook configuration.

        Args:
            config: Hook configuration

        Returns:
            Async callable handler or None if invalid
        """
        if config.type == HookType.COMMAND:
            # External command hook
            working_dir = Path.cwd()
            if self.search_paths:
                # Use first search path as base
                working_dir = self.search_paths[0]
            return ExternalCommandHook(config, working_dir)

        elif config.type == HookType.INTERNAL:
            # Internal hooks are registered directly, not created here
            return None

        elif config.type == HookType.LLM:
            # LLM hooks - try to create if dependencies available
            try:
                from .llm import LLMHookExecutor
                
                # Get model from settings or use default
                model_name = "claude-3-5-haiku-20241022"  # Fast, cheap model
                if self.config_manager:
                    settings = self.config_manager.get_merged_settings()
                    model_name = settings.get("hooks", {}).get("llm_model", model_name)
                
                return LLMHookExecutor(config, model_name=model_name)
            
            except ImportError:
                logger.warning(
                    f"LLM hook {config.name} skipped: pydantic-ai not available. "
                    f"Install with: uv pip install pydantic-ai"
                )
                return None
            except Exception as e:
                logger.error(f"Failed to create LLM hook {config.name}: {e}")
                return None

        elif config.type == HookType.INLINE:
            # Inline matcher hooks
            try:
                from .inline import InlineHookExecutor
                return InlineHookExecutor(config)
            except Exception as e:
                logger.error(f"Failed to create inline hook {config.name}: {e}")
                return None

        return None

    async def register_with_session(self, session) -> None:
        """Register hooks with a session's hook system.

        Args:
            session: AmplifierSession instance
        """
        hooks = session.coordinator.get("hooks")
        if not hooks:
            logger.warning("No hooks system available in session")
            return

        # Clear any previous registrations
        for unregister in self._registered_unregister_fns:
            try:
                unregister()
            except Exception:
                pass
        self._registered_unregister_fns.clear()

        # Register handlers
        for hook_config in self.hooks_config.hooks if self.hooks_config else []:
            handler = self.handlers.get(hook_config.name)
            if not handler:
                continue

            # Map CLI events to kernel events
            events_to_register = []
            for event in hook_config.matcher.events or ["tool:pre", "tool:post"]:
                if event == PRE_TOOL_USE:
                    events_to_register.append(TOOL_PRE)
                elif event == POST_TOOL_USE:
                    events_to_register.append(TOOL_POST)
                else:
                    events_to_register.append(event)

            # Create wrapper that tracks stats
            wrapped_handler = self._wrap_handler(hook_config.name, handler)

            # Register for each event
            for event in events_to_register:
                if hasattr(hooks, "register"):
                    unregister = hooks.register(
                        event,
                        wrapped_handler,
                        priority=hook_config.priority,
                        name=hook_config.name,
                    )
                    if unregister:
                        self._registered_unregister_fns.append(unregister)

        logger.debug(f"Registered {len(self._registered_unregister_fns)} hook handlers")

    def _wrap_handler(
        self,
        name: str,
        handler: Callable,
    ) -> Callable[[str, dict], Awaitable[HookResult]]:
        """Wrap a handler to track statistics.

        Args:
            name: Hook name
            handler: Original handler

        Returns:
            Wrapped async handler
        """
        async def wrapped(event: str, data: dict[str, Any]) -> HookResult:
            import time
            start = time.time()

            try:
                result = await handler(event, data)
                duration_ms = (time.time() - start) * 1000

                # Update stats
                stats = self.stats.get(name, {})
                stats["calls"] = stats.get("calls", 0) + 1
                stats["total_duration_ms"] = stats.get("total_duration_ms", 0) + duration_ms

                return result

            except Exception as e:
                logger.exception(f"Hook {name} failed")

                # Update error stats
                stats = self.stats.get(name, {})
                stats["errors"] = stats.get("errors", 0) + 1

                return HookResult.error(str(e))

        return wrapped

    async def emit(self, session, event: str, data: dict[str, Any]) -> list[HookResult]:
        """Emit a CLI event through the session's hooks.

        This is for CLI-specific events that may not be emitted by the kernel.

        Args:
            session: AmplifierSession instance
            event: Event name
            data: Event data

        Returns:
            List of hook results
        """
        hooks = session.coordinator.get("hooks")
        if not hooks or not hasattr(hooks, "emit"):
            return []

        try:
            return await hooks.emit(event, data)
        except Exception as e:
            logger.error(f"Error emitting event {event}: {e}")
            return []

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """Get hook execution statistics.

        Returns:
            Dict of hook name to stats
        """
        return dict(self.stats)

    def get_hooks_for_event(self, event: str) -> list[HookConfig]:
        """Get hooks configured for an event.

        Args:
            event: Event name

        Returns:
            List of matching hook configurations
        """
        if not self.hooks_config:
            return []
        return self.hooks_config.get_hooks_for_event(event)

    def disable_hook(self, name: str) -> bool:
        """Disable a hook by name.

        Args:
            name: Hook name

        Returns:
            True if hook was disabled
        """
        if not self.hooks_config:
            return False

        for hook in self.hooks_config.hooks:
            if hook.name == name:
                hook.enabled = False
                logger.info(f"Disabled hook: {name}")
                return True

        return False

    def enable_hook(self, name: str) -> bool:
        """Enable a hook by name.

        Args:
            name: Hook name

        Returns:
            True if hook was enabled
        """
        if not self.hooks_config:
            return False

        for hook in self.hooks_config.hooks:
            if hook.name == name:
                hook.enabled = True
                logger.info(f"Enabled hook: {name}")
                return True

        return False

    def list_hooks(self) -> list[dict[str, Any]]:
        """List all loaded hooks.

        Returns:
            List of hook info dicts
        """
        result = []
        for hook in self.hooks_config.hooks if self.hooks_config else []:
            stats = self.stats.get(hook.name, {})
            result.append({
                "name": hook.name,
                "type": hook.type.value,
                "enabled": hook.enabled,
                "priority": hook.priority,
                "events": hook.matcher.events,
                "tools": hook.matcher.tools,
                "calls": stats.get("calls", 0),
                "errors": stats.get("errors", 0),
                "description": hook.description,
            })
        return result
