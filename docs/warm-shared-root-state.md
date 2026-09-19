# Shared root sessions

Top-level CLI sessions use Foundation's `SessionHistoryStore` to read and write
`transcript.jsonl` and `metadata.json` in the existing project/session directory.
The transcript is the sole saved conversation authority. Context Intelligence's
`context-intelligence/events.jsonl` enriches history with activity; it is never
used to replace missing or damaged transcript messages. The shared history API
can read those events on demand; normal resume reads only messages and metadata.
The CLI retains its existing message sanitization, secret redaction, backup
filenames, config snapshots, naming metadata, and child-session conventions.
No checkpoint, merged log, or additional message database is created.

On supported POSIX hosts the CLI acquires Foundation's workspace-plus-session-ID
writer lock before constructing provider context. Every root save checks the
held capability and writes native history while holding that lock. Resume reads
native files freshly, including changes made by transcript-compatible CLI
versions. A damaged transcript recovers from its `.backup`; damage to both files
is a visible error, never an empty conversation or a fallback to stale events.

Old Foundation checkpoints remain read-only compatibility inputs **only when no
native transcript or transcript backup exists**. Opening a checkpoint-only
session does not migrate it; its next ordinary save writes native files. Existing
checkpoint files are retained until explicit deletion. Previous releases that
prefer checkpoints must be updated before continuing such a session: those
binaries can otherwise load an old checkpoint even though native history is newer.
Transcript-only CLI versions continue to see the native files written here.

Windows retains native session persistence and emits one stderr-only notice per
invocation that shared locking is unavailable; JSON stdout is not polluted.
Existing session IDs outside Foundation's portable letter/digit/hyphen vocabulary
also remain native-only. Supported shared roots must use the same canonical
workspace and Foundation state-root environment on both hosts (normally
`AMPLIFIER_SESSION_STATE_HOME`, otherwise Foundation's platform state root).
Busy errors identify the current owner and ask the user to finish/exit it before
retrying. Older hosts that do not participate in this lock must not concurrently
write the same session.

Children keep their independent paths. Forks use Foundation's message operations
and refresh native history under the root lock before saving a new session.
Explicit deletion removes native files and any legacy checkpoint while retaining
the stable shared lock directory. Cleanup skips busy sessions and historical
checkpoint sessions, reporting the reason rather than leaving stale legacy data
that could resurrect a deleted session.


`session fork --no-events` controls only the older root `events.jsonl` copy.
Context Intelligence captures are not copied or assigned a new owner during a
fork: parent lineage identifies the original activity and the child logger
captures new work. Cost restoration prefers the CI capture, honors the
`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` projects-root relocation, and falls
back to the legacy root log only when that CI capture is absent.
