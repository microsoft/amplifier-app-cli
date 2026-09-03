# DONE-NOTE — `model_performance-eem` · lane `eem-partial-accumulator-widen`

**Item:** PREREQUISITE ($0, app-cli): the partial-result accumulator collects only `text` blocks,
so a timed-out delegate can never carry a partial.
**Repo:** `microsoft/amplifier-app-cli` · branch `lane/eem-partial-accumulator-widen` · parent `26e5f10` (#297)
**Date:** 2026-09-03
**Spend:** **$0.00** of a **$0.00** authority. No runs, no container, no DTU, no API calls.
The cap's arithmetic (`0 runs x 0 arms x $0 / 1.00 = $0.00`) closes: this is a pure code change,
and every number below is a **reanalysis of k64's already-purchased captures** ($14.70, already spent),
not a new purchase.

**Terminal state: RESOLVED (OUTCOME branch A).** Every deliverable is DONE. Nothing was recorded
NOT-POSSIBLE, and the cap never bound — because nothing this item required cost money.

---

## 1. THE DEFECT, AND WHY IT WAS UNREACHABLE BY TEST

`session_spawner._open_partial._accumulate_partial` collected `content_block:end` payloads where
`block["type"] == "text"` and nothing else. Correctly wired (`session.partial` IS registered), a
correct consumer (foundation `f42f48c`), and **structurally incapable of firing on a real workload**.

k64 measured it across 18 delegate legs in 7 runs: a leg emits **at most one `text` block**, in the
final 0.19–0.72 s of a 5.4–222.0 s leg. Everything before it is `thinking` (1–25/leg) and `tool_call`
(0–5) — invisible to the filter.

The cross-repo round-trip test passed anyway, for **two** reasons, and both are now closed:

1. its fixture sub-session emitted **text** blocks, which real legs do not until they finish;
2. it called `_seal_partial` with a hand-built `{"chunks": [...]}` record — **bypassing `_open_partial`
   entirely**, i.e. never running the accumulator whose filter was the whole defect. A test that never
   executes the broken code cannot fail on it. (Point 2 is not in the item text; it was found here and
   is the more general lesson.)

---

## 2. THE FIX — option (a), picked with evidence; (b) considered and declined

**Chosen: (a), widen the accumulator**, with the text channel kept strictly separate.

`_open_partial` now collects three channels — assistant `text`, `thinking`, and a rendered
`tool_call` trace. `get_partial_output` returns the **first channel that has anything**:

| leg produced | returns | `source` |
|---|---|---|
| assistant text | exactly today's record, field for field | `spawn-accumulator` |
| no text, but thinking and/or tool calls | a labelled reasoning payload | `spawn-accumulator:reasoning` |
| nothing at all | `None` (unchanged) | — |

Block shapes are **measured, not assumed**, from k64's captures (236 `thinking`, 53 `tool_call`,
38 `text` blocks inspected): `thinking` carries its reasoning under `text` (same field name as a text
block); a `tool_call` carries `{"id", "name", "input", "visibility"}` — arguments live under **`input`**,
not `arguments`. Guessing `arguments` would have produced a silently empty trace.

**(b) reading the child's `transcript.jsonl` was declined, and here is the reason rather than a
preference.** The transcript is checkpointed on `provider:request`, throttled to one write per 30 s
(`_DEFAULT_CHECKPOINT_INTERVAL_S`), so it lags the live event stream by up to a full window and adds
filesystem I/O plus a `SessionStore` layout dependency to the read path. It buys one thing the
accumulator lacks — tool *results* — and, per §4 below, the accumulator already reaches
`partial_available: true` on **18/18** measured legs without it. (b) is therefore a strictly more
expensive route to a result already obtained. It stays available as a later enrichment if tool
*results* are ever wanted; it is not needed to make the feature reachable. Option (c) collapses to (a)
for the same reason.

**Memory.** `chunks` was self-limiting (≤1 text block/leg); reasoning is not, and the wall-clock
backstop allows legs of hours. Retained reasoning is bounded at 100,000 chars, oldest-first
(`_PARTIAL_REASONING_MAX_CHARS`). **CHOSEN, NOT MEASURED** — 5× the consumer's 20,000-char forward cap,
so trimming here is never what the consumer sees.

---

## 3. THE GUIDANCE STRING — what was done, and what is foundation's to do

foundation `f42f48c` picks its guidance from `bool(text)` alone:

> `_PARTIAL_GUIDANCE`: "…is unfinished work salvaged from the agent mid-flight — it has NOT been
> checked, concluded, or self-reviewed…"

That is true of assistant prose and **overclaims for raw thinking**: unfinished prose was at least
addressed to a reader; private reasoning never was. Handing a model its own unreviewed reasoning under
that sentence is its own defect, exactly as the item warned.

**This lane stops at the repo boundary and reports.** What the producer owns, it did:

1. **`partial_source` distinguishes the kinds** — `spawn-accumulator` vs `spawn-accumulator:reasoning`
   — so a consumer can branch **without parsing prose**;
2. **the payload labels itself, at head AND tail.** The tail matters: foundation truncates to the
   **last** `partial_max_chars` (default 20,000), and 25 thinking blocks routinely exceed that, so a
   head-only label is lost on exactly the long partials that most need it. A test pins the footer's
   survival through a >20,000-char tail cut.

### REPORTED, NOT CROSSED — the change that belongs in `amplifier-foundation`

`modules/tool-delegate/amplifier_module_tool_delegate/__init__.py`, `_partial_output_fields`:
select the guidance on the **kind** of partial, not only on `bool(text)`. Concretely, add a third
string used when `partial.get("source")` ends in `:reasoning`, saying the content is the agent's
own private reasoning and tool trace — evidence of what it was doing — rather than "unfinished work".
Two lines and a constant; it needs a foundation PR and is **not** made here.

