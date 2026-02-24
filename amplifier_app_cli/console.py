"""Shared Rich console instance for CLI output."""

from rich.console import Console
from rich.console import ConsoleOptions
from rich.console import RenderResult
from rich.errors import MarkupError
from rich.markdown import Heading as RichHeading
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text


class LeftAlignedHeading(RichHeading):
    """Heading with left alignment and Claude-style hierarchical styling."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        """Render heading with Claude UI-style emphasis.

        H1: Italic + underlined + spacing
        H2: Bold (brightest) + blank line before
        H3-H6: Dim (subdued)
        """
        text = self.text
        text.justify = "left"  # Override Rich's default "center"

        if self.tag == "h1":
            # H1: Italic + underlined + spacing
            yield Text("")  # Blank line before
            text.stylize("italic underline")
            yield text
            yield Text("")  # Blank line after

        elif self.tag == "h2":
            # H2: Bold (brightest/most prominent) + blank line before
            yield Text("")  # Blank line before
            text.stylize("bold")
            yield text

        else:
            # H3-H6: Dim (subdued)
            text.stylize("dim")
            yield text


class Markdown(RichMarkdown):
    """Markdown with left-aligned headings."""

    elements = {
        **RichMarkdown.elements,
        "heading_open": LeftAlignedHeading,  # Use our custom heading
    }


console = Console()

# Defense-in-depth: catch MarkupError from unescaped dynamic content
# (e.g., file paths like [/Users/...] interpreted as closing tags).
# Primary fix is escape_markup() at interpolation sites; this is the safety net.
_original_console_print = Console.print


def _safe_console_print(self, *args, **kwargs):
    try:
        _original_console_print(self, *args, **kwargs)
    except MarkupError:
        kwargs["markup"] = False
        kwargs["highlight"] = False
        _original_console_print(self, *args, **kwargs)


Console.print = _safe_console_print

__all__ = ["console", "Markdown"]
