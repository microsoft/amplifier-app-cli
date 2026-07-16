# Agent guide — amplifier-app-cli

Reference CLI for the Amplifier platform. Source lives in `amplifier_app_cli/`,
tests in `tests/`, documentation in `docs/`. Read files before editing them;
prefer changing existing modules over creating new ones.

## Verify loop (run before claiming done)

| Command | What | Typical runtime |
|---------|------|-----------------|
| `uv run ruff check amplifier_app_cli tests` | lint | <1s |
| `uv run pyright` | types (basic mode, `amplifier_app_cli/` only) | ~4.5s |
| `uv run pytest` | default suite (~1,800 tests; integration deselected) | ~31s |
| `uv run pytest -m integration` | 13 PTY tests (fork a real pty, probe termios) | seconds, needs a real POSIX terminal |

Shortcuts: `just check` runs the first three; `just check-full` adds the
integration marker; `just fmt` formats. See `justfile`.

While iterating, run the focused test file(s) for what you touched
(`uv run pytest tests/test_<area>.py -q`), then the full suite before finishing.

## Module map

Entry flow for the interactive TUI:

```
main.py (click group, thin compat adapters)
  └─ runtime/interactive_resume_loop.py   in-process resume switching
      └─ runtime/interactive_host.py      assembles one interactive session
          ├─ runtime/interactive_*.py     input routing, turn runner, cleanup,
          │                               resources, persistence, repair
          └─ ui/layered_repl*.py          full-screen prompt_toolkit app
              ├─ ui/transcript_blocks.py  typed block rendering (Rich)
              └─ ui/footer.py             persistent two-zone footer
```

- `amplifier_app_cli/runtime/` — session lifecycle: host, turn execution,
  interrupts, persistence, transcript repair, spawn/resume, config resolution.
  No rendering decisions here.
- `amplifier_app_cli/ui/` — presentation and interaction: layered REPL
  surfaces, transcript blocks, footer, approval, palette, agent lanes, slash
  command processing (`command_processor.py` + `command_*.py` mixins).
- `amplifier_app_cli/commands/` — non-interactive click subcommands
  (provider, bundle, init, session, …).
- Single-shot path: `main.py execute_single` → `runtime/single_execution.py`.

`docs/designs/interactive-tui-architecture.md` has the full picture with
diagrams. `docs/MIGRATION-main-decomposition.md` maps the old monolithic
`main.py` (~3,500 lines) to the current modules.

## Presentation source of truth

`docs/designs/tui-v3-cohesive.md` is the approved presentation spec (colors,
glyphs, labels, layout, hints). Theme tokens live in
`amplifier_app_cli/ui/layered_repl_style.py` (`TOKENS` / `THEMES`) — never
hardcode hex values in rendering surfaces. Mechanisms (trust postures,
steering, evidence, ledger) are governed by
`docs/decisions/ADR-0005-interaction-modes-and-trust-postures.md` and
`docs/decisions/ADR-0006-full-screen-pinned-interactive-shell.md`.

TUI interaction realities worth knowing (spec sections 3, 4, 6, 9):

- shift+enter (queue a next-turn message mid-turn) works natively on kitty,
  WezTerm, foot, ghostty, iTerm2 3.5+, and recent xterm via progressive
  keyboard enhancement (kitty keyboard protocol + xterm modifyOtherKeys, see
  `amplifier_app_cli/ui/keyboard_protocol.py`); alt+enter is the fallback on
  legacy terminals. The footer's running hint advertises shift+enter, unless
  the startup capability probe (`amplifier_app_cli/ui/terminal_probe.py`)
  finds no kitty keyboard protocol support, in which case it advertises
  alt+enter.
- Keybindings live in one table (`amplifier_app_cli/ui/key_bindings_table.py`)
  that drives both dispatch (`layered_repl_keys.py`) and footer hint labels
  (`footer.py`), so keys and hints cannot drift. Notable chords: ctrl-g (edit
  draft in `$VISUAL`/`$EDITOR`), alt+up (recall the newest queued message),
  y/a/d (approval decide), ctrl-a (approval full detail).
- Transcript click affordances are single-click, no-drag actions with keyboard
  equivalents: expand/collapse tool output (ctrl-o), open rewind at a turn
  rule (ctrl-r), reveal evidence for an answer (ctrl-e). Drag/selection stays
  with the terminal.
- The footer is responsive: the `mode <id>` prefix shows at >=100 columns
  (the trust dial abbreviates first); below that the prefix is dropped.

## Golden tests and regeneration (readable snapshots)

`tests/test_transcript_golden_widths.py` and
`tests/test_footer_golden_widths.py` pin the exact rendered screens as plain
text files under `tests/goldens/` — transcript blocks at widths 40/80/120
plus a full-sequence gallery at 40/80/97/120 (`transcript/gallery_<w>.txt`),
and the idle footer at 80/120/198 (`footer/idle_<w>.txt`). A failure prints a
unified diff of the screen; read it as a UI diff (before/after screens), and
review checked-in golden diffs in PRs the same way. A second layer of
semantic marker assertions (`GOLDEN_MARKERS`) guards meaning independently of
exact layout.

Snapshot hygiene: golden inputs are deterministic (fixed `Telemetry` values,
fixed session ids); environment-dependent artifacts (project/tmp paths, OSC 8
hyperlinks, trailing padding) are canonicalized by
`tests/helpers.normalize_for_golden` — route every golden write and read
through it (`helpers.write_golden` / `helpers.assert_matches_golden`). Never
hand-edit files under `tests/goldens/`.

```bash
uv run python tests/regen_goldens.py          # dry run: list pending golden changes (exit 1 if any)
uv run python tests/regen_goldens.py --write  # rewrite tests/goldens/**/*.txt (prunes stale files)
# or: just regen-goldens / just goldens-status
```

**Policy:** any change to user-visible rendering must add or update a golden
in the same commit; review golden diffs as UI diffs. An *intentional*
presentation change also updates `docs/designs/tui-v3-cohesive.md` in that
commit. Never regen to make an *unintended* diff pass — that is a
regression, not a regen.

## Invariant suites (boundary tests)

These encode architectural contracts; if one fails, fix your change, not the
test:

- `tests/test_private_api_boundaries.py` — no cross-module private-API reach-ins
- `tests/test_main_entrypoint_boundary.py` — `main.py` stays a thin adapter
- `tests/test_command_processor_boundary.py` — command processor facade contract
- `tests/test_layered_repl_boundary.py` — layered REPL surface contract
- `tests/test_runtime_config_boundaries.py` — runtime config resolution seams
- `tests/test_paste_execution_boundary.py` — paste handling vs execution split

## Conventions

- `uv` for everything (`uv sync --all-groups`, `uv run …`). Python 3.11+.
- Keep public APIs typed and modules focused; avoid files over 500 lines when
  practical.
- Never commit credentials, API keys, `.env` files, or other secrets.
- Validate input at system boundaries and sanitize filesystem paths.
- Make only the changes the task requires; preserve unrelated worktree changes.
