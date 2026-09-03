# Is `partial_available: true` reachable on a REAL leg shape?

Recomputed from k64's own captures (`treatment-validation/20260903-k64-delegate-timeout/`),
$0, no new runs. Population: the 18 delegate sub-sessions (`0000000000000000-*`) --
the same 18 legs k64's TEXT-WINDOW-TABLE.md reports. Timestamps are each leg's own
`events.jsonl`; `dur_s` is first-to-last event, which is why it differs from k64's
harness-measured duration by a few tenths.

* **evidence_window** = the share of the leg during which the WIDENED accumulator holds
  something (from the first `thinking`/`tool_call` block to the end of the leg).
* **text_window** = the share during which the OLD accumulator held something (from the
  single `text` block to the end). This is k64's ~0.5% figure.

| leg | dur_s | think | tool | text | 1st evidence s | 1st text s | evidence_window | text_window |
|---|---|---|---|---|---|---|---|---|
| `27fd2a47` | 221.8 | 25 | 0 | 1 | 3.15 | 221.75 | 98.6% | 0.02% |
| `760e7ff5` | 156.2 | 20 | 1 | 1 | 5.26 | 156.11 | 96.6% | 0.03% |
| `3d698bd3` | 144.2 | 24 | 0 | 1 | 3.64 | 144.16 | 97.5% | 0.03% |
| `cbdb7bf1` | 117.2 | 10 | 1 | 0 | 3.16 | never | 97.3% | 0.00% |
| `1f4bec9b` | 115.7 | 7 | 5 | 1 | 3.15 | 115.69 | 97.3% | 0.04% |
| `2b4aa500` | 105.4 | 8 | 3 | 1 | 3.98 | 105.35 | 96.2% | 0.02% |
| `caeba80f` | 101.0 | 18 | 0 | 0 | 3.52 | never | 96.5% | 0.00% |
| `da8990c3` | 94.1 | 10 | 0 | 1 | 41.03 | 94.11 | 56.4% | 0.02% |
| `d0bd9db3` | 82.8 | 12 | 0 | 1 | 4.87 | 82.80 | 94.1% | 0.04% |
| `813e7db0` | 72.8 | 6 | 5 | 1 | 4.46 | 72.77 | 93.9% | 0.05% |
| `5019ae7b` | 60.7 | 6 | 0 | 1 | 3.64 | 60.66 | 94.0% | 0.08% |
| `05148a3b` | 54.8 | 2 | 0 | 1 | 3.46 | 54.74 | 93.7% | 0.09% |
| `7015903a` | 50.3 | 7 | 3 | 1 | 3.30 | 50.32 | 93.4% | 0.03% |
| `59997875` | 49.5 | 10 | 0 | 1 | 6.77 | 49.45 | 86.3% | 0.03% |
| `bcb7ec94` | 41.6 | 1 | 0 | 1 | 35.54 | 41.57 | 14.5% | 0.03% |
| `a5f1b7ee` | 29.4 | 5 | 1 | 1 | 3.34 | 29.41 | 88.7% | 0.04% |
| `080c7821` | 20.6 | 3 | 2 | 1 | 3.31 | 20.62 | 84.0% | 0.05% |
| `e8811418` | 5.2 | 1 | 0 | 1 | 5.20 | 5.20 | 0.3% | 0.24% |

**Legs with at least one `thinking` or `tool_call` block: 18/18.**
**Legs with a `text` block: 16/18.**

* evidence_window: min **0.3%**, max **98.6%**, mean **82.2%** (n=18)
* text_window:     min **0.00%**, max **0.24%**, mean **0.05%** (n=18)

**ANSWER: yes, on this measured population.** A timeout firing uniformly at random inside a
leg now recovers content on **82.2%** of the leg on average, against
**0.05%** before -- a ~1779x wider window -- and the two zero-text legs
(which could NEVER have recovered anything) become recoverable for ~97% of their duration.

**The honest limit.** The first evidence block lands 3.15-41.03 s into a leg, so a timeout
shorter than the first block still recovers nothing -- correctly. And the worst case here
(`bcb7ec94`, first evidence at 35.54 s of a 41.6 s leg) shows the head of a slow leg is
still dark. This is a much wider window, not a guarantee.

*(knob moved: none -- reanalysis of k64's captures . terra S1 root, sub-work matrix-routed .
confidence: **measured**, n=18 legs / 7 runs . evidence pointer:
`treatment-validation/20260903-k64-delegate-timeout/runs/*/all-sessions/projects/*/sessions/0000000000000000-*/events.jsonl`)*
