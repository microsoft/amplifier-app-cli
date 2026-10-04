# CLI location notices, v1

The CLI owns optional announcements after successful native persistence and bounded per-session removal. Foundation remains the owner of canonical transcript/metadata writes and shared writer locks; the independent catalog owns derived discovery. This uses the existing catalog location-only inbox contract, not a new history store or execution receipt.

`AMPLIFIER_SESSION_CATALOG_HINT_DIRECTORY` opts into an existing, canonical absolute, same-user private POSIX directory (mode 0700). An unset variable performs no notice I/O. Invalid, missing, nonprivate or symlinked destinations never prevent a successful native save. The CLI does not create the inbox, inspect its contents, start a catalog or agent, activate providers, or watch historical directories.

After `SessionStore.save`, `save_new`, `_save_transcript`, `_save_metadata`, `update_metadata` or `rename` succeeds, the CLI publishes only `{"version":1,"sessionDirectory":"/canonical/absolute/native/session/directory"}`. No title, transcript, event, credential, actor or visibility fields are included. Existing shared-root checkpoint and actual `/rename` paths delegate through these covered wrappers. Failed canonical writes emit no notice.

The compact UTF-8 body is at most 8 KiB. Its filename is SHA-256 of the canonical UTF-8 source path plus `.json`. A same-directory unique mode-0600 temporary file is fully written and fsynced, atomically replaced into that filename, then the held inbox descriptor is fsynced. Publication uses an already validated directory descriptor, preventing a later pathname replacement from redirecting writes. Repeated saves coalesce; a consumer's existing `.processing-...` claim is preserved. Temporary cleanup and notice failures never roll back native persistence. Notice delivery is best effort and diagnostic output contains only exception class, not configured paths or source payloads.

The consumer validates configured native roots and rereads current metadata. A notice is neither proof of native save outcome nor deletion authority. Successful `session delete` and `SessionStore.cleanup_old_sessions` (including the CLI cleanup policy callback) publish the same location-only notice after capturing an existing exact source path before removal. The capture requires a canonical nonsymlink same-user directory and at most 128 exact ancestor stat identities; it never reads native bodies or authorizes the remover. After native success, all captured ancestors must still be the same nonsymlink directories and the selected path must report ENOENT. Recreated paths, symlinks, replaced parents, missing pre-capture sources, busy/refused/failed removals, and incomplete checkpoint deletion do not announce. The consumer independently observes absence; it retains the historical row read-only and excludes ordinary discovery without changing nativeDeleted or productHidden. It must not manufacture another owner's `lifecycle.json`, mark nativeDeleted, change host productHidden, or treat missing metadata as an authoritative tombstone. Existing configured native-owner lifecycle markers remain separately identity checked by the catalog. Present-directory scans cannot infer missing unvisited directories; exact hints or an explicitly fenced host rebuild reobserve retained locations. Save reconciliation can repair missed save notices, but synthetic consumer acceptance does not establish adoption by older installed CLI versions.

## Reviewed implementations and checks

The local ecosystem catalog `amplifier/docs/MODULES.md` identifies CLI, Foundation and independent application ownership. The CLI baseline is `5aaafb478d02cee8c396967b954299a08e8a1efd`. All CLI-owned `SessionHistoryStore` and `SessionMetadataStore` write call sites are centralized in the wrappers above; `SharedRootSession.checkpoint` calls native_store.save. Native ACP and Foundation save implementations remain unchanged. No portable runtime module or bundle asset is added.

Tests use the installed Foundation dependency and cover all write variants, actual CLI rename, source-byte preservation, disabled/malformed/private-directory gates, bounded exact payloads, atomic/fsynced publication, consumer-claim preservation, opened-directory replacement, canonical save failure and notice failure. `scripts/qualify_catalog_notices.py` additionally requires an independently installed catalog and exercises actual installed CLI save/rename, coalesced updates and configured native-owner lifecycle proof on isolated physical fixtures. That fixture does not qualify real CLI deletion, provider/account work, browser/device rendering, or live installation.

## Removal implementation audit and limits

The deletion class audit covers both actual Click `session delete` branches,
shared-root locking and final checkpoint removal, both CLI cleanup branches,
and direct/callback `SessionStore.cleanup_old_sessions`. The callback still owns
its removal policy/lock and reports success; capture is derived path validation,
not a new authorization or lease. Announcement checks actual absence after that
callback returns. Foundation `HeldSession.delete_checkpoint` deletes only its
historical checkpoint under its retained lock; it does not remove native history.
These Foundation and shared-root implementations remain unchanged.

`save_new` rollback removes only its uncommitted exclusive reservation after a
failed write and emits no success notice. `reset --full` and selected `projects`
reset can remove a whole native home/tree; they are separate bulk reset authority,
not covered by these bounded per-session notices. No inventory of those trees is
added. Cache/module/install-state cleanup is also outside native session removal.
This patch does not claim coverage of every bulk history reset or current live CLI.

Removal tests cover actual shared/ordinary Click deletion, actual cleanup,
protected/checkpoint/busy refusal, callback outcomes, native/checkpoint failures,
unsafe inbox refusal after success, changed ancestors/recreated sources, zero
opt-out I/O and no emitter source-body/spool reads. The installed causal qualifier
`scripts/qualify_catalog_removals.py` exercises actual Click delete, Click cleanup
and direct store cleanup against an explicitly selected installed catalog on
owned physical sources. It preserves unrelated canonical bytes, requires one
bounded notice/consumer pass, and distinguishes derived absence from both native
and product visibility. It starts no model and scans no global source tree.
The original save/rename qualifier remains a separate preserved acceptance case.
