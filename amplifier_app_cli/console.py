"""Shared Rich console instance for CLI output."""

from rich.console import Console
from rich.console import ConsoleOptions
from rich.console import RenderResult
from rich.markdown import Heading as RichHeading
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text


class LeftAlignedHeading(RichHeading):
    """Heading with left alignment (overrides Rich's default center alignment)."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Render heading with left alignment instead of center."""
        text = self.text
        text.justify = "left"  # Override Rich's default "center"

        # Simple rendering without Panel (no heavy borders)
        if self.tag == "h2":
            yield Text("")  # Blank line before h2
        yield text


class Markdown(RichMarkdown):
    """Markdown with left-aligned headings."""

    elements = {
        **RichMarkdown.elements,
        "heading_open": LeftAlignedHeading,  # Use our custom heading
    }


console = Console()

__all__ = ["console", "Markdown"]
