# DONE-NOTE — `model_performance-f26u`

**The root bundle's system instruction is silently REPLACED**

- **Item:** `model_performance-f26u` (project `model_performance`)
- **Lane:** `f26u-root-instruction-replaced`, branch `lane/f26u-root-instruction-replaced`
- **Repo:** `microsoft/amplifier-app-cli`, branched from `6afcae3`
- **Outcome:** **branch A — RESOLVED.** Every deliverable is DONE; none was cut by the cap.
- **Draft PRs:** [amplifier-app-cli#315](https://github.com/microsoft/amplifier-app-cli/pull/315)
  (primary fix) · [amplifier-bundle-notify#10](https://github.com/microsoft/amplifier-bundle-notify/pull/10)
  (upstream mitigation)
- **Date:** 2026-09-06

---

## 1. Result in one paragraph

A behavior or app bundle whose `bundle.md` carries a markdown body was silently taking over
the user's system prompt, because a body *is* an `instruction` and `Bundle.compose()`
replaces rather than merges that one field. `lib/bundle_loader/prepare.py` now re-asserts
the root bundle's non-empty instruction after each behavior compose and **warns**, naming
the bundle whose body was dropped. Verified in a real session on the reporting host: before
the fix `raw.system` was 98,406 chars beginning `"# Notify Bundle"` with the marker
`configured for development OF` absent and 22 mentions resolved; after the fix it begins
`@anchors-amp-dev:context/system.md`, the marker is present, `# Notify Bundle` is gone, and
23 mentions resolve — including the root's own. Full suite green (1,841 passed).

## 2. Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | Fail-before unit test (fails on main, passes on branch), both transcripts | **DONE** |
| 2 | The fix, chosen with evidence between (a) and (b) | **DONE** — (a), argued in §4 |
| 3 | @mentions inside the restored instruction still expand | **DONE** |
| 4 | Real-session verification (`amplifier run "hi"` + `jq`) | **DONE** |
| 5 | Say plainly what happens to a behavior bundle that *does* carry a body | **DONE** — dropped **and warned**, §5 |
| 6 | File the notify mitigation upstream, linking this item | **DONE** — draft PR notify#10 (Issues are disabled on that repo) |
| 7 | Full suite green, pasted in the PR body | **DONE** — 1,841 passed |
| 8 | Draft PR on origin, branch `lane/f26u-root-instruction-replaced` | **DONE** — app-cli#315 |
| 9 | This DONE-NOTE at the pinned artifact root | **DONE** |

Nothing was recorded NOT-POSSIBLE. The item carried no run-buying deliverable, so the cap
never bound (§7).

## 3. The defect, confirmed independently

The item's evidence reproduced exactly on a fresh session from this worktree
(`evidence/real-session-before.txt`, session `212e9f74`):

```
chars=98406  head=# Notify Bundle\n\nThis bundle provides desktop and terminal notifications...
marker "configured for development OF" present? false
mentions:resolved -> 22 mentions; anchors-amp-dev:context/system.md NOT among them
```

Three facts compose, all confirmed by reading the shipped code:

1. foundation `bundle/_dataclass.py` — docstring *"instruction: later replaces earlier"*;
   implementation `if other.instruction: result.instruction = other.instruction`.
2. app-cli `runtime/config.py` — **always** composes modes, cli-expertise, skills, wayfinder
   and notification behaviors, then settings' `bundle.app` list, **onto** the user's root
   bundle (root is `self`, behaviors are `others`).
3. `_build_notification_behaviors()` composes notify's **root** `bundle.md` deliberately
   ("Root bundle first — a minimal marker that just identifies the repo"), and that file
   carries a 2,988-char README body.

Two details worth recording because they were not in the item:

- **app-cli already knew about this hazard and was dodging it per-behavior.** The docstring
  of `_build_app_cli_behaviors()` says: *"Only the behavior, never the root `bundle.md`.
  `Bundle.compose()` replaces the instruction whenever the composed bundle has a non-empty
  markdown body… Composing the root bundle here would silently clobber the user's system
  prompt."* `_build_modes_behaviors`, `_build_skills_behaviors` and `_build_wayfinder_behaviors`
  carry the same note. `_build_notification_behaviors()` is the one that composes a root
  bundle anyway. So the defect is not an unknown — it is a known hazard with no enforcement,
  which is exactly what this change adds.
- **`instruction` is not the only thing the last behavior takes over.** `compose()` also does
  `result.name = other.name or result.name` and `result.base_path = other.base_path`, so the
  composed bundle ends up carrying the *last behavior's* name and cache directory. Observed
  directly (`name: behaviorbundle`, `base_path: /tmp/.../behaviorbundle`). Not fixed here —
  named in §6 as follow-up.

## 4. Fix (a) vs (b) — the argument from the code

**Taken: (a)** — after composing a behavior, restore the root's instruction if it was
non-empty.

**(b) — compose behaviors first and the user bundle last — is the better long-term shape and
was still not taken.** In its favour, and confirmed by reading foundation rather than
assuming:

- `_dataclass.py` says so itself: *"In typical usage: result.compose(user_bundle), so
  other=user_bundle."*
- foundation's `registry.py` resolves `includes:` exactly that way — `result =
  result.compose(included)` in a loop, then `return result.compose(bundle)`. **The owning
  bundle composes last, deliberately.** app-cli's behavior loop is the deviation.

Against it, and decisive at this scope: **`instruction` is the only field in `compose()`
whose earlier value is destroyed.** `session`/`spawn` deep-merge, `providers`/`tools`/`hooks`
merge by module id, `context` accumulates under a bundle prefix, `agents` updates by name.
Moving the user bundle to last would invert precedence on **nine** surfaces — `session`,
`spawn`, `providers`, `tools`, `hooks`, `agents`, `name`, `version`, `description`,
`base_path` — for every app bundle a user has configured (20 in the reproducing settings
file), none of which the evidence shows to be wrong today. One broken field is repaired in
that field. The reorder is a separate change that needs its own evidence, and is recorded as
such in §6 and in the PR.

**Boundary respected:** option (c) — a warning inside foundation's `compose()` when a
non-empty instruction is replaced — is `amplifier-foundation`'s to make. This lane must not
edit that repo, so it is reported rather than implemented. It remains the right *additional*
guard: it would catch this class of defect for every host, not just this CLI.

## 5. What now happens to a behavior bundle that carries a body

**It is DROPPED, and a WARNING names it.** Never silent — a silent drop in the other
direction is the same class of defect this item exists to remove:

```
Bundle 'notify' (git+https://github.com/microsoft/amplifier-bundle-notify@main) carries a
markdown body; dropping it. A composed behavior/app bundle must not replace the root
bundle's system instruction. Move that prose into README.md and leave bundle.md's body empty.
```

Appending was rejected: the body that would be appended is a README (2,988 chars for notify
alone, and the mechanism is per-bundle), so appending re-imports the exact noise the defect
was injecting, just lower down the prompt. Dropping with attribution tells the bundle author
what to do — and notify#10 does precisely that.

A root bundle with **no** body of its own still inherits a composed body (only a non-empty
root instruction is restored), so bodyless roots are unchanged. Covered by
`test_behavior_body_still_used_when_root_has_none`.

## 6. Follow-ups this lane did not take (all reported, none absorbed)

1. **foundation**: emit a warning in `compose()` when a non-empty `instruction` is replaced
   (option (c)). Out of lane scope — different repo.
2. **app-cli**: align the behavior compose loop with `registry.py`'s owner-last ordering
   (option (b)). Must be measured against the nine surfaces listed in §4.
3. **app-cli**: the composed bundle inherits the *last behavior's* `name` and `base_path`.
   `base_path` is only a fallback while the CLI passes `session_cwd`, so it is latent rather
   than live — but it is the same replace-semantics family and (b) would fix it too.
4. **Sibling lane `62pg`** is auditing which evals ran without the prompt; this root-cause and
   both before/after captures are in `evidence/` for it to use. No overlap taken.

## 7. Spend

| Item | Amount |
|---|---|
| Authority for this item | **$0.00** purchases — `0 runs × 0 arms × $0 / 1.00 = $0.00`, slack $0.00 |
| Purchases made | **$0.00** — no runs bought, no arms, no DTU, no infrastructure |
| Local verification invocations | **$1.02** — `amplifier run "hi"` ×2 (before $0.75 / after $0.27) |
| Infrastructure registered | **none** — nothing to tear down; `lane_teardown.sh` not run, `infra_ledger.sh sweep` never run |

The item states that `amplifier run "hi"` for the real-session check is a normal local
invocation, not a purchase. Two were needed rather than one, because a *before* run from this
same worktree/venv is what makes the *after* run a controlled comparison — the only variable
between sessions `212e9f74` and `6c22e2f8` is `prepare.py` (`git stash` / `git stash pop`).
The $1.02 is recorded here in full regardless of that classification.

**The cap did not bind.** Its arithmetic closes trivially, because this item buys no runs:
the deliverables are code, tests, and two local invocations. No deliverable was shrunk, and
nothing is recorded NOT-POSSIBLE.

## 8. Deviations from the goal text, and why

1. **Captures kept in-lane, not written to the evals repo.** Procedure 3 points captures at
   `/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/treatment-validation/`,
   which is a **different repository**; SCOPE-OUTS say never touch other repos, and the
   landing-stage rule says a deliverable that can only be satisfied by writing outside this
   worktree is a goal defect to report rather than a task to perform. All captures are
   therefore under this lane's artifact root, `docs/lanes/f26u-root-instruction-replaced/evidence/`,
   committed on the lane branch and readable by any later lane. **Reported as a goal defect,
   not absorbed.**
2. **The notify mitigation is a draft PR, not an issue.** The deliverable allows either;
   `gh repo view microsoft/amplifier-bundle-notify` reports `hasIssuesEnabled: false`, so a
   PR is the only upstream filing that repo accepts. It is a draft, it is not merged, and the
   change was made in a throwaway clone under `/tmp` — this lane's worktree contains no notify
   files.
3. **Two verification sessions rather than one** — see §7.

## 9. Evidence index

All paths relative to `docs/lanes/f26u-root-instruction-replaced/`:

| File | What it shows |
|---|---|
| `evidence/fail-before.txt` | 4 failed, 1 passed on `main` (`6afcae3`) — the fail-before transcript |
| `evidence/pass-after.txt` | 5 passed on this branch — the pass-after transcript |
| `evidence/real-session-before.txt` | session `212e9f74`: 98,406 chars, `# Notify Bundle`, marker **false**, 22 mentions |
| `evidence/real-session-after.txt` | session `6c22e2f8`: `@anchors-amp-dev:context/system.md`, marker **true**, `# Notify Bundle` **false**, 23 mentions |
| `evidence/full-suite.txt` | 1,841 passed, 1 skipped, 13 deselected, 1 xfailed |

The known order-dependent failure named in the goal
(`test_truststore_wrap_bio_shim.py::test_real_truststore_is_covered_at_cli_import`) did not
reproduce in the full-suite run recorded here. It was neither absorbed nor worked around.
