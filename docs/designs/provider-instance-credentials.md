# Design: Per-instance provider credential binding (+ add-provider scope & id fixes)

Status: proposed (v2 — revised after 6-lens adversarial council review; all six
returned CONCERN, direction confirmed sound, gaps resolved below; see §12 changelog)
Scope: `amplifier-app-cli` only. **No `amplifier-core` changes required** (see §3 verification).
One new dependency: `filelock` (see §5.5).
Author: systems-architect

---

## 1. Problem framing

The `amplifier provider` / `amplifier init` wizard mishandles **two instances of the
same provider type** (the documented "configure two providers" pattern, e.g.
`anthropic-fable` + `anthropic-opus`). Three defects, traced to source:

- **Bug 1 — no id-uniqueness check on add.** Both add paths prompt for an instance
  `id` with zero validation and unconditionally append, producing silent duplicate
  YAML entries.
- **Bug 2 — add ignores the selected write scope.** Both add paths hardcode
  `"global"`, contradicting the dashboard's `[w]` scope banner and diverging from
  `edit`/`reorder`, which thread `scope`.
- **Bug 3 (primary) — no per-instance credential binding.** Credential naming is
  type-keyed end to end. Configuring a second same-type instance overwrites the
  first's key in the shared `keys.env`, and both `settings.yaml` entries carry the
  **identical** `${ENV_VAR}` placeholder, silently resolving to whichever key was
  saved last. No error, no warning — a wrong, silently-mismatched credential.

The design goal: two same-type instances must be able to hold **distinct**
credentials with no collision, no silent overwrite, and a wizard flow that makes the
distinction obvious — without pushing policy into the kernel.

---

## 2. Explicit assumptions

1. The dominant real-world case is 2–5 same-type instances per user, configured
   interactively. Non-interactive (`non_interactive=True`) and CI paths must remain
   correct but are secondary for UX — and per council review, must **never** silently
   inherit interactive "smart defaults" (§5.4.5).
2. Users legitimately set real credential env vars in their shell / CI / DTU
   `passthrough.services`. The credential store must stay interoperable with real
   named environment variables (this is an ecosystem invariant — DTU passthrough
   forwards secrets **by env-var name**; see `amplifier-ecosystem-map.md`).
3. `ConfigField.env_var` expresses the provider author's **default/conventional**
   env var for a field — a *type-level hint*, not an instance-level fact.
4. Existing already-broken configs in the wild are out of scope to auto-repair; the
   wizard should *detect and warn* when it encounters one, but migration tooling is a
   separate follow-up.
5. **Ordinary concurrent CLI invocations are an expected, non-adversarial case**, not
   an edge case to wave away — two terminals, or a script racing a human, both running
   `amplifier init`/`provider add`. Both write paths this design touches must not
   silently corrupt or drop each other's writes (§5.5, council-mandated).

---

## 3. The decisive verification (why this is an app-cli-only problem)

Bug 3's fix direction hinges entirely on **how the `${VAR}` placeholder is resolved
at runtime**: is the env-var name re-derived from `ConfigField.env_var` (kernel-owned,
type-level) at session load, or is it read from the **placeholder string** stored in
the instance's own config (instance-level)? I verified this at every resolution site:

| Site | File:line | Behavior |
|------|-----------|----------|
| Runtime session load | `runtime/config.py:268-269`, `expand_env_vars` at `:737-752` | Parses the name **out of the `${VAR}` string** (`os.environ.get(var_name)`), expands **before** syncing to the mount-plan, so provider modules receive the already-resolved **value** — never the name. |
| Provider loader | `provider_loader.py:191-205` `_resolve_env_placeholder` | `value[2:-1]` → `os.environ.get(env_var)`. Name comes from the placeholder. |
| Wizard default-fill | `provider_config_utils.py:193-208` `_resolve_config_value` | Same: name parsed from the placeholder string. |

`amplifier-core`'s `ProviderInfo.credential_env_vars` / `ConfigField.env_var` are
**type-level declarations consumed at authoring/prompt time only**. The runtime never
re-derives the credential name from them — it reads whatever `${VAR}` the instance's
config holds.

**Consequence:** the per-instance credential binding *already exists* — it is the
placeholder string in the instance's config value. The bug is purely that the wizard
(a) hardcodes the type-default name into every instance's placeholder and (b) saves
every instance's secret under that one name. Fixing both is entirely within app-cli.
`KeyManager` needs no schema change: it is already a flat `name → value` store
(`key_manager.py:38-69`); distinct **names** cannot collide.

This verification is the spine of the recommendation. If a reviewer disproves it
(e.g. finds a fourth resolution path that re-derives from `ConfigField.env_var`), the
recommendation weakens and option (a) returns to contention — so it is called out
explicitly as the top review target, and is the one assumption backed by a standing
regression tripwire (§5.4.6, §9) so a future contributor can't silently reintroduce it.

---

## 4. Bug 3 — candidate directions

### Kernel-philosophy litmus test (applied first)

`KERNEL_PHILOSOPHY.md`: *mechanism, not policy; if two teams could reasonably want
different behavior, it's policy → keep it out of the kernel.*

"When a user has two instances of the same provider type, how is the second
instance's credential source named?" — two apps on `amplifier-core` could each want
something different: prompt for a custom name; auto-generate `NAME_2`; use an OS
keyring with instance-scoped entries; namespace by scope. **This is unambiguously
policy/UX.** The only kernel-worthy concern would be *"can an instance bind to an
arbitrary credential source?"* — and the answer is already **yes** (arbitrary `${VAR}`
in the config value, §3). So the kernel mechanism exists; only app-cli policy is
missing.

### (a) Kernel-level fix — extend `ConfigField`/`ProviderInfo`

Add a templated/interpolatable env-var concept to `ConfigField` (amplifier-core), which
app-cli fills per instance.

