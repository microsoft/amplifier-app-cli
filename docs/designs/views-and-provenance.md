# Views and Provenance

**Status:** Approved, in flight.
**Scope:** `amplifier-foundation` (data model) + `amplifier-app-cli` (rendering).
**Strategy:** Retcon, no back-compat, no phased migration. Single coordinated drop.

---

## 1. Summary

Two coupled changes:

1. **Foundation** gains a unified origin/provenance data model that answers
   "what brought this in?" for every item the dashboard renders, joined with
   the existing bundle include graph at query time.
2. **App-cli** gains a single `ItemRenderer` with three view modes
   (`compact` / `regular` / `detailed`) and uniform flags
   (`--compact`, `--detailed`, `--format`). Every list/show site in the CLI
   adopts it. The 21 bespoke rendering implementations are deleted.

The two-graph principle is preserved: the **behavior-merge graph**
(inside merged Bundles) and the **bundle-on-disk include graph**
(`BundleRegistry`) stay as separate structures. They are joined only at the
render boundary, never denormalized into each other.

---

## 2. Out of Scope (Documented Gaps)

Provenance lineage ends at one of four runtime injection paths. For these,
the chain terminates at the injection event, not the original author:

| Injection path | What we record |
|---|---|
| `tool-task` agent spawning | "Injected at runtime by tool-task" |
| MCP dynamic tool registration | "Injected at runtime by MCP server `<name>`" |
| Hook-driven context/tool mutation | "Injected by hook `<id>` during session" |
| Mid-session `tool-skills` loads | "Loaded by skills tool at turn N" |

These are explicit terminators in the data model. Not "we don't know."

Also out of scope: graph visualizations that are not list/show
(`session list --tree`, `source show` waterfall, `module validate`
output). These keep their bespoke renderers; documented in §6 as exceptions.

---

## 3. Foundation Changes (`amplifier-foundation`)

### 3.1 New types in `amplifier_foundation.configurator`

```python
@dataclass(frozen=True)
class Origin:
    """One claim on an item, attached to its merge-step parent."""
    bundle: str                  # bundle name that owns the claim
    via_behavior: str | None     # immediate parent in the merge graph; None = self-introduced


@dataclass(frozen=True)
class IncludeStep:
    """One link in the bundle-on-disk include graph."""
    bundle: str
    version: str | None
    uri: str | None


@dataclass(frozen=True)
class ItemRecord:
    """Foundation/app contract. The only thing the renderer consumes."""
    category: Literal["provider","tool","hook","agent","context","behavior",
                      "session.orchestrator","session.context","spawn","instruction",
                      "skill","mode"]
    name: str
    enabled: bool
    module_id: str | None
    source_uri: str | None
    config_summary: dict[str, Any]
    origins: list[Origin]                  # merge-graph chain (behaviors)
    include_path: list[IncludeStep]        # disk-graph chain (bundles), root first
    runtime_injection: Literal["static","mode","hook","skills","mcp","task"] | None
```

`include_path` is computed at query time by walking `BundleRegistry._registry`.
It is not stored on the Bundle.

### 3.2 `Bundle._provenance` → `Bundle.origins`

- Rename. Public field, no underscore.
- Type changes from `dict[str, list[str]]` to `dict[str, list[Origin]]`.
- Single writer: `_prov_add` in `bundle/_provenance.py`. List preserves
  insertion order; no `step` field.
- Phase-2 overlay in `track_provenance` sets `via_behavior = other.name`
  for each entry it propagates.
- All call sites updated in the same PR. No shim, no alias.

### 3.3 Cover the three missing static categories

Wrap each of these in the existing `capture_existing_ids` / `track_provenance`
snapshot/diff pattern (already used for tool/hook/provider/agent/context):

| Call site | New `origins` keys |
|---|---|
| `bundle/_dataclass.py:179` (session deep_merge) | `session.orchestrator:<id>`, `session.context:<id>` |
| `bundle/_dataclass.py:183` (spawn deep_merge) | `spawn:<key>` per top-level key |
| `bundle/_dataclass.py:215-216` (instruction) | `instruction:` |

### 3.4 Replace the lossy fuzzy match

Delete `_lookup_prov_behavior` fuzzy logic in
`configurator/_provenance_utils.py:90-145`.

Replace with a deterministic `module_id → exported_names` map published by
`ModuleActivator.activate_all` (modules/activator.py). The activator already
knows which tools a module registers; it just doesn't expose that map.

