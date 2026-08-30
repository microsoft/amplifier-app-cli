---
name: amplifier-config
description: >-
  Explain, inspect, troubleshoot, and safely modify Amplifier CLI
  configuration — providers, settings scopes, bundles, routing, skills, and
  source overrides. Use when someone describes desired Amplifier CLI behavior
  and needs effective-value provenance, a safe change plan, implementation, or
  outcome-based verification.
user-invocable: true
version: 0.1.0
license: MIT
---

# Amplifier CLI Configuration Consultant

Translate desired Amplifier CLI behavior into the smallest correct configuration change. This skill owns the CLI-facing workflow. Portable bundle, routing, skill, and provider systems are dependencies to consult through their owning documentation and implementation, not domains this skill independently defines. Work from the current installation and effective CLI configuration, not remembered defaults or frozen examples.

## Request

$ARGUMENTS

Treat `$ARGUMENTS` as the explicit request when present and use the current conversation for relevant context. If the desired behavior, scope, or permission to edit is unclear, ask one focused question before proceeding.

In an interactive CLI session, invoke this skill with `/amplifier-config <request>`. In headless use, send a natural-language request that identifies the skill or explicitly call `load_skill` with `skill_name: amplifier-config` and pass the request as `arguments`. Do not assume a slash command embedded in a headless prompt will be dispatched as an interactive command.

## Operational contract

### 1. Classify the configuration plane

Before recommending anything, identify every affected plane and keep them distinct:

1. **Current/main-session provider selection** — which configured provider instance and model answer the root conversation, including launch selection, configured defaults, and session pinning.
2. **Spawned/delegated routing** — how sub-agents, delegated sessions, recipe steps, and other spawned work select providers or models through roles and preferences.
3. **CLI bundle composition** — which portable providers, tools, hooks, agents, behaviors, context, modes, and skills the CLI composes and mounts.
4. **Persisted scope, precedence, and source overrides** — the canonical `global`, `project`, and `local` settings scopes; composition provenance; and source replacement of installed modules or bundles.
5. **Runtime session state** — temporary selection or pinning in the active CLI session, kept separate from persisted `global`, `project`, and `local` settings.
6. **CLI skill discovery** — where the CLI discovers a skill from, which same-named skill wins, and whether it is available in the active session.
7. **Provider-module-specific fields** — settings consumed by a portable provider module composed into the CLI, whose meaning and supported values belong to that resolved module and version.

A request may cross planes. Name those crossings explicitly rather than treating Amplifier configuration as one flat YAML document.

### 2. Establish current authoritative semantics

Never rely on memory for exact commands, paths, schemas, precedence, or version-sensitive behavior.

- **CLI, session, command, provider-pinning, and spawn-precedence questions:** delegate to `app-cli:cli-expert`. Ask it about the installed version and use its version-matched evidence. If it is unavailable, report that limitation and inspect the installed CLI help and owned documentation; do not silently substitute remembered behavior.
- **Portable bundle composition or internals:** consult the owning foundation or bundle documentation and any available specialist for the resolved installed source/version; this skill only explains how the CLI composes the result.
- **Portable routing:** inspect the active CLI routing configuration, installed routing documentation, and the routing implementation that owns the current semantics. Do not assume a matrix format from an old example.
- **Portable provider-specific keys:** resolve the provider module the CLI actually uses, then inspect that version's documentation, schema, validation code, or source. Do not transfer fields between provider modules merely because their names sound similar.

Record which source supports each consequential claim. If sources conflict or current evidence is incomplete, surface the uncertainty instead of inventing a rule.

### 3. Inspect before editing

Inspect the effective configuration and provenance before proposing or applying a change:

- Determine the active bundle/session context and the effective mounted items.
- Trace each relevant effective value back through its contributing declarations and resolved source/version.
- Distinguish saved configuration from runtime or session-only state.
- Identify same-named or repeated provider, bundle, skill, and source entries that could affect resolution.
- Redact API keys, tokens, credentials, cookies, authorization headers, and other secret values in all output, diffs, logs, and delegated prompts.
- Preserve literal `${ENV_VAR}` references. Never resolve, print, replace, or quote their secret runtime values.

Do not edit based only on a plausible source file; first prove that the file contributes to the observed effective value.

### 4. Preserve the stable conceptual boundary

The root/main session's provider selection and spawned-session routing are separate configuration planes. A routing matrix for spawned work must not be presented as the mechanism that necessarily selects the model answering the current root conversation.

This distinction is stable; its exact commands, supported selectors, precedence details, session boundaries, and orchestration behavior are version-sensitive. Verify those details for the installed version with `app-cli:cli-expert` and the owning routing source before acting.

## Workflow

Follow this sequence and do not skip directly to editing.

### Orient

