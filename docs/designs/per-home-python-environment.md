# A Python environment per `AMPLIFIER_HOME`

**Status:** design + proof-of-concept, shipped opt-in. The default path is
unchanged until the foundation half lands.
**Work item:** `model_performance-xq95`, deferred from `model_performance-q99f`.
**Spend:** $0. Every number below was measured on the host at no API cost; no
DTU was needed, for the reason given in §7.

---

## 1. The defect, and why the guard is not the end of it

`AMPLIFIER_HOME` isolates config, cache, registry and sessions. It does **not**
isolate the Python environment. Modules are installed *editable* into the
interpreter running Amplifier —

```
uv pip install -e <AMPLIFIER_HOME>/cache/<repo>-<16 hex>/... --python sys.executable
```

— and under `uv tool install` that interpreter is one venv per machine. Every
`_editable_impl_<name>.pth` file in it encodes exactly one home's cache root, and
the next run under a different home overwrites it.

That is measured, not theorised. From
[`docs/lanes/q99f-amplifier-home-pth-bug/FINDINGS.md`](../lanes/q99f-amplifier-home-pth-bug/FINDINGS.md),
DTU `q99f-pth-repro`:

| step | `.pth` total | → `home-a/cache` | → `home-b/cache` |
|---|---:|---:|---:|
| fresh install | 1 | 0 | 0 |
| after run A (`AMPLIFIER_HOME=/root/home-a`) | 37 | 36 | 0 |
| after run B (`AMPLIFIER_HOME=/root/home-b`) | 37 | 36 | 0 |
| after `rm -rf /root/home-a/cache`, then run B | 37 | **27** | **9** |

and, on the host that filed it, *"61 of 63 `_editable_impl_*.pth` files in the
owner's real install were silently rewritten"*, repaired by hand from a backup.
Run B's only visible symptom before that was one module's own self-check:

> `_editable_impl_amplifier_module_tool_recipes.pth points at
> /root/home-a/cache/…, but this engine was imported from /root/home-b/cache/…
> — the two disagree about whose code runs.`

`model_performance-q99f` shipped fix **(b)**: `lib/venv_home_guard.py` refuses to
run when the environment's `.pth` files are owned by a foreign home. It stops the
corruption. It does not remove the shared resource, so a user with two homes
still cannot run both — they get a refusal instead of a silent rewrite. This
document is fix **(a)**: give each home its own environment, so there is nothing
left to refuse.

---

## 2. The shape: an overlay, not a second Amplifier

The obvious reading of "per-home venv" is the one q99f measured as a manual
remedy — `UV_TOOL_DIR=<home>/uv-tools uv tool install git+…` — a complete second
Amplifier per home. Automating *that* means Amplifier provisioning an install of
itself and re-exec'ing into it: minutes, network, and a duplicate of the whole
stack per home.

It is not necessary. The shared resource is not "Amplifier"; it is the one
`site-packages` that runtime editable installs write into. So:

> **Each home gets `<AMPLIFIER_HOME>/env/py<major>.<minor>` — a bare venv holding
> only the packages Amplifier installs at runtime. Amplifier itself, and
> everything it shipped with, stay in the base environment.**

Two wires connect it:

1. **Write side.** Every `uv pip install -e` targets the overlay's interpreter
   instead of `sys.executable`.
2. **Read side.** The running process calls `site.addsitedir(<overlay>)` at
   startup and after each install, which is how modules installed there become
   importable *in this process*.

The overlay carries one `.pth` naming the base `site-packages`, so the overlay's
own interpreter can still import Amplifier and its dependencies — needed for
`uv`'s build step and for any subprocess that runs the overlay python directly.

**No re-exec.** Re-exec costs a process restart on every invocation, needs the
whole stack installed per home, and is fragile under `uv tool run`, `python -m`,
editable dev checkouts, pytest, and Windows. The overlay reaches the same
property — no shared writable resource — for a `site.addsitedir` call.

### Measured, on this host (uv 0.12.6, CPython 3.13.11, `UV_OFFLINE=1`)

| what | measurement |
|---|---|
| `uv venv --python <base>` | **0.374 s** wall, offline |
| editable install of a module whose dep is already in the base env | **0.794 s** wall; uv reports *"Installed 5 packages in 6 ms"* |
| base env `.pth` count, before / after that install | **75 / 75** |
| overlay disk for one module + 4 deps | 7.2 MB apparent, hardlinked from uv's cache |

And end-to-end, the q99f shape run through the library
([`probe-overlay-isolation.sh`](../lanes/xq95-per-home-venv/probe-overlay-isolation.sh),
output in `evidence/`), against the very uv tool venv that collided:

```
== base environment
  /home/bkrabach/.local/share/uv/tools/amplifier/lib/python3.13/site-packages
  .pth files before: 75
== claims
  PASS  base .pth count unchanged                            75
  PASS  home-a .pth points into home-a                       yes
  PASS  home-b .pth points into home-b                       yes
  PASS  home-a survived home-b's install                     yes
  PASS  base environment has no probe .pth                   0
```

