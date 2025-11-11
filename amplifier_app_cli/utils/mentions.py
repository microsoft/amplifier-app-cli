"""Pure text processing for @mentions - no file I/O."""

import re
from re import Pattern

# @mention pattern: matches @FILENAME or @path/to/file or @collection:path
# Negative lookbehind to exclude email addresses (no alphanumeric before @)
# Supports: @file.md, @path/to/file.md, @collection:path/file.md, @user:path, @project:path
MENTION_PATTERN: Pattern = re.compile(r"(?<![a-zA-Z0-9])@([a-zA-Z0-9_\-/\.:]+)")

# @~/ pattern: matches @~/path/to/file (optional path after ~/)
HOME_PATTERN: Pattern = re.compile(r"@~/([a-zA-Z0-9_\-/\.]*)")


def parse_mentions(text: str) -> list[str]:
    """
    Extract all @mentions from text, excluding examples in code/quotes.

    Supports two types:
    - @~/path - User home directory files
    - @path - Project/CWD files or collection references

    Excludes @mentions within:
    - Inline code: `@mention`
    - Double quotes: "@mention"
    - Single quotes: '@mention'

    Returns @mentions WITH prefix (e.g., ['@~/.amplifier/custom.md', '@AGENTS.md', '@foundation:context/file.md'])

    Args:
        text: Text to parse for @mentions

    Returns:
        List of @mentions with prefixes intact

    Examples:
        >>> parse_mentions("@foundation:context/file.md and @AGENTS.md")
        ['@foundation:context/file.md', '@AGENTS.md']
        >>> parse_mentions("Example: `@foundation:file.md`")
        []
        >>> parse_mentions('read_file("@toolkit:path")')
        []
    """
    # Filter out examples in inline code and quotes
    # Remove inline code (`...`) on same line
    text_filtered = re.sub(r"`[^`\n]+`", "", text)

    # Remove double-quoted strings on same line
    text_filtered = re.sub(r'"[^"\n]*"', "", text_filtered)

    # Remove single-quoted strings on same line
    text_filtered = re.sub(r"'[^'\n]*'", "", text_filtered)

    # Extract each type separately to preserve prefixes
    homes = [f"@~/{m}" if m else "@~/" for m in HOME_PATTERN.findall(text_filtered)]

    # Regular mentions - exclude those that are part of ~/
    all_at_mentions = MENTION_PATTERN.findall(text_filtered)
    regulars = []
    for m in all_at_mentions:
        # Check if this @ is part of @~/
        # Look at what precedes it in text_filtered
        idx = text_filtered.find(f"@{m}")
        if idx > 0:
            # Check if preceded by "~/"
            before = text_filtered[max(0, idx - 2) : idx]
            if before.endswith("~/"):
                continue  # Skip - it's part of ~/

        # Skip generic "@mention" keyword used in documentation
        if m == "mention":
            continue

        regulars.append(f"@{m}")

    return homes + regulars


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


def extract_mention_type(mention: str) -> tuple[str, str]:
    """
    Extract mention type and path from @mention.

    Returns:
        Tuple of (type, path) where type is 'home' or 'regular'

    Args:
        mention: @mention string with prefix

    Returns:
        Tuple of (type, path) identifying the mention type and its path

    Examples:
        >>> extract_mention_type("@~/.amplifier/custom.md")
        ('home', '.amplifier/custom.md')
        >>> extract_mention_type("@AGENTS.md")
        ('regular', 'AGENTS.md')
        >>> extract_mention_type("@foundation:context/file.md")
        ('regular', 'foundation:context/file.md')
    """
    if mention.startswith("@~/"):
        return ("home", mention[3:])
    return ("regular", mention.lstrip("@"))