- **Category error.** `ConfigField` is the **type** schema, baked into module source
  and shared by every instance. Per-instance data does not belong there; you would
  still need to store the actual per-instance value somewhere else.
- **Redundant with an existing mechanism.** Runtime already honors an arbitrary
  per-instance placeholder (§3). The kernel change buys nothing the config value
  can't already express.
- **Cross-repo blast radius.** Touches the core contract + the `.proto` + every
  provider module that declares `ConfigField`, plus propagation/version awareness —
  for zero functional gain.
- **Verdict: rejected.** Violates "mechanism not policy," "additive/boring kernel,"
  and "two-implementation rule."

### (b) App-cli-only fix — per-instance placeholder (RECOMMENDED)

Keep `ConfigField.env_var` as a *default hint*. Do per-instance namespacing entirely
in app-cli:

- On a same-type credential collision, resolve a **distinct real env-var name** for
  this instance (smart default; exact-name prefill; explicit prompt — see §5.2).
- Write `${THAT_NAME}` into the instance's config value and `save_key(THAT_NAME, …)`.
- Runtime resolution (§3) then reads each instance's own placeholder — no collision.

Two sub-variants for *where the name lives*:

- **(b1) Placeholder is the single source of truth (CHOSEN).** The name is carried by
  the config value `${THAT_NAME}` and recovered by parsing on edit. No new field
  anywhere. Text-first, inspectable, zero drift risk (`grep api_key settings.yaml`
  shows exactly which env var each instance reads).
- **(b2) Explicit sidecar field** (e.g. `config.api_key_env: ANTHROPIC_FABLE_API_KEY`
  alongside `api_key: ${...}`). More self-describing but **duplicates** the name
  already in the placeholder → drift risk (which wins if they disagree?) and a new
  key to define/validate. Rejected in favor of (b1); the placeholder is already the
  contract the runtime reads.

### (c) Auto-derived name, no prompt

Same as (b) but derive the name silently (`ANTHROPIC_API_KEY` → `ANTHROPIC_FABLE_API_KEY`
from the id) with no confirmation. Lower friction, but can bind a secret to a name the
user didn't choose and won't think to set in CI. **Adopted only as the *default* inside
(b)'s prompt**, never as a silent auto-commit.

### (d) Instance-scoped credential store

Re-key `KeyManager`/`keys.env` by `(instance_id, field)` instead of by env-var name.

- Breaks assumption 2: credentials would no longer be plain named env vars, so a user
  who exports `ANTHROPIC_FABLE_API_KEY` in their shell / CI / DTU passthrough would no
  longer be picked up. Breaks ecosystem interop for zero benefit over (b).
- Larger change to a working store. **Rejected.**

---

## 5. Recommended design (option b1) — concrete spec

### 5.1 Data model (no schema additions)

An instance's credential binding **is** its config placeholder. Example end state:

```yaml
config:
  providers:
    - module: provider-anthropic
      id: anthropic-opus          # first same-type instance → keeps the type default
      config:
        api_key: ${ANTHROPIC_API_KEY}
    - module: provider-anthropic
      id: anthropic-fable         # second → its own name
      config:
        api_key: ${ANTHROPIC_FABLE_API_KEY}
```

`keys.env` (flat, `name → value`, unchanged mechanism):

```
ANTHROPIC_API_KEY="sk-ant-…opus…"
ANTHROPIC_FABLE_API_KEY="sk-ant-…fable…"
```

Distinct names ⇒ no collision. Real shell env vars still win over `keys.env`
(`key_manager.py:28`), preserving CI / DTU passthrough.

### 5.2 Wizard flow for the "second same-type instance" case

Ordering matters: **decide the credential name before running the field wizard**, so
the secret is saved and placeholdered under the right name in one pass.

Collision detection + name resolution live in the **callers** (`_manage_add_provider`,
`provider_add`) — they know instances and scope. `configure_provider` stays
mechanism-only: it uses whatever name it is handed. (Mechanism/policy split *within*
app-cli.)

New helpers (new module-private functions in `provider_config_utils.py`,
unit-testable):

```python
def _claimed_env_vars(settings: AppSettings) -> set[str]:
    """Env-var names already referenced by ${VAR} config values, across ALL scopes
    (global, project, local, session). See §5.4.1 for the real multi-scope
    implementation and why it must NOT copy the silent-except pattern used by
    AppSettings.get_provider_overrides() (settings.py:382-383).
    Tolerates literal (non-placeholder) values → they claim nothing."""

def _secret_env_var_for(module_id: str) -> str | None:
    """Default env var of the provider type's secret ConfigField
    (field_type == 'secret'), i.e. the collision-prone name."""

def _suggest_instance_env_var(module_id: str, instance_id: str,
                              claimed: set[str]) -> str:
    """<TYPE_PREFIX>_<ID-SUFFIX>_API_KEY, NFC-normalized then sanitized to
    ^[A-Z_][A-Z0-9_]*$, de-duplicated against `claimed`. E.g.
    (anthropic, anthropic-fable) → ANTHROPIC_FABLE_API_KEY.
    Raises ValueError if the sanitized ID-SUFFIX is empty or collides with an
    existing suggestion after normalization — see §5.4.2. Must fail loudly here,
    never emit an invalid or re-colliding name."""
```

Flow in the add paths, after the type + id are chosen:

1. `claimed = _claimed_env_vars(settings)` (all scopes; loud-warns/raises on any
   unparseable scope file instead of silently under-counting — §5.4.1).
2. `default_name = _secret_env_var_for(module_id)`.
3. **First instance / name unclaimed** → `default_name`; **preserve today's UX**
   (no extra prompt). `env_var_overrides = {}` (empty ⇒ wizard uses type default).
