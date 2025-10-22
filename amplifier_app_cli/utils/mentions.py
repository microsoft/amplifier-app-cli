"""Pure text processing for @mentions - no file I/O."""

import re
from re import Pattern

# @mention pattern: matches @FILENAME or @path/to/file
# Negative lookbehind to exclude email addresses (no alphanumeric before @)
MENTION_PATTERN: Pattern = re.compile(r"(?<![a-zA-Z0-9])@([a-zA-Z0-9_\-/\.]+)")


def parse_mentions(text: str) -> list[str]:
    """
    Extract all @mentions from text.

    Returns @mentions WITH @ prefix (e.g., ['@AGENTS.md', '@ai_context/FILE.md'])

    Args:
        text: Text to parse for @mentions

    Returns:
        List of @mentions with @ prefix included

    Examples:
        >>> parse_mentions("See @AGENTS.md and @ai_context/FILE.md")
        ['@AGENTS.md', '@ai_context/FILE.md']
        >>> parse_mentions("No mentions here")
        []
    """
    matches = MENTION_PATTERN.findall(text)
    return ["@" + m for m in matches] if matches else []


def has_mentions(text: str) -> bool:
    """
    Check if text contains any @mentions.

    Args:
        text: Text to check

    Returns:
        True if text contains at least one @mention

    Examples:
        >>> has_mentions("Check @AGENTS.md")
        True
        >>> has_mentions("No mentions")
        False
    """
    return bool(MENTION_PATTERN.search(text))


def extract_mention_path(mention: str) -> str:
    """
    Extract path from @mention (remove @ prefix).

    Args:
        mention: @mention string (e.g., '@AGENTS.md')

    Returns:
        Path without @ prefix

    Examples:
        >>> extract_mention_path('@AGENTS.md')
        'AGENTS.md'
        >>> extract_mention_path('@ai_context/FILE.md')
        'ai_context/FILE.md'
    """
    return mention.lstrip("@")
