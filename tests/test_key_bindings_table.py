"""The keymap table is the single source for key handlers and hint labels."""

import dataclasses

import pytest
from prompt_toolkit.keys import Keys

from amplifier_app_cli.ui.key_bindings_table import (
    ALL_CONTEXTS,
    CONTEXT_APPROVAL,
    CONTEXT_PALETTE,
    KEYMAP,
    NO_APPROVAL_CONTEXTS,
    Binding,
    hint_label,
    validate,
)
from amplifier_app_cli.ui.keyboard_protocol import uninstall_shift_enter_sequences
from amplifier_app_cli.ui.layered_repl_keys import build_layered_key_bindings


@pytest.fixture(autouse=True)
def _clean_sequence_table():
    uninstall_shift_enter_sequences()
    yield
    uninstall_shift_enter_sequences()


def _escape_binding(action: str, contexts: frozenset[str]) -> Binding:
    return Binding(
        action=action,
        pt_keys=("escape",),
        display_label="esc",
        contexts=contexts,
    )


class _Owner:
    """Minimal binding owner: only what registration touches at build time."""

    input_buffer = None
    _tasks_visible = False

    def _approval_visible(self):
        return False


def test_shipped_keymap_validates_cleanly():
    validate(KEYMAP)


def test_validate_rejects_same_key_in_same_context():
    conflicting = (
        _escape_binding("close_palette", frozenset({CONTEXT_PALETTE})),
        _escape_binding("dismiss", frozenset({CONTEXT_PALETTE, CONTEXT_APPROVAL})),
    )
    with pytest.raises(ValueError, match="palette"):
        validate(conflicting)


def test_validate_allows_same_key_in_disjoint_contexts():
    disjoint = (
        _escape_binding("close_palette", frozenset({CONTEXT_PALETTE})),
        _escape_binding("deny_approval", frozenset({CONTEXT_APPROVAL})),
    )
    validate(disjoint)


def test_validate_rejects_unknown_contexts():
    with pytest.raises(ValueError, match="unknown context"):
        validate((_escape_binding("close", frozenset({"basement"})),))


def test_validate_rejects_unlabeled_display_only_bindings():
    display_only = Binding(
        action="open_palette", pt_keys=(), display_label="", contexts=frozenset()
    )
    with pytest.raises(ValueError, match="display label"):
        validate((display_only,))


def test_bindings_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        KEYMAP[0].action = "hijacked"  # type: ignore[misc]


def test_hint_labels_come_from_the_first_labeled_binding():
    assert hint_label("queue_message") == "shift+enter"
    assert hint_label("cycle_mode") == "shift+tab"
    assert hint_label("cycle_permission") == "ctrl-p"
    assert hint_label("toggle_tasks") == "ctrl-t"
    assert hint_label("show_needs_you") == "ctrl-y"
    assert hint_label("open_palette") == "/"
    assert hint_label("approval_move") == "arrows"
    assert hint_label("palette_move") == "↑↓"


def test_cycle_mode_and_cycle_permission_are_independent_bindings():
    """Regression guard for the Shift-Tab/permission collision bug: mode and
    permission cycling must be two distinct, non-conflicting chords, not one
    shared control special-cased for two axes."""
    mode_binding = next(b for b in KEYMAP if b.action == "cycle_mode")
    permission_binding = next(b for b in KEYMAP if b.action == "cycle_permission")

    assert mode_binding.pt_keys != permission_binding.pt_keys
    assert mode_binding.contexts == permission_binding.contexts == NO_APPROVAL_CONTEXTS
    validate(KEYMAP)  # no key/context collision between the two chords


def test_hint_label_override_map_is_the_capability_seam():
    # A terminal probe that finds no shift+enter support swaps the advertised
    # queue key without touching the table.
    overrides = {"queue_message": "alt+enter"}
    assert hint_label("queue_message", overrides) == "alt+enter"
    assert hint_label("cycle_mode", overrides) == "shift+tab"


def test_hint_label_fails_loudly_for_unknown_actions():
    with pytest.raises(KeyError, match="not-an-action"):
        hint_label("not-an-action")


def test_approval_suppresses_exactly_the_no_approval_bindings():
    assert NO_APPROVAL_CONTEXTS == ALL_CONTEXTS - {CONTEXT_APPROVAL}


def test_every_table_chord_registers_exactly_one_prompt_toolkit_binding():
    bindings = build_layered_key_bindings(_Owner())
    expected = [binding for binding in KEYMAP if binding.pt_keys]

    assert len(bindings.bindings) == len(expected)
    registered_keys = {registered.keys for registered in bindings.bindings}
    assert (Keys.PageUp,) in registered_keys
    assert (Keys.Escape, Keys.ControlM) in registered_keys  # alt+enter chord
    assert (Keys.F21,) in registered_keys  # shift+enter carrier


def test_only_the_bare_escape_interrupt_is_non_eager():
    non_eager = [binding for binding in KEYMAP if not binding.eager]
    assert [binding.action for binding in non_eager] == ["interrupt_running"]
    assert non_eager[0].pt_keys == ("escape",)
