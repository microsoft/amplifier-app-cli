---
name: goal-batch
description: >
  Plan a batch of independent work into isolated lanes, get your approval, then
  run each lane as its own autonomous /goal session — one git worktree, one
  branch, one tmux session each — and verify and merge the results yourself.
  Use when work decomposes into pieces that can run at the same time: "run these
  in parallel", "goal-batch", "launch lanes for these", "work these N tasks
  simultaneously", "batch these as goals". Nothing launches until you have seen
  the lane split and said go. This is NOT fire-and-forget: the orchestrating
  session re-runs the full test suite itself after every merge and never accepts
  a lane's own claim that it finished. NOT for bounded edits that each end in
  their own PR — use mass-change for that. Requires git, tmux, the amplifier CLI
  on PATH, and the goalify and monitor skills.
version: 2.0.0
user-invocable: true
argument-hint: "<the work to batch, or where it is enumerated>"
allowed-tools: [bash, read_file, write_file, edit_file, grep, glob, delegate, load_skill, todo]
model_role: general
---

# Goal Batch

Split large work into lanes that cannot collide, run each as an autonomous
`/goal` session in its own worktree, and land the results through a merge pass
you verify yourself.

You are the ORCHESTRATOR. You plan, launch, watch, verify, merge, report. You do
not do the lanes' work — if you catch yourself editing a lane's code mid-flight,
stop and fix the goal file or relaunch instead.

Every rule here was bought with a real failure in production use — roughly 45
lanes across three operators. Where a rule looks fussy, it is load-bearing.

Shape references (plan screen, approval vocabulary, DONE.json, report) live in
`examples/plan-and-report.md`. This file is the rules.

**Invoking.** `/goal-batch <batch>` in the interactive TUI, or **"Use the
goal-batch skill and …"** in natural language — both load this skill reliably
(measured). What does NOT work is a bare `/goal-batch …` in a headless
`amplifier run "<prompt>"`: the model reads the token as prose and does the work
itself, producing plausible branches with no gate, no manifest, and no lanes.
In a headless one-shot, **direct the assistant in natural language** — e.g.
`amplifier run "Use the goal-batch skill and run this batch: …"` — or call
`load_skill(skill_name="goal-batch", arguments=…)` outright. Naming the skill
is what loads it; the bare slash token is not.

**If `$ARGUMENTS` is empty, you have no batch.** This skill forks — the
sub-session cannot see the parent conversation, so an `arguments`-less
`load_skill` call arrives with nothing to plan against. That happened in a real
run and the whole invocation died silently. Do not produce an empty plan and do
not guess: reconstruct the batch from whatever context you were given, and if
there genuinely is none, say so in one line and ask for the work. One question,
not an interview.

**Running the companion scripts.** `load_skill` returns `skill_directory`.
Resolve every script from it — `<skill_directory>/scripts/<name>.sh` — and use
that absolute path. Never a bare relative path: the working directory is the
target repo, not the skill, and in a bundle the skill lives under a
hash-suffixed cache path that changes between installs. **Never copy a script
into the target repo** — a real run did exactly that, leaving `.amplifier/bin/`
behind as untracked pollution in someone else's repository.

**Input** — `$ARGUMENTS`: the work to batch, or a pointer to where it is
enumerated. If lane independence is unclear, that is Phase 1's job, not a
question for the user yet.

---

## Phase 1 — Plan

Read enough to decompose honestly. Lanes each own a disjoint set of files, have
a provable outcome, and do not need a sibling to finish first.

Then do the work that decides whether this batch lands:

- **Collision analysis.** Map every candidate lane to the files it will touch
  and print the intersections. Where two lanes want the same file, FOLD THEM
  INTO ONE LANE, or assign sole ownership and have the other record its needed
  edit as a residual. Merging two lanes that would have collided is a win.
- **Pre-assign anything numbered** — issue IDs, task IDs, migration numbers.
  Parallel lanes *will* both grab the next free number. Two really did take 075.
- **Name the seams.** List files no lane owns. That is where it breaks: a
  packaging script nobody owned shipped broken because nobody tested it. Either
  give a lane the seam, or carry it to Phase 7 as untested.
