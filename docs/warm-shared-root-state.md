# Shared root sessions

Top-level CLI sessions use Foundation's
`amplifier_foundation.session.shared_state` API. On supported POSIX hosts the
CLI acquires the workspace-plus-exact-session-ID writer before it constructs
provider context, reads common checkpoint authority before its native
projection, and writes common authority before refreshing compatibility files.
The installed Foundation must provide both POSIX `fcntl` locking and
`HeldSession.delete_checkpoint()`; a missing API or an actual lock failure is a
visible error, never an unlocked native fallback.

Windows does not attempt the POSIX shared-lock protocol. It retains established
native session persistence and writes one stderr-only notice per invocation
that shared-root features are unavailable; JSON stdout is not polluted.
Existing native session IDs outside Foundation's portable letter/digit/hyphen
vocabulary also remain native-only so older conversations continue to resume.
Shared roots are addressed from the same canonical workspace and the same
Foundation state-root environment on both hosts (normally
`AMPLIFIER_SESSION_STATE_HOME`, otherwise Foundation's platform state root).
Busy errors include the resolved state root when available plus bounded
advisory owner details, then direct the user to finish/exit that owner and
retry.

Child sessions remain on their independent persistence paths. A shared root
can be forked from its authoritative checkpoint into a new independent native
session. Deletion keeps the existing confirmation, removes the native
projection, then asks the held Foundation capability to remove only the
checkpoint: it never deletes the shared lock or directory. Cleanup skips
shared roots with an explicit count and reason while safely cleaning eligible
ordinary sessions.