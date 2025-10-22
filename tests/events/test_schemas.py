"""Tests for MessageEvent Pydantic models."""

import pytest
from amplifier_app_cli.events.schemas import AssistantMessage
from amplifier_app_cli.events.schemas import ToolCall
from amplifier_app_cli.events.schemas import ToolResult
from amplifier_app_cli.events.schemas import UserMessage
from pydantic import ValidationError


class TestUserMessage:
    """Test UserMessage validation."""

    def test_valid_message(self):
        """Test creating valid user message."""
        msg = UserMessage(content="Hello, world!")
        assert msg.type == "user_message"
        assert msg.content == "Hello, world!"

    def test_empty_content(self):
        """Test user message with empty content is allowed."""
        msg = UserMessage(content="")
        assert msg.content == ""

    def test_missing_content(self):
        """Test user message requires content field."""
        with pytest.raises(ValidationError) as exc_info:
            UserMessage()
        assert "content" in str(exc_info.value)

    def test_multiline_content(self):
        """Test user message with multiline content."""
        content = "Line 1\nLine 2\nLine 3"
        msg = UserMessage(content=content)
        assert msg.content == content

    def test_type_is_literal(self):
        """Test type field is automatically set to literal."""
        msg = UserMessage(content="test")
        assert msg.type == "user_message"

    def test_invalid_type_override(self):
        """Test that type cannot be overridden to invalid value."""
        with pytest.raises(ValidationError):
            UserMessage(type="wrong_type", content="test")


class TestAssistantMessage:
    """Test AssistantMessage validation."""

    def test_valid_message(self):
        """Test creating valid assistant message."""
        msg = AssistantMessage(content="Response")
        assert msg.type == "assistant_message"
        assert msg.content == "Response"

    def test_empty_content(self):
        """Test assistant message with empty content is allowed."""
        msg = AssistantMessage(content="")
        assert msg.content == ""

    def test_missing_content(self):
        """Test assistant message requires content field."""
        with pytest.raises(ValidationError) as exc_info:
            AssistantMessage()
        assert "content" in str(exc_info.value)

    def test_long_content(self):
        """Test assistant message with long content."""
        content = "A" * 10000
        msg = AssistantMessage(content=content)
        assert msg.content == content

    def test_type_is_literal(self):
        """Test type field is automatically set to literal."""
        msg = AssistantMessage(content="test")
        assert msg.type == "assistant_message"


class TestToolCall:
    """Test ToolCall validation."""

    def test_valid_tool_call(self):
        """Test creating valid tool call."""
        call = ToolCall(
            name="read_file",
            id="call_123",
            arguments={"path": "/tmp/test.txt"},
        )
        assert call.type == "tool_call"
        assert call.name == "read_file"
        assert call.id == "call_123"
        assert call.arguments == {"path": "/tmp/test.txt"}

    def test_empty_arguments(self):
        """Test tool call with empty arguments dict."""
        call = ToolCall(name="list_files", id="call_456", arguments={})
        assert call.arguments == {}

    def test_nested_arguments(self):
        """Test tool call with nested arguments."""
        call = ToolCall(
            name="complex_tool",
            id="call_789",
            arguments={
                "config": {"timeout": 30, "retry": True},
                "items": [1, 2, 3],
            },
        )
        assert call.arguments["config"]["timeout"] == 30
        assert call.arguments["items"] == [1, 2, 3]

    def test_missing_required_fields(self):
        """Test tool call requires name, id, and arguments."""
        with pytest.raises(ValidationError) as exc_info:
            ToolCall(name="test")
        assert "id" in str(exc_info.value)
        assert "arguments" in str(exc_info.value)

    def test_arguments_must_be_dict(self):
        """Test arguments must be a dict."""
        with pytest.raises(ValidationError):
            ToolCall(name="test", id="call_001", arguments="not a dict")

    def test_arguments_with_various_types(self):
        """Test arguments can contain various JSON-compatible types."""
        call = ToolCall(
            name="multi_type",
            id="call_999",
            arguments={
                "string": "text",
                "number": 42,
                "float": 3.14,
                "bool": True,
                "null": None,
                "list": [1, 2, 3],
                "dict": {"nested": "value"},
            },
        )
        assert call.arguments["string"] == "text"
        assert call.arguments["number"] == 42
        assert call.arguments["bool"] is True
        assert call.arguments["null"] is None

    def test_type_is_literal(self):
        """Test type field is automatically set to literal."""
        call = ToolCall(name="test", id="call_1", arguments={})
        assert call.type == "tool_call"