- **Pin the base SHA** every lane branches from, and **record current test
  baselines** so "no regressions" is a number.

No lane cap. Width is bounded by the work. **That no-cap rule governs *lanes*,
not the resources lanes create.** Anything a lane stands up that outlives a
process — containers, VMs, background servers, environments — does need a
cumulative ceiling for the whole batch, because no single lane can enforce one:
each lane sees only its own usage, never the total. The orchestrator is the only
actor with the cross-lane view, so it keeps a running resource ledger for the
batch — beside the manifest and goal files — and holds combined usage under a
declared ceiling. Unbounded lanes are fine; unbounded resource accumulation
across them is what exhausts the host.

## Phase 2 — Review and go

**Nothing is created and nothing launches before the user has seen the plan.**

**Open with what the batch will NOT deliver.** The first two lines are:

```
When this finishes you WILL have: <the concrete end state>
You will NOT have:                <what stays undone, and what it blocks>
```

This is the whole point of the gate and the one thing it has actually failed at.
In a real run the fact that the batch *would not produce a working component*
was disclosed — as the third bullet, inside a code block, under a heading about
testing — and the user found out only after it finished: *"So the batch had post
work associated with it? That was not clear to me."* Anything a reader would be
upset to discover afterwards goes in line two, not in a section they will skim.

Then the plan on one screen — lane table, contested files, parked items, unowned
files, per-lane bounds, watching mode. Fifteen seconds to read, one sentence to
argue with. Shape in `examples/`.

Ask questions ONLY where a wrong guess costs a relaunch: an ownership call you
cannot make from the code, a repo you are not sure you may push to, a
human-reserved action. Bring a plan, not a form.

**Every lane must trace to something the user asked for.** State the origin of
each lane in the table. A run once invented a lane out of the orchestrator's own
scratch notes and got 32 minutes into the gate before the user caught it:
*"Lane F drafts five upstream feature requests against repos we don't own."
What? Why are we changing other repos?* If you cannot name where a lane came
from, it is not a lane.

**Approval is conversational.** Accept a bare `go`, and accept a `go` carrying
options — reporting cadence, per-lane bounds, dropping a lane — and honor them
without re-asking. Anything that is not an affirmative is feedback: revise, show
the diff, re-present. Never infer approval from enthusiasm, repetition, or
silence.

