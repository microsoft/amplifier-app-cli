# DONE-NOTE — `model_performance-n1i`

**FIX: narrow the resume credential refresh to secrets, and thread
`model_role`/`provider_preferences` through the delegate resume path**

Lane `n1i-resume-thread-role` · repo `microsoft/amplifier-app-cli` ·
branch `lane/n1i-resume-thread-role` · 2026-09-02 · **spend $0.00**

Implements the root cause diagnosed in `model_performance-rc0`
(`ai-notes/w3-rc0-resume-role-loss/FINDINGS.md`). That diagnosis was CONFIRMED
ON WIRE EVIDENCE and was **not** re-litigated here; this lane implemented it.

---

## 1. DELIVERABLES

| # | Deliverable | Status |
|---|---|---|
| 1 | DRAFT PR on origin, branch `lane/n1i-resume-thread-role`, two fixes as separate commits, tests green | **DONE** |
| 2 | Fail-before/pass-after test proving a resumed delegate retains its `model_role` promotion | **DONE** (§5) |
| 3 | Explicit confirmation root-session resume + credential refresh are unaffected, with how verified | **DONE** (§4) |
| 4 | DONE-NOTE.md in the PR body quoting the file:line sites rc0 identified | **DONE** (this file) |

**Commits:**

```
fix(resume): narrow the sub-session credential refresh to secrets only
fix(resume): thread model_role/provider_preferences through the delegate resume path
```

---

## 2. COMMIT A — the DROP SITE, narrowed to secrets

`amplifier_app_cli/runtime/config.py`, `amplifier_app_cli/session_spawner.py`,
`tests/test_narrow_overrides_to_secrets.py`

### The sites rc0 identified, quoted

Pre-fix line numbers are rc0's, taken against
`openai-evals-team-ci/amplifier-app-cli @ ed89a9f`. This lane's worktree is at
`0d93352`, a later commit that shifted the block down ~200 lines; current
numbers are given alongside.

**`session_spawner.py:1005-1010`** (here `:1214-1219` pre-fix), inside
`resume_sub_session`:

```python
            _live_provider_overrides = _live_settings.get_provider_overrides()
            if _live_provider_overrides:
                _refreshed_providers = _apply_provider_overrides(
                    merged_config["providers"], _live_provider_overrides
                )
```

**`runtime/config.py:564`** — `_apply_provider_overrides`:

```python
                merged = merge_module_items(provider, override_map[key])
```

**`lib/merge_utils.py:149-152`** — where the promotion is actually lost:

```python
        if key == "config" and key in merged:
            # Deep merge configs
            if isinstance(merged["config"], dict) and isinstance(value, dict):
                merged["config"] = deep_merge(merged["config"], value)
```

**`lib/merge_utils.py:64-65`**:

```python
def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts, with overlay winning conflicts.
```

`base` = the child's persisted provider config (`priority: 0`, installed at
spawn by `spawn_utils.py:772-773`); `overlay` = the settings override
(`priority: 14` for luna on the measured host). **Overlay wins.**

The enclosing comment block (`session_spawner.py:971-991`) scopes this refresh
to *secrets*. `priority` is not a secret — collateral damage from the
metadata-redaction security fix.

### The change

New `narrow_overrides_to_secrets()` in `runtime/config.py` reduces a settings
override to the keys `redact_secrets()` actually redacted, reusing
`amplifier_core.utils.truncate.SENSITIVE_KEYS` **so the two directions can
never drift apart** — if redaction learns a new secret key, the refresh learns
it too, with no second list to maintain.

Pruning rules, and why each is what it is:

- **dict** — a key whose *name* is sensitive is kept outright; otherwise
  recurse, keeping the key only if something secret survives beneath it
  (covers `config.auth.token`).
- **list** — kept **whole** if any element carries a secret, else dropped.
  `deep_merge` *replaces* lists rather than merging them, so a partially
  pruned list would silently truncate the merged result. All-or-nothing is the
  only safe choice.
- **identity keys** — `module` and `id` carried through so the override still
  matches its target; **every other top-level key dropped**, so a settings
  override cannot rewrite `source` at resume time either.

**Scope, deliberately narrow:** applied at the RESUME call site only. Root and
fresh config assembly (`resolve_bundle_config`) still merges overrides in
full — there settings *are* the intended source of truth and there is no
persisted child promotion to protect. Verified: `narrow_overrides_to_secrets`
has exactly one call site (§4).

### rc0 §4.6 — the INFERRED-NOT-CONFIRMED sub-claim, now settled

rc0 could not observe `reasoning_effort` drift because the capture had `high`
on both sides. `merge_utils.py:152` merges *every* settings key, so the drift
was structurally possible but unobservable.
`test_per_candidate_config_keys_survive` uses a preference effort of `medium`
against a settings effort of `high` and asserts the child's own value wins.
**Verdict upgraded: CONFIRMED, and fixed.**

---

## 3. COMMIT B — the THREADING

`amplifier_app_cli/session_spawner.py`, `amplifier_app_cli/session_runner.py`,
`tests/test_resume_preserves_provider_promotion.py`

