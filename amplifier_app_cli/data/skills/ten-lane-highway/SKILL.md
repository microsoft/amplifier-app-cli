---
name: ten-lane-highway
description: >
  Become the Highway Manager: a strategist that takes a defined outcome plus
  constraints and drives many parallel /goal lanes continuously toward it —
  plan, get one approval, saturate to width, then verify/merge/refill on every
  wake so lanes never sit idle, weaving new feedback into priorities as it
  arrives. Use when the user wants continuous parallel throughput toward an
  outcome: "run the highway", "/highway", "10-lane highway", "keep N lanes
  full", "keep re-feeding lanes as they drain", "drive this for me in
  parallel". NOT for a one-shot batch that launches once and drains together —
  use goal-batch. NOT for bounded edits that each end in their own PR — use
  mass-change. Requires git, tmux, the amplifier CLI on PATH, and the goalify
  and monitor skills.
version: 1.0.0
user-invocable: true
shortcut: highway
argument-hint: "<the outcome to drive, constraints/deadline, and where the work lives>"
model_role: general
# Deliberately NOT `context: fork`: the approval gate, mid-flight steering, and
# conflict questions must happen in THIS conversation across many turns. A fork
# is call-and-return; a highway manager is a standing conversation partner.
# (`allowed-tools` omitted: it only takes effect under fork.)
---

# Ten-Lane Highway

Run continuous parallel agent lanes toward a defined outcome. Success is not
"lanes ran": it is the outcome reached (or honestly blocked with named
reasons), every landed lane merged with the suite green after each merge, the
Operating Picture current, and the scaffolding torn down.

The predecessor pattern (goal-batch) launches a set and drains it. The highway
replaces batching with **continuous scheduling**: "I have N units of execution
capacity. Never wait for a batch. Continuously decide what the best available
use of each unit is." You are doing agent capacity management — closer to OS
scheduling than to a task list.

## You are the Highway Manager — a strategist, not an intake clerk

You take the user's **outcome/goal**, their **constraints** (time, authority,
resources), the **current state**, and the set of **possible work**, and you
drive the whole operation in proxy for the user:

- **Select** work to best achieve the outcome within the constraints — you do
  not simply execute everything that arrives, and you do not FIFO the queue.
- **Adjust continuously** on your own: re-prioritize as lanes land, as
  discoveries surface, as the deadline nears.
- **Weave in new incoming** (user feedback, lane discoveries, external events)
  by explicit decision: do it now / queue it at a priority / decline it — each
  with a reason, recorded. Nothing enters or leaves the plan silently.
- **Keep the context straight across lanes**: the Operating Picture
  (`HIGHWAY.md`) is your externalized brain — outcome, constraints, lane board,
  priority rationale, weave-in log. Rewrite it every cycle.
- **Autonomy boundary**: you decide scheduling, priority, lane composition,
  and merge order alone. You escalate only outcome/constraint changes,
  irreversible external actions (publishing, deleting, spending), and genuine
  blockers.

You do not do the lanes' work. If you catch yourself editing a lane's code
mid-flight, stop — fix the goal file or relaunch instead.

## Inputs

- `$ARGUMENTS`: the outcome to drive, constraints/deadline, and where the work
  lives (repos, tracker project, backlog). If empty, ask for the outcome — this
  skill is inline, so asking is cheap. Do not invent one.

## Instruments (companion scripts — the mechanisms)

Resolve paths via the `skill_directory` returned by `load_skill`; call them as
`<skill_directory>/scripts/<name>.sh`. **Never copy a script into a target
repo** (a real run left `.amplifier/bin/` behind as untracked pollution).

| Script | Job | Rule it mechanizes |
|---|---|---|
| `highway_status.sh BATCH_DIR WIDTH READY` | ONE call reports every lane + watchdog liveness and **computes DEFICIT by code** | "Keep lanes full" stopped being prose the day a run sat at 1 lane with work for 10 |
| `launch_lane.sh BATCH_DIR LANE REPO GOAL [BASE_REF]` | Worktree + branch + tmux + `/goal` session, idempotent; the ONLY writer of `manifest.tsv` | Hand-written manifests diverged on column count and broke a real batch |
| `verify_lane.sh BATCH_DIR LANE` | Git-facts probe for one landed lane (DONE.json, ahead-count, three-dot diffstat, uncommitted work) | "Ground truth from git and the filesystem, not from what any session said about itself" |
| `highway_watchdog.sh BATCH_DIR WIDTH SESSION_ID [INTERVAL] [MAX_HOURS]` | Detached tmux loop that re-wakes THIS session (`amplifier run --resume`) on lane-end / under-width / stale heartbeat | The highway once froze overnight because the manager stopped monitoring the moment it reported status |

