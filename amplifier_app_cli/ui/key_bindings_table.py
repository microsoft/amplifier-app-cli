"""Keymap as data: one binding table feeding key handlers and on-screen hints.

Modeled on the Codex TUI's ``key_hint.rs``/``keymap.rs``: every binding knows
how to match input (``pt_keys`` for prompt_toolkit registration, done in
``layered_repl_keys``) and how to render its own hint label (``display_label``,
looked up by the footer). Because both sides read the same ``KEYMAP`` tuple,
the keys that work and the keys the UI advertises can never drift apart.

Contexts name the UI states a binding is active in; ``validate`` rejects two
bindings claiming the same key while the same context is active (per-context
conflict validation, as in Codex ``keymap.rs``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from prompt_toolkit.keys import Keys

from .keyboard_protocol import SHIFT_ENTER_KEY

# UI contexts a binding can be active in. "composer" is the idle composer
# (empty input, no turn running); the overlay contexts mirror the transient
# surfaces of spec section 5; "running" is a mid-turn composer.
CONTEXT_COMPOSER = "composer"
CONTEXT_RUNNING = "running"
CONTEXT_PALETTE = "palette"
CONTEXT_TASKS = "tasks"
CONTEXT_REWIND = "rewind"
CONTEXT_EVIDENCE = "evidence"
CONTEXT_APPROVAL = "approval"

ALL_CONTEXTS = frozenset(
    {
        CONTEXT_COMPOSER,
        CONTEXT_RUNNING,
        CONTEXT_PALETTE,
        CONTEXT_TASKS,
        CONTEXT_REWIND,
        CONTEXT_EVIDENCE,
        CONTEXT_APPROVAL,
    }
)
# The approval bar owns the keyboard while visible (spec section 5); most
# composer bindings are suppressed under it.
NO_APPROVAL_CONTEXTS = frozenset(ALL_CONTEXTS - {CONTEXT_APPROVAL})

_MAX_LABEL_CHARS = 32


@dataclass(frozen=True)
class Binding:
    """One key chord bound to a named action in a set of UI contexts.

    ``pt_keys`` is the prompt_toolkit key chord (empty for display-only
    affordances such as ``/`` opening the palette, which is plain text input,
    not a key handler). ``display_label`` is the hint text for this chord;
    the first table entry for an action provides the advertised label (see
    ``hint_label``). ``arg`` parametrizes shared handlers (movement deltas).
    ``eager`` mirrors prompt_toolkit's eager flag; the bare-Esc interrupt is
    the one non-eager binding so the alt+enter chord can still match.
    """

    action: str
    pt_keys: tuple[str | Keys, ...]
    display_label: str
    contexts: frozenset[str]
    eager: bool = True
    arg: int | None = None


def _binding(
    action: str,
    pt_keys: tuple[str | Keys, ...],
    display_label: str,
    contexts: frozenset[str],
    *,
    eager: bool = True,
    arg: int | None = None,
) -> Binding:
    return Binding(
        action=action,
        pt_keys=pt_keys,
        display_label=display_label,
        contexts=contexts,
        eager=eager,
        arg=arg,
    )


_PALETTE = frozenset({CONTEXT_PALETTE})
_TASKS = frozenset({CONTEXT_TASKS})
_REWIND = frozenset({CONTEXT_REWIND})
_EVIDENCE = frozenset({CONTEXT_EVIDENCE})
_APPROVAL = frozenset({CONTEXT_APPROVAL})
_RUNNING = frozenset({CONTEXT_RUNNING})
_COMPOSER_IDLE = frozenset({CONTEXT_COMPOSER})

# Registration order matters for prompt_toolkit when several bindings for the
# same key are active at once (the last registered active match wins), so the
# relative order below preserves the pre-table registration order.
KEYMAP: tuple[Binding, ...] = (
    _binding("show_shortcut_help", ("?",), "?", _COMPOSER_IDLE),
    _binding("submit", ("enter",), "enter", ALL_CONTEXTS),
    # Real shift+enter first: its label is the advertised queue hint; the
    # alt+enter chord is the legacy-terminal fallback (spec section 9).
    _binding("queue_message", (SHIFT_ENTER_KEY,), "shift+enter", NO_APPROVAL_CONTEXTS),
    _binding("queue_message", ("escape", "enter"), "alt+enter", NO_APPROVAL_CONTEXTS),
    _binding("scroll_transcript", (Keys.PageUp,), "pgup", NO_APPROVAL_CONTEXTS, arg=-1),
    _binding(
        "scroll_transcript", (Keys.PageDown,), "pgdn", NO_APPROVAL_CONTEXTS, arg=1
    ),
    _binding("palette_move", ("up",), "↑↓", _PALETTE, arg=-1),
    _binding("palette_move", ("down",), "↑↓", _PALETTE, arg=1),
    _binding("approval_move", ("left",), "arrows", _APPROVAL, arg=-1),
    _binding("approval_move", ("up",), "arrows", _APPROVAL, arg=-1),
    _binding("approval_move", ("right",), "arrows", _APPROVAL, arg=1),
    _binding("approval_move", ("down",), "arrows", _APPROVAL, arg=1),
    _binding("approval_move", ("tab",), "arrows", _APPROVAL, arg=1),
    _binding("approval_allow_once", ("y",), "y", _APPROVAL),
    _binding("approval_allow_always", ("a",), "a", _APPROVAL),
    _binding("approval_deny_shortcut", ("d",), "d", _APPROVAL),
    _binding("approval_show_detail", ("c-a",), "ctrl-a", _APPROVAL),
    _binding("approval_ignore_text", (Keys.Any,), "", _APPROVAL),
    _binding("lane_move", ("up",), "↑↓", _TASKS, arg=-1),
    _binding("lane_move", ("down",), "↑↓", _TASKS, arg=1),
    _binding("rewind_move", ("left",), "‹ ›", _REWIND, arg=-1),
    _binding("evidence_move", ("left",), "←/→", _EVIDENCE, arg=-1),
    _binding("rewind_move", ("up",), "‹ ›", _REWIND, arg=-1),
    _binding("evidence_move", ("up",), "←/→", _EVIDENCE, arg=-1),
    _binding("rewind_move", ("right",), "‹ ›", _REWIND, arg=1),
    _binding("evidence_move", ("right",), "←/→", _EVIDENCE, arg=1),
    _binding("rewind_move", ("down",), "‹ ›", _REWIND, arg=1),
    _binding("evidence_move", ("down",), "←/→", _EVIDENCE, arg=1),
    _binding("insert_newline", ("c-j",), "ctrl-j", ALL_CONTEXTS),
    _binding("paste_image", ("c-v",), "ctrl-v", ALL_CONTEXTS),
    _binding("paste_text_or_image_path", (Keys.BracketedPaste,), "", ALL_CONTEXTS),
    _binding("interrupt", ("c-c",), "ctrl-c", ALL_CONTEXTS),
    _binding("exit", ("c-d",), "ctrl-d", ALL_CONTEXTS),
    _binding("toggle_tasks", ("c-t",), "ctrl-t", NO_APPROVAL_CONTEXTS),
    _binding("expand_latest_tool", ("c-o",), "ctrl-o", ALL_CONTEXTS),
    _binding("show_ledger", ("c-l",), "ctrl-l", ALL_CONTEXTS),
    _binding("open_rewind", ("c-r",), "ctrl-r", ALL_CONTEXTS),
    _binding("show_needs_you", ("c-y",), "ctrl-y", ALL_CONTEXTS),
    _binding("show_evidence", ("c-e",), "ctrl-e", ALL_CONTEXTS),
    _binding("cycle_mode", ("s-tab",), "shift+tab", NO_APPROVAL_CONTEXTS),
    # Independent permission-posture control (ADR-0005 amendment). Shift-Tab
    # and ctrl-p used to be the same shared control, special-cased to smuggle
    # a 5th "bypass" state into the mode cycle -- which meant Shift-Tab could
    # never reach `brainstorm` from `auto` (the two 5-state cycles share four
    # members but diverge at the fifth: brainstorm vs bypass). Now they are
    # two fully independent controls.
    _binding("cycle_permission", ("c-p",), "ctrl-p", NO_APPROVAL_CONTEXTS),
    _binding("composer.external_edit", ("c-g",), "ctrl-g", NO_APPROVAL_CONTEXTS),
    _binding("composer.edit_queued", ("escape", "up"), "alt+up", NO_APPROVAL_CONTEXTS),
    _binding("close_palette", ("escape",), "esc", _PALETTE),
    _binding("close_rewind", ("escape",), "esc", _REWIND),
    _binding("close_evidence", ("escape",), "esc", _EVIDENCE),
    _binding("deny_approval", ("escape",), "esc", _APPROVAL),
    _binding("close_tasks", ("escape",), "esc", _TASKS),
    # Not eager: bare Esc must wait (``ttimeoutlen``) so the alt+enter
    # (escape, enter) queue binding can match when both keys arrive together.
    _binding("interrupt_running", ("escape",), "esc", _RUNNING, eager=False),
    # Display-only: "/" is ordinary composer text that opens the palette, not
    # a registered key handler, but the footer still advertises it.
    _binding("open_palette", (), "/", frozenset()),
)


def validate(keymap: tuple[Binding, ...] = KEYMAP) -> None:
    """Reject malformed tables: unknown contexts, oversized or missing labels,
    and — the point of the exercise — two bindings claiming the same key while
    the same context is active."""
    claimed: dict[tuple[tuple[str | Keys, ...], str], Binding] = {}
    for binding in keymap:
        if not binding.action:
            raise ValueError("binding with empty action")
        unknown = binding.contexts - ALL_CONTEXTS
        if unknown:
            raise ValueError(
                f"binding {binding.action!r} names unknown contexts {sorted(unknown)!r}"
            )
        if len(binding.display_label) > _MAX_LABEL_CHARS:
            raise ValueError(f"binding {binding.action!r} display label too long")
        if not binding.pt_keys:
            if not binding.display_label:
                raise ValueError(
                    f"display-only binding {binding.action!r} needs a display label"
                )
            continue
        for context in binding.contexts:
            slot = (binding.pt_keys, context)
            other = claimed.get(slot)
            if other is not None:
                raise ValueError(
                    f"key {binding.pt_keys!r} in context {context!r} is claimed by "
                    f"both {other.action!r} and {binding.action!r}"
                )
            claimed[slot] = binding


def _build_hint_labels(keymap: tuple[Binding, ...]) -> dict[str, str]:
    """Precompute action -> first labeled binding, so lookups are O(1).

    ``hint_label`` is called several times per footer render; scanning the
    whole table on every call would repeat the same linear search on every
    frame for no benefit, since ``KEYMAP`` is fixed at import time.
    """
    labels: dict[str, str] = {}
    for binding in keymap:
        if binding.display_label and binding.action not in labels:
            labels[binding.action] = binding.display_label
    return labels


_HINT_LABELS = _build_hint_labels(KEYMAP)


def hint_label(action: str, overrides: Mapping[str, str] | None = None) -> str:
    """Return the on-screen label for *action* (first labeled table entry wins).

    ``overrides`` is the capability seam: callers that probe the terminal can
    substitute labels per action — e.g. ``{"queue_message": "alt+enter"}`` on
    legacy terminals where real shift+enter never arrives — without mutating
    the table. Raises ``KeyError`` for unknown actions so a typo in a hint
    lookup fails loudly instead of rendering a stale shortcut.
    """
    if overrides is not None:
        override = overrides.get(action)
        if override:
            return override[:_MAX_LABEL_CHARS]
    try:
        return _HINT_LABELS[action]
    except KeyError:
        raise KeyError(f"no display label for action {action!r}") from None


__all__ = [
    "ALL_CONTEXTS",
    "Binding",
    "CONTEXT_APPROVAL",
    "CONTEXT_COMPOSER",
    "CONTEXT_EVIDENCE",
    "CONTEXT_PALETTE",
    "CONTEXT_REWIND",
    "CONTEXT_RUNNING",
    "CONTEXT_TASKS",
    "KEYMAP",
    "NO_APPROVAL_CONTEXTS",
    "hint_label",
    "validate",
]
