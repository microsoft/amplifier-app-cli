"""Parsing helpers for interactive configuration commands."""

from __future__ import annotations


def parse_config_flags(
    parts: list[str],
) -> tuple[list[str], bool, bool, bool, str]:
    """Strip display flags from command parts, with the last view flag winning."""
    compact = False
    detailed = False
    trees = False
    fmt = "text"
    remaining: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if part == "--compact":
            compact = True
        elif part == "--detailed":
            detailed = True
            trees = False
        elif part == "--trees":
            trees = True
            detailed = False
        elif part == "--format" and index + 1 < len(parts):
            fmt = parts[index + 1].lower()
            index += 1
        else:
            remaining.append(part)
        index += 1
    return remaining, compact, detailed, trees, fmt


_parse_config_flags = parse_config_flags

__all__ = ["parse_config_flags", "_parse_config_flags"]