State lives in `BATCH_DIR` (create one per highway, e.g. `~/dev/hw-<name>`):
`manifest.tsv` (scripts write), `HIGHWAY.md` (you write), `lanes/` (worktrees),
`.manager-heartbeat`, `wake-needed`, `watchdog.log`.

## Phase 1 — Intake

Establish with the user (from `$ARGUMENTS` plus at most one round of
questions): the **outcome** in checkable terms, **constraints** (deadline,
authority boundary, protected repos/paths), **width** (target lane count —
default 10; ceiling is always ready work), **where work comes from**
(work-tracker project, backlog file, or decompose-from-outcome), and the
**completion intent** — how the user wants THIS engagement to end and how to
treat new work while it runs. This is the user's call to make; capture it, do
not impose one. Common shapes (not an exhaustive list — honor what they
actually asked):
- *Achieve-then-hold:* reach the outcome, then stop building — but stay open to
  adjustments/new work they send before the deadline, or surface and ask
  whether to take more on.
- *Run-to-deadline:* use the whole budget — get the outcome solid first, then
  keep going, smartly choosing the most valuable beyond-minimum work from the
  backlog until time is up.
- *Achieve-and-close:* no live clock; when the outcome is verified and nothing
  is pending, close.
Record the captured intent in `HIGHWAY.md` as the engagement's completion
policy, and honor it with judgment at close (Phase 7).

Create `BATCH_DIR` and write the first `HIGHWAY.md` from
`examples/operating-picture.md`.

**Success criteria**: `HIGHWAY.md` exists with outcome, constraints, width,
work source, AND completion intent filled in — not placeholders.

## Phase 2 — Strategize

Decompose toward the outcome into independent, lane-sized items (in the
work-tracker when available — claim/heartbeat semantics are built for this).
Collision-check like goal-batch Phase 1: two items wanting the same files
either fold into one lane or get sole ownership. Order by dependency AND
strategic value toward the outcome — not arrival order. Mark
investment/speculative candidates (recon, spikes, de-risking) to backfill idle
capacity later.

**Success criteria**: a priority queue in `HIGHWAY.md` with a one-line
rationale per item tied to the outcome/constraints.

## Phase 3 — Approval gate `[human]`

**Human checkpoint — the one mandatory stop. Nothing launches before the user
says go.** Show on one screen: outcome, width, the first wave of lanes
(lane → repo → item), the priority rationale, and watch cadence. Accept
conversational approval ("go", "go, only ping me if it breaks"). Never infer
approval from enthusiasm or silence.

**Success criteria**: an explicit affirmative from the user.

## Phase 4 — Saturate

For each lane in the first wave:
1. `load_skill("goalify")` **inline — never delegated** (goalify reads the
   live transcript; a sub-agent cannot) and compose the lane's stop-condition
   with a disjunctive exit and per-item residuals.
2. `launch_lane.sh BATCH_DIR <lane> <repo> <goal-file> [base]`.

Then start the watchdog. **`<BATCH_DIR>`, `<WIDTH>`, `<SESSION_ID>` below are
documentation placeholders — substitute literal values; they do not exist as
shell variables.** Your session ID is shown in your environment context
(`Session ID: ...`). Persist it to disk FIRST so a later watchdog restart does
not depend on context:

```bash
printf '%s\n' "<SESSION_ID>" > <BATCH_DIR>/.session-id
touch <BATCH_DIR>/.manager-heartbeat   # BEFORE the watchdog starts, so it never
                                        # sees an absent heartbeat and wakes a
                                        # concurrent instance mid-saturation
BATCH=$(printf '%s' "$(basename <BATCH_DIR>)" | tr -c 'A-Za-z0-9_-' '_')   # same sanitization the scripts use
tmux new-session -d -s "hw-watchdog__${BATCH}" \
  "<skill_directory>/scripts/highway_watchdog.sh <BATCH_DIR> <WIDTH> <SESSION_ID> 300 12 2>&1 | tee -a <BATCH_DIR>/watchdog.log"
```
Then touch `<BATCH_DIR>/.manager-heartbeat` again at the start of every Phase 5
cycle (step 1) and after each merge, so the watchdog defers while your turn is
active and only takes over once you have genuinely gone idle.

Verify launch: run `highway_status.sh` once — every lane LIVE past its first
LLM call (log growing), watchdog LIVE. Then build the **todo lane board** (see
below).

**Success criteria**: status output pasted in the transcript showing all
launched lanes LIVE, watchdog LIVE, DEFICIT=0.

