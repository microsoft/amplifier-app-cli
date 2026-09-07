# q99f — `AMPLIFIER_HOME` does not isolate the Python environment

Work item: `model_performance-q99f`. Spend authority: **$0**. No paid API
measurement was taken; the one DTU is infrastructure and was registered and torn
down per the lane's infra-ledger rules.

---

## 1. The incident this lane exists for (verbatim from the item)

> **INCIDENT 2026-09-07:** `hd-*` head-census lanes ran `amplifier` with
> `AMPLIFIER_HOME=/tmp/hd-*-scratch-*` on the HOST to render a tool/skill surface
> for a before/after byte count. Amplifier's first-run editable-install logic
> writes into the SHARED uv tool venv
> (`~/.local/share/uv/tools/amplifier/lib/python3.13/site-packages/*.pth`)
> regardless of `AMPLIFIER_HOME` — the env var does NOT isolate the venv, only
> the config/cache/session directories. Result: **61 of 63 `_editable_impl_*.pth`
> files in the owner's real install were silently rewritten** to point at
> `/tmp/hd-android-scratch-before-bfKCxP/...` and `/tmp/hd-stock-fbf8ce6/...`.
> The owner's CLI then printed something to the effect of *"the two disagree
> about whose code runs"* — a real, user-visible symptom of a completely
> different root cause (the shared venv, not a version mismatch). Repaired by
> hand from a backup at `~/dev/_backups/pth-backup-20260907-1512`.

---

## 2. Mechanism, in one paragraph

`AMPLIFIER_HOME` selects the **cache root** (`<home>/cache/<repo>-<16 hex>/…`).
Modules and bundles are installed **editable** into the interpreter running
Amplifier — `uv pip install -e <cache path> --python sys.executable`
(`amplifier_app_cli/provider_sources.py:425` for providers; foundation's
`ModuleActivator` for everything else). `sys.executable` is the **uv tool venv**,
which is one per machine and completely outside `AMPLIFIER_HOME`'s reach. So every
`.pth` file in that venv encodes *one* home's cache root, and the next run under a
different home overwrites them.

---

## 3. Reproduction (DTU `q99f-pth-repro`, ubuntu 24.04, amplifier 2026.09.07-10af274 / core 1.6.1)

Scripts: [`repro.sh`](repro.sh) (runs A/B), [`repro-flip.sh`](repro-flip.sh) (forces the rewrite), [`verify-fix.sh`](verify-fix.sh), [`verify-remedy1.sh`](verify-remedy1.sh), [`patch_installed.py`](patch_installed.py). Profile:
[`q99f-pth-repro.dtu.yaml`](q99f-pth-repro.dtu.yaml). No API key —
each run reaches the provider and fails auth *after* module activation, which is
the part under test.

### 3.1 Two homes, one venv — measured

| step | `.pth` total | → `home-a/cache` | → `home-b/cache` |
|---|---:|---:|---:|
| fresh install | 1 | 0 | 0 |
| after run A (`AMPLIFIER_HOME=/root/home-a`) | 37 | 36 | 0 |
| after run B (`AMPLIFIER_HOME=/root/home-b`) | 37 | 36 | 0 |
| after run B again | 37 | 36 | 0 |
| after `rm -rf /root/home-a/cache`, then run B | 37 | **27** | **9** |

**Two distinct failures, both silent, both present in one environment:**

1. **The disagreement.** Run B populated `/root/home-b/cache` and imported
   modules from it, while the venv's `.pth` files still pointed at
   `/root/home-a/cache`. The only signal the user got was one module's own
   self-check, verbatim from `/tmp/run-b.log`:

   > `tool-recipes engine: editable install
   > /root/.local/share/uv/tools/amplifier/lib/python3.12/site-packages/_editable_impl_amplifier_module_tool_recipes.pth
   > points at /root/home-a/cache/amplifier-bundle-recipes-2b1e350432fea9ba/modules/tool-recipes,
   > but this engine was imported from /root/home-b/cache/…
   > — the two disagree about whose code runs.`

   That is the incident's reported symptom, reproduced.

2. **The rewrite.** Once home-a's cache was gone — exactly what happens when a
   scratch home under `/tmp` is cleaned — the modules stopped being importable,
   the install path ran, and **9 `_editable_impl_*.pth` files flipped from
   `/root/home-a/cache/…` to `/root/home-b/cache/…` with no warning of any
   kind.** All nine are provider modules, i.e. installed by *this repo's*
   `install_known_providers()`:

   ```
   _editable_impl_amplifier_module_provider_anthropic.pth
   _editable_impl_amplifier_module_provider_azure_openai.pth
   _editable_impl_amplifier_module_provider_chat_completions.pth
   _editable_impl_amplifier_module_provider_gemini.pth
   _editable_impl_amplifier_module_provider_github_copilot.pth
   _editable_impl_amplifier_module_provider_ollama.pth
   _editable_impl_amplifier_module_provider_openai.pth
   _editable_impl_amplifier_module_provider_openai_chatgpt.pth
   _editable_impl_amplifier_module_provider_vllm.pth
   ```

