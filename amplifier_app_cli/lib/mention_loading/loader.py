"""Main mention loading with recursive support and cycle detection."""

from pathlib import Path

from amplifier_core.message_models import Message

from ...utils.mentions import extract_mention_path
from ...utils.mentions import parse_mentions
from .deduplicator import ContentDeduplicator
from .models import ContextFile
from .resolver import MentionResolver


def prepend_context_to_markdown(context_messages: list[Message], markdown_body: str) -> str:
    """Extract content from context messages and prepend to markdown body.

    This utility prevents duplication of the content extraction logic across
    ProfileLoader and _process_profile_mentions().

    Args:
        context_messages: Messages with loaded @mention content
        markdown_body: Original markdown with @mention references

    Returns:
        Markdown with content prepended: "[content]\n\n[original markdown with @mentions as references]"
    """
    context_parts = []
    for msg in context_messages:
        if isinstance(msg.content, str):
            context_parts.append(msg.content)
        elif isinstance(msg.content, list):
            # Handle structured content (ContentBlocks) - extract text from TextBlock types
            text_parts = []
            for block in msg.content:
                # Only TextBlock has .text attribute
                if block.type == "text":
                    text_parts.append(block.text)
                else:
                    # For other block types, use string representation
                    text_parts.append(str(block))
            context_parts.append("".join(text_parts))
        else:
            context_parts.append(str(msg.content))

    if context_parts:
        context_content = "\n\n".join(context_parts)
        return f"{context_content}\n\n{markdown_body}"

    return markdown_body


class MentionLoader:
    """Loads files referenced by @mentions with deduplication and cycle detection.

    Features:
    - Recursive loading (follows @mentions in loaded files)
    - Cycle detection (prevents infinite loops)
    - Content deduplication (same content = one copy, all paths credited)
    - Silent skip on missing files
    """

    def __init__(self, resolver: MentionResolver | None = None):
        """Initialize loader with optional custom resolver.

        Args:
            resolver: MentionResolver to use (default: creates new with defaults)
        """
        self.resolver = resolver or MentionResolver()

    def has_mentions(self, text: str) -> bool:
        """Check if text contains @mention patterns.

        Args:
            text: Text to check for @mentions

        Returns:
            True if @mentions found, False otherwise
        """
        mentions = parse_mentions(text)
        return len(mentions) > 0

    def load_mentions(self, text: str, relative_to: Path | None = None) -> list[Message]:
        """Load all @mentioned files recursively.

        Args:
            text: Text containing @mentions
            relative_to: Base path for relative mentions (updates resolver)

        Returns:
            List of Message objects with role=developer containing loaded context
        """
        if relative_to is not None:
            self.resolver.relative_to = relative_to

        deduplicator = ContentDeduplicator()
        visited_paths: set[Path] = set()
        to_process: list[str] = parse_mentions(text)

        while to_process:
            mention = to_process.pop(0)
            path = self.resolver.resolve(mention)

            if path is None:
                continue

            resolved_path = path.resolve()
            if resolved_path in visited_paths:
                continue

            visited_paths.add(resolved_path)

            try:
                content = resolved_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            deduplicator.add_file(resolved_path, content)

            nested_mentions = parse_mentions(content)
            for nested in nested_mentions:
                nested_path = extract_mention_path(nested)
                if nested_path not in [extract_mention_path(m) for m in to_process]:
                    to_process.append(nested)

        return self._create_messages(deduplicator.get_unique_files())

    def _create_messages(self, context_files: list[ContextFile]) -> list[Message]:
        """Create Message objects from loaded context files.

        Args:
            context_files: List of deduplicated context files

        Returns:
            List of Message objects with role=developer
        """
        messages = []
        for ctx_file in context_files:
            paths_str = ", ".join(str(p) for p in ctx_file.paths)
            content = f"<system-reminder>\n[Context from {paths_str}]\n\n{ctx_file.content}\n</system-reminder>"
            messages.append(Message(role="developer", content=content))

        return messages
