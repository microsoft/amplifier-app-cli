"""Stateful terminal-output capture for prompt-toolkit transcript panes."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.utils import get_cwidth


_ESC = "\x1b"
_BEL = "\x07"
_C1_CSI = "\x9b"
_C1_OSC = "\x9d"
_C1_ST = "\x9c"
_C1_STRINGS = {"\x90", "\x98", "\x9e", "\x9f"}
_STRING_INTRODUCERS = {"P", "X", "^", "_"}
_MAX_CSI_PARAM = 9999
_MAX_LINE_CELLS = 1_024
_MAX_CELL_CODEPOINTS = 32

_COLOR_NAMES = (
    "black red green yellow blue magenta cyan gray "
    "brightblack brightred brightgreen brightyellow brightblue "
    "brightmagenta brightcyan white"
).split()
_ANSI_16 = tuple(f"ansi{name}" for name in _COLOR_NAMES)
_FG_COLORS = dict(zip((*range(30, 38), *range(90, 98)), _ANSI_16, strict=True))
_BG_COLORS = dict(zip((*range(40, 48), *range(100, 108)), _ANSI_16, strict=True))


@dataclass(slots=True)
class _Cell:
    text: str = " "
    style: str = ""
    width: int = 1
    continuation: bool = False


@dataclass(frozen=True, slots=True)
class _RenderedLine:
    plain: str
    fragments: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class _SgrState:
    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    blink: bool = False
    reverse: bool = False
    hidden: bool = False
    strike: bool = False

    def reset(self) -> None:
        self.foreground = None
        self.background = None
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.blink = False
        self.reverse = False
        self.hidden = False
        self.strike = False

    def style(self) -> str:
        parts: list[str] = []
        if self.foreground:
            parts.append(self.foreground)
        if self.background:
            parts.append(f"bg:{self.background}")
        for enabled, name in (
            (self.bold, "bold"),
            (self.dim, "dim"),
            (self.italic, "italic"),
            (self.underline, "underline"),
            (self.blink, "blink"),
            (self.reverse, "reverse"),
            (self.hidden, "hidden"),
            (self.strike, "strike"),
        ):
            if enabled:
                parts.append(name)
        return " ".join(parts)


class TerminalTranscript:
    """Incrementally capture terminal writes without retaining control bytes.

    Completed and current lines are bounded together when ``max_lines`` is an
    integer. Passing ``None`` retains the complete in-session transcript. ANSI
    Select Graphic Rendition (SGR) state is retained as prompt-toolkit style
    fragments; OSC, DCS, APC, PM, SOS, and unsupported escape sequences are
    consumed without reaching the returned text.
    """

    def __init__(self, max_lines: int | None = 260, *, tab_size: int = 8) -> None:
        if max_lines is not None and max_lines < 1:
            raise ValueError("max_lines must be positive")
        if tab_size < 1:
            raise ValueError("tab_size must be positive")
        self.max_lines = max_lines
        self.tab_size = tab_size
        # Completed rows are immutable and compact. Only the active terminal
        # row needs cell-level cursor semantics.
        self._lines: list[_RenderedLine] = []
        self._current: list[_Cell] = []
        self._cursor = 0
        self._current_visible = False
        self._omitted_line_count = 0
        self._parser_state = "text"
        self._sequence = ""
        self._sgr = _SgrState()
        self._active_link: str | None = None

    def write(self, text: str) -> int:
        """Consume a terminal write and return its original character count."""
        value = str(text)
        for char in value:
            self._consume(char)
        self._enforce_bound()
        return len(value)

    def flush(self) -> None:
        """Provide the no-op flush expected by file-like output adapters."""

    @property
    def omitted_line_count(self) -> int:
        return self._omitted_line_count

    @property
    def omitted_count(self) -> int:
        """Short alias for callers that do not need the line qualifier."""
        return self._omitted_line_count

    @property
    def plain_lines(self) -> tuple[str, ...]:
        lines = tuple(line.plain for line in self._lines)
        if self._current_visible:
            lines += (self._plain_line(self._current),)
        return lines

    @property
    def line_count(self) -> int:
        """Return the number of completed and currently visible rows."""
        return len(self._lines) + int(self._current_visible)

    def plain_line(self, line_number: int) -> str:
        """Return one logical row without materializing the whole history."""
        if line_number < 0:
            return ""
        if line_number < len(self._lines):
            return self._lines[line_number].plain
        if line_number == len(self._lines) and self._current_visible:
            return self._plain_line(self._current)
        return ""

    def plain_slice(self, start: int, stop: int) -> tuple[str, ...]:
        """Return a bounded logical-row slice for a transcript viewport."""
        start = max(0, int(start))
        stop = max(start, min(int(stop), self.line_count))
        completed_stop = min(stop, len(self._lines))
        lines = tuple(line.plain for line in self._lines[start:completed_stop])
        if self._current_visible and start <= len(self._lines) < stop:
            lines += (self._plain_line(self._current),)
        return lines

    @property
    def formatted_lines(self) -> tuple[FormattedText, ...]:
        lines = tuple(FormattedText(line.fragments) for line in self._lines)
        if self._current_visible:
            lines += (FormattedText(self._line_fragments(self._current)),)
        return lines

    def formatted_line(self, line_number: int) -> FormattedText:
        """Return one formatted row without materializing the whole history."""
        if line_number < 0:
            return FormattedText()
        if line_number < len(self._lines):
            return FormattedText(self._lines[line_number].fragments)
        if line_number == len(self._lines) and self._current_visible:
            return FormattedText(self._line_fragments(self._current))
        return FormattedText()

    @property
    def plain_text(self) -> str:
        return "\n".join(self.plain_lines)

    @property
    def formatted_text(self) -> FormattedText:
        fragments: list[tuple[str, str]] = []
        for index, line in enumerate(self._lines):
            if index:
                fragments.append(("", "\n"))
            fragments.extend(line.fragments)
        if self._current_visible:
            if self._lines:
                fragments.append(("", "\n"))
            fragments.extend(self._line_fragments(self._current))
        return FormattedText(fragments)

    def clear(self) -> None:
        """Reset captured output, parser state, and active SGR attributes."""
        self._lines.clear()
        self._current.clear()
        self._cursor = 0
        self._current_visible = False
        self._omitted_line_count = 0
        self._parser_state = "text"
        self._sequence = ""
        self._sgr.reset()
        self._active_link = None

    def _consume(self, char: str) -> None:
        state = self._parser_state
        if state == "esc":
            self._consume_escape(char)
        elif state == "csi":
            self._consume_csi(char)
        elif state == "osc":
            self._consume_osc(char)
        elif state == "osc_esc":
            self._consume_string_escape(char, "osc")
        elif state == "string":
            self._consume_string(char)
        elif state == "string_esc":
            self._consume_string_escape(char, "string")
        else:
            self._consume_text(char)

    def _consume_text(self, char: str) -> None:
        if char == _ESC:
            self._parser_state = "esc"
        elif char == _C1_CSI:
            self._start_sequence("csi")
        elif char == _C1_OSC:
            self._start_sequence("osc")
        elif char in _C1_STRINGS:
            self._start_sequence("string")
        elif char == "\n":
            self._finish_line()
        elif char == "\r":
            self._cursor = 0
        elif char == "\b":
            self._cursor = max(0, self._cursor - 1)
        elif char == "\t":
            self._move_cursor(((self._cursor // self.tab_size) + 1) * self.tab_size)
        elif char == _C1_ST or ord(char) < 32 or 127 <= ord(char) <= 159:
            return
        else:
            self._write_character(char)

    def _consume_escape(self, char: str) -> None:
        if char == "[":
            self._start_sequence("csi")
        elif char == "]":
            self._start_sequence("osc")
        elif char in _STRING_INTRODUCERS:
            self._start_sequence("string")
        elif char == _ESC:
            return
        elif char in "\n\r\t\b":
            self._parser_state = "text"
            self._consume_text(char)
        else:
            self._parser_state = "text"

    def _consume_csi(self, char: str) -> None:
        if char == _ESC:
            self._parser_state = "esc"
            self._sequence = ""
            return
        if 0x40 <= ord(char) <= 0x7E:
            params = self._parse_params(self._sequence)
            if params is not None:
                self._apply_csi(char, params)
            self._parser_state = "text"
            self._sequence = ""
            return
        if ord(char) < 32:
            return
        if len(self._sequence) < 128:
            self._sequence += char

    def _consume_osc(self, char: str) -> None:
        if char in {_BEL, _C1_ST}:
            self._finish_osc()
            self._parser_state = "text"
            self._sequence = ""
        elif char == _ESC:
            self._parser_state = "osc_esc"
        elif len(self._sequence) < 8192:
            self._sequence += char

    def _consume_string(self, char: str) -> None:
        if char == _C1_ST:
            self._parser_state = "text"
        elif char == _ESC:
            self._parser_state = "string_esc"

    def _consume_string_escape(self, char: str, return_state: str) -> None:
        if char == "\\" or char == _C1_ST or (return_state == "osc" and char == _BEL):
            if return_state == "osc":
                self._finish_osc()
            self._parser_state = "text"
            self._sequence = ""
        elif char == _ESC:
            return
        else:
            self._parser_state = return_state

    def _start_sequence(self, state: str) -> None:
        self._parser_state = state
        self._sequence = ""

    def _finish_osc(self) -> None:
        parts = self._sequence.split(";", 2)
        if len(parts) != 3 or parts[0] != "8":
            return
        target = "".join(char for char in parts[2].strip() if char.isprintable())
        if target:
            parsed = urlsplit(target[:2048])
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                port = f":{parsed.port}" if parsed.port else ""
                self._active_link = f"{parsed.scheme}://{parsed.hostname}{port}"
            else:
                self._active_link = None
            return
        if self._active_link:
            suffix = f" ({self._active_link})"
            self._active_link = None
            for char in suffix:
                self._write_character(char)

    @staticmethod
    def _parse_params(value: str) -> list[int] | None:
        if any(char not in "0123456789;" for char in value):
            return None
        if not value:
            return [0]
        return [
            _MAX_CSI_PARAM if len(item) > 4 else min(int(item or 0), _MAX_CSI_PARAM)
            for item in value.split(";")
        ]

    def _apply_csi(self, final: str, params: list[int]) -> None:
        if final == "m":
            self._apply_sgr(params)
        elif final == "K":
            self._erase_line(params[0] if params else 0)
        elif final == "G":
            self._move_cursor(max(0, (params[0] if params else 1) - 1))
        elif final == "C":
            self._move_cursor(self._cursor + max(1, params[0] if params else 1))
        elif final == "D":
            self._cursor = max(0, self._cursor - max(1, params[0] if params else 1))

    def _apply_sgr(self, params: list[int]) -> None:
        index = 0
        while index < len(params):
            value = params[index]
            index += 1
            if value == 0:
                self._sgr.reset()
            elif value == 1:
                self._sgr.bold = True
            elif value == 2:
                self._sgr.dim = True
            elif value == 3:
                self._sgr.italic = True
            elif value == 4:
                self._sgr.underline = True
            elif value in {5, 6}:
                self._sgr.blink = True
            elif value == 7:
                self._sgr.reverse = True
            elif value == 8:
                self._sgr.hidden = True
            elif value == 9:
                self._sgr.strike = True
            elif value == 22:
                self._sgr.bold = self._sgr.dim = False
            elif value == 23:
                self._sgr.italic = False
            elif value == 24:
                self._sgr.underline = False
            elif value == 25:
                self._sgr.blink = False
            elif value == 27:
                self._sgr.reverse = False
            elif value == 28:
                self._sgr.hidden = False
            elif value == 29:
                self._sgr.strike = False
            elif value in _FG_COLORS:
                self._sgr.foreground = _FG_COLORS[value]
            elif value in _BG_COLORS:
                self._sgr.background = _BG_COLORS[value]
            elif value == 39:
                self._sgr.foreground = None
            elif value == 49:
                self._sgr.background = None
            elif value in {38, 48}:
                color, consumed = self._extended_color(params[index:])
                index += consumed
                if color is not None:
                    if value == 38:
                        self._sgr.foreground = color
                    else:
                        self._sgr.background = color

    @staticmethod
    def _extended_color(params: list[int]) -> tuple[str | None, int]:
        if len(params) >= 2 and params[0] == 5:
            return _color_256(params[1]), 2
        if len(params) >= 4 and params[0] == 2:
            red, green, blue = (max(0, min(255, value)) for value in params[1:4])
            return f"#{red:02x}{green:02x}{blue:02x}", 4
        return None, min(1, len(params))

    def _write_character(self, char: str) -> None:
        width = get_cwidth(char)
        if width <= 0:
            primary = self._primary_before_cursor()
            if (
                primary is not None
                and len(self._current[primary].text) < _MAX_CELL_CODEPOINTS
            ):
                self._current[primary].text += char
            return
        if self._cursor + width >= _MAX_LINE_CELLS:
            return

        self._ensure_columns(self._cursor + width)
        for column in range(self._cursor, self._cursor + width):
            self._clear_glyph(column)
        style = self._sgr.style()
        self._current[self._cursor] = _Cell(char, style, width, False)
        for column in range(self._cursor + 1, self._cursor + width):
            self._current[column] = _Cell("", style, 0, True)
        self._cursor += width
        self._current_visible = True

    def _primary_before_cursor(self) -> int | None:
        column = min(self._cursor - 1, len(self._current) - 1)
        while column >= 0 and self._current[column].continuation:
            column -= 1
        return column if column >= 0 else None

    def _clear_glyph(self, column: int) -> None:
        if column >= len(self._current):
            return
        primary = column
        while primary > 0 and self._current[primary].continuation:
            primary -= 1
        width = max(1, self._current[primary].width)
        for target in range(primary, min(len(self._current), primary + width)):
            self._current[target] = _Cell()

    def _move_cursor(self, column: int) -> None:
        column = min(max(0, column), _MAX_LINE_CELLS - 2)
        self._ensure_columns(column)
        self._cursor = column

    def _ensure_columns(self, count: int) -> None:
        count = min(count, _MAX_LINE_CELLS)
        if count > len(self._current):
            self._current.extend(_Cell() for _ in range(count - len(self._current)))

    def _erase_line(self, mode: int) -> None:
        if mode == 2:
            self._current.clear()
            self._current_visible = False
            return
        if mode == 1:
            end = min(len(self._current), self._cursor + 1)
            for column in range(end):
                self._clear_glyph(column)
        else:
            del self._current[min(self._cursor, len(self._current)) :]

    def _finish_line(self) -> None:
        self._lines.append(
            _RenderedLine(
                plain=self._plain_line(self._current),
                fragments=tuple(self._line_fragments(self._current)),
            )
        )
        self._current = []
        self._cursor = 0
        self._current_visible = False
        self._enforce_bound()

    def _enforce_bound(self) -> None:
        if self.max_lines is None:
            return
        visible_count = len(self._lines) + int(self._current_visible)
        while visible_count > self.max_lines and self._lines:
            self._lines.pop(0)
            self._omitted_line_count += 1
            visible_count -= 1

    @staticmethod
    def _display_cells(line: list[_Cell]) -> list[_Cell]:
        end = len(line)
        while end and line[end - 1].text == " " and not line[end - 1].style:
            end -= 1
        return line[:end]

    @classmethod
    def _plain_line(cls, line: list[_Cell]) -> str:
        return "".join(
            cell.text for cell in cls._display_cells(line) if not cell.continuation
        )

    @classmethod
    def _line_fragments(cls, line: list[_Cell]) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        for cell in cls._display_cells(line):
            if cell.continuation or not cell.text:
                continue
            if fragments and fragments[-1][0] == cell.style:
                style, text = fragments[-1]
                fragments[-1] = (style, text + cell.text)
            else:
                fragments.append((cell.style, cell.text))
        return fragments


def _color_256(value: int) -> str | None:
    if not 0 <= value <= 255:
        return None
    if value < 16:
        return _ANSI_16[value]
    if value < 232:
        value -= 16
        steps = (0, 95, 135, 175, 215, 255)
        red = steps[value // 36]
        green = steps[(value % 36) // 6]
        blue = steps[value % 6]
    else:
        red = green = blue = 8 + (value - 232) * 10
    return f"#{red:02x}{green:02x}{blue:02x}"


__all__ = ["TerminalTranscript"]
