# First run — a 15-minute throwaway highway

The point of a first run is to see the whole loop — **gate → lanes → watchdog →
merges → refill → close** — on work you do not care about, at width 2, before
you trust it with something real. Nothing here is special to width 2; it is just
small enough to watch every moving part.

## Before you start

- A **throwaway git repo** you can break (a scratch clone, or `git init` a
  sandbox with a couple of trivial files). A remote is optional — lanes commit
  locally and merge fine with no network.
- `git`, `tmux`, and the `amplifier` CLI on PATH.
- Two or three tiny, independent chores in that repo (e.g. "add a docstring to
  each of files A, B, C") — enough to fill two lanes and leave one to refill.

## The sequence

1. **Invoke** in the interactive TUI:

   ```
   /highway reach a green throwaway: do the three chores in <scratch-repo>,
   width 2, achieve-and-close
   ```

   In a headless one-shot, name the skill in prose instead of the bare slash
   token: `amplifier run "Use the ten-lane-highway skill and drive: <outcome>"`.

2. **Intake + strategize (Phases 1–2).** The manager writes `BATCH_DIR`
   (e.g. `~/dev/hw-firstrun`), a `HIGHWAY.md`, pins `BATCH_DIR/.width` to `2`,
   and **pre-composes a goal file per queued item** into `BATCH_DIR/goals/`.

3. **Approve at the gate (Phase 3).** You will see one screen: outcome, width 2,
   the first wave (lane → repo → item), the priority rationale, watch cadence.
   Reply `go`. Nothing launched before this.

4. **Saturate (Phase 4).** Two lanes come up — each a worktree + branch + tmux
   session running `/goal` — then the **watchdog** starts on the `hw` tmux
   socket. Confirm with the status instrument: both lanes LIVE, watchdog LIVE,
   `DEFICIT=0`.

5. **Watch it run (Phase 5).** As a lane finishes, the manager verifies it from
   git facts, merges `--no-ff`, tears the lane down, and **refills the instant a
   slot opens** from the pre-composed queue — a bare `launch_lane.sh` call, no
   re-goalify. The todo lane board is your live dashboard.

6. **Close (Phase 7).** With `achieve-and-close` and nothing pending, the
   manager does a final full-suite sweep, runs the infra-ledger sweep
   (`infra_ledger.sh BATCH_DIR sweep` must exit clean), archives per-lane
   evidence, prunes worktrees/branches, kills the watchdog, and reports
   `DONE:`.

## What you should observe

- The **gate blocks** until you say go — enthusiasm and silence are not consent.
- Width holds at 2 while work remains: a drained lane refills immediately, not
  after the whole wave lands.
- Every merge is proven by the merged lane's **own tests**, with the full suite
  on a cadence and at close — never a lane's self-report.
- `HIGHWAY.md` and the todo board agree at the end of every cycle.

## Stop everything (panic button)

```bash
# BATCH is the sanitized batch name (basename of BATCH_DIR, non-alnum → _)
tmux -L hw kill-session -t "hw-watchdog__${BATCH}"        # the watchdog
tmux -L hw list-sessions -F '#{session_name}' \
  | grep "^hw__${BATCH}__" | xargs -r -n1 tmux -L hw kill-session -t   # lanes
infra_ledger.sh "$BATCH_DIR" sweep    # tear down anything lanes stood up
rm -rf "$BATCH_DIR"                   # worktrees, goals, HIGHWAY.md, logs
```

All highway tmux commands use `-L hw` (the `HIGHWAY_TMUX_SOCKET`, default `hw`),
so a stray `tmux kill-server` on the default socket never touches the highway,
and this never touches your other tmux work.
