"""Tests for @mention parsing utilities."""

from amplifier_app_cli.utils.mentions import extract_mention_path
from amplifier_app_cli.utils.mentions import has_mentions
from amplifier_app_cli.utils.mentions import parse_mentions


class TestParseMentions:
    """Test parse_mentions() function."""

    def test_single_mention(self: "TestParseMentions") -> None:
        """Parse single @mention."""
        text = "Check @AGENTS.md for details"
        result = parse_mentions(text)
        assert result == ["@AGENTS.md"]

    def test_multiple_mentions(self: "TestParseMentions") -> None:
        """Parse multiple @mentions."""
        text = "See @AGENTS.md and @README.md for context"
        result = parse_mentions(text)
        assert result == ["@AGENTS.md", "@README.md"]

    def test_mentions_with_paths(self: "TestParseMentions") -> None:
        """Parse @mentions with directory paths."""
        text = "Read @ai_context/FILE.md and @docs/README.md"
        result = parse_mentions(text)
        assert result == ["@ai_context/FILE.md", "@docs/README.md"]

    def test_mentions_with_nested_paths(self: "TestParseMentions") -> None:
        """Parse @mentions with nested paths."""
        text = "See @project/docs/guides/tutorial.md"
        result = parse_mentions(text)
        assert result == ["@project/docs/guides/tutorial.md"]

    def test_no_mentions(self: "TestParseMentions") -> None:
        """Return empty list when no @mentions."""
        text = "Just regular text without mentions"
        result = parse_mentions(text)
        assert result == []

    def test_empty_text(self: "TestParseMentions") -> None:
        """Handle empty text."""
        result = parse_mentions("")
        assert result == []

    def test_ignore_email_addresses(self: "TestParseMentions") -> None:
        """Don't parse email addresses as @mentions."""
        text = "Contact user@example.com for help"
        result = parse_mentions(text)
        assert result == []

    def test_ignore_twitter_handles(self: "TestParseMentions") -> None:
        """Don't parse twitter-style @handles as file mentions."""
        text = "Follow @username on twitter"
        result = parse_mentions(text)
        # This will match - we parse ANY @identifier
        # The resolver layer will determine if it's a valid file
        assert result == ["@username"]

    def test_double_at_symbol(self: "TestParseMentions") -> None:
        """Parse @@mention (treats second @ as start of mention)."""
        text = "Check @@AGENTS.md"
        result = parse_mentions(text)
        assert result == ["@AGENTS.md"]

    def test_mentions_in_code_blocks(self: "TestParseMentions") -> None:
        """Parse @mentions even in code blocks."""
        text = "```\n@AGENTS.md\n@README.md\n```"
        result = parse_mentions(text)
        assert result == ["@AGENTS.md", "@README.md"]

    def test_mentions_with_underscores_and_dashes(self: "TestParseMentions") -> None:
        """Parse @mentions with underscores and dashes."""
        text = "@my_file.md and @another-file.md"
        result = parse_mentions(text)
        assert result == ["@my_file.md", "@another-file.md"]

    def test_mentions_with_dots_in_path(self: "TestParseMentions") -> None:
        """Parse @mentions with dots in directory names."""
        text = "@dir.with.dots/file.md"
        result = parse_mentions(text)
        assert result == ["@dir.with.dots/file.md"]

    def test_mixed_content(self: "TestParseMentions") -> None:
        """Parse @mentions mixed with other content."""
        text = """
        See @AGENTS.md and contact user@example.com.
        Also check @ai_context/FILE.md for implementation details.
        """
        result = parse_mentions(text)
        assert result == ["@AGENTS.md", "@ai_context/FILE.md"]


class TestHasMentions:
    """Test has_mentions() function."""

    def test_with_mention(self: "TestHasMentions") -> None:
        """Detect text with @mention."""
        text = "Check @AGENTS.md"
        assert has_mentions(text) is True

    def test_without_mention(self: "TestHasMentions") -> None:
        """Detect text without @mention."""
        text = "No mentions here"
        assert has_mentions(text) is False

    def test_empty_text(self: "TestHasMentions") -> None:
        """Handle empty text."""
        assert has_mentions("") is False

    def test_with_email(self: "TestHasMentions") -> None:
        """Email addresses don't count as mentions."""
        text = "Contact user@example.com"
        assert has_mentions(text) is False

    def test_with_multiple_mentions(self: "TestHasMentions") -> None:
        """Detect text with multiple @mentions."""
        text = "@AGENTS.md and @README.md"
        assert has_mentions(text) is True


class TestExtractMentionPath:
    """Test extract_mention_path() function."""

    def test_extract_simple_filename(self: "TestExtractMentionPath") -> None:
        """Extract path from simple filename @mention."""
        mention = "@AGENTS.md"
        result = extract_mention_path(mention)
        assert result == "AGENTS.md"

    def test_extract_with_path(self: "TestExtractMentionPath") -> None:
        """Extract path from @mention with directory."""
        mention = "@ai_context/FILE.md"
        result = extract_mention_path(mention)
        assert result == "ai_context/FILE.md"

    def test_extract_nested_path(self: "TestExtractMentionPath") -> None:
        """Extract nested path from @mention."""
        mention = "@project/docs/guides/tutorial.md"
        result = extract_mention_path(mention)
        assert result == "project/docs/guides/tutorial.md"

    def test_extract_without_at_prefix(self: "TestExtractMentionPath") -> None:
        """Handle path that doesn't start with @."""
        path = "AGENTS.md"
        result = extract_mention_path(path)
        assert result == "AGENTS.md"

    def test_extract_multiple_at_symbols(self: "TestExtractMentionPath") -> None:
        """Remove all leading @ symbols."""
        mention = "@@AGENTS.md"
        result = extract_mention_path(mention)
        assert result == "AGENTS.md"