The same probe with the feature off, against a throwaway base
([`probe-fail-before.sh`](../lanes/xq95-per-home-venv/probe-fail-before.sh)):

```
  FAIL  base .pth count unchanged                            got 2, want 1
  FAIL  home-a survived home-b's install                     got no (missing), want yes
the shared .pth the two homes fought over, and who won:
  _editable_impl_amplifier_module_probe.pth -> …/home-b/cache/amplifier-module-probe-…/src
```

That last line is q99f's flip, reproduced and then removed.

---

## 3. Mechanics

### 3.1 Where, and keyed on what

`<AMPLIFIER_HOME>/env/py<major>.<minor>`.

Under the home, so it is destroyed with the home and shares its lifetime.
Version-keyed on the *running* interpreter, so upgrading the base Python starts a
fresh overlay instead of importing 3.13 builds into 3.14. The stale one is inert
and can be deleted whenever.

### 3.2 When created

**Lazily, on the first install for that home** — not at startup.
`ensure_home_env()` is called from `uv_target_args()`, which only the install
call sites use. Startup pays only `activate_home_env()`, which is a directory
check plus one `site.addsitedir` when the overlay exists, and returns
immediately when it does not.

### 3.3 Dependency resolution, and the one hazard it creates

`uv` reads a venv's own `dist-info`; it does not follow the base `.pth`. Measured:
`uv pip list` on a fresh overlay reports **0** packages while the base has **156**
`dist-info` directories. So a module's dependencies that already exist in the base
environment are *re-resolved into the overlay*. They come from uv's cache as
hardlinks — the 6 ms above — so the cost is real but small.

The hazard is not the duplication, it is the **version skew**. `site.addsitedir`
appends, so for any package present in both, the **base copy wins at import**. A
dependency resolved to a newer version in the overlay would be installed and then
silently ignored — precisely the class of silent disagreement this whole item
exists to end.

**So every overlay install is passed a constraints file pinning each base
distribution to its installed version.** An incompatible requirement then fails
loudly at install time, naming both versions, instead of quietly at import time.
Measured:

```
  rich>15.0.0
and rich==15.0.0, we can conclude that amplifier-module-fake2==0.1.0
cannot be used.
```

(exit 1, against a base pinned at `rich==15.0.0`.)

The constraints file is rewritten on every `ensure`, because `amplifier update`
changes the base environment and a stale pin would pin to a version that is gone.

### 3.4 Ordering

Base first, overlay appended. Deliberate: it is what makes "one copy of a shared
dependency is the one Amplifier shipped with" true. Modules themselves exist only
in the overlay, so nothing they provide is shadowed. A pre-existing base `.pth`
for a module *would* shadow the overlay's copy — but only for the home that
already owns it, pointing at the same cache path (§4), and for a foreign home the
guard fires first.

### 3.5 Fallback, and why it is a fallback

`ensure_home_env()` returns `None` — and the caller silently keeps using
`sys.executable` — when the feature is off, `uv` is missing, `uv venv` fails, or
the overlay cannot be written. That is deliberate: making a missing `uv` fatal
would break a CLI that is otherwise perfectly able to run, and the fallback is
*recoverable* precisely because the q99f guard turns the resulting cross-home
collision into a refusal on the next run. This is the strongest single argument
for §5's conclusion.

---

## 4. Migration: an existing single-venv install

**It is a no-op by construction, and nothing is stranded.**

Today's base environment holds N editable `.pth` files owned by the default home
(`~/.amplifier`). After this change, that home's *new* installs go to
`~/.amplifier/env/pyX.Y`. The old `.pth` files keep working untouched: they point
at `~/.amplifier/cache/...`, which still exists, and the base `site-packages` is
still the running interpreter's own — first on `sys.path`, `.pth` files processed
by normal `site` initialisation. No move, no rewrite, no reinstall.

Three consequences worth stating rather than discovering:

1. **The base environment stays mixed for a while.** A module installed before
   the change resolves from the base; one installed after resolves from the
   overlay. Both work. The mix drains naturally as modules are reinstalled or
   updated, and can be drained deliberately with `amplifier reset --remove cache`
   (which already forces a reinstall).
2. **A foreign home's leftovers are still a conflict**, and still handled by the
   q99f guard plus `amplifier reset`. This change prevents new ones; it does not
   retroactively clean up an install that was already corrupted.
3. **`amplifier reset` should learn about `<home>/env`.** It currently removes
   `<home>/cache`; leaving the overlay behind after a cache wipe leaves editable
   pointers into directories that no longer exist. Small, mechanical, and listed
   in §8 rather than smuggled into this change.

Explicitly **not** proposed: automatically moving existing base `.pth` files into
the overlay. It buys nothing (they already work), and a partial move during an
in-flight install is a new failure mode for no gain.