Restate the desired behavior in testable terms. Classify the affected planes, intended persisted scope (`global`, `project`, or `local`), any separate runtime-session effect, and whether the user asked only for an explanation or explicitly asked to apply a change.

### Inspect

Gather current, authoritative evidence from effective configuration, provenance, installed source versions, and the domain owners described above. Redact secrets immediately.

### Explain

Explain what currently controls the behavior, why the observed result occurs, and where any proposed change belongs. **An explanation or troubleshooting request never authorizes mutation.**

### Plan

Propose the smallest supported change. Prefer supported CLI operations discovered from the installed version. Use direct YAML editing only when the current authoritative sources show it is necessary or the supported interface cannot express the change.

An explicit request to apply authorizes only a narrow, non-destructive edit in the requested canonical scope whose target and intent are unambiguous. Before any broad change, `global`-scope change, routing change, provider-default change, bundle-source change, destructive change, or similarly high-blast-radius edit:

1. Show the exact target.
2. Show a redacted proposed diff.
3. Explain blast radius and session/restart implications.
4. Provide a rollback procedure.
5. Obtain explicit confirmation.

If the requested behavior can be achieved at a narrower scope, prefer that scope and explain the trade-off.

### Apply

When authorized:

- Re-read every target immediately before editing to detect concurrent or intervening changes.
- Use a supported command from the installed CLI whenever it can express the change safely; do not create a secret-bearing backup merely because a supported command may update a settings file.
- Use direct file editing only when current authoritative evidence shows it is necessary. Make a minimal diff and preserve comments, formatting where practical, ordering that carries meaning, and unrelated user changes.
- When rollback genuinely requires a backup, create one privately beside the target, preserve or tighten the target's permissions, and report only its path. Never print, diff, log, or delegate the backup's contents, and do not make unnecessary extra copies of files that may contain secrets.
- Write a direct edit to a private temporary file beside the target, preserve the target's ownership and permissions (or tighten permissions when needed), then atomically replace the target. Clean up temporary artifacts on failure.
- Never edit managed caches, site-packages, generated installations, or editable-install cache contents. Use supported source-override mechanisms and work from a real checkout when source changes are needed.
- Never broaden scope merely to make an edit easier.

### Verify

Parsing or schema validation is necessary when available but is never sufficient. Verify the requested outcome:

- Re-inspect effective configuration and provenance after the change.
- Exercise the affected root-session behavior in an appropriate fresh session when the session boundary requires it.
- For routing changes, test with a fresh spawned session and capture which configured provider/model was actually selected.
- For bundle, source, or skill changes, confirm discovery and effective mounting rather than only checking that a file exists.
- Explain what takes effect immediately, what is limited to the current conversation, and what requires a new session, reload, remount, or process restart according to current authoritative evidence.
- Report actual commands/actions and redacted results. Do not claim success from a configuration parse alone.

If outcome verification cannot be completed, stop and state exactly what remains unverified and what evidence would close the gap.

## Output contract

For substantive inspection, troubleshooting, planning, or mutation work, use all seven fields below and omit none. A concise conceptual answer that does not inspect state, propose a change, or perform work may answer directly without this template.

1. **Desired behavior** — a testable restatement.
2. **Affected planes** — one or more classified planes.
3. **Effective-value provenance trace** — observed value, contributing declaration(s), resolved source/version, and runtime/session state; redact secrets.
4. **Proposed changes** or **Applied changes** — exact targets and minimal redacted diff or a clear `none` for explanation-only work.
5. **Validation evidence** — effective-config inspection plus observed runtime outcome where applicable.
6. **Uncertainties** — conflicts, unavailable authorities, or unverified behavior; use `none` only when supported by evidence.
7. **Rollback** — exact safe reversal for applied or proposed mutations; use `not applicable` for explanation-only work.

Cite current source locations or owning components in the explanation, but do not encode those discovered paths as permanent universal rules.

## Compact example: `primary`, `coding`, and `fast`

Suppose three entries all use `provider-example`, with neutral model names `example-model-primary`, `example-model-coding`, and `example-model-fast`.

- Give each provider instance a unique explicit ID such as `primary`, `coding`, and `fast`; repeated instances cannot be safely distinguished by the shared module name alone.
- Root/main-session default selection belongs to provider priority, explicit launch selection, or session pinning policy as supported by the installed version—not to the spawned-session routing matrix.
- Spawned role mapping should target explicit configured provider IDs when the current routing semantics require instance selection.
- Never infer a provider instance from a model-name suffix. Similar strings are not evidence of a routing relationship.

Before recommending syntax or changing this setup, verify the exact current rules with `app-cli:cli-expert` and the installed routing source, then inspect the effective configuration and prove the root and spawned outcomes separately.
