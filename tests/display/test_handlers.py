"""Tests for display event handlers."""

from io import StringIO
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from amplifier_app_cli.display.handlers import display_assistant_message
from amplifier_app_cli.display.handlers import display_tool_call
from amplifier_app_cli.display.handlers import display_tool_result
from amplifier_app_cli.display.handlers import display_user_message
from amplifier_app_cli.display.handlers import handle_event
from amplifier_app_cli.events.schemas import AssistantMessage
from amplifier_app_cli.events.schemas import ToolCall
from amplifier_app_cli.events.schemas import ToolResult
from amplifier_app_cli.events.schemas import UserMessage
from amplifier_app_cli.profile_system.schema import UIConfig
from rich.console import Console


@pytest.fixture
def default_ui_config() -> UIConfig:
    """Create default UI configuration."""
    return UIConfig()


@pytest.fixture
def console_output() -> tuple[Console, StringIO]:
    """Create a Rich Console with string capture."""
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=80)
    return console, output


class TestDisplayUserMessage:
    """Test display_user_message() handler."""

    def test_prints_blank_line(self, default_ui_config: UIConfig) -> None:
        """Test that user message displays a blank line."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = UserMessage(content="Hello")
            display_user_message(event, default_ui_config)

        result = output.getvalue()
        assert result == "\n"

    def test_ignores_content(self, default_ui_config: UIConfig) -> None:
        """Test that actual message content is ignored."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = UserMessage(content="This should not appear")
            display_user_message(event, default_ui_config)

        result = output.getvalue()
        assert "This should not appear" not in result


class TestDisplayAssistantMessage:
    """Test display_assistant_message() handler."""

    def test_empty_content_displays_nothing(self, default_ui_config: UIConfig) -> None:
        """Test that empty content produces no output."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = AssistantMessage(content="")
            display_assistant_message(event, default_ui_config)

        result = output.getvalue()
        assert result == ""

    def test_displays_markdown_when_enabled(self, default_ui_config: UIConfig) -> None:
        """Test markdown rendering when render_markdown=True."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            config = UIConfig(render_markdown=True)
            event = AssistantMessage(content="**bold** text")
            display_assistant_message(event, config)

        result = output.getvalue()
        assert "bold" in result

    def test_displays_plain_text_when_disabled(self, default_ui_config: UIConfig) -> None:
        """Test plain text display when render_markdown=False."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            config = UIConfig(render_markdown=False)
            event = AssistantMessage(content="**bold** text")
            display_assistant_message(event, config)

        result = output.getvalue()
        assert "**bold** text" in result

    def test_includes_bullet_marker(self, default_ui_config: UIConfig) -> None:
        """Test that output includes bullet marker."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = AssistantMessage(content="Hello")
            display_assistant_message(event, default_ui_config)

        result = output.getvalue()
        assert "●" in result

    def test_strips_whitespace(self, default_ui_config: UIConfig) -> None:
        """Test that content whitespace is stripped."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = AssistantMessage(content="  \n  content  \n  ")
            display_assistant_message(event, default_ui_config)

        result = output.getvalue()
        assert "content" in result

    def test_adds_blank_line_after(self, default_ui_config: UIConfig) -> None:
        """Test that blank line is added after message."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = AssistantMessage(content="Hello")
            display_assistant_message(event, default_ui_config)

        result = output.getvalue()
        assert result.endswith("\n\n")