Add `PreparedBundle.module_exports: dict[str, list[str]]` so the inspector
can look up directly: `tool_name "grep"` → `module_id "tool-search"` →
origins for `tool:tool-search`.

### 3.5 Fold `RuntimeOverlay._owned` into `origins`

When `RuntimeOverlay.apply()` adds an agent/context/skill, write an `Origin`
entry with `bundle = "mode:<mode-name>"` and
`runtime_injection = "mode"` on the corresponding `ItemRecord`.

Remove the parallel `_owned` table. Single source of truth.

### 3.6 Delete the `behaviors`/`source` field duplication

Currently `BundleInspector.tools_list()` returns both `"behaviors"` and
`"source"` keys with the same value. Pick one (`origins`, returned as
`list[Origin]`) and delete the other. Update inspector callers in app-cli.

### 3.7 BundleInspector public API

The six `*_list()` methods now return `list[ItemRecord]`, not
`list[dict]`. `behaviors_list()` returns
`list[ItemRecord]` with `category="behavior"` and `origins` populated.

---

## 4. App-cli Changes (`amplifier-app-cli`)

### 4.1 New module: `amplifier_app_cli/ui/item_renderer.py`

```python
class ItemRenderer:
    def __init__(self, console: Console): ...

    def render(
        self,
        items: list[ItemRecord],
        *,
        view: Literal["compact","regular","detailed"],
        format: Literal["text","json"] = "text",
        section_title: str | None = None,
    ) -> None: ...

    def render_one(
        self,
        item: ItemRecord,
        *,
        view: Literal["compact","regular","detailed"] = "detailed",
        format: Literal["text","json"] = "text",
    ) -> None: ...
```

Three view shapes:

- **compact** — one line: `[on] name  <root-bundle>`
- **regular** — three to five lines: status, module_id, behavior chain,
  source URI, redacted config summary
- **detailed** — full record: origins chain rendered as
  `root-bundle → behavior-x → behavior-y → item`, full `include_path`,
  all config, runtime_injection annotation if non-None

`--format json` serializes the `ItemRecord` via `dataclasses.asdict`.
The JSON shape **is the public schema** from the moment it ships.

### 4.2 New module: `amplifier_app_cli/ui/view_policy.py`

Single table of default view modes:

```python
DEFAULT_VIEW = {
    ("config", "show", None):       "compact",   # /config show – multi-category, tight
    ("config", "show", "category"): "regular",   # /config show <category>
    ("config", "show", "item"):     "detailed",  # /config show <category> <item>
    ("bundle", "list"):             "compact",
    ("bundle", "show"):             "detailed",
    ("module", "list"):             "compact",
    ("module", "show"):             "detailed",
    ("provider", "list"):           "regular",
    ("tool", "list"):               "compact",
    ("tool", "info"):               "detailed",
    ("source", "list"):             "compact",
    ("session", "list"):            "compact",
    ("session", "show"):            "detailed",
    ("routing", "list"):            "regular",
    ("routing", "show"):            "detailed",
    ("agents", "list"):             "compact",
    ("agents", "show"):             "detailed",
    ("module", "override", "list"): "compact",
}
```

`--compact` / `--detailed` flags override the policy.
`--format json` bypasses view-mode formatting entirely.

### 4.3 Sites being migrated (all in this PR)

| Site | Old impl | New default view |
|---|---|---|
| `/config show` | `_render_config_dashboard` | regular (per category) |
| `/config <category>` | `_render_config_category` | regular |
| `/config show <category> <item>` | (new) | detailed |
| `bundle list` (default + `--all`) | `commands/bundle.py:97-250` | compact |
| `bundle show` | `commands/bundle.py:300-395` | detailed |
| `module list` | `commands/module.py:45-123` | compact |
| `module show` | `commands/module.py:126-151` | detailed |
| `module override list` | `commands/module.py:889-932` | compact |
| `provider list` | `commands/provider.py:335-445` | regular |
| `tool list` | `commands/tool.py:262-343` | compact |
| `tool info` | `commands/tool.py:346-421` | detailed |
| `source list` | `commands/source.py:434-490` | compact |
| `routing list` | `commands/routing.py:226-285` | regular |
| `routing show` | `commands/routing.py:329-407` | detailed |
| `agents list/show/dirs` | `commands/agents.py` | compact / detailed |
| `/tools`, `/agents`, `/skills`, `/modes` slash commands | `main.py` bespoke | compact |

### 4.4 Flags added uniformly

Every command above accepts:

