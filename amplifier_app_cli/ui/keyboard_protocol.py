"""Progressive keyboard enhancement so real shift+enter reaches the REPL.

Legacy terminals encode shift+enter as a bare CR, indistinguishable from
enter. Two opt-in protocols fix that:

- kitty keyboard protocol (kitty, WezTerm, foot, ghostty, iTerm2 3.5+):
  ``CSI > 1 u`` pushes the "disambiguate escape codes" flag and shift+enter
  arrives as ``CSI 13;2u``. ``CSI < u`` pops the flag on the way out.
- xterm modifyOtherKeys (recent xterm and derivatives): ``CSI > 4;2m``
  enables it and shift+enter arrives as ``CSI 27;2;13~``; ``CSI > 4;0m``
  turns it back off.

Terminals that support neither silently ignore the sequences, so alt+enter
stays available as the queue fallback everywhere.

prompt_toolkit has no shift+enter key, so both encodings are parsed to
``Keys.F21`` as a dedicated carrier: F13-F24 have no physical key on modern
keyboards, no default prompt_toolkit binding, and no upstream escape
sequence mapped to F21, so nothing else can collide with the binding.

Pushing the kitty flag also stops the legacy encodings for Esc, ctrl+key
and alt+key (ctrl+c no longer arrives as ``0x03``), and modifyOtherKeys
re-encodes the same modified keys as ``CSI 27;<mod>;<code>~``. The install
below therefore also teaches the vt100 parser those forms for every key the
REPL binds, so enabling the enhancement never orphans existing shortcuts.
"""

from __future__ import annotations

from string import ascii_lowercase

from prompt_toolkit.input import ansi_escape_sequences
from prompt_toolkit.keys import Keys

# Enable/disable pairs; unsupported terminals ignore these sequences.
KITTY_KEYBOARD_ENABLE = "\x1b[>1u"
KITTY_KEYBOARD_DISABLE = "\x1b[<u"
MODIFY_OTHER_KEYS_ENABLE = "\x1b[>4;2m"
MODIFY_OTHER_KEYS_DISABLE = "\x1b[>4;0m"
# xterm focus tracking (mode 1004): the terminal reports window focus changes
# as ``CSI I`` / ``CSI O``. Legacy-safe: unsupported terminals ignore it.
FOCUS_TRACKING_ENABLE = "\x1b[?1004h"
FOCUS_TRACKING_DISABLE = "\x1b[?1004l"

KEYBOARD_ENHANCEMENT_ENABLE = KITTY_KEYBOARD_ENABLE + MODIFY_OTHER_KEYS_ENABLE
KEYBOARD_ENHANCEMENT_DISABLE = MODIFY_OTHER_KEYS_DISABLE + KITTY_KEYBOARD_DISABLE

# Dedicated carrier key for shift+enter (see module docstring).
SHIFT_ENTER_KEY = Keys.F21

# kitty / CSI-u encoding and xterm modifyOtherKeys encoding, respectively.
SHIFT_ENTER_SEQUENCES = ("\x1b[13;2u", "\x1b[27;2;13~")

# Focus reports ride dedicated carrier keys for the same reason shift+enter
# does: F22/F23 have no physical key, no upstream sequence, and no default
# binding, so the focus-flag handlers can never collide with real typing.
FOCUS_IN_KEY = Keys.F22
FOCUS_OUT_KEY = Keys.F23
FOCUS_EVENT_SEQUENCES: dict[str, Keys] = {
    "\x1b[I": FOCUS_IN_KEY,
    "\x1b[O": FOCUS_OUT_KEY,
}


def keyboard_enhancement_enable_sequence(kitty_keyboard: bool | None = None) -> str:
    """Compose the enhancement push for a probed terminal.

    ``None`` means the startup probe never ran (embedders, tests): keep the
    historical blind push, which is safe because unsupported terminals ignore
    both sequences. A probed terminal additionally gets focus tracking, and
    the kitty push is gated on the probe result — modifyOtherKeys stays blind
    either way because it is xterm-legacy-safe.
    """
    if kitty_keyboard is None:
        return KEYBOARD_ENHANCEMENT_ENABLE
    kitty = KITTY_KEYBOARD_ENABLE if kitty_keyboard else ""
    return f"{kitty}{MODIFY_OTHER_KEYS_ENABLE}{FOCUS_TRACKING_ENABLE}"


def keyboard_enhancement_disable_sequence(kitty_keyboard: bool | None = None) -> str:
    """Pop exactly what ``keyboard_enhancement_enable_sequence`` pushed."""
    if kitty_keyboard is None:
        return KEYBOARD_ENHANCEMENT_DISABLE
    kitty = KITTY_KEYBOARD_DISABLE if kitty_keyboard else ""
    return f"{FOCUS_TRACKING_DISABLE}{MODIFY_OTHER_KEYS_DISABLE}{kitty}"


