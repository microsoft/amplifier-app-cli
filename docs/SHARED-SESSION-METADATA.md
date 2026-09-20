# Shared session metadata

The CLI and Unified share the native session directory. `metadata.json` is the
authority for names and descriptions; Unified's display cache is not a separate
name store. `/rename` uses Foundation's `SessionMetadataStore.set_name`, with a
shared limit of 200 characters. Delayed automatic names and later runtime saves
preserve an explicit rename from either application.

Foundation serializes metadata updates with a short sibling lock. Session
execution still uses its separate ownership lock. Both applications must be
updated before relying on concurrent metadata edits; an already-running older
process retains its older save behavior until restarted.

Resuming a native session also selects its `settings.yaml` scope. CLI and Unified
use Foundation's shared settings reader with global, project, local, and native
session paths. Provider instances merge by ID or module; unrelated keys and
credential references remain intact. Reading never rewrites configuration.

This preserves session identity, transcript context, and event history. It does
not replay tools or make web-only pending operations, canvas state, or private
runtime-control files executable by the CLI.