4. **`default_name` already claimed** (collision path):
   a. Explain (exact copy — §6, Bug 2/UX): *"provider-anthropic already uses
      ANTHROPIC_API_KEY (instance 'anthropic-opus'). This instance needs its own
      credential source."*
   b. Compute `suggested = _suggest_instance_env_var(module_id, instance_id,
      claimed)`. **Fails loudly here** (re-prompt for a different id; CLI path
      `ctx.exit(1)`) if the id sanitizes to a degenerate/colliding suggestion
      (§5.4.2) — never silently proceeds with a bad name.
   c. **Exact-name prefill only** (a fuzzy cross-environment scan was proposed in v1
      and removed after council review — §5.4.3 records why): a single
      `os.environ.get(suggested)` lookup on the name just derived in (b). If set,
      prefill the prompt's default with it and note *"(ANTHROPIC_FABLE_API_KEY is
      already set in your environment)"*. This is a one-line dict lookup on a name
      we already computed — not a scan — so it carries no false-positive risk.
   d. `Prompt.ask("Env var for this instance's key", default=suggested)`, editable.
   e. **Validate**: matches `^[A-Z_][A-Z0-9_]*$` **and** not in `claimed` (else you
      recreate the collision) → re-prompt on failure.
   f. **Stale-credential check** (§5.4.4): if `key_manager.has_key(chosen_name)` is
      true and the hit did **not** come from the live-environment prefill in (c) —
      i.e. it's a `keys.env`-only leftover from a previously-removed instance — warn
      explicitly before the secret prompt runs: *"A stored credential already exists
      for ANTHROPIC_FABLE_API_KEY from a previous instance. It will be reused for
      this instance."* (Chosen behavior: warn-and-reuse — see §5.4.4 for the
      rejected alternative and rationale.)
   g. `env_var_overrides = {default_name: chosen_name}`.
5. Call `configure_provider(module_id, key_manager, env_var_overrides=env_var_overrides,
   scope=scope, …)`.

### 5.3 Threading the override through `configure_provider` / `_prompt_for_field`

New parameter (additive, default preserves current behavior):

- `configure_provider(..., env_var_overrides: dict[str, str] | None = None)`
  — `provider_config_utils.py:331`. Map is `{type_default_env_var: instance_env_var}`.
- Pass it into both `_prompt_for_field(...)` calls (`:431`, `:504`) and use it in the
  two `non_interactive` branches (`:421-423`, `:494-496`) — see §5.4.5 for the
  fail-loud contract that applies specifically to those branches.
- `_prompt_for_field(field, key_manager, collected_config, existing_config,
  env_var_overrides=None)` — `:211`. Change the single derivation point:

  ```python
  # was: env_var = field.get("env_var")           # :231
  declared = field.get("env_var")
  env_var  = (env_var_overrides or {}).get(declared, declared)
  ```

  Every downstream use (`save_key` at `:299`/`:322`, placeholder build at
  `:303`/`:306`/`:326`, auto-detect probe at `:237-238`) then flows through the
  resolved `env_var` unchanged. The "(Found in environment/keyring …)" hint
  (`:255-258`) now probes the **instance** name — correct by construction.

- **Edit/reconfigure must not reset to the type default.** `_manage_edit_provider`'s
  path into `configure_provider` must recover the instance's existing name from its
  stored placeholder (`existing_config[secret_field_id]` → strip `${…}`) and pass it
  as the override. Otherwise editing the second instance silently re-collides. This is
  the subtle, must-not-miss part of the fix — pin it with a test.

### 5.4 Failure-mode handling (fail loud, not silent) — council-mandated

Five edge cases raised in review. Each must fail loudly or warn explicitly — none may
be silently absorbed, since a silent wrong answer here reintroduces exactly the class
of bug this design exists to close.

#### 5.4.1 Cross-scope aggregation must be real, and must not swallow parse errors

No multi-scope "claimed env vars" merge exists today — every existing provider call
site (`provider.py:256`, `:411`, `:488`, `:559`, `:665`, `:824`) reads **one** scope
at a time via `get_scope_provider_overrides(scope)`. The one place that already walks
all four scopes is `AppSettings.get_provider_overrides()` (`settings.py:353-385`): it
iterates `global → project → local → session` (`:365-370`) and merges by
`_provider_key` (`:381`, via `_merge_provider_lists` `:387-418`).

`_claimed_env_vars` must mirror that iteration order — **but must not mirror its
error handling**. `get_provider_overrides()` silently swallows a corrupt scope file
(`except Exception: pass` at `:382-383`) — acceptable there because a corrupt file
degrades to "provider missing from that scope," a visible, recoverable state. For
`_claimed_env_vars`, the same silent swallow would **under-count** claimed names — a
corrupt project-scope file would hide an already-claimed `ANTHROPIC_API_KEY`, and the
wizard would then let a new instance claim it anyway, silently reintroducing Bug 3
through a different door.

