# CLI image-capability host — v1 (DRAFT)

Clause prefix: **ICLI**. Owner: `amplifier-app-cli`.
Proposed host obligations, not ratified text or permission to make paid calls.
Implementation and qualification status are recorded separately below; a draft
promise is not evidence that every delivery boundary has passed.
This draft requires no Core changes and does not replace the CLI's existing governance.

## Who builds against this

CLI composition, connection, session, permission and terminal-output maintainers;
imagegen and routing maintainers qualifying portability.

## What it is

The CLI composes shared image behavior, retaining ordinary connections, enforcement,
accounting and textual artifact delivery in the user's existing host.
These canonical documents are **proposed adoption dependencies**, not delivered support;
their publication, accepted versions and installed implementations require qualification:

- [Image lifecycle](https://github.com/microsoft/amplifier-bundle-imagegen/blob/main/contracts/image-capability.v1.md).
- [Provider image operation](https://github.com/microsoft/amplifier-bundle-imagegen/blob/main/contracts/provider-image-operation.v1.md).

Synthetic boundary example; this is not a settings or provider-wire schema:

```text
chosen root + shared image behavior + ordinary routing -> permitted account-blue
request cover-01 -> host admission -> imagegen -> exact mounted provider
saved result -> ./outputs/cover-01.png + hash + receipt path + honest status
```

## The promises

1. **ICLI1 — Compose, do not copy or replace.** Add the external behavior through
   normal app composition, preserving the selected root, root instructions,
   orchestrator, context manager and conversational provider/model. Keep portable
   image code, skills and instructions out of CLI-owned `data/`; compose once even
   when a root already includes it. Preserve explicit tool configuration and opt-outs.

2. **ICLI2 — Keep connections ordinary and selection external.** Supply the exact
   mounted provider instances and generic routing capability through supported seams.
   No separate image account wizard, vendor ranking, native transport or shared-schema
   fork belongs here. Setup/discovery never generates. Missing resolver/capability is
   visible and leaves chat usable, without hidden fallback or credential/account substitution.

3. **ICLI3 — Preserve session identity and scope.** Qualify fresh roots and children
   with independent mutable mount plans, existing preference precedence and inherited
   restrictions. Saved snapshots remain complete plans; installation does not inject
   image behavior or new defaults into them. Resume preserves account/model pins and
   historical instructions, or reports a compatibility limit without rewriting them.
   Child changes cannot alter a parent's or sibling's provider configuration or budget.

4. **ICLI4 — Retain admission and provider-call accountability.** Carry effective
   filesystem restrictions, explicit disable/removal and `allow_paid: false` through
   root/child boundaries. Optional provider methods must remain visible through host
   wrappers without fabricating support. Apply applicable permission, budget and
   cancellation guards before dispatch; account for image/controller usage once each.
   Unsupported cost enforcement stays explicit; missing usage is unknown, not zero.
   A denial never becomes permission to route elsewhere or bypass a refused file path.

5. **ICLI5 — Deliver saved artifacts without replay.** Show the immutable output path,
   hash, receipt path and actual operation status in text, including headless output.
   Preserve input files and enforce the shared lifecycle's file checks; do not claim
   pixel inspection from a receipt. Reading status after restart, duplicate request IDs
   or unknown outcomes reconciles saved evidence without repeating a paid effect.
   Missing/corrupt output is a visible delivery failure, not an automatic regeneration.

## Not in v1

Vendor qualification, rankings, transport and shared schemas remain with their owners;
new requirements trigger owner proposals. A dedicated image UI, replacement root or
mandatory Core capability needs a separate product decision. Additional backends need
adapter qualification, not inference from vision support. This draft changes no runtime settings.

## How the kit checks it

**Acceptance matrix.** Use synthetic providers/resolvers in an isolated installed
CLI, then record exact CLI, Foundation, imagegen, routing and provider revisions.
Positive and negative assertions must pass at the real composition/host boundaries:

| Promise | Positive assertion | Negative assertion |
| --- | --- | --- |
| ICLI1 | Fresh chosen roots mount one shared image tool and retain instruction/runtime identity. | Root plus app inclusion does not duplicate the tool, replace explicit config or resurrect removal; wheel contains no copied portable image implementation. |
| ICLI2 | Two same-module accounts remain distinct; selected generic strategy reaches only the permitted instance without image-only setup. | Missing resolver, unknown capability version or denied account reports unavailable/refused; no key borrowing, vendor fallback or chat-model mutation. |
| ICLI3 | Fresh child and cold-resumed fixtures preserve scoped preferences, restrictions and frozen instructions. | Old snapshots receive no new behavior; a child mutation cannot change parent/sibling settings; incompatible snapshots fail visibly rather than being silently rebuilt. |
| ICLI4 | Admitted synthetic calls traverse host guards and record separate known image/controller usage exactly once. | Permission/file/paid denial or exhausted applicable budget yields zero dispatches; wrappers preserve capability absence and unknown cost rather than asserting enforced caps. |
| ICLI5 | Generate then edit retains both files; terminal/headless delivery identifies exact bytes and receipts after restart. | Duplicate/unknown requests cause zero further dispatches; missing/corrupt files and inaccessible paths never appear as completed delivery or trigger regeneration. |

Fixture success is not live entitlement or visual-quality evidence. Any later live
qualification needs an explicit account, bounded spend and retained receipts/pixels;
report unavailable checks separately and keep JSON stdout free of terminal diagnostics.

### Implemented and checked scope

Fresh composition below the selected root, explicit tool overrides and opt-outs,
saved-plan preservation, child permission narrowing and independent child plans
are implemented in this candidate. Portable image execution remains in Imagegen.
The maintained composition tests replace fetching, installation and execution
boundaries; they do not make image-provider calls.

On 2026-11-05, candidate `6c55a6899a9e719ddce2d5bf33a4122077933abf`
passed 315 focused image/child and adjacent merge/isolation checks both from
source and from its noneditable wheel. The declared locked environment used
Python 3.13.16, Core 1.6.1 and Foundation
`a5f91fc4765dd3c6a1496b400c4fe428e5aa06f2`; its `prepare_operation`
import passed and all 107 installed Python files matched the source.

That result does not qualify live image accounts, paid operations, complete
host accounting, or the final accepted CLI/Imagegen/routing delivery graph.
Installed headless artifact delivery and restart/no-replay acceptance retain
their own exact-source evidence and limits; they are not inferred from these
composition checks.

## Open questions

Which dependency combination and CLI guard/accounting seams qualify image operations?
Which older snapshots can resume unchanged, and which require a new session?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-13 | Initial DRAFT ICLI1–5 for shared routed images. | Read-only review of CLI AGENTS, README, spawn/resume and root-state guidance; checks above remain proposed. |
| 2026-11-05 | Separate implemented composition from remaining acceptance promises; retain draft status. | Independent source and noneditable-wheel focused qualification of `6c55a689`; no live-provider claim. |
