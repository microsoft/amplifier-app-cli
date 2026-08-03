# AGENTS.md — amplifier-app-cli

## Boundary rule: `amplifier_app_cli/data/` vs. an external bundle

`amplifier_app_cli/data/` ships inside the CLI's own wheel — anything placed there loads
for every user, every session, version-locked to the installed CLI, with no bundle
composition step in between. That reach is exactly why it must stay small. Before adding
anything here, run it through these three tests, in order:

1. **Does it depend on something the CLI uniquely provides, that cannot move?** A slash
   command, a settings key, a terminal affordance — something with no home outside this
   process. If no → it belongs in an external bundle, not here.
2. **Would a non-CLI host ever want it?** If yes → external bundle. The CLI may still
   *include* it (compose the bundle), but must not *own* it — ownership belongs wherever
   the capability is portable to.
3. **Is the trigger unconditional?** If the asset is gated on settings, an env var, a flag,
   or runtime state, the asset itself may still live here, but the compose/injection
   *decision* stays in Python (see `runtime/config.py::_ensure_default_skills_dirs` for the
   pattern) — never encode conditional loading in a bundle YAML that lives alongside it.

If the answer to 1 is "no" or the answer to 2 is "yes," it's an external bundle question,
not a `data/` question.

**Resolution rule:** assets under `amplifier_app_cli/data/` are always resolved by
**package-relative path** (e.g. `Path(__file__).parent.parent / "data" / "..."`), **never**
by git URI. A git URI decouples the asset's version from the installed wheel's version —
defeating the reason for co-locating it here in the first place. If it needs independent
versioning, it isn't a `data/` asset.

**Token budget:** this location is auto-loaded for every user, every session — its budget
discipline is stricter than anywhere else in the ecosystem. No always-on context files
here without an explicit, named exception recorded in this section. Prefer mechanisms
that load on demand (skills, agent-scoped context) over anything injected unconditionally.

This section exists to keep `data/` from becoming a junk drawer — re-run the three tests
before adding, not after.