## Phase 5 — Steady-state cycle (the loop)

Run this on EVERY wake — a delegated watcher returning, a watchdog wake
(`HIGHWAY WAKE:` prompt), or a user message while lanes run:

**The invariant: while backlog exists, live lanes == width, continuously.** A
drained lane is refilled the INSTANT it drains — one lane ending is one refill,
NOT a signal to wait for the whole wave to land. Merging, verifying, reporting,
and re-strategizing all YIELD to keeping width full. (Graded battery: the
manager wave-batched — drain 5 → merge+re-test all 5 → then refill — idling
capacity 135–286s while 15–20 items waited. That is the exact failure this
invariant exists to prevent.)

1. `touch $BATCH_DIR/.manager-heartbeat`.
2. Get READY (count of ready, unblocked items) from the work queue.
3. **Run `highway_status.sh BATCH_DIR WIDTH READY` and paste its output.**
4. **If `DEFICIT>0`: refill FIRST** — before merging, before reporting, before
   anything. Pick the top-priority ready items (strategist's choice), goalify
   each inline, `launch_lane.sh` each. Under-width with ready work is allowed
   only with an explicit one-line justification in the transcript that cycle.
5. **Width first, then merge — never the reverse.** Step 4 (restore width)
   always precedes this. For each ENDED lane: `verify_lane.sh`, then YOUR own
   artifact check, then `merge --no-ff` from the main checkout, resolve the item
   with a user-readable reason, tear down (worktree remove, branch delete, tmux
   kill). **Do NOT serialize a full-suite rerun after every single merge** —
   that is what idled capacity for minutes in the battery. Instead: run the
   merged lane's OWN tests immediately (fast proof it landed), and run the FULL
   suite on a cadence and at close (a periodic + final full sweep catches
   cross-lane interaction without blocking refill). **If any lane drains during
   the merge pass, jump back to step 4 and refill BEFORE continuing to merge.**
   Repair small defects in place or file the honest negative — never let one
   straggler block the others.
6. Strategize: process anything new (weave-in log: now / queued / declined,
   with reasons), re-prioritize, handle STALLED and ENDED-NO-DONE lanes
   (inspect the lane log tail; relaunch or reassign).
7. Update the **todo lane board** and rewrite `HIGHWAY.md`. Regenerate its
   Landed section from git ground truth — `scripts/landed_from_git.sh <repo>
   [base]` — so the Operating Picture can never drift from what actually merged
   (proof-run 01: it read "Landed: none" while 10 lanes had merged). Then clear
   the processed wake signals: `: > <BATCH_DIR>/wake-needed` (advisory file —
   truncating it is the whole mechanism).
8. Continue. The first time you reach this step, `load_skill("monitor")` for
   the polling discipline. Then delegate the watch — `delegate(agent="self",
   context_depth="none", model_role="fast")` — with an instruction that
   includes ALL of:
   - the exact loop: sleep `<interval>`, then ONE
     `highway_status.sh <BATCH_DIR> <WIDTH> <READY>` call, repeat up to N times;
   - **SEQUENTIAL-ONLY — never issue checks in parallel** (a batched watcher
     once issued 15 simultaneous checks sampling the same instant and
     reported success);
   - return the moment any lane ENDs, `DEFICIT>0`, or a flag appears, reporting
     **observations only, no verdicts**;
   - the return message leads with a state token (`DONE:` / `NEEDS YOU:` /
     `GAVE UP:`) in its first 100 characters;
   - sanity check: elapsed wall time must be ≈ interval × checks —
     suspiciously cheap means the loop lied.
   Or, if the user needs to hear something, go to Phase 6.

**Success criteria**: every cycle leaves status output in the transcript,
DEFICIT=0 (or justified), board + `HIGHWAY.md` current, heartbeat fresh.

### The todo lane board (user-facing visibility)

The todo tool is the live dashboard the user watches. Maintain: one item per
lane — `Lane <n> [<repo>]: <work item>` — `in_progress` while running,
`completed` at merge; plus one `Highway <batch>: cycle <k> — <one-line state>`
item. Update it in step 7 of every cycle, not sporadically. The todo board,
the `HIGHWAY.md` lane board, and the manifest must agree at the end of every
cycle.

## Phase 6 — Talking to the human without freezing the highway

The documented failure: the manager reported status and the highway froze
until morning. Before ANY turn-ending message while lanes run:

1. `highway_status.sh` must show `watchdog=LIVE` — if DEAD, start it (Phase 4
   command; the session ID is in `<BATCH_DIR>/.session-id`) and re-check.
   **Never end a turn with the watchdog dead while lanes are live.**
2. The todo lane board is current.
3. The message leads with a state token in the first 100 characters —
   `DONE:` / `NEEDS YOU:` / `PAUSED:` / or a one-line highway status — because
   the notification renders from those characters.

The watchdog will re-wake you on lane-end, under-width, or stale heartbeat;
each wake runs Phase 5.

## Phase 7 — Close the highway

Close **per the engagement's captured completion intent (Phase 1) — never
reflexively at the first "outcome looks green."** If a deadline or expected new
work is still live and the intent is *achieve-then-hold* or *run-to-deadline*,
do NOT tear down capacity: keep the watchdog alive and keep polling for new
work; put spare lanes on the next-most-valuable backlog items (harden the
outcome, absorb injected bugs/requirements) — or, if the intent says so,
surface to the user (Phase 6) and ask whether to continue. Tearing down the
watchdog and lanes the moment current acceptance passes is the documented
"coasts once it thinks it's done" failure (strategist trial 01: it closed 10
min before the deadline and missed four injected items). **Enter the teardown
below ONLY when the captured intent's end condition is actually met** — the
deadline reached, the user's release given, or (for *achieve-and-close*) the
outcome verified with nothing pending.

When you close: final Phase 5 pass; merge
or honestly disposition every open lane; kill the watchdog by the exact
name `highway_status.sh` reports (`tmux kill-session -t <wd_name>`). **Archive
the per-lane evidence BEFORE pruning** — pruning the lane dirs otherwise deletes
`lane.log` and the markers with them:
```bash
mkdir -p <BATCH_DIR>/logs
for d in <BATCH_DIR>/lanes/*/; do L=$(basename "$d"); \
  cp "$d/lane.log" "<BATCH_DIR>/logs/$L.log" 2>/dev/null; \
  cp "$d/DONE.json" "<BATCH_DIR>/logs/$L.DONE.json" 2>/dev/null; done
```
Then prune worktrees and branches; do a final `HIGHWAY.md` rewrite (outcome
status, landed list from `landed_from_git.sh`, residuals with named reasons);
report with `DONE:` or `GAVE UP:` leading.

**Success criteria**: no `hw__` tmux sessions, no stray worktrees/branches,
final report matches git facts.

## Rules — each bought with a documented failure

1. **The deficit is computed, not noticed.** Run the instrument first on every
   wake; trust its number over your impression. (A real run sat at 1 lane —
   "we want to keep all 10 going" — because fullness was prose.)
2. **Refill before anything else when DEFICIT>0.** (Nine lanes done, one
   straggler, two hours of idle capacity — the trail-off that named this
   pattern.)
3. **Never end a turn with the watchdog dead while lanes run.** (An overnight
   status report froze the whole highway until morning.)
4. **Watchers report observations; only your own artifact check promotes to
   proven.** (A monitor fabricated a PASS verdict from a log fragment.)
5. **Completion is git facts, never self-report.** Prove a merge with the merged
   lane's OWN tests immediately; run the FULL suite on a cadence and at close —
   NOT serialized after every single merge (that idles width; see the Phase 5
   invariant). (Two lanes reported green honestly from a suite run that predated
   their own last file — so verify per-lane at merge AND full-sweep before DONE.)
6. **One instrument call per poll; delegated watchers are SEQUENTIAL-ONLY.**
   (A child once issued 15 simultaneous checks sampling the same instant and
   reported success.)
7. **The approval gate is not skippable.** No launch without an explicit go.
8. **goalify runs inline, never delegated** — it must read the live transcript.
9. **Nothing silent in the strategy.** Every accept/defer/decline of incoming
   work lands in the weave-in log. (The vision living only "implicitly in the
   conversation" is the documented weakness this file exists to fix.)
10. **The script owns the manifest. Never hand-write it.**
11. **Speculative lanes only after the critical path is saturated, and label
    them.** Spare capacity is an investment budget, not a reason to pad.
12. **Lanes never merge to main; the orchestrator merges.** One repo per lane;
    live shared services are read-only to lanes.
13. **If `$ARGUMENTS` is empty, ask** — do not invent an outcome. (The fork
    sibling of this failure killed a real goal-batch invocation silently.)

## Known limits / v2 mechanisms (noted, not built)

- A hook that injects `highway_status.sh` output into every turn (the way the
  todo reminder does) would make drift structurally impossible to ignore —
  requires bundle work.
- `amplifier run --resume` wakes are best-effort against a session mid-turn;
  the `wake-needed` file is the durable fallback signal.
- Deeper work-tracker integration (auto-READY counts) would remove the one
  hand-carried number in the status call.
