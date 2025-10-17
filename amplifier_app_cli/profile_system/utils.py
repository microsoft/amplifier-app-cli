"""Shared utilities for profile and agent loading."""

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def parse_frontmatter(file_path: Path) -> dict:
    """
    Parse YAML frontmatter from markdown file.

    Args:
        file_path: Path to markdown file with frontmatter

    Returns:
        Dict with parsed YAML data

    Raises:
        ValueError: If frontmatter is invalid or missing
    """
    content = file_path.read_text()

    # Match frontmatter between --- delimiters
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        raise ValueError(f"No frontmatter found in {file_path}")

    frontmatter_yaml = match.group(1)

    try:
        return yaml.safe_load(frontmatter_yaml)
    except yaml.YAMLError as e:
        # Provide friendly error message for common YAML syntax issues
        error_msg = str(e)
        # Check if it's a scanner error with colons (common mistake)
        if "scanner" in error_msg.lower() or "could not find expected" in error_msg:
            raise ValueError(
                f"YAML syntax error in {file_path}:\n\n"
                f"{error_msg}\n\n"
                f"💡 Tip: If your description contains colons (like 'Note: something'), "
                f"you must quote it:\n"
                f'   description: "Note: something"\n\n'
                f"See PROFILE_AUTHORING.md or AGENT_AUTHORING.md for YAML quoting guidelines."
            ) from e
        raise ValueError(f"YAML syntax error in {file_path}: {error_msg}") from e


def parse_markdown_body(file_path: Path) -> str:
    """
    Extract markdown body (content after frontmatter) from markdown file.

    Args:
        file_path: Path to markdown file with frontmatter

    Returns:
        Markdown content after frontmatter (stripped)
    """
    content = file_path.read_text()

    # Match everything after frontmatter
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", content, re.DOTALL)
    if match:
        return match.group(1).strip()

    # No frontmatter, return full content
    return content.strip()
