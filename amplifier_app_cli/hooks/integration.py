"""Integration wrappers for hooks system.

Provides wrapper functions for integrating hooks into tool execution
and session lifecycle without tight coupling.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .events import (
    PRE_TOOL_USE,
    POST_TOOL_USE,
    SESSION_START,
    SESSION_END,
    ERROR,
    CHECKPOINT,
    MODEL_SWITCH,
    MEMORY_UPDATE,
)

logger = logging.getLogger(__name__)


class ToolDeniedError(Exception):
    """Raised when a hook denies a tool call."""
    pass


class ToolExecutionHooks:
    """Wrapper for tool execution with hooks.
    
    Integrates PreToolUse and PostToolUse hooks into tool calls.
    """
    
    def __init__(self, hooks_manager=None):
        """Initialize tool execution hooks.
        
        Args:
            hooks_manager: HooksManager instance (optional)
        """
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
        2. Check for deny action
        3. Execute tool with potentially modified args
        4. Emit PostToolUse event
        5. Return result
        
        Args:
            session: AmplifierSession instance
            tool_name: Name of tool being called
            tool_args: Arguments for tool
            tool_fn: Async callable that executes tool
            
        Returns:
            Tool result
            
        Raises:
            ToolDeniedError: If hook denies tool call
        """
        # If no hooks manager, just execute tool
        if not self.hooks_manager:
            return await tool_fn(**tool_args)
        
        # Pre-tool hooks
        pre_event_data = {
            "tool": tool_name,
            "args": tool_args.copy(),
            "session_id": getattr(session, "session_id", None),
        }
        
        try:
            pre_results = await self.hooks_manager.emit(
                session, PRE_TOOL_USE, pre_event_data
            )
            
            # Check for deny or modify actions
            for result in pre_results:
                if result.action == "deny":
                    reason = result.reason or "Tool call denied by hook"
                    logger.info(f"Tool {tool_name} denied by hook: {reason}")
                    raise ToolDeniedError(reason)
                
                elif result.action == "modify" and result.modified_data:
                    # Apply modifications to args
                    if "args" in result.modified_data:
                        tool_args = result.modified_data["args"]
                        logger.debug(f"Tool {tool_name} args modified by hook")
        
        except ToolDeniedError:
            raise
        except Exception as e:
            logger.error(f"Error in pre-tool hooks: {e}")
            # Continue execution on hook errors
        
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
            # Post-tool hooks (always fire)
            duration_ms = (time.time() - start_time) * 1000
            post_event_data = {
                "tool": tool_name,
                "args": tool_args,
                "result": result,
                "error": str(error) if error else None,
                "duration_ms": duration_ms,
                "session_id": getattr(session, "session_id", None),
            }
            
            try:
                await self.hooks_manager.emit(
                    session, POST_TOOL_USE, post_event_data
                )
            except Exception as e:
                logger.error(f"Error in post-tool hooks: {e}")
        
        return result


class SessionLifecycleHooks:
    """Wrapper for session lifecycle with hooks.
    
    Provides methods to fire lifecycle events.
    """
    
    def __init__(self, hooks_manager=None):
        """Initialize session lifecycle hooks.
        
        Args:
            hooks_manager: HooksManager instance (optional)
        """
        self.hooks_manager = hooks_manager
    
    async def on_session_start(
        self,
        session,
        profile: str | None = None,
        config: dict | None = None,
    ):
        """Fire SessionStart event.
        
        Args:
            session: AmplifierSession instance
            profile: Active profile name
            config: Session configuration
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "session_id": getattr(session, "session_id", None),
            "event_type": "start",
            "profile": profile,
            "config": config or {},
        }
        
        try:
            await self.hooks_manager.emit(session, SESSION_START, event_data)
        except Exception as e:
            logger.error(f"Error in session start hooks: {e}")
    
    async def on_session_end(
        self,
        session,
        duration_ms: float | None = None,
        exit_reason: str | None = None,
    ):
        """Fire SessionEnd event.
        
        Args:
            session: AmplifierSession instance
            duration_ms: Session duration in milliseconds
            exit_reason: Why session ended
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "session_id": getattr(session, "session_id", None),
            "event_type": "end",
            "duration_ms": duration_ms,
            "exit_reason": exit_reason,
        }
        
        try:
            await self.hooks_manager.emit(session, SESSION_END, event_data)
        except Exception as e:
            logger.error(f"Error in session end hooks: {e}")
    
    async def on_error(
        self,
        session,
        error: Exception,
        tool: str | None = None,
        severity: str = "error",
    ):
        """Fire Error event.
        
        Args:
            session: AmplifierSession instance
            error: Exception that occurred
            tool: Tool that caused error (if applicable)
            severity: Error severity level
        """
        if not self.hooks_manager:
            return
        
        import traceback
        
        event_data = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "tool": tool,
            "session_id": getattr(session, "session_id", None),
            "stack_trace": "".join(traceback.format_tb(error.__traceback__)),
            "severity": severity,
        }
        
        try:
            await self.hooks_manager.emit(session, ERROR, event_data)
        except Exception as e:
            logger.error(f"Error in error hooks: {e}")
    
    async def on_checkpoint(
        self,
        session,
        checkpoint_id: str,
        checkpoint_type: str = "auto",
        message_count: int = 0,
        storage_path: str | None = None,
    ):
        """Fire Checkpoint event.
        
        Args:
            session: AmplifierSession instance
            checkpoint_id: Unique checkpoint identifier
            checkpoint_type: Type of checkpoint (auto, manual, periodic)
            message_count: Messages since last checkpoint
            storage_path: Path where checkpoint is stored
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "checkpoint_id": checkpoint_id,
            "session_id": getattr(session, "session_id", None),
            "checkpoint_type": checkpoint_type,
            "message_count": message_count,
            "storage_path": storage_path,
        }
        
        try:
            await self.hooks_manager.emit(session, CHECKPOINT, event_data)
        except Exception as e:
            logger.error(f"Error in checkpoint hooks: {e}")
    
    async def on_model_switch(
        self,
        session,
        old_model: str | None,
        new_model: str,
        reason: str | None = None,
        triggered_by: str = "user",
        profile: str | None = None,
    ):
        """Fire ModelSwitch event.
        
        Args:
            session: AmplifierSession instance
            old_model: Previous model name
            new_model: New model name
            reason: Why model switched
            triggered_by: What triggered the switch
            profile: Active profile name
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "old_model": old_model,
            "new_model": new_model,
            "reason": reason,
            "session_id": getattr(session, "session_id", None),
            "profile": profile,
            "triggered_by": triggered_by,
        }
        
        try:
            await self.hooks_manager.emit(session, MODEL_SWITCH, event_data)
        except Exception as e:
            logger.error(f"Error in model switch hooks: {e}")
    
    async def on_memory_update(
        self,
        session,
        file_path: str,
        update_type: str = "modified",
        content_size: int | None = None,
    ):
        """Fire MemoryUpdate event.
        
        Args:
            session: AmplifierSession instance
            file_path: Path to memory file
            update_type: Type of update (created, modified, deleted)
            content_size: Size of file in bytes
        """
        if not self.hooks_manager:
            return
        
        event_data = {
            "file_path": file_path,
            "update_type": update_type,
            "session_id": getattr(session, "session_id", None),
            "content_size": content_size,
        }
        
        try:
            await self.hooks_manager.emit(session, MEMORY_UPDATE, event_data)
        except Exception as e:
            logger.error(f"Error in memory update hooks: {e}")
