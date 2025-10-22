"""Message event schemas for the conversation event system."""

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class UserMessage(BaseModel):
    """User message event."""

    type: Literal["user_message"] = "user_message"
    content: str = Field(description="User message content")


class AssistantMessage(BaseModel):
    """Assistant message event."""

    type: Literal["assistant_message"] = "assistant_message"
    content: str = Field(description="Assistant message content")


class ToolCall(BaseModel):
    """Tool call event."""

    type: Literal["tool_call"] = "tool_call"
    name: str = Field(description="Tool name")
    id: str = Field(description="Tool call ID")
    arguments: dict[str, Any] = Field(description="Tool arguments")


class ToolResult(BaseModel):
    """Tool result event."""

    type: Literal["tool_result"] = "tool_result"
    id: str = Field(description="Tool call ID")
    name: str = Field(description="Tool name")
    output: str = Field(description="Tool output")


MessageEvent = UserMessage | AssistantMessage | ToolCall | ToolResult
