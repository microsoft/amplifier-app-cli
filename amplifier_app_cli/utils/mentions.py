"""Pure text processing for @mentions - no file I/O."""

import re
from re import Pattern

# @mention pattern: matches @FILENAME or @path/to/file
# Negative lookbehind to exclude email addresses (no alphanumeric before @)
MENTION_PATTERN: Pattern = re.compile(r"(?<![a-zA-Z0-9])@([a-zA-Z0-9_\-/\.]+)")

# @bundle: pattern: matches @bundle:path/to/file
BUNDLE_PATTERN: Pattern = re.compile(r"@bundle:([a-zA-Z0-9_\-/\.]+)")

# @~/ pattern: matches @~/path/to/file (optional path after ~/)
HOME_PATTERN: Pattern = re.compile(r"@~/([a-zA-Z0-9_\-/\.]*)")


def parse_mentions(text: str) -> list[str]:
    """
    Extract all @mentions from text, supporting three types:
    - @bundle:path - Bundled context files
    - @~/path - User home directory files
    - @path - Project/CWD files

    Returns @mentions WITH prefix (e.g., ['@bundle:shared/common.md', '@~/.amplifier/custom.md', '@AGENTS.md'])

    Args:
        text: Text to parse for @mentions

    Returns:
        List of @mentions with prefixes intact

    Examples:
        >>> parse_mentions("@bundle:shared/common.md and @AGENTS.md")
        ['@bundle:shared/common.md', '@AGENTS.md']
        >>> parse_mentions("@~/.amplifier/custom.md")
        ['@~/.amplifier/custom.md']
        >>> parse_mentions("No mentions here")
        []
    """
    # Extract each type separately to preserve prefixes
    bundles = [f"@bundle:{m}" for m in BUNDLE_PATTERN.findall(text)]
    homes = [f"@~/{m}" if m else "@~/" for m in HOME_PATTERN.findall(text)]

    # Regular mentions - exclude those that are part of bundle: or ~/
    all_at_mentions = MENTION_PATTERN.findall(text)
    regulars = []
    for m in all_at_mentions:
        # Check if this @ is part of @bundle: or @~/
        # Look at what precedes it in text
        idx = text.find(f"@{m}")
        if idx > 0:
            # Check if preceded by "bundle:" or "~/"
            before = text[max(0, idx - 7) : idx]
            if before.endswith("bundle:") or before.endswith("~/"):
                continue  # Skip - it's part of bundle: or ~/
        regulars.append(f"@{m}")

    return bundles + homes + regulars


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
        Tuple of (type, path) where type is 'bundle', 'home', or 'regular'

    Args:
        mention: @mention string with prefix

    Returns:
        Tuple of (type, path) identifying the mention type and its path

    Examples:
        >>> extract_mention_type("@bundle:shared/common.md")
        ('bundle', 'shared/common.md')
        >>> extract_mention_type("@~/.amplifier/custom.md")
        ('home', '.amplifier/custom.md')
        >>> extract_mention_type("@AGENTS.md")
        ('regular', 'AGENTS.md')
    """
    if mention.startswith("@bundle:"):
        return ("bundle", mention[8:])
    if mention.startswith("@~/"):
        return ("home", mention[3:])
    return ("regular", mention.lstrip("@"))