### The gap, as rc0 §2.2 mapped it

| hop | spawn path | resume path |
|---|---|---|
| tool-delegate call site | `__init__.py:1509` — prefs + role | `__init__.py:1444` — neither |
| capability invocation | `__init__.py:1771` `spawn_fn(… provider_preferences=…)` | `__init__.py:2084` `resume_fn(sub_session_id=…, instruction=…)` |
| app-cli capability | `session_spawner.py:715,730` | `session_spawner.py:736,1253` — **2 args** |
| app-cli implementation | `session_spawner.py:231,241` `spawn_sub_session(… provider_preferences …)` | `session_spawner.py:923-926` `resume_sub_session(sub_session_id, instruction, parent_session)` |
| promotion applied? | `session_spawner.py:417-421` → `apply_provider_preferences_with_resolution` | **never** |

The decisive grep: `apply_provider_preferences_with_resolution` appeared
**exactly once** in `session_spawner.py`, inside `spawn_sub_session`.

### The change

- `resume_sub_session(..., provider_preferences=None, model_role=None)`
- both `child_resume_capability` closures (`session_spawner.py`) and
  `resume_capability` (`session_runner.py:553`) gain the same two optional
  keyword arguments, matching their `child_spawn_capability` siblings
- the resume path now calls `apply_provider_preferences_with_resolution`
  exactly as `spawn_sub_session` does

**Preference precedence:** threaded by the caller > persisted agent overlay >
persisted mount plan's own copy.

The two recovery sources are the load-bearing design decision. rc0 §3 recorded
that both `provider_preferences` and `model_role` were **still present in the
resumed session's config and simply never consulted again**. Recovering them
means the fix reaches existing sessions and existing callers *without* the
foundation-side change; the threaded argument is what a caller gains once it is
taught to pass them.

**Fallback is named, never silent.** Per the rc0 acceptance criteria: when no
preferred provider is mounted, resume emits `provider:fallback` carrying
`reason`, the requested preferences, `preferences_source`, and the
provider/model the leg actually landed on. Promotion success is verified from
the **outcome** (a preferred provider sitting at `priority: 0`), not from the
apply call's return value, so the check stays honest across foundation
versions.

### NOT IN THIS REPO — stated plainly

The matching change in `microsoft/amplifier-foundation`
(`modules/tool-delegate/.../__init__.py:1997` `_resume_existing_session`, its
call site at `:1444`, and `resume_fn` at `:2084`) is what makes the *caller*
pass these through. This lane owns `amplifier-app-cli` only, so that repo is
untouched. The signatures added here are additive and backward-compatible
precisely so the two can land independently — and the recovery sources above
mean the measured defect is fixed either way.

---

## 4. DELIVERABLE 3 — root resume and credential refresh are unaffected

Verification script output, run against both commits:

```
### 1. narrow_overrides_to_secrets call sites (must be exactly 1, in resume_sub_session)
amplifier_app_cli/session_spawner.py:1370:  _live_provider_overrides = narrow_overrides_to_secrets(

### 3. resume_sub_session importers (root resume must not be among them)
amplifier_app_cli/session_runner.py:519:    from .session_spawner import resume_sub_session
amplifier_app_cli/session_runner.py:563:        return await resume_sub_session(

### 4. root-session resume path (commands/session.py) never imports session_spawner
0

### 5. pre-existing credential-refresh suite
15 passed

### 6. new suites
21 passed

### 7. full suite
1594 passed, 1 skipped, 13 deselected, 1 xfailed
```

**Root-session resume — how it was verified.** `resume_sub_session` has exactly
one importer in the whole package: `session_runner.py:519`, inside
`register_session_spawning`, which registers it as the **`session.resume`
capability** — that is *root-resumes-a-SUB-session*, not *root resumes itself*.
Root-session resume runs through `commands/session.py::_prepare_resume_context`
→ `resolve_config` → `resolve_bundle_config`, which imports `session_spawner`
**zero** times (check 4) and whose override merging this change does not touch
(check 1: the narrowing has a single call site, inside `resume_sub_session`).
This matches rc0's own strongest negative control — **0 of 179 root resumes
affected** — from the opposite direction: rc0 *measured* that roots were never
hit; this shows *structurally* that they still cannot be.

**Credential refresh — how it was verified.** The pre-existing suites
`test_resume_credential_refresh.py` (5) and `test_resume_redaction_guard.py`
(10) pass unmodified. Two new tests assert the refresh still does its job on
the narrowed path: `test_credentials_are_still_refreshed` (unit) and
`test_promotion_survives_with_no_recoverable_preferences` (through the real
`resume_sub_session`, asserting `api_key == "sk-live-luna"` alongside
`priority == 0`).

**Default behaviour otherwise byte-identical.**
`test_no_preferences_leaves_the_plan_byte_identical` resumes a plan carrying no
promotion and asserts `config["providers"] == _persisted_child_providers()`
with no `provider:fallback` emitted. It is the one test that **passes on both
sides of the fix** — the negative control.

---

## 5. FAIL-BEFORE / PASS-AFTER

