"""Shared Rich console instance for CLI output."""

from rich.console import Console
from rich.console import ConsoleOptions
from rich.console import RenderResult
from rich.errors import MarkupError
from rich.markdown import CodeBlock as RichCodeBlock
from rich.markdown import Heading as RichHeading
from rich.markdown import Markdown as RichMarkdown
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text


class CopyPasteCodeBlock(RichCodeBlock):
    """Code block with no per-line whitespace padding, for clean copy/paste.

    Rich's stock CodeBlock renders with ``Syntax(..., padding=1)``, which
    writes a literal space character onto the left (and right) of every
    line. That space is a real character in the terminal's screen buffer,
    so a mouse-drag or triple-click copy captures it -- pasting code out of
    a session then requires manually stripping a leading space from every
    line.

    This override keeps the block visually identifiable (background tint +
    syntax highlighting, plus a thin rule above/below) without baking any
    padding characters into the code lines themselves, so what you copy is
    exactly the code -- nothing more.
    """

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        code = str(self.text).rstrip()
        yield Rule(style="dim", characters="\u2500")
        yield Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=0,
        )
        yield Rule(style="dim", characters="\u2500")


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
        "fence": CopyPasteCodeBlock,  # ``` code blocks: no copy/paste padding
        "code_block": CopyPasteCodeBlock,  # indented code blocks: same treatment
    }


console = Console()

# Defense-in-depth: catch MarkupError from unescaped dynamic content
# (e.g., file paths like [/Users/...] interpreted as closing tags).
# Primary fix is escape_markup() at interpolation sites; this is the safety net.
# Patches at class level so ALL Console instances are covered (shared + standalone).
if not getattr(Console.print, "_is_safe_wrapper", False):
    _original_console_print = Console.print

    def _safe_console_print(self, *args, **kwargs):
        try:
            _original_console_print(self, *args, **kwargs)
        except MarkupError:
            kwargs["markup"] = False
            kwargs["highlight"] = False
            _original_console_print(self, *args, **kwargs)

    _safe_console_print._is_safe_wrapper = True  # type: ignore[attr-defined]
    Console.print = _safe_console_print  # type: ignore[assignment]

__all__ = ["console", "Markdown"]