Until it lands, `test_guidance_string_for_the_reasoning_case_is_foundations_to_change` (in the
round-trip file) **asserts today's real behaviour**, so the day foundation changes the string this
check fails loudly instead of drifting silently.

---

## 4. IS `partial_available: true` REACHABLE ON A REAL LEG SHAPE? — YES, measured

Full table: `evidence/07-real-leg-reachability.md`. Recomputed from k64's own captures, $0, no new runs,
same 18 delegate legs.

| | before (text only) | after (widened) |
|---|---|---|
| legs that could ever recover anything | **16/18** | **18/18** |
| recoverable share of a leg, mean | **0.05%** | **82.2%** |
| recoverable share, range | 0.00–0.24% | 0.3–98.6% |

The two zero-text legs (`caeba80f`, `cbdb7bf1` — 18 and 10 thinking blocks, no text, previously
unrecoverable **by construction**) become recoverable for ~97% of their duration.

**The honest limit, stated rather than buried.** The first evidence block lands 3.15–41.03 s into a leg,
so a timeout shorter than that still recovers nothing — correctly. The worst case measured
(`bcb7ec94`: first evidence at 35.54 s of a 41.6 s leg) leaves 85% of that leg dark. This is a
~1,700× wider window, **not** a guarantee.

*(knob moved: none — reanalysis · terra S1 root, sub-work matrix-routed · confidence: **measured**,
n=18 legs / 7 runs · evidence: `treatment-validation/20260903-k64-delegate-timeout/runs/*/all-sessions/projects/*/sessions/0000000000000000-*/events.jsonl`)*

---

## 5. NORMAL COMPLETIONS BYTE-IDENTICAL — shown, not asserted

`evidence/03-byte-identity.txt` runs the same probe against a `cp -rL` copy of the parent producer and
of this branch, and diffs the canonical JSON:

```
IDENTICAL  normal_completion_result
IDENTICAL  normal_completion_registry_after
IDENTICAL  normal_completion_partial
IDENTICAL  timeout_with_text_partial      <- the text case: same bytes => same guidance string
CHANGED    timeout_no_text_partial        <- null -> a record. This is the fix.
```

Exactly one key moves, and it is the one the item exists to move.

---

## 6. EVIDENCE INDEX

| file | what |
|---|---|
| `evidence/00-baseline-suite-parent.txt` | parent suite green before any edit (1659 passed) |
| `evidence/01-fail-before.txt` | same unit tests, both producers: **parent 5 failed / 18 passed**, patched **23 passed** |
| `evidence/03-byte-identity.txt` (+ `03a`/`03b` JSON) | serialized results diffed, parent vs patched |
| `evidence/04-roundtrip.txt` | cross-repo, foundation `f42f48c`: **parent 2 failed / 3 passed**, patched **5 passed** |
| `evidence/05-full-suite.txt` | **1670 passed**, 1 skipped, 13 deselected, 1 xfailed |
| `evidence/06-integration.txt` | `pytest -m integration`: 13 passed |
| `evidence/07-real-leg-reachability.md` | the reachability table above |
| `byte_identity_probe.py` | the probe behind §5 |
| `test_partial_roundtrip.py` | 37n's check, extended with the no-text case |

Suites run with the repo's own `uv sync --all-extras` venv, matching CI (`.github/workflows/ci.yml`:
`uv run pytest -q` plus `pytest -m integration`).

---

## 7. INCIDENT — a false green, caught, disclosed

The first cross-repo run reported **5 passed on the parent producer**, i.e. the fail-before arm
"passing". Cause: the overlay was on `PYTHONPATH`, but the run was launched **from the checkout**, and
`sys.path[0]` is the CWD — so both arms imported the working tree and the parent arm was never
exercised. Caught by asking the interpreter which file it had actually loaded, rather than trusting the
exit code.

Nothing was published from that run. Every arm is now preceded by a printed
`session_spawner.__file__` + `widened: True/False` check (top of `evidence/04-roundtrip.txt`), and both
round-trip and unit fail-before runs execute from `/tmp`. **This is the same failure shape as the item
itself** — a check that could not fail — one layer further out, and it is recorded here rather than
quietly fixed.

---

## 8. DEVIATIONS

* **`docs/lanes/.../evidence/02-pass-after.txt` was folded into `01-fail-before.txt`** so the two arms
  sit side by side in one file. No content lost.
* **`ruff format` was applied to the two files touched** (repo style; `ruff check` clean). All evidence
  was regenerated afterwards against the formatted tree — no capture predates the final source.
* **No foundation change**, per the item's scope-out. §3 names the change and where it goes.
* **Baseline capture note:** `00-baseline-suite-parent.txt` was taken before the new tests existed, so
  it reports 1659 rather than 1670. The parent-vs-patched comparison that matters is
  `01-fail-before.txt`, which runs the *same* file against both producers.

---

## 9. WHAT REMAINS OPEN

* **The foundation guidance string** (§3) — a separate, named, two-line change in another repo.
* **`model_performance-bnj`** (k64's $45.30 residue, owner-gated) is unblocked by this: a timeout now
  exercises the partial path on a real leg shape, so buying runs can no longer come back
  PARTIAL-PATH-NOT-EXERCISED. Its arithmetic should be re-checked against the then-current price
  before it is funded.
* **Tool *results*** are still not recovered (only the calls). If they are ever wanted, option (b)
  (`transcript.jsonl`) is the route — costed and declined in §2, not forgotten.
* **The sub-second head of a leg** stays unrecoverable (§4). A timeout set below ~3 s recovers nothing,
  correctly.
