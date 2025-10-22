"""Tests for display formatting utilities."""

from amplifier_app_cli.display.formatters import format_tool_arguments
from amplifier_app_cli.display.formatters import format_tree_line
from amplifier_app_cli.display.formatters import truncate_output


class TestFormatToolArguments:
    """Test format_tool_arguments() function."""

    def test_empty_dict(self) -> None:
        """Test formatting empty dictionary."""
        result = format_tool_arguments({})
        assert result == "()"

    def test_single_string_argument(self) -> None:
        """Test formatting single string argument."""
        result = format_tool_arguments({"name": "value"})
        assert result == '(name="value")'

    def test_single_non_string_argument(self) -> None:
        """Test formatting single non-string argument."""
        result = format_tool_arguments({"count": 42})
        assert result == "(count=42)"

    def test_multiple_arguments(self) -> None:
        """Test formatting multiple arguments."""
        result = format_tool_arguments({"name": "file.txt", "count": 42, "flag": True})
        assert 'name="file.txt"' in result
        assert "count=42" in result
        assert "flag=True" in result

    def test_truncation_at_default_length(self) -> None:
        """Test that values are truncated at default 50 chars."""
        long_value = "a" * 60
        result = format_tool_arguments({"key": long_value})
        assert len(result) < len(long_value) + 10  # Account for key name and formatting
        assert "..." in result
        # Check that we truncated to 50 chars plus ellipsis
        assert result == f'(key="{"a" * 50}...")'

    def test_truncation_at_custom_length(self) -> None:
        """Test that values are truncated at custom max_length."""
        long_value = "b" * 40
        result = format_tool_arguments({"key": long_value}, max_length=20)
        assert result == f'(key="{"b" * 20}...")'

    def test_no_truncation_when_under_limit(self) -> None:
        """Test that short values are not truncated."""
        result = format_tool_arguments({"key": "short"})
        assert result == '(key="short")'
        assert "..." not in result

    def test_whitespace_normalization(self) -> None:
        """Test that multiple whitespace characters are normalized to single spaces."""
        result = format_tool_arguments({"key": "hello\n\t  world"})
        assert result == '(key="hello world")'

    def test_string_quoting(self) -> None:
        """Test that string values are quoted but non-strings are not."""
        result = format_tool_arguments({"str": "text", "num": 123, "bool": True})
        assert 'str="text"' in result
        assert "num=123" in result
        assert "bool=True" in result

    def test_boolean_values(self) -> None:
        """Test formatting boolean values."""
        result = format_tool_arguments({"enabled": True, "disabled": False})
        assert "enabled=True" in result
        assert "disabled=False" in result

    def test_none_value(self) -> None:
        """Test formatting None value."""
        result = format_tool_arguments({"value": None})
        assert "value=None" in result

    def test_list_value(self) -> None:
        """Test formatting list value."""
        result = format_tool_arguments({"items": [1, 2, 3]})
        assert "items=[1, 2, 3]" in result

    def test_dict_value(self) -> None:
        """Test formatting nested dict value."""
        result = format_tool_arguments({"config": {"key": "value"}})
        assert "config={'key': 'value'}" in result


class TestTruncateOutput:
    """Test truncate_output() function."""

    def test_empty_output(self) -> None:
        """Test truncating empty output."""
        lines, total = truncate_output("", 3)
        assert lines == [""]
        assert total == 1

    def test_single_line(self) -> None:
        """Test truncating single line output."""
        lines, total = truncate_output("single line", 3)
        assert lines == ["single line"]
        assert total == 1

    def test_multiple_lines_under_limit(self) -> None:
        """Test output with fewer lines than limit."""
        output = "line1\nline2"
        lines, total = truncate_output(output, 5)
        assert lines == ["line1", "line2"]
        assert total == 2

    def test_multiple_lines_at_limit(self) -> None:
        """Test output with exactly the line limit."""
        output = "line1\nline2\nline3"
        lines, total = truncate_output(output, 3)
        assert lines == ["line1", "line2", "line3"]
        assert total == 3

    def test_multiple_lines_over_limit(self) -> None:
        """Test truncation when output exceeds limit."""
        output = "line1\nline2\nline3\nline4\nline5"
        lines, total = truncate_output(output, 3)
        assert lines == ["line1", "line2", "line3"]
        assert total == 5

    def test_show_all_lines_with_negative_one(self) -> None:
        """Test that -1 returns all lines without truncation."""
        output = "\n".join([f"line{i}" for i in range(100)])
        lines, total = truncate_output(output, -1)
        assert len(lines) == 100
        assert total == 100

    def test_zero_limit(self) -> None:
        """Test that 0 limit returns no lines."""
        output = "line1\nline2\nline3"
        lines, total = truncate_output(output, 0)
        assert lines == []
        assert total == 3

    def test_preserves_line_content(self) -> None:
        """Test that line content is preserved exactly."""
        output = "  spaces  \ntabs\t\there\nspecial!@#"
        lines, total = truncate_output(output, 3)
        assert lines == ["  spaces  ", "tabs\t\there", "special!@#"]
        assert total == 3

    def test_total_count_is_accurate(self) -> None:
        """Test that total line count is always accurate."""
        output = "\n".join([f"line{i}" for i in range(50)])
        lines, total = truncate_output(output, 10)
        assert len(lines) == 10
        assert total == 50


class TestFormatTreeLine:
    """Test format_tree_line() function."""

    def test_first_line_has_branch(self) -> None:
        """Test that first line uses branch character."""
        result = format_tree_line("content", is_first=True)
        assert result == "  ⎿  content"

    def test_first_line_preserves_content(self) -> None:
        """Test that first line preserves content exactly."""
        result = format_tree_line("hello world", is_first=True)
        assert "hello world" in result

    def test_subsequent_lines_have_spaces(self) -> None:
        """Test that non-first lines use spaces for indentation."""
        result = format_tree_line("content", is_first=False)
        assert result == "     content"

    def test_subsequent_line_preserves_content(self) -> None:
        """Test that subsequent lines preserve content exactly."""
        result = format_tree_line("hello world", is_first=False)
        assert "hello world" in result

    def test_empty_content_first_line(self) -> None:
        """Test formatting empty content as first line."""
        result = format_tree_line("", is_first=True)
        assert result == "  ⎿  "

    def test_empty_content_subsequent_line(self) -> None:
        """Test formatting empty content as subsequent line."""
        result = format_tree_line("", is_first=False)
        assert result == "     "

    def test_whitespace_preserved(self) -> None:
        """Test that internal whitespace is preserved."""
        result = format_tree_line("  leading and trailing  ", is_first=True)
        assert "  leading and trailing  " in result

    def test_special_characters(self) -> None:
        """Test formatting special characters."""
        result = format_tree_line("!@#$%^&*()[]", is_first=True)
        assert "!@#$%^&*()[]" in result

    def test_unicode_content(self) -> None:
        """Test formatting unicode content."""
        result = format_tree_line("Hello 世界 🌍", is_first=True)
        assert "Hello 世界 🌍" in result

    def test_indentation_alignment(self) -> None:
        """Test that first and subsequent lines align properly."""
        first = format_tree_line("content", is_first=True)
        second = format_tree_line("content", is_first=False)
        # Both should have "content" start at same column
        assert first.index("content") == second.index("content")
