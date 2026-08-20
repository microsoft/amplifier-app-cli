# goal-batch — shape references

Not normative. The SKILL.md rules govern; these show what the artifacts look like
when done well. Read only if you need the shape.

## The Phase 2 plan screen

```
BATCH attend-weave    base main@86ed732    baseline: pytest 871/1, cargo 83

  lane        owns                             issues    size
  contracts   fleet-tools collection reads     008,012   M
  tools       M365 + bash surfaces (additive)  031       S
  continuity  the gateway rail                 049       L

  contested   none — A9 folded into contracts (both rewrote fleet-tools)
  parked      B1-B5 (methods, not code) · C3-C6 (should follow reask)
  unowned     scripts/package.sh  ← nobody will test this

  bounds      2h per lane, then TERM. no lane cap.
  watching    every 15 min, one report when all lanes land   [default]
              also: report as each lands · only ping me if something breaks

go?
```

## Approval vocabulary actually seen in the field

| They say | Means |
|---|---|
| `go` | launch on the shown defaults |
| `go, tell me as each lands` | per-lane reports |
| `go, only ping me if it breaks` | exceptions only |
| `go, check every 30` | interval override |
| `go, 4h on continuity` | per-lane bound override |
| `go but drop continuity` | revise, show the one-line diff, relaunch the gate |

Anything that is not an affirmative is feedback. Revise and re-present.

## batch_status.sh output

```
LANE       VERDICT     IDLE   TURNS    COMMITS DIRTY PUSHED BRANCH
contracts  WORKING     3s     127+14   2       1     0      weave/contracts
tools      LANDED      412s   88+0     3       0     1      weave/tools
reask      STALLED     8667s  41+0     0       11    0      weave/reask
```

Verdicts: `STARTING` · `WORKING` · `SLOW` · `STALLED` · `LANDED` ·
`LANDED-NOT-PUSHED` · `LANDED-DIRTY` · `DIED` · `NOT-STARTED` ·
`WORKTREE-MISSING`.

`SHOW_LAST=1` appends each lane's last 3 conversation turns — that is the
"what is it actually doing" view.

## DONE.json

```json
{"lane":"contracts","verdict":"COMPLETE","branch":"weave/contracts",
 "head":"59c853e","pushed":true,
 "items":[{"id":"D1","state":"PASS","note":"8 red-proven gates"},
          {"id":"D4","state":"BLOCKED","note":"074 is upstream muxplex work"}],
 "residuals":["__bool__ undefined on Bounded — recorded, not fixed"],
 "pending_human":[],
 "suite":"891 passed, 1 skipped (baseline 871)"}
```

## The final report

Verdict first. Per-lane: what shipped, SHAs, suite result. Then what
verification caught that the lanes did not self-report, residuals, anything
`PENDING-HUMAN`, the unowned-files list from Phase 1, and the new baselines.
