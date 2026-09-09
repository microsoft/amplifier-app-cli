# Spawn-Time Precedence Policy

This document describes the spawn-time precedence policy implemented by
`session_spawner.spawn_sub_session` in this app. The precedence is **policy,
not contract** — other apps that register the `session.spawn` capability MAY
choose different precedence semantics. The kernel only provides the capability
slot; it does not enforce a precedence.

## The three levels (highest wins)

| Rank | Source                                            | How it enters `spawn_sub_session`                                                                                                                                                                                                                                |
| ---- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Caller-passed `provider_preferences` argument     | Explicit kwarg to `spawn_fn(...)` from `tool-delegate` / `tool-recipes` / `tool-skills`. Wins over everything else.                                                                                                                                            |
| 2    | Agent overlay's `provider_preferences`            | Either (a) hard-pinned in the agent's `.md` frontmatter, OR (b) written by a routing hook's `session:start` event handler (e.g. `amplifier-bundle-routing-matrix` does this when the frontmatter declares `model_role`). Used only when rank 1 is not present. |
| 3    | Parent session's mount-plan defaults              | From the user's `settings.yaml` — provider priority order + each provider's `default_model`. Used when no preferences are supplied at ranks 1 or 2.                                                                                                            |

**The dual-source design at Rank 2 is intentional.** Frontmatter `provider_preferences:` is the bundle-portable fallback that flows through unchanged when no routing bundle is installed. When a routing bundle (such as `amplifier-bundle-routing-matrix`) IS installed, its `session:start` hook may overwrite that value with resolution from `model_role:`. The two fields together support graceful degradation — agents work everywhere; routing bundles enhance when present.

## How the application mechanism works

When a non-None `provider_preferences` value is in play (caller-passed OR
the fallback read from `agent_config`), `spawn_sub_session` invokes:

```python
merged_config = await apply_provider_preferences_with_resolution(
    merged_config, provider_preferences, parent_session.coordinator
)
```

This function lives in `amplifier_foundation.spawn_utils`. It walks the
preference list in order, finds the first matching entry in
`merged_config["providers"]`, promotes it to `priority: 0`, overrides
`default_model` to the resolved model name, and protects sensitive keys per
`PROTECTED_CONFIG_KEYS`. See foundation's `spawn_utils.py` for the precise
semantics — including how glob patterns in `model:` fields are resolved.

When `provider_preferences` is `None` and the agent overlay has no
`provider_preferences` field either, the merged config is left as-is. The
sub-session then inherits the parent's mount-plan defaults: provider priority
order from `settings.yaml` and each provider's configured `default_model`.

## Defense-in-depth: the agent-config fallback

If the caller doesn't pass a `provider_preferences` argument,
`spawn_sub_session` reads `agent_config["provider_preferences"]` and uses
that. This is the mechanism by which routing-hook writes at `session:start`
flow through to the spawn even when the caller is a direct consumer of the
spawn capability that doesn't know about routing.

Concretely: `tool-delegate` normally reads agent-level prefs itself and passes
them as an explicit kwarg, so its path doesn't depend on this fallback. But a
direct consumer of `coordinator.get_capability("session.spawn")` that simply
forwards `agent_name + instruction` still gets routing-resolved preferences
because the spawner picks them up from the merged agent config.

## This is policy, not contract

Another app embedding Amplifier can register its own `session.spawn`
capability with a different policy. For example:

- **Always prefer agent-overlay prefs over caller-passed.** The opposite of
  this app — useful if an embedding wants the agent author to have final say.
- **Never apply prefs; always inherit parent defaults.** Strips out
  agent-level routing entirely for hosts that want strict centralized control.
- **Merge instead of clobber.** Append matrix-resolved candidates after
  hard-pinned ones rather than replacing.

The kernel doesn't enforce any precedence. The capability contract is just
"spawn a sub-session" — what each implementation does with provider preferences
is its own choice.

## Resume continuity

Resume reconstructs a child from a redacted persisted mount plan. Before the
child is mounted it writes the one effective, serialized preference chain back
to `config.provider_preferences`. The sources are ordered:

1. Preferences supplied to this resume call.
2. The prior explicit caller override saved as
   `caller_provider_preferences`.
3. The persisted agent overlay's authored preferences.
4. Legacy `config.provider_preferences` from sessions saved before the
   caller-provenance field existed.

The separate caller field is deliberate: a caller's temporary routing choice
must survive a cold resume without mutating the agent definition that new
children will inherit. An explicit override on a later resume replaces that
field for subsequent cold resumes.

Credential refresh is also resume-only. It restores only sensitive leaves that
are exactly `[REDACTED]` from matching live provider/module settings, including
registered agent module sections. It never deep-merges live settings into the
persisted plan, so child URLs, priority order, and routing configuration remain
the child's own.

## In-process system-prompt inheritance

For an in-process `agent_name: self` child, the CLI renders the root prepared
bundle's clean, unwrapped prompt factory once for the child, then installs that
resolved text as the child's frozen base prompt. It deliberately does **not**
copy or await the parent's installed context factory: application hooks may
have wrapped that factory with parent-specific state. The child can still apply
its own prompt wrappers around this base; this rule only prevents parent prompt
wrappers from leaking into the child.

An instructed named agent still takes precedence over that root base. Its
top-level `instruction` wins over `system.instruction`, and its mentions are
expanded against the child's capabilities before the frozen base is installed.
The nonempty snapshot is stored only in sub-session persistence metadata so
resume can restore the same base without placing it in `session.metadata`
telemetry. Runtime skills overlay and `session.routing` continue to be inherited
as child discovery and model-policy inputs; neither is parent-prompt leakage.

This behavior has a Foundation release gate: a root bundle with prompt sources
requires the public `PreparedBundle.create_system_prompt_factory` API. If that
API is unavailable, in-process self delegation refuses before child execution
and instructs the user to upgrade `amplifier-foundation` and start a new self
delegation. Ordinary roots, named instructed agents, and no-source root bundles
remain supported. Do not update the CLI lockfile's Foundation revision until the
upstream Foundation merge is available.

Historic self-delegated sessions require a valid persisted frozen snapshot to
resume. The current root cannot safely establish a historic child's identity,
so missing or invalid snapshots fail with an instruction to start a new self
delegation. Older named sessions still use their saved overlay/config fallback.

Subprocess self delegation remains a known limitation. Dispatch happens before
an in-process child can be initialized, so it preserves legacy dispatch and
logs that clean base-prompt inheritance is unavailable.

## Cross-references

- `amplifier_app_cli/session_spawner.py` — reference implementation of
  `session.spawn` for this app.
- `amplifier_foundation.spawn_utils` — `ProviderPreference` data class and
  `apply_provider_preferences_with_resolution` (the actual mechanism this
  policy invokes).
- `amplifier-bundle-routing-matrix` README — documents the matrix-strategy
  resolver and the `session:start` write that populates rank 2 (b) above.
- `tool-delegate/__init__.py` — delegate-level precedence: an explicit
  `provider_preferences` argument to the delegate tool wins over its
  `model_role` argument. Both eventually flow into spawn rank 1.