class TestToolResult:
    """Test ToolResult validation."""

    def test_valid_tool_result(self):
        """Test creating valid tool result."""
        result = ToolResult(
            id="call_123",
            name="read_file",
            output="File contents here",
        )
        assert result.type == "tool_result"
        assert result.id == "call_123"
        assert result.name == "read_file"
        assert result.output == "File contents here"

    def test_empty_output(self):
        """Test tool result with empty output is allowed."""
        result = ToolResult(id="call_456", name="list_files", output="")
        assert result.output == ""

    def test_multiline_output(self):
        """Test tool result with multiline output."""
        output = "Line 1\nLine 2\nLine 3"
        result = ToolResult(id="call_789", name="bash", output=output)
        assert result.output == output

    def test_missing_required_fields(self):
        """Test tool result requires id, name, and output."""
        with pytest.raises(ValidationError) as exc_info:
            ToolResult(id="call_001")
        assert "name" in str(exc_info.value)
        assert "output" in str(exc_info.value)

    def test_long_output(self):
        """Test tool result with long output."""
        output = "X" * 50000
        result = ToolResult(id="call_999", name="large_tool", output=output)
        assert result.output == output

    def test_type_is_literal(self):
        """Test type field is automatically set to literal."""
        result = ToolResult(id="call_1", name="test", output="result")
        assert result.type == "tool_result"


class TestMessageEventUnion:
    """Test MessageEvent union type behavior."""

    def test_discriminated_union_user_message(self):
        """Test union discriminates UserMessage correctly."""
        from amplifier_app_cli.events.schemas import MessageEvent

        msg: MessageEvent = UserMessage(content="test")
        assert msg.type == "user_message"

    def test_discriminated_union_assistant_message(self):
        """Test union discriminates AssistantMessage correctly."""
        from amplifier_app_cli.events.schemas import MessageEvent

        msg: MessageEvent = AssistantMessage(content="test")
        assert msg.type == "assistant_message"

    def test_discriminated_union_tool_call(self):
        """Test union discriminates ToolCall correctly."""
        from amplifier_app_cli.events.schemas import MessageEvent

        call: MessageEvent = ToolCall(name="test", id="call_1", arguments={})
        assert call.type == "tool_call"

    def test_discriminated_union_tool_result(self):
        """Test union discriminates ToolResult correctly."""
        from amplifier_app_cli.events.schemas import MessageEvent

        result: MessageEvent = ToolResult(id="call_1", name="test", output="result")
        assert result.type == "tool_result"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_unicode_content(self):
        """Test messages with unicode content."""
        content = "Hello 世界 🌍 مرحبا"
        msg = UserMessage(content=content)
        assert msg.content == content

    def test_special_characters_in_tool_name(self):
        """Test tool names with special characters."""
        call = ToolCall(name="my_tool-v2.0", id="call_1", arguments={})
        assert call.name == "my_tool-v2.0"

    def test_very_long_tool_id(self):
        """Test tool call with very long ID."""
        long_id = "call_" + "x" * 1000
        call = ToolCall(name="test", id=long_id, arguments={})
        assert call.id == long_id

    def test_arguments_with_empty_strings(self):
        """Test arguments containing empty string values."""
        call = ToolCall(
            name="test",
            id="call_1",
            arguments={"empty": "", "not_empty": "value"},
        )
        assert call.arguments["empty"] == ""
        assert call.arguments["not_empty"] == "value"

    def test_serialization_roundtrip(self):
        """Test that models can serialize and deserialize."""
        original = ToolCall(
            name="test_tool",
            id="call_abc",
            arguments={"key": "value", "number": 42},
        )
        json_data = original.model_dump()
        reconstructed = ToolCall(**json_data)
        assert reconstructed.name == original.name
        assert reconstructed.id == original.id
        assert reconstructed.arguments == original.arguments