**Pre-authorization is valid and must be honored.** If the invocation itself
grants approval — "pre-approved", "don't stop and ask", "go ahead and run it" —
post the plan for the record and proceed without waiting. Users have started
pre-arguing with the gate in the same breath as the request (*"but do NOT bug me
about this simple design, then launch into /goal-batch"*), and the cleanest run
in the field skipped the gate entirely. The plan is always shown; stopping is
what a user may waive. Waiving is theirs to do, never yours to assume.

## Phase 3 — Compose the goal files

Load **goalify** and use it per lane; it owns outcome-first composition and
termination linting.

**Commit the goal files to the base commit before Phase 4 cuts any worktree**,
and pin `BASE_SHA` to that commit. Goal files written only into a worktree die
with it; goal files committed after the worktree exists are not in it, and the
launcher will refuse the lane.

Every goal carries:

- **The disjunctive exit, close to verbatim.** The load-bearing sentence — what
  makes lanes terminate honestly instead of spinning or declaring victory:

  > Complete when **either** every item reaches a terminal state, **or** it is
  > conclusively demonstrated the remainder cannot, naming the blocker for
  > each. Items ending FAIL or BLOCKED are residuals, not failures of the goal.

- **Terminal states** `PASS` / `FAIL-named` / `BLOCKED-named` / `PENDING-HUMAN`.
  `PENDING-HUMAN` is distinct on purpose: a lane that discharges a review by
  assigning it to a person has deferred, not finished, and Phase 7 must show
  that separately.
- **Working directory + branch + base SHA.** "Work ONLY here. Do not touch the
  main checkout or sibling worktrees."
- **File ownership** with the residual protocol: crossing into another lane's
  files is a defect, not a courtesy — record the needed edit and stop. Draw
  ownership around what makes the thing *work*, not around the artifact. A lane
  owning an icon but not the packager does not own a shippable icon.
- **Commit early, push always.** Two tmux-server crashes cost minutes instead of
  hours because every lane pushed as it committed.
- **Never merge to main.** The orchestrator merges.
- **Host capability limits, stated plainly.** A lane that authors what this box
  cannot compile ships code that breaks on someone else's first build. Live
  shared services are read-only evidence to a lane; tests use fixtures.
- **Non-git resources the lane provisions, enumerated with their teardown.**
  Anything a lane stands up that outlives a process — containers, VMs,
  background servers, external environments — must be named in the goal, and
  tearing each one down (or an explicit, named handoff to an owner) must be part
  of the lane's own DONE criteria. File ownership bounds the *files* a lane
  touches and says nothing about the *resources* it starts; a lane that exits
  with resources still running has not finished, and Phase 7 cannot verify
  teardown for what a lane never declared.
- **The time bound**, and that exceeding it is a terminal `BUDGET` state, not a
  reason to rush the work or skip the commit.
- **Add `DONE.json` to the repo's `.gitignore` before writing it.** Four of five
  lanes in one run committed theirs and the batch's own merge pass collided on
  that file. It is a signal, not an artifact.
- **Write `DONE.json` in the worktree root as your final act** — the terminal
  marker. Without it, an exited session is indistinguishable from a killed one.
  Fields: `lane, session_id, verdict, branch, head, pushed, items[],
  residuals[], pending_human[], resources[], suite`. `resources[]` names every
  non-git resource the lane provisioned and whether it was torn down or handed
  off, so Phase 7 can verify teardown instead of guessing. Shape in `examples/`.
  **`verdict` is exactly one of `COMPLETE` / `BLOCKED` / `PARTIAL`**, and
  `session_id` is this lane's own — two lanes in one batch wrote `"PASS"` and
  `"success"`, which no parser reads the same way, and a `DONE.json` without a
  session_id cannot be told apart from one inherited through the base commit.
- **KNOWN section** — env setup, suite commands, baselines, footguns. Speed
  aid, not criteria.

## Phase 4 — Preflight

Fail loud here. Never launch degraded.

- **Every lane's output must be COMMITTABLE — not necessarily pushable.** A
  local-only repo is fine: a lane commits to its own branch, and those commits
  live in the main repo's object store, so they survive `git worktree remove`
  and merge normally with no network at all. Verify that, not a remote.
  - **A remote is recommended, not required.** With one, lanes push as they
    commit and a crash costs minutes; without one, the work is still safe on
    disk but only on this machine. Say which the user is getting, once, and
    move on — do not block the batch.
  - **What actually loses work is output that is never committed at all.** The
    real 47MB loss came from a repo that *had* two remotes: the lane's entire
    output sat under gitignored paths, so it was never committed and pushing
    would have saved none of it. So check the thing that matters: will each
    lane's deliverables land in a commit? If a lane's real product is a
    gitignored artifact directory, say so at the gate — that is a batch that
    cannot deliver, and no remote fixes it.
- **Smoke-test the launcher once**, ~90s, thrown away, and confirm any
  capability a lane depends on is reachable from inside a lane.
- **Create worktrees**, skipping any that already exist rather than failing on
  them — this path is re-entered on resume:
  `[ -d "$WT" ] || git worktree add -b <branch> "$WT" <BASE_SHA>`

**One worktree per lane is not optional.** The session workspace slug is derived
from the working directory, so lanes sharing a directory share a workspace and
their activity becomes unattributable.

## Phase 5 — Launch and register

One tmux session per lane, via the companion script — once per lane:

```bash
<skill_directory>/scripts/launch_lane.sh \
    <batch> <lane> <worktree> .amplifier/goals/<lane>.md \
    <repo>/.amplifier/goals/manifest.tsv [wall_s] [max_turns]
```

**The script owns the manifest. Never hand-write it.** It writes the header on
first call and appends one 8-column row per lane. Both users of v1 ended up
hand-writing manifests because the launcher emitted 5 columns while the status
probe read 7 — they never agreed, and no batch could use both tools as shipped.
If you find yourself composing a `manifest.tsv` heredoc, stop: something is
wrong and hand-writing it will hide the failure.

The tmux session name and log path are **derived** from `<batch>`/`<lane>`, so
the `gb__*` convention that the orphan sweep and pane-viewer match strings depend
on cannot drift.

It also enforces five things you must not reimplement by hand: the tmux command
carries **no user prose** (a lone apostrophe in a launch note silently truncated
a real launch — put launch notes INSIDE the goal file); logging survives; the
lane runs under `timeout` in its own process group so a runaway dies with its
children; any inherited `DONE.json` is purged before launch; and it **fails
loud** if no session ID appears rather than writing a blank into the crash
anchor.

One-phase launch is mandatory. Never start a bare `amplifier` TUI and `send-keys`
the `/goal` into it — keystrokes sent to a busy pane get swallowed silently (one
run left an 8MB job with a zero-byte log), and `amplifier run` *exits* when the
goal loop ends while a TUI sits at a prompt forever, so one-phase is what makes
session death a real signal.

Both bounds are real but live at different layers: `--max-turns` is parsed by
**`/goal`**, not by `amplifier run`, so it rides inside the prompt string
(`/goal --max-turns 40 @file`); wall-clock is `timeout` around the process.
`/goal` is unlimited by default on purpose (ADR-0005) — a batch should set both.

**Session names `gb__<batch>__<lane>`** — double underscore because lane names
contain hyphens. Gives `gb__*` (every lane of every batch, for the orphan sweep
in Phase 7 and for pane-viewer match strings), `gb__<batch>__*` (one batch),
exact (one lane). Never use `.` or `:`: tmux silently rewrites them to `_` and
two differently-named lanes can collide.

**Write the manifest** — TSV, one row per lane: `lane · worktree · branch · tmux
· goal · log · session_id`, beside the goal files. This is the crash anchor.
When the tmux server dies, panes are gone but git plus this file let any session
reconstruct the batch; without it, recovery was 40 minutes of archaeology.

## Phase 6 — Watch

**Every claim you make about a lane must come from a probe you just ran.** This
is the rule; everything below serves it. Watchers that narrated from memory or
from a pane's last line have fabricated in multiple runs — one declared
`TIMEOUT: reached 95-minute max duration` at 30 minutes actual, and one reported
`No DONE.json files written` 68 seconds before the merge collided with those
exact files. The run with the lowest overhead measured (7% of spend) is the one
that probed for everything it said.

The instrument:

```bash
<skill_directory>/scripts/batch_status.sh <manifest.tsv> <BASE_SHA>
SHOW_LAST=1 <skill_directory>/scripts/batch_status.sh <manifest.tsv>
```

Its first line is `BATCH_ELAPSED`, computed from the manifest header — **that is
the only elapsed time that means anything.** The per-lane `LANE_IDLE` column is
seconds since that lane last emitted an event; reading it as batch elapsed time
is what killed a watch an hour early.

Five signals are counter-intuitive and the script encodes them so you do not
have to reason about them:

- Terminal is `DONE.json`, checked against the manifest's session_id — an
  inherited one from the base commit otherwise reads as a finished lane.
- Alive is `kill -0` on the lane PID, **not** tmux presence. The tmux server has
  died mid-batch in three separate runs, leaving healthy lanes running as
  orphans while tmux-keyed probes went blind.
- Liveness is `events.jsonl` mtime, **not** `transcript.jsonl` mtime — the
  transcript freezes entirely while a lane delegates to a sub-agent.
- Progress is assistant turns from the transcript, **not**
  `metadata.turn_count`, which is corrupt (reports 1 for a 21-turn session).
- Pushed is `git ls-remote`, **not** `@{upstream}`, never set by an
  explicit-refspec push.

**Wait on state change, not on a clock.** Sleep between probes; do not spend a
model call to decide whether to sleep again. In one run 51 of 70 monitor calls
were bare `sleep`, and monitoring ran 3.9× longer than the work it watched.
Load **monitor** if you delegate the loop — it owns the SEQUENTIAL-ONLY rule and
the batched-imposter check — but the loop's job here is mechanical: probe,
compare, sleep, repeat.

Report per the mode approved in Phase 2. Interrupt regardless for: a lane
`DIED`, a `STALE-DONE-IGNORED` verdict, a lane asking a human a question, or the
probe itself failing twice. Stall thresholds (120s/900s) are starting guesses —
calibrate before letting `STALLED` justify killing anything.

**Verify the watcher's verdict yourself before acting on it.** Both false-DONE
and false-crash have happened.

**Inherited artifacts are the single biggest source of false signals.** A lane's
worktree starts as a full copy of the base commit, so every status file, every
evidence directory, every `BLOCKED.md` that was already tracked appears to
belong to the lane. Three false alarms in one day came from this: a `BLOCKED.md`
byte-identical to main — *whose first line read "The lane is not blocked"* —
read as a real block; "23 evidence files, great progress" where 20 were
inherited; a monitor citing a device transcript that shipped with the branch.
Before reading any artifact as a lane's own output, prove the lane wrote it:
compare against the base commit (`git log <BASE_SHA>..HEAD -- <path>`, or a
checksum against base). `launch_lane.sh` purges `DONE.json` for exactly this
reason; every other artifact is on you.

**Provider rate-limit errors in a pane are healthy, not a stall.** If a lane
dies of them, that is a model-routing problem: switch the model and relaunch.
Do not serialize the batch.

**A lane that died — crashed, or killed by its time bound — takes the same
path:** rescue-commit anything real it banked, then relaunch the same goal with
a RESUME note naming what the prior attempt completed with SHAs, "treat that as
your own prior work, do not redo it", and what remains. Max 2 resumes. A lane
that hit its bound twice is a scoping problem — report it, do not extend
forever. Never let a bounded-out lane vanish silently from the Phase 7 report.

## Phase 7 — Land

**Re-verify everything. Lane self-reports are hints.** Two lanes reported green
honestly from a suite run that predated their own last file.

1. `git fetch`. Diff-stat each landed branch with **three dots** —
   `git diff main...HEAD` — not two. Two dots compares against main's *current*
   tip, so every commit main gained while the lane ran shows up as the lane's
   work. That produced a near-miss ownership accusation against a lane that had
   touched none of the files.
2. **Read the diffs across lanes, not just within them.** File ownership does
   not catch two lanes inventing the *same* thing at *different* paths — two
   lanes once created separate `ThreadVisibility` singletons, one written by
   one lane and read by the other, and **both lanes' tests passed** while the
   behavior was silently broken. It was caught only by reading the combined
   diff. Anything that looks like a new shared abstraction deserves a
   cross-lane look before merging.
2. Merge **sequentially, ascending by churn, `--no-ff`**. Run the full suite
   yourself after EVERY merge. Verify actual test counts from result files, not
   "BUILD SUCCESSFUL".
3. On conflict: resolve per the lanes' own merge notes. If notes are missing or
   intents genuinely conflict, stop and surface it rather than guessing.
4. On red: fix at the cause, or revert that merge and report. Never weaken a
   test to pass — the gate is doing its job.
5. Push main. Then per lane: `git worktree remove`, delete branch local and
   remote, `tmux kill-session`. Sweep for orphaned sessions: `tmux ls -F
   '#{session_name}' | grep '^gb__<batch>__'` must come back empty. **Then verify
   teardown of everything the lanes provisioned** — teardown is part of landing,
   not an afterthought. Read each lane's reported `resources[]`, confirm every
   entry is actually gone, and extend the orphan sweep beyond tmux to whatever
   the lanes could have started: check `docker ps`, `incus list`, or the process
   list for survivors tied to this batch. Reconcile the total against the batch
   resource ledger from Phase 1 — it must read zero still-running. A batch is not
   landed while it is still holding resources open.
6. Report verdict-first: per-lane table (shipped, SHAs, suite results), what
   verification caught that lanes did not self-report, residuals, anything
   `PENDING-HUMAN`, the unowned-files list from Phase 1, and new baselines.