Note also that `AppSettings._read_scope` (`settings.py:1202-1211`) already collapses
"file absent" and "file present but fails to parse" into the same return value (`{}`,
via its own `except Exception: return {}` at `:1210-1211`) — so a corrupt file and an
empty scope are indistinguishable to any caller that only inspects the return value.
`_claimed_env_vars` therefore cannot rely on catching exceptions from
`get_scope_provider_overrides` alone (it won't raise); it must detect the corrupt
case explicitly:

```python
def _claimed_env_vars(settings: AppSettings) -> set[str]:
    claimed: set[str] = set()
    for scope in ("global", "project", "local", "session"):
        path = settings._get_scope_path(scope)          # :1185-1200; may raise for
        if path is None or not path.exists():            # unset session scope — skip
            continue
        if path.stat().st_size > 0:
            raw = path.read_text(encoding="utf-8")
            try:
                parsed = yaml.safe_load(raw) or {}
            except Exception as e:
                console.print(
                    f"[red]⚠ {scope} settings file exists but failed to parse "
                    f"({e}). Skipping it would under-count in-use credential "
                    f"names and risk a silent collision — please fix or remove "
                    f"{path} before adding another same-type provider "
                    f"instance.[/red]"
                )
                raise
            providers = (parsed.get("config") or {}).get("providers", [])
            for p in providers if isinstance(providers, list) else []:
                if not isinstance(p, dict):
                    continue
                for v in (p.get("config") or {}).values():
                    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                        claimed.add(v[2:-1])
    return claimed
```

This reads the scope file directly (rather than through
`get_scope_provider_overrides`/`_read_scope`) specifically so a parse failure can be
detected and surfaced instead of silently collapsing to "no providers here."

**Follow-up (flagged, not blocking this fix):** `_read_scope`'s blanket
`except Exception: return {}` (`settings.py:1210-1211`) is itself a latent
silent-swallow that this design works around rather than fixes at the source.
Making `_read_scope` distinguish "absent" from "present but corrupt" for every
caller in the codebase is a wider-blast-radius change — tracked as a separate
follow-up, not bundled into this fix.

#### 5.4.2 Malformed / degenerate instance ids fail loudly at suggestion time

`_suggest_instance_env_var` NFC-normalizes (§6, Bug 1) then sanitizes the id into an
`ID-SUFFIX` token (`^[A-Z_][A-Z0-9_]*$`). Two ways this can silently misbehave if
unguarded:

- An id built only from non-ASCII/symbol characters (e.g. `"🎉🎉🎉"`, `"---"`)
  sanitizes to an **empty** suffix → would silently produce `ANTHROPIC__API_KEY` or
  collide with the bare type default.
- Two ids differing only in separator style (`"anthropic-fable"` vs
  `"anthropic_fable"` vs `"anthropic fable"`) sanitize to the **identical**
  suggestion → silently re-creates the exact collision this fix exists to prevent.

Both **must raise** `ValueError` from `_suggest_instance_env_var` rather than return
a degenerate or re-colliding string. The caller catches this and re-prompts for a
different instance id with an explicit message: *"Instance id 'X' doesn't produce a
usable credential variable name (it sanitizes to the same name as instance 'Y').
Please choose a more distinct id."*

#### 5.4.3 Why the fuzzy environment scan was cut (council debate resolution)

v1 of this design proposed scanning all of `os.environ` for names matching
`startswith(TYPE_PREFIX) or endswith("_API_KEY")`, minus `claimed`, and offering the
best match. Council review found this unsalvageable, not just imprecise:

- Even correcting the `or` to `and` doesn't close the false-positive class — e.g. for
  `TYPE_PREFIX="AZURE"`, an unanchored substring match still matches
  `AZUREOPENAI_API_KEY` or `AZURESTORAGE_API_KEY` for a plain `provider-azure`
  instance. Anchoring more strictly just moves the boundary, it doesn't remove it.
- Multi-match cases have no deterministic tiebreak (which of several matches wins?).
- It solves a case nobody actually named — "the user's credential exists under some
  *other*, unpredictable name" — at real risk: silently offering to bind an instance
  to an unrelated tool's secret (e.g. `STRIPE_API_KEY` happens to end in `_API_KEY`).

**Decision: cut entirely, not narrowed.** Kept: the zero-risk version — §5.2 step
4c's exact-name prefill, a single `os.environ.get(suggested_name)` lookup on the
name *we already derived*, not a scan across the environment. If real user demand
for fuzzy matching emerges later, it is a separately-justified, separately-designed
feature, not bundled into this fix.

#### 5.4.4 Orphaned-then-reused stale credential

Sequence: configure `anthropic-fable` (writes `ANTHROPIC_FABLE_API_KEY` to
`keys.env`) → remove the `anthropic-fable` instance (per §8 risk 6, the key is
deliberately *not* deleted on remove) → later, re-add an instance that lands on the
same suggested name. Two candidate behaviors, debated and resolved:

- **(chosen) Warn-and-reuse.** Detect via `key_manager.has_key(chosen_name)` when the
  hit did *not* come from the live-environment prefill (§5.2.4c) — i.e. it's a
  `keys.env`-only leftover — and warn explicitly: *"A stored credential already
  exists for ANTHROPIC_FABLE_API_KEY from a previous instance. It will be reused for
  this instance."* The user can Ctrl-C and pick a different name if that's wrong.
- **(rejected) Require fresh entry.** Force re-entry of the secret whenever the name
  is a stale-store hit, even when the user genuinely wants to reuse the same key for
  the same purpose (e.g. re-adding a provider they only briefly removed). More
  surprising for the common case, and a clear warning already gives the user
  everything they need to notice and correct a bad reuse — forcing re-entry adds
  friction without adding safety.

Chosen: **warn-and-reuse**, not silent-reuse, not forced-re-entry. The warning is the
load-bearing part — it must appear *before* the secret prompt's "(press Enter to keep
existing)" affordance, so the user isn't misled into thinking this is a first-time
entry.

#### 5.4.5 Non-interactive path never guesses

`configure_provider(..., non_interactive=True)` must **not** inherit any of the
smart-default/prefill/auto-derive behavior above. Its contract: take an explicit
`env_var_overrides` from the caller, or fail loudly.

```python
# provider_config_utils.py — non_interactive branches (:420-428, :493-501)
if non_interactive:
    declared = field.get("env_var")
    env_var = (env_var_overrides or {}).get(declared, declared)
    if (env_var == declared and declared in _claimed_env_vars(settings)
            and declared not in (env_var_overrides or {})):
        raise ValueError(
            f"Non-interactive configuration would reuse the same credential "
            f"env var ({declared}) as another configured instance. Pass an "
            f"explicit env_var_overrides mapping for this instance instead of "
            f"relying on the type default."
        )
    if env_var and os.environ.get(env_var):
        collected_config[field_id] = f"${{{env_var}}}"
    ...
```

