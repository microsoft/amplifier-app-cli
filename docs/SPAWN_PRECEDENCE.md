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
