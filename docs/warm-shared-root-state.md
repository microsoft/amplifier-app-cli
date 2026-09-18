# Shared root sessions

Top-level CLI sessions use Foundation's
`amplifier_foundation.session.shared_state` API.  The CLI acquires the
workspace-plus-exact-session-ID writer before it constructs provider context,
reads common checkpoint authority before its native projection, and writes the
common checkpoint before refreshing the native compatibility files.

The CLI deliberately does not implement a local lock or checkpoint fallback.
Until a Foundation release provides this frozen API, the existing native-only
CLI behavior remains available but cannot participate in manual CLI/web
switching. Once the API is available, all public CLI root entry points opt in
automatically; an unlocked compatibility writer is never presented as shared.

Child sessions remain on their existing independent persistence paths.
`amplifier session delete` refuses a root with shared authority because
destructive shared-history operations are outside the WARM contract.