Silent fallback to the type default in non-interactive mode is exactly Bug 3
reintroduced through the CI/scripted path — the one place a human isn't watching the
prompts to catch it. Fail loud, always, here. (Callers building configs
programmatically — e.g. a future `provider add --non-interactive` — must resolve
`env_var_overrides` themselves using the same `_claimed_env_vars`/
`_suggest_instance_env_var` helpers, or supply their own name; they cannot rely on
the wizard to guess for them.)

#### 5.4.6 Regression tripwire for §3

The entire design rests on §3's finding that no runtime path re-derives a credential
env-var name from `ConfigField.env_var`. A standing test guards against a future
contributor reintroducing a "helpful" kernel-level fallback that would silently
defeat this fix (see §9 for the concrete test).

### 5.5 Concurrency: file locking on both write paths (BLOCKING requirement)

Both persistent-write paths this design touches are naive read-modify-write with no
locking:

- `KeyManager.save_key` (`key_manager.py:38-61`) reads all of `keys.env` into a
  dict, mutates one key, and rewrites the whole file.
- `AppSettings._write_scope` (`settings.py:1213-1218`) — and every caller that does
  read-then-mutate-then-`_write_scope` (e.g. `provider_add:306-321`,
  `_manage_add_provider:1018-1023`, `set_provider_override:429-450`) — has the same
  shape.

Two ordinary, non-adversarial concurrent `amplifier init` / `provider add` runs (two
terminals, or a script racing a human — assumption 5) silently clobber each other's
write: last writer wins, first writer's change vanishes with no error. This predates
Bug 3 but is *directly relevant* to it: this design's fix widens the race window per
operation (`_claimed_env_vars` now reads all-scopes state, then the flow writes both
`keys.env` and a scope file) rather than narrowing it. Council treated this as a
**correctness requirement**, not a nice-to-have, to be fixed in the same change.

**Mechanism:** advisory file locking around each read-modify-write critical section,
using [`filelock`](https://pypi.org/project/filelock/) (new dependency). Raw
`fcntl.flock` is POSIX-only and this codebase explicitly supports Windows
(`key_manager.py:64-66`: `platform.system() != "Windows"` before `chmod`), so a
cross-platform library is required, not optional. `filelock` is the de facto
standard for this in the Python ecosystem (used by `pip`/`uv` themselves), is a
single, mature, ~zero-transitive-dependency addition to `pyproject.toml`.

```python
# key_manager.py
from filelock import FileLock

class KeyManager:
    def __init__(self):
        self.keys_file = Path.home() / ".amplifier" / "keys.env"
        self._lock = FileLock(str(self.keys_file) + ".lock", timeout=10)
        self._load_keys()

    def save_key(self, key_name: str, key_value: str) -> None:
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:                       # serializes the whole RMW below
            existing_keys: dict[str, str] = {}
            if self.keys_file.exists():
                with open(self.keys_file, encoding="utf-8") as f:
                    for line in f:              # unchanged parse, was :45-50
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            existing_keys[k.strip()] = v
            existing_keys[key_name] = f'"{key_value}"'

            fd, tmp = tempfile.mkstemp(dir=self.keys_file.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write("# Amplifier API Keys\n")
                    f.write("# Auto-generated by amplifier init or amplifier provider use\n")
                    f.write("# These are loaded automatically on startup\n\n")
                    for k, v in existing_keys.items():
                        f.write(f"{k}={v}\n")
                os.replace(tmp, self.keys_file)     # atomic swap, survives a crash
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

            if platform.system() != "Windows":
                self.keys_file.chmod(0o600)
            os.environ[key_name] = key_value
```

```python
# settings.py
from filelock import FileLock

class AppSettings:
    def _scope_lock(self, scope: Scope) -> FileLock:
        path = self._get_scope_path(scope)
        return FileLock(str(path) + ".lock", timeout=10)

    def _write_scope(self, scope: Scope, settings: dict[str, Any]) -> None:
        """Atomic write: tmp-then-replace, matching this codebase's
        config-state-patterns convention, independent of the lock below."""
        path = self._get_scope_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(settings, f, default_flow_style=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
```

Callers wrap the *entire* read-check-write sequence in the lock — locking only the
final `_write_scope` call and not the read that precedes it does not close the race:

```python
# provider.py add paths (both _manage_add_provider and provider_add), per §5.2/§6
with settings._scope_lock(scope):
    scope_settings = settings._read_scope(scope)
    scope_providers = (scope_settings.get("config") or {}).get("providers", [])
    # ... id-uniqueness check (§6, Bug 1) against scope_providers happens here,
    # inside the lock, so a concurrent writer can't sneak in a colliding id
    # between the check and the write ...
    scope_settings.setdefault("config", {})["providers"] = scope_providers
    settings._write_scope(scope, scope_settings)
```

**Scope of the lock fix:** apply to the specific read-modify-write sequences this
design's new/changed code paths exercise — `KeyManager.save_key` and the
add-provider callers' read-check-write around `_write_scope`. Locking *every*
`AppSettings` mutator (`_update_setting`, `set_provider_override`, module overrides,
etc.) is the same mechanical pattern and should eventually be applied everywhere for
full correctness, but is called out as a **follow-up sweep** so this change stays
reviewable; the two paths this design adds new writers to are non-negotiable and in
scope now.

**Alternative considered:** POSIX-only `fcntl.flock`, no new dependency. Rejected —
this codebase explicitly supports Windows; `filelock` abstracts the platform
difference at negligible cost and is the lower-risk choice.

### 5.6 What does *not* change

- `KeyManager`'s data model — untouched (distinct names already work; only its write
  path gains locking + atomic replace, §5.5).
- `amplifier-core` — untouched. Optional doc-only clarification: note in the
  `ConfigField.env_var` docstring that it is the **default** hint and runtime honors
  the instance placeholder. No code/proto change.