---

## 5. Does the q99f guard become unreachable? — argued both ways

**The case that it does.** Once no editable install targets the base environment,
no base `.pth` can name a `<home>/cache/<repo>-<16 hex>` path, so
`detect_home_conflict()` returns `None` forever. On a green-field install the
guard is dead code, and dead code that prints a scary message is a liability: it
will one day fire on something that is not this defect (a dev checkout laid out
like a cache entry, a vendored path) and cost someone an afternoon. The honest
end state of a fix is that its tripwire is removed.

**The case that it does not — and this is the recommendation: RETAIN.** Three
reasons, in decreasing strength:

1. **The fallback in §3.5 is a real, reachable code path.** No `uv`, an
   unwritable home, a read-only filesystem, a locked-down CI image — any of them
   sends installs straight back to `sys.executable`. The guard is what turns that
   from a silent cross-home rewrite into a refusal naming both homes. Removing
   the guard would mean the fallback has to become fatal instead, which trades a
   rare loud failure for a common one.
2. **The two halves ship on different release trains.** foundation's
   `ModuleActivator` performs most editable installs (app-cli owns only the nine
   provider modules). Between the two releases, a home is *partly* isolated —
   exactly the window in which a tripwire earns its keep.
3. **It costs approximately nothing.** It is a glob over `*.pth` and a path-prefix
   comparison, on paths already in the page cache, before click dispatches. It
   has no side effects and an escape hatch.

There is also a fourth, softer reason: the guard is the only thing that will ever
explain to a user with a *pre-existing* corrupted install what happened to them.
That population does not shrink to zero on the day this ships.

**Decision:** retain, unchanged in behaviour. One line should be added to its
message once per-home environments are the default — *"this environment predates
per-home environments; `amplifier reset --remove cache` will move these into
`<home>/env`"* — so the message names the new remedy rather than only the old
manual one.

---

## 6. The cross-repo split

The isolation property needs both repos. app-cli alone cannot deliver it.

| repo | change | risk |
|---|---|---|
| **amplifier-app-cli** | `lib/home_env.py` (the library); `main()` activation; the three `uv pip install -e` call sites (`provider_sources.py` ×2, `provider_manager.py` ×1) | low — this PR |
| **amplifier-foundation** | `ModuleActivator._install_dependencies` takes the target interpreter from an injected policy rather than `sys.executable` | low, and *additive* |

**Suggested seam, and ship order.** Give `ModuleActivator` an optional
`install_python: str | None = None` (and an optional `install_constraints: Path |
None`), defaulting to `sys.executable`. Foundation shipping that on its own
changes **no** behaviour. app-cli then passes `home_env.uv_target_args()`'s
values when it constructs the activator, and the property arrives with the
second release. Foundation first, app-cli second; the reverse order leaves a
release in which app-cli isolates providers while foundation does not, which is
the confusing half-state §5.2 describes.

Do **not** put `home_env` in foundation. The policy "where does this home install
to" is a host decision, exactly like the rest of `amplifier_app_cli/paths.py`;
foundation should receive the answer, not compute it.

---

## 7. Why no DTU was needed here

q99f needed one because it reproduced a *behavioural* failure: run the real CLI
under two homes and watch a real install get rewritten. That is unsafe on a host
by definition — it is the incident.

This lane's claims are about where an install *lands*, which can be established
without invoking `amplifier` at all: the probe drives `uv` directly through the
library, installs only into its own temp directories, and merely **reads** the
base environment's `.pth` count before and after. The fail-before half builds its
own throwaway base venv rather than borrowing a real one, because with the
feature off the installs do land in the base — and pointing that at a real
install is the corruption itself.

**What a DTU is still needed for, and what it should assert** (the acceptance
criteria this lane does not close — see §8): the real CLI, two homes, in
sequence, asserting each home's `<home>/env/pyX.Y` holds its own ~36 `.pth` and
the base environment's count does not move. That is the direct analogue of q99f's
table and it needs the foundation half to exist first, or it will measure nine
provider modules isolated and everything else still colliding.

---

## 8. Open, and deliberately not done here

1. **The foundation half** (§6) — filed as a linked follow-up item. Without it
   this is nine modules, not isolation.
2. **The two-homes DTU verification** (§7) — blocked on 1.
3. **Flip the default.** `AMPLIFIER_HOME_ENV` is opt-in in this PR. Partial
   isolation by default is harder to reason about than today's honest collision
   plus a guard.
4. **`amplifier reset` should remove `<home>/env`** (§4.3).
5. **Unmeasured: total overlay cost on a real home.** One module cost 7.2 MB
   apparent / 6 ms. A real home activates dozens. The prediction is "hardlinks,
   so near-zero incremental disk and under a second", and it is a prediction, not
   a measurement.
6. **Unmeasured: Windows.** The path branches (`Scripts/`, `Lib/site-packages`)
   are written and unit-tested against a synthetic layout; no Windows run exists.
