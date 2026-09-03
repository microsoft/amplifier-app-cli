# Lane artifacts

Automated work on this repo arrives in **lanes**. A lane is one branch, one
work item, one directory of its own record: its `DONE-NOTE.md`, its findings,
the evidence behind them, and any patch it proposes for another repo.

**This repo's artifact root is `docs/lanes/<lane>/`.** One directory per lane,
created by that lane, owned by that lane.

```
docs/lanes/<lane-id>/
├── DONE-NOTE.md          # what was done, what was measured, what is still open
├── evidence/             # command output the note's claims are quoted from
└── PROPOSED-*.patch      # a change this lane proposes to a different repo
```

## Rules

1. **Never write `DONE-NOTE.md` at the repo root.** The root path is shared by
   every lane, so the last writer silently overwrites the previous one. That
   happened; the notes were recovered, and the shape is now refused by a guard.
2. **Never write into another lane's directory.** A lane's record is only
   meaningful because exactly one lane produced it.
3. **Lane artifacts are not repo content.** Source, tests, and documentation
   the repo wanted go where the repo's own structure says, exactly as if a
   human had written them. Only the lane's *record* belongs here.

## `ai_working/`

Four lanes landed their notes under `ai_working/<lane>/` before this repo's
convention was settled (`3yc`, `adq`, `9kk`, and a since-corrected `2nz`).
Those notes are left exactly where they are: they are the evidence trail of
merged, verified work, and each followed the instruction it was given. New
lanes use `docs/lanes/`.

The rule is machine-checked by `check_lane_artifact_paths.py` in the evaluation
repo (`artifact-path/v1`), which resolves this repo to `docs/lanes/` by
declaration — see `docs/lanes/aof-artifact-path-conflict/DONE-NOTE.md` for why
that is a declaration rather than something inferred from the directory tree.
