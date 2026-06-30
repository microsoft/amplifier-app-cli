# Steering Input Reuse — honor slash commands & @-mentions (DRY)

## Problem
Steered input (typed mid-turn) is injected RAW: `SteeringInputManager._enqueue` →
`steer_cap(text)` (`session.steer`). It bypasses the normal REPL input processing, so a
steered `/mode plan` or `@file.py look at this` reaches the agent as literal text. We want
steered input treated like any other input by **reusing the same processing code** — one
home, so future improvements to the main input pipeline flow to steering automatically and
we never maintain two input paths.

## Discovery (file:line)
Normal REPL pipeline (`main.py:2947+`), three cleanly-separable stages:
1. `action, data = command_processor.process_input(text)` — slash dispatch.
   `CommandProcessor.process_input` (`main.py:445`) — **public, pure, sync**. Non-slash →
   `("prompt", {text})`; slash → `(action, data)` over 16 COMMANDS + MODE/SKILL shortcuts.
2. If `action == "prompt"`: `expanded = await _process_runtime_mentions(session, data["text"])`
   — @-mention expansion (`main.py:2476`, **module-private**, async, read-only).
3. `await _execute_with_interrupt(expanded)` — new turn (`session.execute`). Else
   `handle_command(action, data)` (REPL-level) + `trailing_prompt`.

Steering path: `_enqueue(text)` (`steering_input.py:203`) → `self._steer_cap(text)` →
`session.steer` (mid-turn inject, NOT a new turn). It skips stages 1 and 2 entirely.

**Key:** the shared part is the FRONT (classify + expand); the DELIVERY tail differs
(`session.execute` for the REPL vs `session.steer` for steering).

## Plan — one front-end, two delivery tails
1. Make the front-end reusable from the steering path (no logic duplication):
   - Export `process_runtime_mentions` (drop the leading `_`).
   - Pass the existing `CommandProcessor` instance into `SteeringInputManager.__init__`
     (it currently lives only in `interactive_chat`'s scope).
2. In `_enqueue`, run the SAME classification + expansion before delivering:
   - `action, data = command_processor.process_input(text)`
   - `action == "prompt"` → `expanded = await process_runtime_mentions(session, data["text"])`
     → `steer_cap(expanded)`  ← the @-mention + clean-message win.
   - else (a slash command) → mid-turn command policy (below).
3. Mid-turn command policy (the genuine design decision):
   - **Default = fail loud.** Commands that mutate the running turn or session
     (`/clear`, `/fork`, `/save`, `/config`, `/quit`) are NOT applied mid-turn; print an
     honest "`/clear` isn't applied mid-turn — finish or cancel the turn first." No silent
     drop, no raw injection.
   - Optionally apply a small whitelist of mid-turn-safe, non-context-mutating commands
     (e.g. `/mode`) via the same `handle_command` path.
4. DRY guarantee: classification + expansion live in the shared functions; both the REPL and
   steering call them. Improvements to `process_input` / mention expansion reach steering for
   free — the user's core requirement.

## Design forks for the council
- **Reuse depth:** (A) steering calls the existing `process_input` + `process_runtime_mentions`
  directly — logic single-homed, ~5 lines of orchestration duplicated, no change to the proven
  REPL. (B) Extract a shared `prepare_input_line(text)` wrapper both the REPL and steering call
  — orchestration also single-homed (guards against a future REPL step being missed by
  steering), but touches the proven REPL loop.
- **Command policy:** reject-all-commands-mid-turn (MVP, simplest) vs apply-a-safe-whitelist
  (e.g. `/mode`) + fail-loud for the rest.

## Out of scope / parked
Steering-prompt history; FollowUpQueue (defer commands to turn-end); fixed-input TUI.

## Risk × impact
- Message + @-mention reuse: low risk, high value, clearly correct — the actual gap hit.
- Mid-turn command execution: higher risk (mutating state mid-turn) — hence fail-loud default
  and this review.

## Decisions (locked, post-council)
Council verdict: 6 CONCERN, 0 FAIL. Converged: lock **Fork A** (don't touch the proven REPL —
B's only gain guards a hypothetical future step, paid by re-opening a tested surface before
ship); the @-mention + clean-message reuse is the real win; the mid-turn *command* policy is
the genuinely hard part (split-brain-turn risk via `handle_command` mid-turn).

User decisions:
- **Fork A.** Steering calls the existing `process_input` + `process_runtime_mentions`; ~5 lines
  of orchestration in `_enqueue`. No REPL surgery.
- **Command policy = REJECT-ALL mid-turn** (skip `/mode` and all command *application* during
  steering for now). Honest, actionable rejection on the existing steering-ack console path:
  "commands aren't applied mid-turn — finish or cancel the turn first, then run it." No silent
  drop, no raw injection. No `handle_command` call on the steering path at all.
- **Ship: hold.** Fold everything into ONE PR per repo (loop-streaming + app-cli), not
  ship-then-fast-follow.

Parked (with reasons): `/mode`-whitelist / safe-mid-turn-command apply (needs a written
"mid-turn-safe" invariant + ordering guarantee between `steer` and `handle_command` + DTU
proof); FollowUpQueue (defer rejected/queued commands to turn-end — the livability upgrade
user-advocate flagged); steering-prompt history; fixed-input TUI.

Edge-handling required (from tester-breaker / crusty):
- `_enqueue` must wrap expansion **defensively** — an exception from `process_runtime_mentions`
  (bad/huge/missing `@path`, permission error) must NEVER strand the drain loop. Catch, fail
  loud to the console, and do not steer a half-expanded message.
- Empty / whitespace / delimiter-only inputs (`""`, `" "`, `"/"`, `"//x"`, `"@"`, `"@ "`) must
  classify + handle without crashing.
- **Ordering preserved:** `_enqueue` awaits expansion inline before the next prompt read
  (sequential) → delivery order == type order. Confirm the `run()` loop is sequential.
- Rejection renders on the existing steering-ack console path (defined channel; never injected
  into the agent's context).
- Windows path handling is **inherited** from the reused `process_runtime_mentions` (reuse, not
  rewrite) — no new path logic on the steering side.
