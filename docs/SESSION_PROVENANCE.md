# Explicit internal session provenance

A launcher that creates an implementation-only root job may set:

```sh
AMPLIFIER_SESSION_VISIBILITY=internal \
AMPLIFIER_SESSION_PURPOSE=memory.suggestion \
amplifier run --output-format json '...'
```

The CLI records `session_visibility` and an optional bounded `session_purpose` in
native `metadata.json`. Purpose is a machine label matching
`[a-z][a-z0-9_.-]{0,79}`, not a prompt, credential, or permission. Missing or
unrecognized visibility means an ordinary `chat`. Agent origin, JSON mode,
launcher ancestry, and lack of a TTY do not imply an internal session.

For a new internal root, an empty native history is created exclusively before
bundle initialization. This makes its classification available while the job
runs or if initialization fails. Existing history is never replaced by this
creation step. Incremental, final, failed-turn, and handoff saves preserve the
original fields. Resume does not read a new creation declaration from the
launcher's environment, including for unmarked legacy history. A deliberate
independent fork records `chat` without inheriting an internal parent's purpose.

Consumers such as Amplifier Unified can hide explicitly internal histories from
ordinary chat lists while retaining diagnostic access. This is presentation
provenance, not authorization. Older consumers may ignore these fields; older
CLI versions do not persist the launcher's declaration. No legacy history is
reclassified or deleted.
