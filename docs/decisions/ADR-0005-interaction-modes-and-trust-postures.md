# ADR-0005: Interaction Modes and Trust Postures

Status: Accepted

## Context

The interactive CLI has two related but independent policy dimensions:

- an interaction mode controls how the app presents and orchestrates work;
- a trust posture controls which capability classes are automatic, require
  approval, or are blocked.

Bundle-discovered modes are ecosystem content. The built-in terminal modes and
permission UX are application policy because they define the behavior of the
user-facing `amplifier` process.

## Decision

The app CLI owns the built-in interaction modes `chat`, `plan`, `brainstorm`,
`build`, and `auto`. Bundles may advertise additional workflow modes, but those
do not replace or silently mutate the app's trust posture.

Trust is a separate typed state with `chat` as the safe default. `bypass` is
available only after an explicit user action, such as cycling to it with the
dedicated permission control (ctrl-p) or choosing the bypass permissions
preset. The active posture must always be visible in the persistent footer.

Persisted state records the policy schema version and whether bypass was an
explicit choice. Legacy sessions that cannot prove explicit bypass selection
resume in the safe `chat` posture.

One app-owned interaction state service is the authority for:

- active built-in UI mode;
- active bundle mode, when present;
- active trust posture;
- persistence and restore metadata;
- mode and posture transition events.

Callers consume typed snapshots and transition methods rather than mutating
coordinator dictionaries directly.

## Consequences

- Mode changes cannot silently grant broader permissions.
- New and legacy sessions have a predictable safe posture.
- Bundles remain composable without owning terminal safety policy.
- The footer, approval system, governance hooks, subprocess children, and
  persistence layer must derive from the same interaction-state snapshot.

## Non-Goals

This decision does not move app UI profiles into bundles and does not remove
explicit bypass mode. It separates ownership so either policy can evolve
without becoming an implicit side effect of the other.

## Amendment: independent controls for mode and permission

The original implementation exposed mode and trust posture as two typed
states (as decided above) but a single shared keybinding, Shift-Tab, to
cycle both: `next_shift_tab_state()` special-cased `permission_posture ==
"bypass"` to advance the mode, and special-cased `active_mode == "auto"` to
force `permission_posture` to `"bypass"`. Mode (`chat, plan, brainstorm,
build, auto`) and permission (`chat, build, plan, auto, bypass`) are both
five-state cycles that share four names but diverge at the fifth --
`brainstorm` is a mode with no permission-posture counterpart, and `bypass`
is a posture with no mode counterpart. The shared control could not express
both cycles: from `auto`, Shift-Tab could reach `bypass` but could never
reach `brainstorm`, because the special-case for `auto` always won.

Mode and trust posture are independent policy dimensions per the Decision
above; they now have independent controls to match:

- Shift-Tab cycles mode only (chat → build → plan → auto → brainstorm →
  chat) via `InteractionController.cycle()`. It never reads or writes
  permission posture.
- Ctrl-P cycles permission posture only (chat → build → plan → auto →
  bypass → chat) via the new `InteractionController.cycle_permission()`,
  which reuses the mode-independent `TrustState.cycle()` that already
  existed for this purpose.

The explicit-bypass-selection guarantee is preserved and generalized: using
the dedicated permission control is itself the explicit user action this
ADR requires, for any posture it lands on (not only `bypass`) -- it latches
`_trust_explicitly_set` so a later mode-only Shift-Tab cycle never silently
reverts the chosen posture to a mode's default preset.