- Runtime resolution (`expand_env_vars`, `_resolve_env_placeholder`) — untouched
  (and now guarded by the §5.4.6/§9 tripwire against ever changing this).

---

## 6. Bug 1 & Bug 2 — concrete fixes

Both are mechanical; mirror established patterns. Do them in the **same** change so
the uniqueness check runs against the correct (scope-selected, lock-protected) list.

### Bug 1 — id uniqueness (mirror `_provider_key`, `merge_utils.py:52-58`) + Unicode normalization

In `_manage_add_provider` (after id prompt `:966-976`, before append `:1020`) and
`provider_add` (after `:260-263`, before append `:310`), inside the `_scope_lock`
critical section from §5.5:

```python
import unicodedata
from amplifier_app_cli.lib.merge_utils import _provider_key

def _normalize_id(value: str) -> str:
    """NFC-normalize so visually-identical ids differing only in Unicode
    composition (e.g. precomposed 'é' U+00E9 vs. combining 'e' + U+0301) compare
    equal. Without this, the uniqueness check below is defeated by construction:
    two ids that render identically in a terminal, and that a user would
    reasonably believe are 'the same id', are treated as distinct byte strings.
    Copy-paste from a document or a different OS's clipboard is a realistic path
    to a decomposed form, not a contrived edge case."""
    return unicodedata.normalize("NFC", value)

new_key = _normalize_id(instance_id or module_id)
if any(_normalize_id(_provider_key(p)) == new_key for p in scope_providers):
    console.print(f"[red]An instance with id '{new_key}' already exists "
                  f"in {scope} scope.[/red]")
    # interactive: re-prompt for a new id; CLI provider_add: ctx.exit(1)
```

- Hard-block a same-scope duplicate key.
- Soft-warn (not block) if the id exists in a *different* scope — cross-scope
  shadowing can be intentional (override semantics). Keep it a one-line warning.
- The same `_normalize_id` is reused by `_suggest_instance_env_var` (§5.4.2) before
  comparing sanitized suggestions, so two ids that are Unicode-distinct but
  canonically identical can't slip past *either* the id-uniqueness check or the
  credential-name-collision check.
- **Pin with a specific test** (§9): an id submitted as NFD (`"cafe\u0301"`) must be
  rejected as colliding with an existing NFC id (`"café"`) even though the raw
  strings differ byte-for-byte.

### Bug 2 — thread the write scope (mirror `edit`/`reorder` + `provider edit --scope`)

- `_manage_add_provider(settings: AppSettings, scope: Scope = "global") -> None`
  (`:932`). Pass the dashboard-selected scope from the manage loop (same call site
  that already passes `scope` to `_manage_edit_provider`/`_manage_reorder_providers`).
  Replace hardcoded `get_scope_provider_overrides("global")` (`:1018`), the
  `_read_scope`/`_write_scope("global", …)` writes, and the unconditional "saved to
  global" confirmation with `scope`.
- `provider_add`: add a Click option mirroring `provider edit`:
  `@click.option("--scope", default="global", type=click.Choice(["global","project","local"]))`
  and thread it through `:306`, `:317-321`, and the confirmation `:327`.
- **Exact confirmation copy** (both paths, council-requested — §8 UX): replace the
  unconditional `f"\n[green]✓ Provider added: {name_display}{model_display}[/green]"`
  (`:327`) and its counterpart at `:1017` with a scope-qualified message:
  `f"\n[green]✓ Provider added: {name_display}{model_display} "
  f"(saved to {scope} settings)[/green]"` — so the confirmation is never wrong about
  *where* it wrote, matching what the dashboard's `[w]` banner told the user before
  they started.

---

## 7. Tradeoff analysis (recommended = b1)

| Dimension | (a) Kernel template | **(b1) Per-instance placeholder** | (d) Instance keystore |
|-----------|---------------------|-----------------------------------|-----------------------|
| Latency | n/a | n/a | n/a |
| Complexity | High — cross-repo contract + proto + every module | **Low — one derivation point + caller policy; no new schema** | Med — new store schema + migration |
| Reliability | Med — new contract, propagation risk | **High — reuses proven placeholder+expand path** | Med — reimplements a working store |
| Security | Neutral | **Good — secrets stay in `keys.env` 0600; distinct names, no cross-instance leak** | Neutral |
| Scalability | Poor — every provider module must adopt | **Good — works for N instances, any provider, no per-module work** | Good |
| Reversibility | Poor — kernel contract is sticky | **High — app-cli-local; revert without ecosystem coordination** | Med |
| Org fit | Poor — kernel PR bar, multi-repo | **Excellent — single repo, mirrors existing patterns** | Med |
| Ecosystem interop | Neutral | **Excellent — real env vars compose with shell/CI/DTU passthrough** | **Poor — breaks passthrough-by-name** |
| **Optimizes for** | contract purity (misplaced) | **minimal, reversible, policy-at-edge fix** | central credential ownership |
| **Sacrifices** | huge blast radius for no gain | a tiny bit of magic (name derived, not declared) | ecosystem env-var interop |

Dominant tradeoff: **complexity/reversibility/interop vs. a small loss of
explicitness.** (b1) wins decisively; the lost explicitness is recovered by the
placeholder itself being human-readable in `settings.yaml`.

The §5.4/§5.5 hardening (fail-loud edge cases, file locking, Unicode normalization)
is orthogonal to this table — it doesn't change which direction wins, it closes gaps
in *how b1 is implemented* that the council found during review.

---

## 8. Risks, failure modes, and "what would make this wrong"

1. **§3 is false** (a resolution path re-derives from `ConfigField.env_var`). Would
   break b1 and revive (a)/(b2). *Mitigation:* top review target; DTU end-to-end test
   asserting two instances resolve two different keys, plus a standing regression
   tripwire test (§5.4.6, §9). **This is the one assumption the whole design rests
   on.**
