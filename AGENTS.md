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

## In-process self-child prompts

For `agent_name: self`, `session_spawner` must build a new foundation prompt
factory for the target child from the root prepared bundle, render it once, and
install only that frozen result. Never copy or await a parent's installed
context factory: hooks can wrap it with parent-specific state. Persist a
nonempty resolved snapshot only in sub-session persistence metadata, never in
`session.metadata` telemetry; keep subprocess self dispatch as its explicit
legacy limitation.

## Root instruction AGENTS.md tail

`load_and_prepare_bundle()` appends `@~/.amplifier/AGENTS.md` and then
`@.amplifier/AGENTS.md` only after all behavior composition and before
preparation. Do not move this seam: injecting earlier makes a bodyless
root instruction truthy and changes its existing behavior-body inheritance.
Keep the injection non-mutating because the foundation registry caches bundles;
foundation's prompt factory resolves these optional files freshly per request.

## Update reporting

Keep report labels separate from update eligibility: a missing mutable cache is
a download, not a newer revision, and an unchecked source is not current.
Use the same word-status vocabulary in every `amplifier update` section,
including verbose output.
Exercise the real Click command in `tests/test_update_reporting.py`; mock only
status/apply boundaries so tests never touch a user's caches or installation.

## Persisted reminder display

`ui.is_displayable_session_message()` is display-only: history and replay hide
only persisted transcript entries marked `ephemeral is True` and
`persisted is True` whose content is a reminder envelope. Keep the original
transcript and its source metadata intact so resume context restores the reminders;
`reminder_placement` controls ordering, not display eligibility.

## Interactive slash completion

Keep completion candidate generation in `ui/completion.py`.  Its live-session
snapshot is rebuilt only at REPL construction and immediately before each
normal `prompt_async()` call; prompt-toolkit completion callbacks may read only
that already-built snapshot.  Do not add discovery, provider, filesystem,
network, or configurator calls to a keypress path.
Keep history search enabled: its prompt-toolkit compatibility path uses the
public buffer insertion hook only for a trailing, whitespace-free leading slash token; arguments stay Tab-only.
Resolve the slash-popup UI setting once at interactive-session construction,
outside prompt-toolkit callbacks and the per-prompt refresh loop.

## Interactive control-flow exits

REPL exit commands return an action from `CommandProcessor`; only the normal
REPL loop may terminate so its shared `finally` runs cleanup and closes TTY input once.
