# x99c — delegate-catalog SOURCES sweep (kp79 standard), amplifier-app-cli

Work item: `model_performance-x99c` (project `model_performance`), one item / many
lanes across 7 repos. **This lane owns amplifier-app-cli's slice only.**

Branch: `lane/x99c-catalog-app-cli`, **rebased onto `origin/main` @ `f166328`**.
Ships as a **DRAFT PR**. This lane may not merge — the merge is the manager's next stage.

> **Baseline note.** The worktree was cut from `6f2ad04`, but `main` had moved 20+
> commits ahead and had itself already trimmed this description (`68c8cf4` removed its
> 7-line `MUST be used for` block: 3,037 → 2,659 chars). The branch is rebased, and
> **every number below is measured against `origin/main` @ `f166328`** — the code this PR
> actually merges into, not the stale fork point.

---

## 1. Scope: what this repo actually has

`agents/*.md` repo-wide, using validate-agents v1.8.0's own discovery
(`meta:`-key classifier, exclusions `.git .venv docs node_modules test-fixtures tests`):

| | |
|---|---|
| agents discovered | **1** (`agents/cli-expert.md`) |
| candidates scanned | 1 |
| non-agents found | 0 |
| locations | `agents/` = 1 |

Repo-wide grep for `<example>` outside `.git/` returns exactly two files:
`agents/cli-expert.md` (this lane's target) and the lane's own transient `GOAL.md`
(untracked, git-excluded, not part of the repo). `behaviors/cli-expertise.yaml` carries
only `agents.include: [app-cli:cli-expert]` — no second agent hides there.

**There is no sibling agent to sweep. One agent, one edit.**

## 2. Before / after

| agent | stock chars | lean chars | delta | examples | commentary |
|---|---:|---:|---:|---:|---:|
| `cli-expert` | **2,659** | **600** | **−2,059 (−77.4%)** | 3 → **0** | 3 → **0** |
| **repo total** | **2,659** | **600** | **−2,059 (−77.4%)** | 3 → 0 | 3 → 0 |

Measured on the YAML-parsed `meta.description` value — the string the delegate catalog
injects into every session, every turn. Stock is a `|` literal block (one trailing
newline; 2,658 stripped); lean is a `>-` folded block (no trailing newline).
validate-agents independently reports the same two numbers: `description_length` 2659 →
600, i.e. 664 → 150 estimated tokens.

The item records this agent at *"app-cli (cli-expert, 2,684 ch)"*; the verified
measurement on current `main` is **2,659**. Close enough to be the same census, 25 chars
apart — most likely a whitespace-normalisation difference. Verified, not re-derived; the
figure used here is the one this lane measured twice (own YAML parse + validate-agents'
`description_length`).

## 3. FIDELITY TABLE — every stock routing fact, checked against lean

Rule applied: any USE WHEN / DO NOT USE WHEN fact, trigger condition or constraint
present in stock and **absent** in lean must be RESTORED, with the byte delta noted.
Stock text: `evidence/description-STOCK.txt`. Lean text: `evidence/description-LEAN.txt`.

| # | fact in STOCK description | in LEAN? | how |
|---|---|---|---|
| 1 | Authority is the Amplifier CLI *application itself* (the `amplifier` command) | ✅ | "the question is about the Amplifier CLI itself" |
| 2 | …rather than help with the user's own code | ✅ | "not the user's code" (restated in DO NOT USE WHEN) |
| 3 | Carries docs shipped with THIS installed CLI version → answers match the running binary | ✅ | "Carries THIS version's docs, so answers match the running binary." |
| 4 | Trigger: slash commands as a class | ✅ | "any slash command (…)" — a **superset** of stock's enumeration |
| 5 | `/provider`, provider pinning, switching models mid-conversation, `amplifier provider` | ✅ | "switching model/provider mid-conversation"; `/provider`; "`amplifier` subcommand" |
| 6 | `/config` | ✅ | named |
| 7 | `/mode`, `/modes` | ✅ | `/mode` named; `/modes` under "any slash command" |
| 8 | `/goal`, autonomous continuation, stop conditions | ✅ | `/goal` named (continuation and stop conditions are that command's own surface) |
| 9 | `/fork`, `/status` | ✅ | both named |
| 10 | `/save`, `/rename`, `/clear`, `/agents`, `/tools`, `/skills`, `/skill`, `/allowed-dirs`, `/denied-dirs`, `/help` | ✅ | covered by the superset clause "any slash command" — a question naming any of them still routes here |
| 11 | interactive mode | ✅ | named |
| 12 | session resume | ✅ | named |
| 13 | session state location | ✅ | "session state location" |
| 14 | `@mention` context loading | ✅ | "@mention/bundle context loading + precedence" |
| 15 | bundle context precedence | ✅ | same clause |
| 16 | `amplifier bundle`, app bundles | ✅ | "`amplifier` subcommand" (superset) + the bundle-context clause |
| 17 | `--output json`, `--output json-trace`, output formats for automation | ✅ | "`--output json`/`json-trace` scripting" |
| 18 | spawn-time precedence / what tools and providers a sub-agent inherits | ✅ | "what a spawned sub-agent inherits (spawn precedence)" |
| 19 | session lifecycle, context loading (headline surface list) | ✅ | rows 11–15 name every member of it |
| 20 | Worked example 1 — switching models without losing the conversation | ✅ (fact) | its routing fact is row 5; the tutorial prose is dropped per the standard |
| 21 | Worked example 2 — parsing Amplifier output in CI | ✅ (fact) | its routing fact is row 17 |
| 22 | Worked example 3 — "why doesn't my sub-agent have the tool I configured?" | ✅ (fact) | its routing fact is row 18 |

**Facts present in stock and ABSENT from lean: none. Restorations required: 0.
Byte delta from restorations: 0.**

One thing deliberately **not** carried across, and it is not a routing fact: the *motive
prose* — "CLI behavior is version-specific and changes between releases — answering from
memory produces confident, wrong instructions." That argues *why* the agent exists; its
routing consequence ("answers match the running binary") is kept verbatim in row 3. An
argument does not route.

One thing lean **adds**, sourced from the agent body's own `## Boundaries` section
(`agents/cli-expert.md`, unchanged): an explicit **DO NOT USE WHEN** clause naming the
three off-ramps *with their owners* — user's project code (root session), bundle/agent
authoring (foundation), kernel internals (core). Stock's description had no DO NOT USE
WHEN at all; the standard requires one. No fact is invented — all three come from the
body.

Metric-only change, recorded for honesty: validate-agents' `triggers_found` moves
`["PROACTIVELY"]` → `["DO NOT"]`. The recipe states plainly that
"MUST/ALWAYS/REQUIRED/PROACTIVELY keyword presence is reported as a metric …, not a
gate", and `has_strong_trigger` stays `true`.

## 4. Body byte-identity

Only the frontmatter `description` changed. The body — everything after the closing
`---` — is byte-identical:

```
STOCK  (git show origin/main:agents/cli-expert.md)  md5 b229c3d9258265c012d074146c74ed8e  2550 bytes
BRANCH (working tree)                               md5 b229c3d9258265c012d074146c74ed8e  2550 bytes
IDENTICAL: True
```

`meta.name` (`cli-expert`) and `model_role` (`general`) are unchanged.
Diff on the agent file: `1 file changed, 10 insertions(+), 51 deletions(-)`.

## 5. validate-agents ON THE BRANCH

`@foundation:recipes/validate-agents.yaml` v1.8.0, foundation @v2.1.2
(`a27d5824517d078097b60d84779dd3eae80202cd`), run `run-846079b2a5c5`, all 11 steps
completed.

> **Overall Verdict: PASS WITH WARNINGS**
> Agents Found: **1** total across 1 location
> Quality Breakdown: 0 good, 0 polish, 1 needs_work, 0 critical
> Issues: **0 errors**, 1 warning, 1 suggestion (optional)

Structural summary: `{"errors": 0, "passed": 1, "total": 1, "warnings": 1}`.
Discovered agent count for this repo: **1**.

That run executed before the rebase. It is still the branch's verdict, because
**`agents/cli-expert.md` is byte-identical pre- and post-rebase — md5
`59bb85a00f7dab26d9466c41b80f5e38` both times** — and it is the only agent in the repo,
so nothing the rebase brought in can change what the validator saw. The deterministic
phases were additionally re-run on the rebased tree and reproduce the same numbers
(`evidence/structural-before-after.txt`).

**Transition, measured at $0 on both sides** (discovery + structural are pure python — no
LLM call — so the stock side is reproducible without spend):

| | stock @ `origin/main f166328` | branch |
|---|---|---|
| structural summary | `{"errors": 3, "passed": 0, "total": 1, "warnings": 1}` | `{"errors": 0, "passed": 1, "total": 1, "warnings": 1}` |
| errors | `DESCRIPTION_EXCESSIVE`, `COMMENTARY_TAG_PRESENT`, `EXAMPLE_BLOCK_PRESENT` | none |
| warnings | `NO_TOOLS_SECTION` | `NO_TOOLS_SECTION` (identical) |

So this is **FAIL → PASS WITH WARNINGS**, not "PASS held": stock carries 3 structural
ERRORs against v1.8.0's gates (`DESCRIPTION_WARN_CHARS = 600`,
`DESCRIPTION_ERROR_CHARS = 1200`, plus outright rejection of any
`<example>`/`<commentary>` block). Lean lands at **600 chars — at, not over, the warn
line**, so no length finding fires either.

**The one remaining warning is pre-existing and deliberately not remediated.**
`NO_TOOLS_SECTION` is byte-for-byte the same finding stock produces; it is a `tools:`
question, not a description question. The run's own scope note agrees:

> "Adding `tools:` is a frontmatter change but **not** a description change — it falls
> outside x99c's deliverables and would contaminate that PR's fidelity story. Land it as
> a separate commit or PR."

Recommendation for the manager: file the `tools:` block separately. It is genuinely
worth doing — the body names `read_file` at `agents/cli-expert.md:39` for three docs
reachable no other way, and explicit tools survive a restrictive spawn policy where
inherited ones do not — but it carries two real trade-offs (`tool-filesystem`
over-grants write/edit to a read-only consultant; a `@main` `source:` pin fights this
repo's version-lock rationale). That decision is the work; the YAML is three lines.

The description-quality phase's own verdict on this lane's rewrite:
`[LOW] cli-expert - Description: make NO edit / Improved: (none -- leave unedited)`.

## 6. Tests and CI

This repo **has** CI: `.github/workflows/ci.yml` — `pytest` on
{ubuntu, macos, windows} × py{3.11, 3.12}, plus a POSIX-only `-m integration` job.
Both legs run locally on the rebased branch, green:

```
uv run pytest -q                 → 1933 passed, 1 skipped, 13 deselected, 1 xfailed in 20.51s
uv run pytest -m integration -q  → 13 passed, 1935 deselected in 26.36s
```

Nothing in this change is executable — it is one frontmatter string — so no test was
added; the suites are the regression guard that the agent file still parses and ships.

**GitHub Actions CI: all 9 checks green, on every push of this branch that GitHub
scheduled a run for** — `aa66d2c` (run
[`34165026202`](https://github.com/microsoft/amplifier-app-cli/actions/runs/34165026202))
and `c3d16c7` (run
[`34165290063`](https://github.com/microsoft/amplifier-app-cli/actions/runs/34165290063)).
`agents/cli-expert.md` is byte-identical across all of them — only this note differs — so
the code under test never changed. Timings from the first:

```
pytest (ubuntu-latest,  py3.11)   pass  29s
pytest (ubuntu-latest,  py3.12)   pass  31s
pytest (macos-latest,   py3.11)   pass  46s
pytest (macos-latest,   py3.12)   pass  39s
pytest (windows-latest, py3.11)   pass  54s
pytest (windows-latest, py3.12)   pass  52s
pytest -m integration (ubuntu-latest)  pass  41s
pytest -m integration (macos-latest)   pass  42s
license/cla                            pass
```

Recorded because it was briefly confusing: on the FIRST push — before the rebase, while
the PR was still `mergeable: CONFLICTING` against a main that had moved 20+ commits ahead
— **zero** workflow runs appeared, only `license/cla`. GitHub could not build the merge
ref a `pull_request`-triggered workflow runs against, so nothing was scheduled. Rebasing
onto `f166328` resolved the conflict and CI started immediately and passed everywhere. If
a future lane sees an empty check list, check `mergeable` before concluding CI is broken.

## 7. Deliverable status

| deliverable | status |
|---|---|
| every targeted agent's description trigger-first, ≤~600 chars, USE WHEN / DO NOT USE WHEN, zero example/commentary blocks | **DONE** — 1/1 agent, 600 chars exactly |
| fidelity table, any lost fact restored | **DONE** — 0 facts lost, 0 restorations |
| bodies byte-identical, md5 both sides | **DONE** — `b229c3d9…` both sides |
| before/after char counts per agent + repo total | **DONE** — 2,659 → 600 (−77.4%) |
| `validate-agents` on the branch, verdict quoted, agent count quoted | **DONE** — PASS WITH WARNINGS, 0 errors, 1 agent |
| CI green, or its absence stated | **DONE** — GitHub Actions: **9/9 checks pass** on every scheduled run of this branch (`34165026202`, `34165290063`) (pytest on 3 OSes × py3.11/3.12, both integration legs, cla). Local suites green too (§6). |
| anything already compliant left unedited and named | **N/A** — the repo's only agent was non-compliant (3 structural ERRORs). Nothing compliant was touched: the body, `meta.name`, `model_role`, `behaviors/cli-expertise.yaml` and `context/cli-awareness.md` are all byte-identical to `main`. |

**LANDING STAGE.** Draft PR only. This lane does not merge.

## 8. Evidence

- `evidence/description-STOCK.txt` — the stock description (origin/main), verbatim
- `evidence/description-LEAN.txt` — the lean description, verbatim
- `evidence/body-md5.txt` — body md5 both sides + char-count arithmetic
- `evidence/structural-before-after.txt` — validate-agents structural phases, both sides, $0
- `evidence/validate-agents-BRANCH.txt` — the full branch run: verdict, agent count, warning disposition

Spend: **$0.00** against a $0.00 authority. Text edits, one recipe run, byte counts,
two local test suites. No API measurement was performed.

### One flag for the manager: `docs/` ships inside the wheel

`pyproject.toml`'s `[tool.hatch.build.targets.wheel.force-include]` maps
`"docs" = "amplifier_app_cli/_bundle/docs"` — so **everything under `docs/`, including
this lane directory, is packaged into the installed wheel** (~20 KB of markdown here).
That is not true of the sibling repos in this sweep, where `docs/` is repo-only.

The lane charter names `docs/lanes/x99c-catalog-app-cli/` explicitly, so that is where
these artifacts went. Two clean options if the wheel bloat is unwanted, both trivial and
both the manager's call, not this lane's:

1. Add an explicit exclude for `docs/lanes/` to the wheel build (keeps the charter path,
   drops the bytes from the package) — my recommendation.
2. Drop the lane artifacts before merge and keep only the PR body as the record.

Note that validate-agents' own discovery already excludes `docs/`, so these files can
never be mistaken for agents by the validator.
