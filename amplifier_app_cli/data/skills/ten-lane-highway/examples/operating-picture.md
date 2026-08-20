# HIGHWAY.md — Operating Picture (template)

The strategist's externalized brain. Continually REWRITTEN (no changelog — the
current picture, always). Lives in BATCH_DIR. This file is the answer to the
documented weakness: "the vision exists implicitly in the conversation... not
one continually maintained artifact describing the current end-state."

```markdown
# Highway: <batch-name>
Updated: <UTC timestamp> (rewrite this file every cycle)

## Outcome (the destination)
<The user's defined outcome/goal, in checkable terms. What "arrived" looks like.>

## Constraints
- Deadline / time budget: <e.g. "fresh-install candidate by Friday">
- Authority boundary: <what the manager decides alone vs. escalates>
- Resources: width target = <N> lanes; model/rate notes; protected paths/repos.

## Current state (one paragraph)
<Where the effort stands right now, in plain language.>

## Lane board (mirrors the todo list)
| Lane | Repo | Work item | Status | Since |
|---|---|---|---|---|
| 1 <name> | <repo> | <item> | running / verifying / merging | <ts> |
| ... | | | | |
Open slots: <n>  ·  Ready queue: <n>  ·  Deficit last cycle: <n>

## Priority queue + rationale (the strategy)
1. <item> — <why it is next, tied to Outcome/Constraints>
2. ...
Speculative/investment lanes (only when critical path is saturated): <recon/spike items>

## Weave-in log (incoming work decisions — nothing silent)
| When | What arrived | Decision (now / queued@prio / declined) | Why |
|---|---|---|---|

## Risks / blockers / needs-user
- <blocker> — <what would unblock it; escalated? y/n>

## Landed (this highway)
- <lane> -> <repo> @ <sha> — <verified-by-me note>
```

Rules for this file:
- The ORCHESTRATOR writes it (scripts own manifest.tsv; this file owns strategy).
- Rewrite, don't append — stale sections are worse than missing ones.
- Every accept/defer/decline of new incoming work MUST land in the weave-in log.
- The lane board here and the todo tool must agree at the end of every cycle.