**Fail-before, both fixes reverted** (`git stash push amplifier_app_cli/`, run,
pop) — genuine assertion failures, not a collection error, because the
behavioural module deliberately imports no symbol introduced by either fix:

```
7 failed, 1 passed

test_resumed_leg_keeps_its_model_role_promotion
E  AssertionError: A resumed delegate must resolve to the SAME provider its
   spawn leg did. Landing on the settings priority-0 provider is the rc0
   defect: 39/66 delegate resumes changed model, 37 cheap->expensive.
E  assert 14 == 0
```

`14` is the promoted provider's **byte-exact settings priority** from the
measured host (`~/.amplifier/settings.yaml` declares `luna: priority: 14`).
The unit test reproduces the wire signature exactly.

The 1 pre-existing pass is the negative control described above.

**Fail-before for commit B alone** (commit A applied, B reverted): `4 failed, 4
passed`. The 4 that already pass are the ones commit A alone fixes — which is
what the item predicted ("This alone resolves the observed defect"). The 4 that
still fail are commit B's distinct value: explicit preference threading,
preference-config re-assertion, `model_role` threading, and the
`provider:fallback` event.

**Pass-after:** 21/21 across both new modules; **1594 passed, 1 skipped, 1
xfailed** for the full suite. No API calls in any new test; the whole suite
runs in ~8 s.

**Lint:** `ruff check` reports **14 errors before and 14 after** — all
pre-existing, none introduced. `ruff format` applied to the touched files.

---

## 6. DECISIONS TAKEN WITHOUT ESCALATION

Per the lane rules ("no waiting on any human decision: choose, record, continue"):

1. **The hook refresh in the same block is NOT narrowed.** The item asked to
   "guard against the same class in the sibling paths". For providers the guard
   is the code change; for hooks it is a documented boundary, and the reasoning
   is recorded inline at the site. Two reasons: (a) nothing in a hook entry
   carries per-session *resolution* state — the provider wipe mattered because
   `config.priority` decides which model a leg runs on, and a hook has no
   analogue; (b) `get_notification_hook_overrides()` legitimately **appends**
   hooks absent from the persisted plan, so narrowing to secrets would append
   them stripped of `enabled`/`topic`, breaking notifications on resumed
   sub-sessions to fix a defect nobody has observed. The same over-reach *is*
   structurally possible for a hook whose config an agent overlay customised;
   **that needs its own evidence, not a speculative change.** Flagged as a known
   open edge rather than silently fixed or silently ignored.
2. **`_apply_provider_overrides` / `_apply_hook_overrides` / the tool override
   merge themselves are unchanged.** Root config assembly depends on their full
   merge semantics. Narrowing them at source would have changed root behaviour
   to fix a resume-only defect.
3. **The fallback event is a new string, `provider:fallback`**, matching the
   `provider:` namespace of `provider:resolve` / `provider:retry` /
   `provider:error` in `amplifier_core.events`. No existing constant covered
   "the pin could not be honoured".
4. **This DONE-NOTE is committed under `docs/lanes/n1i-resume-thread-role/`**
   as its own third commit, so the two fix commits stay clean and the lane
   bookkeeping is trivially droppable before any merge. (First choice was to
   keep it outside the repo entirely; the lane's write sandbox is the repo, and
   the deliverable requires it committed under this lane's own directory.)
5. **Test module split.** Unit coverage of the new helper lives in
   `test_narrow_overrides_to_secrets.py`; the behavioural module imports **no**
   new symbol, so a fail-before run produces real assertion failures rather than
   an `ImportError` at collection. The first draft did not do this and its
   fail-before run was a worthless collection error — corrected before commit.

---

## 7. SPEND

**$0.00**, against a $0 authority. No API calls, no DTU, no containers, no
infrastructure created — so nothing to register in the infra ledger and nothing
to tear down. Every test runs offline against mocked sessions.

---

## 8. WHAT REMAINS OPEN

1. **`microsoft/amplifier-foundation` tool-delegate is untouched** (§3). Until
   it threads `provider_preferences`/`raw_model_role` into `resume_fn`, the
   promotion is rebuilt from the *persisted* preference rather than a freshly
   routed one. Functionally equivalent for the measured defect; it does mean a
   routing-matrix change between two legs of the same delegate is not picked up
   mid-session.
2. **The hook-refresh over-reach** (§6.1) — same class, unmeasured, deliberately
   left with a named comment instead of a speculative fix.
3. **`amplifier-bundle-routing-matrix`'s `role_pin.py`** (rc0 §5) should stay as
   defense-in-depth. This fix is upstream of it; it is now belt-and-braces
   rather than the only guard.
4. **Not computed:** the dollar delta of the 402 drifted requests. rc0 recorded
   this as NOT COMPUTED and this lane did not change that — it needs per-request
   usage joined to per-model pricing, and would not have changed any decision
   here.
5. **`00-what-we-know.md` §2c's "symmetric confounder" justification** should
   still be amended per rc0 §4.5 — this bias is asymmetric and exaggerates the
   measured cost spread between cells. That is a docs change in the evals repo,
   outside this lane's paths.
