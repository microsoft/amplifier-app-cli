# Bundle Include Source Override Design

## Goal

Enable users to override bundle include sources via `settings.yaml` configuration, redirecting entire bundles (e.g., `superpowers@main` → `superpowers@v2` or a local path) at the include level before foundation resolves them.

## Background

Foundation's `bundle.md` includes superpowers via a hardcoded git URI:

```
git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/superpowers-methodology.yaml
```

Full git URIs bypass the registry in `_resolve_include_source()` (line 817 of `registry.py`) — there is no interception point. Users cannot override transitive includes from settings.

The existing `source_resolver` callback in `bundle.prepare()` only covers module sources (tools, hooks, providers), not bundle include URIs. This means users who want to test a different branch of superpowers, or develop against a local checkout, have no supported mechanism to do so.

## Approach

A two-layer solution following the established "foundation provides mechanism, app provides policy" pattern:

1. **Foundation** adds an `include_source_resolver` callback hook on `BundleRegistry` — the same pattern already used for module source resolution.
2. **App-CLI** reads `sources.bundles` from settings, builds a resolver callback with substring matching, and sets it on the registry before loading bundles.

This was validated by three expert agents (zen-architect, core-expert, amplifier-expert) who all converged on this approach. The gap between module source resolution (already injectable) and bundle include resolution (not injectable) was accidental, not intentional.

## Architecture

```
settings.yaml                 App-CLI                        Foundation
┌──────────────┐    ┌──────────────────────┐    ┌─────────────────────────┐
│ sources:     │    │ load_and_prepare_     │    │ BundleRegistry          │
│   bundles:   │───>│ bundle()             │    │                         │
│     key: uri │    │                      │    │ _include_source_resolver│
│              │    │ builds resolver ─────────>│ (callback)              │
└──────────────┘    │ from settings        │    │                         │
                    └──────────────────────┘    │ _resolve_include_source │
                                                │   1. call hook          │
                                                │   2. if override: use it│
                                                │   3. else: normal path  │
                                                └─────────────────────────┘
```

The callback fires for every include at every nesting level — transitive by construction.

## Components

### Layer 1: Foundation — The Mechanism

**File:** `amplifier-foundation/amplifier_foundation/registry.py`

**Callback signature:** `Callable[[str], str | None]`

- Receives the include source string (e.g., `git+https://...@main#subdirectory=...`)
- Returns an override URI string to use instead, or `None` to proceed with normal resolution

**Changes to `BundleRegistry.__init__` (line 168):**

- Add parameter: `include_source_resolver: Callable[[str], str | None] | None = None`
- Store as `self._include_source_resolver`

**New method `set_include_source_resolver()`:**

- Accepts `Callable[[str], str | None] | None`
- Sets `self._include_source_resolver`
- Returns `None`

**Changes to `_resolve_include_source()` (line 817):**

Add 4 lines at the TOP of the method, before the existing URI pass-through check:

```python
if self._include_source_resolver:
    override = self._include_source_resolver(source)
    if override is not None:
        logger.debug(f"Include source overridden: {source} -> {override}")
        return override

# Then existing logic unchanged...
if "://" in source or source.startswith("git+"):
    return source
```

~15 lines of new code. Zero breaking changes — callback defaults to `None`, existing behavior preserved.

### Layer 2: App-CLI — The Policy

**File:** `amplifier-app-cli/amplifier_app_cli/lib/bundle_loader/prepare.py`

In `load_and_prepare_bundle()`, before calling `load_bundle()`:

- Read `sources.bundles` from settings via `app_settings.get_bundle_sources()`
- If overrides exist, build a resolver callback
- Set it on the registry via `registry.set_include_source_resolver()`

**Resolver builder function:**

```python
def _build_include_source_resolver(
    bundle_overrides: dict[str, str],
) -> Callable[[str], str | None]:
    if not bundle_overrides:
        return lambda _: None

    def resolver(source: str) -> str | None:
        for key, override_uri in bundle_overrides.items():
            if key in source:
                # Preserve #subdirectory= fragment from original
                # if override doesn't have one
                if "#" in source and "#" not in override_uri:
                    fragment = source[source.index("#"):]
                    return override_uri + fragment
                return override_uri
        return None

    return resolver
```

**File:** `amplifier-app-cli/amplifier_app_cli/lib/settings.py`

- Verify `get_bundle_sources()` exists (line 424) and returns `dict[str, str]` from `sources.bundles`
- If it doesn't exist, add it following the pattern of `get_module_sources()` (line 392)

~25 lines of new code in the app layer.

## Data Flow

1. User adds override to `settings.yaml` under `sources.bundles`
2. App-CLI reads settings during `load_and_prepare_bundle()`
3. App-CLI builds a substring-matching resolver callback
4. App-CLI sets the callback on `BundleRegistry` via `set_include_source_resolver()`
5. Foundation's `_resolve_include_source()` is called for each include URI
6. Callback checks if any configured key is a substring of the URI
7. If match found: returns override URI (with `#subdirectory=` fragment preserved if needed)
8. If no match: returns `None`, foundation proceeds with normal resolution
9. The matched bundle loads entirely from the override source — all context files, skills, modes, and behavior YAML come from the override

## Error Handling

- If the override URI is invalid (bad path, unreachable git repo), foundation's existing fetch/load error handling applies — the error surfaces naturally with the override URI in the message.
- If `sources.bundles` is missing or empty, no resolver is set — existing behavior unchanged.
- If `get_bundle_sources()` returns an unexpected type, the resolver builder handles it gracefully (empty dict → no-op lambda).

## User-Facing Config

```yaml
# .amplifier/settings.yaml (any scope: global, project, workspace)
sources:
  bundles:
    # Override to a specific version/branch:
    amplifier-bundle-superpowers: "git+https://github.com/microsoft/amplifier-bundle-superpowers@v2"

    # Override to a local path for development:
    amplifier-bundle-superpowers: "/home/user/repos/superpowers-v2"
```

The key (`amplifier-bundle-superpowers`) is matched as a substring against include URIs. This handles the common case where foundation's include URI is `git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=...` — the key matches because `amplifier-bundle-superpowers` appears in the URI.

The `#subdirectory=` fragment from the original include is preserved and appended to the override URI when the override doesn't specify its own fragment.

## Testing Strategy

**Foundation tests:**

- `_resolve_include_source()` with callback returning an override → uses override
- `_resolve_include_source()` with callback returning `None` → proceeds normally
- `_resolve_include_source()` without callback → existing behavior unchanged
- `set_include_source_resolver()` sets and clears the callback

**App-CLI tests:**

- `_build_include_source_resolver()` matches substring → returns override
- `_build_include_source_resolver()` no match → returns `None`
- `_build_include_source_resolver()` preserves `#subdirectory=` fragment
- `_build_include_source_resolver()` override with own fragment → uses override's fragment
- `_build_include_source_resolver()` with empty dict → no-op resolver
- Integration: `load_and_prepare_bundle()` with bundle source overrides in settings

## Implementation Order

1. Foundation change first (mechanism must exist before app can use it)
2. App-CLI change second (wires settings into the mechanism)
3. Test on the superpowers-test workspace

## Repos Affected

| Repo | Files Changed | Scope |
|------|---------------|-------|
| `amplifier-foundation` | `registry.py` | ~15 lines new code + tests |
| `amplifier-app-cli` | `prepare.py`, `settings.py` | ~25 lines new code + tests |

## Open Questions

None — design was validated by expert agents and approved.