2. **Edit path re-collision** (§5.3) — editing instance 2 resets to the type default
   and silently re-collides. *Mitigation:* recover name from stored placeholder;
   pinned test.
3. **Auto-detect false positive** — *resolved by design, not just mitigated.* The v1
   fuzzy environment scan that created this risk was cut entirely (§5.4.3); the only
   remaining environment probe is the exact-name prefill (§5.2.4c), a single lookup
   on a name we already derived, which has no false-positive surface.
4. **Literal (non-placeholder) credential values** — a hand-edited config with a raw
   key claims no env var. *Mitigation:* `_claimed_env_vars` tolerates and skips
   non-`${…}` values; such an instance simply doesn't participate in collision maths.
5. **Pre-existing duplicate configs in the wild** — not repaired by this change.
   *Mitigation:* detect-and-warn when the wizard encounters an already-claimed
   default; file a separate `provider doctor` migration follow-up (out of scope
   here).
6. **Orphaned keys on remove** — removing instance 2 leaves `ANTHROPIC_FABLE_API_KEY`
   in `keys.env`. *Mitigation:* deliberately not auto-deleted (a shared secret may
   still be a real shell var elsewhere); if a later instance lands on the same name,
   §5.4.4's warn-and-reuse makes the reuse explicit rather than silent.
7. **Non-interactive path silently guessing** — *resolved by design.* §5.4.5 makes
   this a hard `ValueError` rather than a "should probably be careful here" note:
   the non-interactive branches never fall back to the type-default name on a
   detected collision.
8. **Concurrent writes clobbering each other** — two ordinary concurrent CLI
   invocations racing on `keys.env` or a scope's `settings.yaml`. *Mitigation:*
   `filelock`-based advisory locking around both read-modify-write paths, atomic
   tmp-then-replace writes (§5.5). Treated as blocking, not deferred.
9. **Unicode-canonically-identical ids defeating the uniqueness check.**
   *Mitigation:* NFC normalization on both sides of every id-equality comparison
   (§6, Bug 1), reused by the credential-name suggestion logic (§5.4.2).
10. **Malformed/degenerate ids producing an invalid or re-colliding env-var
    suggestion.** *Mitigation:* `_suggest_instance_env_var` raises loudly instead of
    returning a bad name (§5.4.2).
11. **A corrupt scope file silently under-counting claimed credential names,**
    reopening Bug 3 through the cross-scope aggregation path itself. *Mitigation:*
    `_claimed_env_vars` detects non-empty-but-unparseable scope files and raises
    rather than silently treating them as "no providers here" (§5.4.1).

Monitoring signals the design is failing: users reporting "second provider uses the
wrong key," duplicate `id` entries in `settings.yaml`, a `keys.env` with a single
credential shared by two same-type instances, or one of two concurrent `provider add`
runs silently vanishing from `settings.yaml`.

---

## 9. Test plan (mirror `tests/test_provider_commands.py`)

- **Bug 1:** adding a colliding `id` in a scope is blocked/re-prompted; no duplicate
  in the per-scope YAML. Cross-scope same id → warning, not block. **Unicode case:**
  an id submitted as NFD (`"cafe\u0301"`, i.e. `c-a-f-e` + combining acute accent)
  must be rejected as colliding with an existing NFC id (`"café"`, precomposed
  U+00E9) even though the two are different byte sequences.
- **Bug 2:** add with `--scope project` (CLI) and dashboard `[w]`→project
  (interactive) writes to the project scope file and the confirmation names
  "project" verbatim (`"...saved to project settings..."`); global untouched.
- **Bug 3 unit:** `_claimed_env_vars`, `_secret_env_var_for`, `_suggest_instance_env_var`
  (derivation, sanitization, de-dup, and the `ValueError` cases from §5.4.2 — empty
  suffix, separator-style collision). `_prompt_for_field` with `env_var_overrides`
  saves under and placeholders with the overridden name.
- **Bug 3 integration:** configure `anthropic-opus` then `anthropic-fable`; assert
  two distinct `keys.env` entries and two distinct `${VAR}` placeholders. **Edit
  `anthropic-fable`, press Enter to keep — assert it still points at
  `ANTHROPIC_FABLE_API_KEY`** (re-collision guard).
- **Bug 3 end-to-end (DTU):** two same-type instances resolve to two different
  actual key values at session load (proves §3 and the whole fix from the user's
  perspective).
- **Exact-name prefill (§5.4.3 replacement):** with `ANTHROPIC_FABLE_API_KEY` set in
  the test environment and unclaimed, the collision-path prompt's default is
  prefilled with it; with no such var set, the default is the plain derived
  suggestion. No test should assert on fuzzy/pattern-matched suggestions — that
  behavior no longer exists.
- **Stale-credential warn-and-reuse (§5.4.4):** pre-seed `keys.env` with
  `ANTHROPIC_FABLE_API_KEY` (simulating a previously-removed instance, with the var
  *not* set in the live environment), then add a new colliding instance; assert the
  warning is shown before the secret prompt and the stale value is reused if the
  user accepts.
- **Cross-scope aggregation robustness (§5.4.1):** a syntactically-corrupt (but
  non-empty) project-scope YAML file causes `_claimed_env_vars` to raise/warn
  loudly, not silently return an empty set.
- **Non-interactive fail-loud (§5.4.5):** calling `configure_provider(...,
  non_interactive=True)` for a second same-type instance without an explicit
  `env_var_overrides` raises `ValueError`; supplying the override succeeds and
  produces the expected distinct placeholder.
- **Concurrency (§5.5):** two threads/processes calling `KeyManager.save_key` with
  different key names concurrently must not lose either write (assert both present
  after both complete); same shape for two concurrent add-provider scope writes.