_KeySpec = Keys | tuple[Keys, ...]

# Sequence -> (previous mapping or None, mapping we installed); None while
# the enhancement table is not installed.
_active: dict[str, tuple[_KeySpec | None, _KeySpec]] | None = None


def _enhanced_sequences() -> dict[str, _KeySpec]:
    """Sequences a terminal starts sending once enhancements are pushed."""
    sequences: dict[str, _KeySpec] = {
        sequence: SHIFT_ENTER_KEY for sequence in SHIFT_ENTER_SEQUENCES
    }
    # Focus tracking (mode 1004) reports, delivered as carrier keys so an
    # app-level handler can flip its focused flag without any text dispatch.
    sequences.update(FOCUS_EVENT_SEQUENCES)
    # Esc key (and its ctrl+[ alias) loses its legacy 0x1b encoding.
    sequences["\x1b[27u"] = Keys.Escape
    sequences["\x1b[27;1u"] = Keys.Escape
    sequences["\x1b[91;5u"] = Keys.Escape
    sequences["\x1b[27;5;91~"] = Keys.Escape
    # Enter variants: plain/ctrl+enter behave like enter, alt+enter keeps
    # working as the queue fallback binding (escape, enter).
    sequences["\x1b[13u"] = Keys.ControlM
    sequences["\x1b[13;5u"] = Keys.ControlM
    sequences["\x1b[13;3u"] = (Keys.Escape, Keys.ControlM)
    sequences["\x1b[27;3;13~"] = (Keys.Escape, Keys.ControlM)
    # shift+tab cycles modes.
    sequences["\x1b[9;2u"] = Keys.BackTab
    sequences["\x1b[27;2;9~"] = Keys.BackTab
    # ctrl+letter shortcuts (interrupt, exit, panes, ledger, rewind, ...).
    for letter in ascii_lowercase:
        control_key = Keys(f"c-{letter}")
        code = ord(letter)
        sequences[f"\x1b[{code};5u"] = control_key
        sequences[f"\x1b[27;5;{code}~"] = control_key
    return sequences


def install_shift_enter_sequences() -> bool:
    """Teach prompt_toolkit's vt100 parser the enhanced key encodings.

    Idempotent (repeat calls are no-ops), guarded (never clobbers an
    upstream mapping except the shift+enter carriers, whose prior values are
    recorded), and reversible via ``uninstall_shift_enter_sequences``.
    Returns True when the table was newly installed.
    """
    global _active
    if _active is not None:
        return False
    table = ansi_escape_sequences.ANSI_SEQUENCES
    active: dict[str, tuple[_KeySpec | None, _KeySpec]] = {}
    for sequence, key in _enhanced_sequences().items():
        previous = table.get(sequence)
        if previous == key:
            continue
        if previous is not None and sequence not in SHIFT_ENTER_SEQUENCES:
            continue
        table[sequence] = key
        active[sequence] = (previous, key)
    _active = active
    _clear_prefix_cache()
    return True


def uninstall_shift_enter_sequences() -> None:
    """Restore the mappings recorded by ``install_shift_enter_sequences``."""
    global _active
    if _active is None:
        return
    table = ansi_escape_sequences.ANSI_SEQUENCES
    for sequence, (previous, installed) in _active.items():
        if table.get(sequence) != installed:
            continue
        if previous is None:
            del table[sequence]
        else:
            table[sequence] = previous
    _active = None
    _clear_prefix_cache()


def _clear_prefix_cache() -> None:
    """Drop stale prefix verdicts cached before the table was mutated."""
    try:
        from prompt_toolkit.input import vt100_parser
    except ImportError:  # pragma: no cover - platforms without vt100 input
        return
    cache = getattr(vt100_parser, "_IS_PREFIX_OF_LONGER_MATCH_CACHE", None)
    if cache is not None:
        cache.clear()


__all__ = [
    "FOCUS_EVENT_SEQUENCES",
    "FOCUS_IN_KEY",
    "FOCUS_OUT_KEY",
    "FOCUS_TRACKING_DISABLE",
    "FOCUS_TRACKING_ENABLE",
    "KEYBOARD_ENHANCEMENT_DISABLE",
    "KEYBOARD_ENHANCEMENT_ENABLE",
    "KITTY_KEYBOARD_DISABLE",
    "KITTY_KEYBOARD_ENABLE",
    "MODIFY_OTHER_KEYS_DISABLE",
    "MODIFY_OTHER_KEYS_ENABLE",
    "SHIFT_ENTER_KEY",
    "SHIFT_ENTER_SEQUENCES",
    "install_shift_enter_sequences",
    "keyboard_enhancement_disable_sequence",
    "keyboard_enhancement_enable_sequence",
    "uninstall_shift_enter_sequences",
]