**Honest scope note.** The flip needed a second condition (the first home's cache
gone) that the host incident supplied naturally and a same-session A/B does not.
The *shared, unisolated venv* is the defect; whether a given run rewrites or
merely disagrees depends on whether the already-installed module still imports.
Both outcomes are corruption of a resource `AMPLIFIER_HOME` is widely assumed to
isolate, and the guard below fires before either.

---

## 4. The fix: (b), refuse before installing

`amplifier_app_cli/lib/venv_home_guard.py` (library) +
`amplifier_app_cli/main.py::_guard_shared_venv_home` (thin CLI wrapper, called in
`main()` before `cli()`).

Before click dispatches anything, the guard reads the `.pth` files already in
`sysconfig.get_paths()["purelib"]`, maps each `<home>/cache/<repo>-<16 hex>/…`
target back to the home that owns it, and compares against the resolved
`AMPLIFIER_HOME`. Any foreign owner ⇒ refuse, exit 1, with a message that names
both homes, the environment, the count, five example pointers, and three ranked
remedies.

### Why (b) and not (a) or (c)

| option | verdict |
|---|---|
| **(a) per-home venv** | Correct, and **deferred** — filed as a follow-up. The venv is created by `uv tool install`, outside Amplifier's control; owning it means Amplifier provisioning and re-execing into a per-home environment. Not a $0 change, and shipping it half-done is worse than the guard. |
| **(b) refuse loudly** | **Shipped.** It is the only option that stops the corruption *before* it happens, it lives entirely in this repo, and one check at the entrypoint covers **every** editable install in the process — this repo's provider installs *and* foundation's `ModuleActivator`. |
| **(c) name the fix in the existing warning** | **Not editable from this repo.** The "the two disagree about whose code runs" string belongs to `tool-recipes`, in `amplifier-bundle-recipes` — see the verbatim quote in §3.1. Its intent is satisfied anyway: the guard's own message names `amplifier reset --remove cache -y` and the isolation command. |

### Deliberate design choices

- **Refuse, not warn.** The incident happened inside automated lanes with nobody
  reading stdout. A warning would have been missed exactly as the silence was.
- **`AMPLIFIER_ALLOW_SHARED_VENV=1` escape hatch,** named in the refusal. A guard
  with no documented way past it gets routed around with something worse. Under
  the override the same message prints as a warning and the run continues.
- **`version` and `reset` stay reachable** while tripped. Blocking `reset` would
  make the recommended repair unreachable.
- **Never fatal by accident.** Any unexpected exception inside the guard is
  logged at debug and the CLI proceeds.
- **Dev checkouts are not claimed.** Only `<home>/cache/<name>-<16 hex>` counts,
  so `uv pip install -e ~/dev/my-module` never trips it.
- **`import`-form `.pth` files are skipped, not half-parsed.** uv — which wrote
  every `.pth` in the incident — uses the plain-path form.

---

## 5. After-fix verification, same DTU, same scenario

The patched `main.py` + new module were applied to the DTU's installed CLI
(`patch_installed.py`), then `verify-fix.sh` re-ran the two-homes scenario:

| check | result |
|---|---|
| run A claims the venv | 36 `.pth` → `home-a` |
| run B (`AMPLIFIER_HOME=/root/home-b`) | **exit 1, "Refusing to run…"** |
| `.pth` files changed by the refused run | **0** |
| `amplifier version` while tripped | exit 0 |
| `amplifier reset --help` while tripped | exit 0 |
| `AMPLIFIER_ALLOW_SHARED_VENV=1` | warns ("AMPLIFIER_HOME changed…"), continues, reaches the LLM |
| remedy #1 verbatim on a fresh `home-c` | install exit 0 · 0 refusals · reached the LLM · isolated venv gets **36 of 37** `.pth` pointing at `home-c/cache` · **shared venv 0 changes** |

Remedy #1 as printed is therefore **measured to work**, not asserted.

**DTU artifact, not a product issue:** `uv` hardlinks package files from its
cache into each venv, so patching the installed `main.py` in place also reached
uv's cache and thus the fresh `home-c` install. The verification script copies
the new module alongside it. This affected only the patch method used to test a
pre-release CLI inside the DTU.

---

## 6. Fail-before / pass-after

- `tests/test_venv_home_guard.py` — **25 tests**. With
  `amplifier_app_cli/lib/venv_home_guard.py` removed (i.e. shipped behavior) the
  module fails to import and the file errors at collection; with the fix, 25
  pass. Its fixture is the nine flipped filenames and cache hashes captured in
  §3.1, not invented paths.
- Behavioral fail-before is the DTU itself: the shipped CLI ran the scratch home
  to completion and rewrote nine pointers; the patched CLI exits 1 and changes
  nothing.
- Full suite: **1473 passed, 1 skipped, 13 deselected, 1 xfailed.**

---

## 7. Follow-ups filed

- **(a) per-home venv** — `model_performance-xq95`.
- Adjacent, noticed while reading and **not** fixed here (out of scope, no
  evidence gathered): `amplifier_app_cli/paths.py:48` (`get_install_state_path`)
  and `amplifier_app_cli/commands/reset.py:68` (`_get_amplifier_dir`) both
  hardcode `Path.home() / ".amplifier"` and ignore `AMPLIFIER_HOME`.
