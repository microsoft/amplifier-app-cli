"""Formatting utilities for CLI display."""


def format_tool_arguments(args: dict, max_length: int = 50) -> str:
    """Format tool arguments for display.

    Args:
        args: Tool arguments dictionary
        max_length: Maximum length for individual argument values

    Returns:
        Formatted argument string like "(key=value, key2=value2)"
    """
    if not args:
        return "()"

    formatted_pairs = []
    for key, value in args.items():
        value_str = str(value)
        cleaned_value = " ".join(value_str.split())

        if len(cleaned_value) > max_length:
            truncated_value = cleaned_value[:max_length] + "..."
        else:
            truncated_value = cleaned_value

        if isinstance(value, str):
            formatted_pairs.append(f'{key}="{truncated_value}"')
        else:
            formatted_pairs.append(f"{key}={truncated_value}")

    return f"({', '.join(formatted_pairs)})"


def truncate_output(output: str, max_lines: int) -> tuple[list[str], int]:
    """Truncate tool output to specified number of lines.

    Args:
        output: Tool output string
        max_lines: Maximum number of lines to show (-1 for all)

    Returns:
        Tuple of (lines_to_show, total_line_count)
    """
    lines = output.split("\n")
    total = len(lines)

    if max_lines == -1:
        return lines, total

    return lines[:max_lines], total


def format_tree_line(content: str, is_first: bool) -> str:
    """Format line with tree-style indentation.

    Args:
        content: Line content
        is_first: Whether this is the first line

    Returns:
        Formatted line with tree characters
    """
    if is_first:
        return f"  ⎿  {content}"
    return f"     {content}"
