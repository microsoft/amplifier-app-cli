# xq95 — a Python environment per `AMPLIFIER_HOME`

Work item: `model_performance-xq95` (deferred fix **(a)** from
`model_performance-q99f`). Spend authority: **$0**; **$0 spent** — no paid API
measurement, and **no DTU was created**, for the reason argued in §5.

**Outcome branch: B — resolved at the cap.** The item flags itself as *"larger
than a $0 lane"* and asks, if both cannot be funded, for the **design plus a
proof-of-concept demonstrating the mechanism in one repo**. That is what shipped:
a design covering every question the acceptance criteria ask, and a working,
measured, **opt-in** implementation of the app-cli half. The default path is
byte-identical to today. The foundation half and the two-homes DTU verification
are filed, not attempted.

---

## 1. What shipped

| deliverable | state |
|---|---|
| Design for per-home Python environments | **DONE** — [`docs/designs/per-home-python-environment.md`](../../designs/per-home-python-environment.md) |
| — creation: how and when | DONE — §3.1–3.2 |
| — detection / routing (re-exec or otherwise) | DONE — §2, §3.4; re-exec **rejected**, with the reason |
| — migration for an existing single-venv install | DONE — §4; no-op by construction, nothing stranded |
| — is the q99f guard unreachable, or retained? | DONE — §5, argued both ways, **retain** |
| Proof-of-concept, one repo | **DONE** — `amplifier_app_cli/lib/home_env.py`, opt-in behind `AMPLIFIER_HOME_ENV`, 40 tests |
| Two-homes reproduction in a DTU (before/after) | **NOT-POSSIBLE at $0 *as specified*** — see §5. A host-safe equivalent of the *mechanism* claim was measured instead, fail-before and pass-after. |
| Design quotes the q99f DTU findings as motivation | DONE — design §1 reproduces q99f's table and the verbatim symptom |

## 2. The design, in five lines

A home does **not** get a second Amplifier. It gets
`<AMPLIFIER_HOME>/env/py<major>.<minor>` — a bare venv holding only what Amplifier
installs at runtime — plus one `.pth` pointing back at the base environment.
Installs target the overlay's interpreter; the running process reaches them with
`site.addsitedir`. No re-exec, no duplicated stack, no shared writable resource.
Every overlay install carries a constraints file pinning each base distribution,
because the base wins at import and a silently-ignored version skew is the same
class of bug all over again.

## 3. Measured, on this host (uv 0.12.6, CPython 3.13.11, `UV_OFFLINE=1`, $0)

| claim | measurement |
|---|---|
| overlay creation is cheap | `uv venv` **0.374 s**, offline |
| an install into the overlay is cheap | **0.794 s** wall; uv: *"Installed 5 packages in 6 ms"* |
| **the base environment does not move** | `.pth` count **75 → 75** against the real uv tool venv |
| uv cannot see the base env from the overlay (the cost driver) | `uv pip list` on a fresh overlay: **0** packages, vs **156** `dist-info` in the base |
| version skew fails loudly rather than silently | constrained install exits **1**, naming `rich>15.0.0` vs `rich==15.0.0` |

## 4. Fail-before / pass-after

`evidence/probe-overlay-isolation.txt` — the q99f shape (one module name, two
home cache roots) routed through the library, against the real uv tool venv:

```
  PASS  base .pth count unchanged                            75
  PASS  home-a .pth points into home-a                       yes
  PASS  home-b .pth points into home-b                       yes
  PASS  home-a survived home-b's install                     yes
  PASS  base environment has no probe .pth                   0
```

`evidence/probe-fail-before.txt` — same probe, feature off, against a throwaway
base venv:

```
  FAIL  base .pth count unchanged                            got 2, want 1
  FAIL  home-a survived home-b's install                     got no (missing), want yes
the shared .pth the two homes fought over, and who won:
  _editable_impl_amplifier_module_probe.pth -> …/home-b/cache/amplifier-module-probe-…/src
```

That last line is q99f's measured flip, reproduced at $0 and then removed.

Unit level: `tests/test_home_env.py`, **40 tests**, all fail against the shipped
CLI (the module does not exist and the call sites hardcode `sys.executable`).

## 5. Why no DTU — and what the DTU still owes

The item and the lane rules require any *reproduction* to happen in a DTU. That
requirement is about running the real CLI under a scratch `AMPLIFIER_HOME`, which
is the incident itself. **No such run happened here, on the host or anywhere.**

The claims this lane makes are narrower — *where does an editable install land* —
and they are establishable without invoking `amplifier` at all. The probes drive
`uv` directly through the library, write only into their own temp directories,
and merely **read** the base environment's `.pth` count. The fail-before half
builds its own throwaway base venv rather than borrowing a real one, precisely
because with the feature off the installs do land in the base.

**What the DTU still owes, and cannot be given yet:** the real CLI, two homes, in
sequence, asserting each home's overlay holds its own ~36 `.pth` while the base
count does not move. Running it *today* would measure nine provider modules
isolated and every foundation-installed module still colliding — a misleading
result. It is blocked on the foundation half, and filed with it.

## 6. Cross-repo — the half this lane may not touch

`ModuleActivator` in **amplifier-foundation** performs most editable installs;
app-cli owns only the nine provider modules. **This lane was given
amplifier-app-cli only and edited nothing else.**

Proposed seam (design §6): `ModuleActivator` gains an optional
`install_python: str | None = None` (plus `install_constraints: Path | None`)
defaulting to `sys.executable`, so foundation can ship it with **zero** behaviour
change; app-cli then passes `home_env.uv_target_args()`'s values. **Foundation
first, app-cli second** — the reverse order creates a release where providers are
isolated and nothing else is.

Filed as a linked follow-up item (see §8).

## 7. Test suite

`1990 passed, 2 skipped, 13 deselected, 1 xfailed, 7 failed`.

**The 7 failures are pre-existing on `origin/main` and unrelated.** Verified by
`git stash`: the same 7 fail at `2e2d6a0` with this branch's changes removed.
They are `tests/lib/bundle_loader/test_root_instruction_survives_behaviors.py`
(5), `tests/test_fail_loud_bundle_activation.py` (1) and
`tests/test_transitive_include_updates.py` (1); each fails cloning a fixture repo
(`fatal: Remote branch <sha> not found in upstream origin`), i.e. an environment
issue in this worktree, not a product regression. Delta from this branch: **0**.

## 8. Open / filed

1. **The foundation half** — `ModuleActivator` install target. Filed as a linked
   follow-up. Without it this is nine modules, not isolation.
2. **The two-homes DTU verification** — blocked on 1 (§5).
3. **Flip `AMPLIFIER_HOME_ENV` to default-on** — after 1 and 2.
4. **`amplifier reset` should remove `<home>/env`** — it removes `<home>/cache`
   today, which would leave overlay pointers into deleted directories.
5. **Unmeasured:** total overlay cost on a real home (dozens of modules, not
   one), and Windows (the `Scripts/` / `Lib/site-packages` branches are
   unit-tested against a synthetic layout, never run).

## 9. Reproduce

```bash
# pass-after, against the environment that actually collided
PROBE_PYTHON=~/.local/share/uv/tools/amplifier/bin/python \
  bash docs/lanes/xq95-per-home-venv/probe-overlay-isolation.sh

# fail-before, against a throwaway base it builds itself
bash docs/lanes/xq95-per-home-venv/probe-fail-before.sh

python -m pytest tests/test_home_env.py -q
```
