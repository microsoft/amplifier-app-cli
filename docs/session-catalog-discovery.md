# Optional session catalog discovery notices

The CLI can notify an independently running `amplifier-session-catalog` after a
successful native session save or metadata rename. This makes new/changed sessions
visible sooner without a full metadata scan. It does not synchronize CLI editing
state or change native history ownership.

Configure a private absolute directory for both processes:

```sh
export AMPLIFIER_SESSION_CATALOG_HINT_DIRECTORY="$HOME/.amplifier/catalog-hints"
amplifier-session-catalog serve --db /private/catalog.sqlite \
  --home "$HOME/.amplifier" \
  --hint-directory "$AMPLIFIER_SESSION_CATALOG_HINT_DIRECTORY" --scan-on-start
```

Run the CLI with the same environment setting. The configured catalog `--home` must
match the CLI's native home; additional native homes can be supplied explicitly to
the catalog. Its existing workspace/parent/internal-session visibility rules still
apply. An ordinary root session becomes visible only when the catalog can establish
its workspace, and child history stays behind its parent by default.

Inbox v1 requires POSIX ownership/mode checks (macOS/Linux). On other platforms
the optional writer remains disabled and ordinary catalog scanning still works.

This integration is disabled by default and adds no CLI package dependency or
background process. A notice contains only the absolute saved session directory and
protocol version. It follows the catalog's public
[writer hint inbox contract](https://github.com/microsoft/amplifier-session-catalog#optional-writer-hint-inbox-v1),
using a bounded atomic notice coalesced by directory. It does not send transcript,
metadata, event contents, secrets, or UI drafts. The catalog independently validates
the location under its configured roots before reading bounded native metadata.

A missing/unavailable inbox never turns a successful session save into an error.
Notices are best effort; the catalog's periodic metadata scan repairs lost notices,
and remains necessary for writers which do not emit them. The native transcript,
metadata, event logs, backups, resume identity, and fork semantics stay authoritative
and unchanged. No historical files need migration.
