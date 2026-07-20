"""Progressive keyboard enhancement: sequences, install guards, bindings."""

from types import SimpleNamespace
from typing import Any

import pytest
from prompt_toolkit.input import ansi_escape_sequences
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys

from amplifier_app_cli.ui.keyboard_protocol import KEYBOARD_ENHANCEMENT_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import KEYBOARD_ENHANCEMENT_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import KITTY_KEYBOARD_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import KITTY_KEYBOARD_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import MODIFY_OTHER_KEYS_DISABLE
from amplifier_app_cli.ui.keyboard_protocol import MODIFY_OTHER_KEYS_ENABLE
from amplifier_app_cli.ui.keyboard_protocol import SHIFT_ENTER_KEY
from amplifier_app_cli.ui.keyboard_protocol import SHIFT_ENTER_SEQUENCES
from amplifier_app_cli.ui.keyboard_protocol import install_shift_enter_sequences
from amplifier_app_cli.ui.keyboard_protocol import uninstall_shift_enter_sequences
from amplifier_app_cli.ui.layered_repl_keys import build_layered_key_bindings
from amplifier_app_cli.ui.layered_repl_terminal import LayeredReplTerminalMixin


@pytest.fixture(autouse=True)
def _isolated_sequence_table():
    uninstall_shift_enter_sequences()
    yield
    uninstall_shift_enter_sequences()


def test_enable_disable_sequences_are_the_documented_pairs():
    assert KITTY_KEYBOARD_ENABLE == "\x1b[>1u"
    assert KITTY_KEYBOARD_DISABLE == "\x1b[<u"
    assert MODIFY_OTHER_KEYS_ENABLE == "\x1b[>4;2m"
    assert MODIFY_OTHER_KEYS_DISABLE == "\x1b[>4;0m"
    assert (
        KEYBOARD_ENHANCEMENT_ENABLE == KITTY_KEYBOARD_ENABLE + MODIFY_OTHER_KEYS_ENABLE
    )
    assert (
        KEYBOARD_ENHANCEMENT_DISABLE
        == MODIFY_OTHER_KEYS_DISABLE + KITTY_KEYBOARD_DISABLE
    )


def test_install_maps_both_shift_enter_encodings_to_the_carrier_key():
    assert install_shift_enter_sequences() is True

    table = ansi_escape_sequences.ANSI_SEQUENCES
    assert table["\x1b[13;2u"] == SHIFT_ENTER_KEY
    assert table["\x1b[27;2;13~"] == SHIFT_ENTER_KEY


def test_install_keeps_bound_shortcuts_reachable_under_the_kitty_flag():
    install_shift_enter_sequences()

    table = ansi_escape_sequences.ANSI_SEQUENCES
    assert table["\x1b[27u"] == Keys.Escape
    assert table["\x1b[99;5u"] == Keys.ControlC
    assert table["\x1b[27;5;100~"] == Keys.ControlD
    assert table["\x1b[9;2u"] == Keys.BackTab
    assert table["\x1b[13;3u"] == (Keys.Escape, Keys.ControlM)


def test_install_is_idempotent():
    install_shift_enter_sequences()
    snapshot = dict(ansi_escape_sequences.ANSI_SEQUENCES)

    assert install_shift_enter_sequences() is False
    assert ansi_escape_sequences.ANSI_SEQUENCES == snapshot


def test_install_does_not_clobber_upstream_mappings():
    before = ansi_escape_sequences.ANSI_SEQUENCES["\x1b[27;5;13~"]
    install_shift_enter_sequences()

    assert ansi_escape_sequences.ANSI_SEQUENCES["\x1b[27;5;13~"] == before


def test_uninstall_restores_the_upstream_table():
    snapshot = dict(ansi_escape_sequences.ANSI_SEQUENCES)
    install_shift_enter_sequences()
    uninstall_shift_enter_sequences()

    table = ansi_escape_sequences.ANSI_SEQUENCES
    assert table == snapshot
    assert "\x1b[13;2u" not in table
    assert table["\x1b[27;2;13~"] == Keys.ControlM

    # Uninstalling again is a harmless no-op.
    uninstall_shift_enter_sequences()
    assert ansi_escape_sequences.ANSI_SEQUENCES == snapshot


@pytest.mark.parametrize("sequence", SHIFT_ENTER_SEQUENCES)
def test_vt100_parser_delivers_the_carrier_key(sequence):
    install_shift_enter_sequences()
    pressed = []
    parser = Vt100Parser(pressed.append)

    parser.feed(sequence)
    parser.flush()

    assert [press.key for press in pressed] == [SHIFT_ENTER_KEY]


class _QueueOwner:
    """Minimal binding owner: only what the queue bindings touch."""

    def __init__(self):
        self.queued = 0
        self._tasks_visible = False

    def _approval_visible(self):
        return False

    def queue_current_input(self):
        self.queued += 1


def _dispatch(bindings, keys):
    matches = [
        binding for binding in bindings.get_bindings_for_keys(keys) if binding.filter()
    ]
    assert matches, f"no active binding for {keys}"
    for binding in matches:
        binding.handler(None)


def test_shift_enter_and_alt_enter_both_dispatch_queue():
    owner = _QueueOwner()
    bindings = build_layered_key_bindings(owner)

    _dispatch(bindings, (SHIFT_ENTER_KEY,))
    assert owner.queued == 1

    _dispatch(bindings, (Keys.Escape, Keys.ControlM))
    assert owner.queued == 2


class _RecordingOutput:
    def __init__(self):
        self.raw = []
        self.flushes = 0

    def write_raw(self, data):
        self.raw.append(data)

    def flush(self):
        self.flushes += 1


class _TerminalHarness(LayeredReplTerminalMixin):
    """Just enough owner state for the render-flush hook."""

    application: Any = None
    _ambient_state = "idle"
    _background_process: Any = None
    _background_shell_task: Any = None
    _backgrounded = False
    _notices: Any = None
    _owner_loop: Any = None
    _session_id: Any = None
    _terminal_file: Any = None

    def __init__(self):
        self._pending_terminal_sequences = []
        self._background_terminal_active = False

    def commit_plan_state(self, lifecycle: str) -> bool:
        return True


def test_render_flush_pushes_enhancements_once_and_pops_on_done():
    harness = _TerminalHarness()
    output = _RecordingOutput()
    application = SimpleNamespace(output=output, is_done=False)

    harness._flush_terminal_sequences(application)
    harness._flush_terminal_sequences(application)
    assert output.raw == [KEYBOARD_ENHANCEMENT_ENABLE]

    application.is_done = True
    harness._flush_terminal_sequences(application)
    harness._flush_terminal_sequences(application)
    assert output.raw == [KEYBOARD_ENHANCEMENT_ENABLE, KEYBOARD_ENHANCEMENT_DISABLE]
    assert output.flushes == 2


def test_render_flush_stays_quiet_while_the_background_shell_owns_the_tty():
    harness = _TerminalHarness()
    harness._background_terminal_active = True
    output = _RecordingOutput()
    application = SimpleNamespace(output=output, is_done=False)

    harness._flush_terminal_sequences(application)

    assert output.raw == []
    assert output.flushes == 0