class TestDisplayToolCall:
    """Test display_tool_call() handler."""

    def test_displays_tool_name(self, default_ui_config: UIConfig) -> None:
        """Test that tool name is displayed."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolCall(name="read_file", id="call_123", arguments={})
            display_tool_call(event, default_ui_config)

        result = output.getvalue()
        assert "read_file" in result

    def test_displays_empty_arguments(self, default_ui_config: UIConfig) -> None:
        """Test that empty arguments show as ()."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolCall(name="list_files", id="call_123", arguments={})
            display_tool_call(event, default_ui_config)

        result = output.getvalue()
        assert "()" in result

    def test_displays_arguments(self, default_ui_config: UIConfig) -> None:
        """Test that arguments are formatted and displayed."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolCall(name="read_file", id="call_123", arguments={"path": "test.txt"})
            display_tool_call(event, default_ui_config)

        result = output.getvalue()
        assert "path" in result
        assert "test.txt" in result

    def test_respects_max_arg_length(self, default_ui_config: UIConfig) -> None:
        """Test that max_arg_length config is respected."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            long_value = "a" * 200
            config = UIConfig(max_arg_length=50)
            event = ToolCall(name="write_file", id="call_123", arguments={"content": long_value})
            display_tool_call(event, config)

        result = output.getvalue()
        assert "..." in result
        assert len(result) < 300  # Should be truncated

    def test_includes_colored_marker(self, default_ui_config: UIConfig) -> None:
        """Test that output includes colored marker."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolCall(name="test", id="call_123", arguments={})
            display_tool_call(event, default_ui_config)

        result = output.getvalue()
        assert "●" in result


class TestDisplayToolResult:
    """Test display_tool_result() handler."""

    def test_empty_output_shows_blank_line(self, default_ui_config: UIConfig) -> None:
        """Test that empty output produces blank line."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolResult(id="call_123", name="test", output="")
            display_tool_result(event, default_ui_config)

        result = output.getvalue()
        assert result == "\n"

    def test_single_line_output(self, default_ui_config: UIConfig) -> None:
        """Test displaying single line output."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolResult(id="call_123", name="test", output="single line")
            display_tool_result(event, default_ui_config)

        result = output.getvalue()
        assert "single line" in result

    def test_truncation_at_line_limit(self, default_ui_config: UIConfig) -> None:
        """Test that output is truncated to configured line limit."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            lines = [f"line{i}" for i in range(10)]
            config = UIConfig(tool_output_lines=3)
            event = ToolResult(id="call_123", name="test", output="\n".join(lines))
            display_tool_result(event, config)

        result = output.getvalue()
        assert "line0" in result
        assert "line1" in result
        assert "line2" in result
        assert "line9" not in result

    def test_shows_more_lines_indicator(self, default_ui_config: UIConfig) -> None:
        """Test that 'more lines' indicator is shown when truncated."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            lines = [f"line{i}" for i in range(10)]
            config = UIConfig(tool_output_lines=3)
            event = ToolResult(id="call_123", name="test", output="\n".join(lines))
            display_tool_result(event, config)

        result = output.getvalue()
        assert "7 more lines" in result

    def test_no_indicator_when_not_truncated(self, default_ui_config: UIConfig) -> None:
        """Test that no indicator is shown when all lines fit."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            config = UIConfig(tool_output_lines=5)
            event = ToolResult(id="call_123", name="test", output="line1\nline2")
            display_tool_result(event, config)

        result = output.getvalue()
        assert "more lines" not in result

    def test_show_all_lines_with_negative_one(self, default_ui_config: UIConfig) -> None:
        """Test that -1 shows all lines without truncation."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            lines = [f"line{i}" for i in range(50)]
            config = UIConfig(tool_output_lines=-1)
            event = ToolResult(id="call_123", name="test", output="\n".join(lines))
            display_tool_result(event, config)

        result = output.getvalue()
        assert "line0" in result
        assert "line49" in result
        assert "more lines" not in result

    def test_tree_formatting_enabled(self, default_ui_config: UIConfig) -> None:
        """Test tree formatting when use_tree_formatting=True."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            config = UIConfig(use_tree_formatting=True, tool_output_lines=-1)
            event = ToolResult(id="call_123", name="test", output="line1\nline2\nline3")
            display_tool_result(event, config)

        result = output.getvalue()
        assert "⎿" in result  # First line should have branch

    def test_tree_formatting_disabled(self, default_ui_config: UIConfig) -> None:
        """Test plain formatting when use_tree_formatting=False."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            config = UIConfig(use_tree_formatting=False, tool_output_lines=-1)
            event = ToolResult(id="call_123", name="test", output="line1\nline2\nline3")
            display_tool_result(event, config)

        result = output.getvalue()
        assert "⎿" not in result  # No tree characters

    def test_strips_line_whitespace(self, default_ui_config: UIConfig) -> None:
        """Test that individual line whitespace is stripped."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            config = UIConfig(tool_output_lines=-1)
            event = ToolResult(id="call_123", name="test", output="  line1  \n  line2  ")
            display_tool_result(event, config)

        result = output.getvalue()
        assert "line1" in result
        assert "line2" in result

    def test_adds_blank_line_after(self, default_ui_config: UIConfig) -> None:
        """Test that blank line is added after output."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            event = ToolResult(id="call_123", name="test", output="content")
            display_tool_result(event, default_ui_config)

        result = output.getvalue()
        assert result.endswith("\n\n")


class TestHandleEvent:
    """Test handle_event() dispatcher."""

    def test_routes_user_message(self, default_ui_config: UIConfig) -> None:
        """Test that UserMessage events are routed correctly."""
        with patch("amplifier_app_cli.display.handlers.display_user_message") as mock:
            event = UserMessage(content="test")
            handle_event(event, default_ui_config)
            mock.assert_called_once_with(event, default_ui_config)

    def test_routes_assistant_message(self, default_ui_config: UIConfig) -> None:
        """Test that AssistantMessage events are routed correctly."""
        with patch("amplifier_app_cli.display.handlers.display_assistant_message") as mock:
            event = AssistantMessage(content="test")
            handle_event(event, default_ui_config)
            mock.assert_called_once_with(event, default_ui_config)

    def test_routes_tool_call(self, default_ui_config: UIConfig) -> None:
        """Test that ToolCall events are routed correctly."""
        with patch("amplifier_app_cli.display.handlers.display_tool_call") as mock:
            event = ToolCall(name="test", id="call_123", arguments={})
            handle_event(event, default_ui_config)
            mock.assert_called_once_with(event, default_ui_config)

    def test_routes_tool_result(self, default_ui_config: UIConfig) -> None:
        """Test that ToolResult events are routed correctly."""
        with patch("amplifier_app_cli.display.handlers.display_tool_result") as mock:
            event = ToolResult(id="call_123", name="test", output="result")
            handle_event(event, default_ui_config)
            mock.assert_called_once_with(event, default_ui_config)

    def test_ignores_unknown_event_types(self, default_ui_config: UIConfig) -> None:
        """Test that unknown event types are silently ignored."""
        mock_event = MagicMock()
        mock_event.__class__.__name__ = "UnknownEvent"
        handle_event(mock_event, default_ui_config)  # Should not raise


class TestIntegration:
    """Integration tests for display handlers."""

    def test_complete_conversation_flow(self, default_ui_config: UIConfig) -> None:
        """Test displaying a complete conversation sequence."""
        output = StringIO()
        with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
            # User message
            handle_event(UserMessage(content="Hello"), default_ui_config)

            # Assistant response
            handle_event(AssistantMessage(content="Let me help"), default_ui_config)

            # Tool call
            handle_event(ToolCall(name="read_file", id="call_1", arguments={"path": "test.txt"}), default_ui_config)

            # Tool result
            handle_event(ToolResult(id="call_1", name="read_file", output="file contents"), default_ui_config)

            # Final response
            handle_event(AssistantMessage(content="Here's the file"), default_ui_config)

        result = output.getvalue()
        assert "Let me help" in result
        assert "read_file" in result
        assert "file contents" in result
        assert "Here's the file" in result

    def test_config_variations_dont_break(self) -> None:
        """Test that various config combinations work without errors."""
        configs = [
            UIConfig(render_markdown=True, use_tree_formatting=True, tool_output_lines=3),
            UIConfig(render_markdown=False, use_tree_formatting=False, tool_output_lines=1),
            UIConfig(render_markdown=True, use_tree_formatting=False, tool_output_lines=-1),
            UIConfig(render_markdown=False, use_tree_formatting=True, tool_output_lines=10),
        ]

        events = [
            UserMessage(content="test"),
            AssistantMessage(content="response"),
            ToolCall(name="tool", id="call_1", arguments={"key": "value"}),
            ToolResult(id="call_1", name="tool", output="result\nline2\nline3"),
        ]

        for config in configs:
            output = StringIO()
            with patch("amplifier_app_cli.display.handlers.console", Console(file=output, force_terminal=False)):
                for event in events:
                    handle_event(event, config)

            # Should not raise and should produce output
            assert len(output.getvalue()) > 0