- `--compact` — force compact view
- `--detailed` — force detailed view
- `--format [text|json]` — output channel (default `text`)

Where existing command-specific flags exist (e.g. `bundle list --all`,
`session list --tree`, `module list --type`), they stay. They modify
*what is queried*, not *how it is rendered*.

### 4.5 Sites NOT migrated (documented exceptions)

- `session list --tree` — graph view, fundamentally different shape
- `source show` — 6-step resolution waterfall, not a list/show
- `module validate` — validator output, not items
- `tool invoke` — invocation result, not list/show
- `_render_legacy_config` fallback — to be **deleted**, not preserved

### 4.6 Cleanup deletions in app-cli

- `_render_legacy_config` (`main.py:1491+`) — dead with new path
- `_render_bundle_config` (`main.py:1513`) — same
- All backward-compat wrappers on `CommandProcessor` that delegate to
  `DashboardRenderer` (~12 methods in `main.py:328-1290`) — replaced by
  direct `ItemRenderer` calls
- The inline `session` block in `_render_config_dashboard`
  (`main.py:1318-1338`) — replaced by `ItemRenderer` rendering of the
  `session.orchestrator` and `session.context` items
- Duplicated URI-truncation helpers in `module.py:71-73`,
  `source.py:465-467,486-488`, `module.py:925-928` — single helper in
  `ui/item_renderer.py`

### 4.7 New command: `/config show <category> <item>`

Single-item detail view. Walks `ItemRecord.include_path` to render the
bundle-on-disk chain. Walks `ItemRecord.origins` to render the behavior
chain. Renders both side by side so it's obvious which is which.

`/config provenance <item>` is **not** added. One surface.

---

## 5. Execution Order Within the Single PR

The work is one PR but commits are sequenced so that any intermediate
state compiles and tests pass:

1. **Foundation, internal types**
   - Add `Origin`, `IncludeStep`, `ItemRecord` dataclasses.
   - Rename `_provenance` → `origins`, change value type to `list[Origin]`.
   - Update `_prov_add`, `track_provenance`, `build_initial_provenance`.
   - Update `_inspector.py` to return `list[ItemRecord]`.
   - Delete fuzzy `_lookup_prov_behavior`. Publish `module_exports`.
   - Fold `RuntimeOverlay._owned` into the origins flow.
   - Cover `session.*`, `spawn`, `instruction`.
   - Foundation tests pass.

2. **App-cli, renderer scaffold**
   - Add `ui/item_renderer.py`, `ui/view_policy.py`.
   - Migrate `/config show` and `/config <category>` to `ItemRenderer`.
   - Delete `DashboardRenderer`, `_render_legacy_config`,
     compat wrappers.

3. **App-cli, command migration**
   - Migrate the 17 remaining list/show commands.
   - Add `--compact` / `--detailed` / `--format` to each.
   - Add `/config show <category> <item>` route.
   - Delete duplicated URI-truncation helpers.

4. **App-cli, JSON contract docs**
   - Write `docs/OUTPUT_FORMATS.md` schema section documenting
     `ItemRecord` JSON shape as a contract.

Each numbered block is a commit. Foundation lands first so app-cli can
pin to it. No version dance — the workspace tracks both as submodules.

---

## 6. Risks Acknowledged

Skipping phased migration means:

- **Visual regressions go unnoticed.** No snapshot tests for Rich
  output. Mitigation: hand-run every migrated command before merge,
  paste output into PR description for spot review.
- **A bug in foundation data model blocks all of app-cli.** Mitigation:
  foundation block lands as a clean commit with passing tests before
  app-cli work starts (sequencing in §5).
- **The JSON schema ships without time to soak.** Mitigation: mark it
  `--format json` (experimental) in the help text for one release;
  `OUTPUT_FORMATS.md` notes the schema may evolve until a future tag.
- **`module_exports` may not be reachable from every module type
  uniformly.** Mitigation: if a module type genuinely doesn't expose a
  static export list, the inspector falls back to `module_id` itself as
  the displayed name (no fuzzy match, just less specific). Behavior is
  predictable; the gap is visible.

---

## 7. Open Questions

None blocking. Locked decisions:

- Entry shape: `(bundle, via_behavior)` only. No `step`, no `version`,
  no `path`.
- No back-compat: rename `_provenance` → `origins`, public field.
- View policy: single table in `ui/view_policy.py`. App-cli owns.
- One surface: `/config show <category> <item>`, not a separate
  `/config provenance` command.