- **Regression tripwire (§5.4.6) — pins the §3 assumption:**

  ```python
  def test_no_kernel_level_env_var_rederivation():
      """If this test starts failing, someone has reintroduced a runtime path
      that re-derives a credential env-var name from ConfigField.env_var /
      get_provider_info() instead of reading the instance's own ${VAR}
      placeholder. That would silently defeat every per-instance credential
      binding this design creates — see docs/designs/provider-instance-credentials.md §3."""
      forbidden = ("get_provider_info", "ConfigField", "credential_env_vars")
      for rel_path in ("amplifier_app_cli/runtime/config.py",
                       "amplifier_app_cli/provider_loader.py"):
          text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
          for name in forbidden:
              assert name not in text, (
                  f"{rel_path} references {name!r} — this looks like a "
                  f"kernel-level env-var re-derivation creeping into the "
                  f"runtime resolution path (see §3, §5.4.6)."
              )
  ```

  A grep-based check rather than a behavioral one deliberately: the whole point is
  to catch the re-derivation *before* it ships, at review time, not after it's
  already live and silently wrong in production.

---

## 10. Simplest credible alternative

Fix **only** the save/placeholder collision: on any same-type add, auto-derive a
distinct name from the id (option c, silent), no prompt, no exact-name prefill.
Fewer lines. Rejected as the primary because it binds secrets to names the user
never chose (bad for CI/shell reproducibility) and gives no chance to reuse an
already-set real env var. It is, however, the exact *default* inside the recommended
prompt — so the recommended design degrades gracefully to it if the user just
presses Enter. It does **not**, however, get to skip the §5.4 fail-loud requirements
or §5.5 locking — those are correctness requirements independent of which naming UX
is chosen.

---

## 11. One-paragraph recommendation

Fix all three in `amplifier-app-cli` only. For Bug 3, treat the instance's config
placeholder as the per-instance credential binding it already is (verified §3):
thread an `env_var_overrides` map from the add paths (which own the
collision-detection and naming *policy*) into `configure_provider`/
`_prompt_for_field` (which stay *mechanism*), so the second same-type instance saves
under and points at its own real env var. No `amplifier-core` change, no
`KeyManager` schema change — this is UX/policy, which `KERNEL_PHILOSOPHY.md` places
at the edge, not in the kernel. Fold in the Bug 1 `_provider_key` uniqueness check
(Unicode-normalized) and the Bug 2 `scope` threading in the same change so the
uniqueness check runs against the correct scope. Treat file locking on both write
paths (§5.5) and the fail-loud edge-case handling (§5.4) as blocking parts of this
change, not follow-ups — they close gaps that would otherwise reintroduce silent,
Bug-3-shaped failures through a different door.

---

## 12. Revision history

**v2 (this revision)** — incorporates the resolution of a 6-lens adversarial council
review (intent-keeper, cranky-old-sam, crusty-old-engineer, restless-old-brian,
user-advocate, tester-breaker; all six returned CONCERN, direction confirmed sound).
Changes from v1:

- **§5.2/§5.4.3 — simplified the auto-detect UX.** Split the original single
  "auto-detect" idea into (1) exact-name prefill on the derived suggestion (kept,
  zero risk) and (2) fuzzy cross-environment pattern scanning (deleted entirely —
  even `and`-ing the prefix/suffix conditions doesn't close the false-positive
  class, e.g. `AZURE*` unanchored-matching `AZUREOPENAI_API_KEY`/
  `AZURESTORAGE_API_KEY`; no deterministic tiebreak on multi-match; solves an
  unrequested case at real risk of binding to an unrelated tool's secret).
- **§5.5 — added file locking as a BLOCKING requirement** (new in v2). Both
  `KeyManager.save_key` and the `AppSettings` scope read-modify-write are naive and
  unlocked; two ordinary concurrent CLI invocations can silently clobber each
  other. Added `filelock`-based locking plus atomic tmp-then-replace writes as a
  correctness requirement, not a nice-to-have.
- **§6, Bug 1 — added NFC Unicode normalization** (new in v2) to the id-uniqueness
  comparison (and reused it in the credential-name-suggestion comparison, §5.4.2).
  Without it, visually-identical ids differing only in Unicode composition defeat
  the uniqueness check the fix exists to add.
- **§5.4.1 — added a real cross-scope aggregation implementation** (new in v2; none
  existed before) for `_claimed_env_vars`, explicitly *not* copying the silent
  `except Exception: pass` pattern used elsewhere in `settings.py`, since silently
  under-counting claimed names here reopens Bug 3 through a different door.
- **§5.4.2 — added fail-loud handling for malformed/degenerate ids** (new in v2):
  ids that sanitize to an empty or mutually-colliding suggested env-var name now
  raise instead of silently producing a bad name.
- **§5.4.4 — added a documented, chosen policy for stale credential reuse** (new in
  v2): warn-and-reuse, not silent-reuse or forced-re-entry, when a same-id
  re-add lands on a leftover `keys.env` entry from a previously-removed instance.
- **§5.4.5 — added an explicit non-interactive fail-loud contract** (new in v2): the
  non-interactive path must never silently fall back to the type-default name on a
  detected collision; it now raises unless the caller supplies an explicit
  `env_var_overrides`.
- **§5.4.6/§9 — added a standing regression tripwire test** (new in v2) pinning the
  §3 assumption the entire design rests on, so a future contributor can't
  accidentally reintroduce a kernel-level env-var re-derivation that would silently
  defeat this fix.
- **§6 — added exact UX copy** (new in v2) for the collision-explanation prompt and
  the scope-qualified "saved to X settings" confirmation, so both are specified,
  not left to implementation-time judgment.
- Everything else (the §3 verification, the b1-vs-a-vs-d direction, the b1 data
  model, the core wizard flow shape) is unchanged from v1 — the council confirmed
  the direction, and revised the implementation details.
