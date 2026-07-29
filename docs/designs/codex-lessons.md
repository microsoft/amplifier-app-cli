# Codex TUI lessons — institutional record

Status: Record of the 2026-07 study of the OpenAI Codex TUI
(`codex-rs/tui/src`, read in source form) and what this repo did about each
lesson. Presentation spec: `tui-v3-cohesive.md`. Architecture decision:
`ADR-0006-full-screen-pinned-interactive-shell.md`.

Verdicts: **adopted** (implemented this round, in the working tree),
**deferred** (worth doing, not this round), **rejected** (considered and
declined, with reasons).

## Lessons table

| Lesson | Codex source | Verdict | Reason |
|---|---|---|---|
| Title/notification sanitization: strip control chars + Trojan-Source bidi + invisible formatting, cap at 240 chars | `terminal_title.rs` | adopted | Untrusted model text is interpolated into OSC sequences; see `ui/repl.py` and `tests/test_title_sanitization.py` |
| Progressive keyboard enhancement (kitty protocol + modifyOtherKeys) so shift+enter is real | `tui.rs`, `keymap.rs` | adopted | `ui/keyboard_protocol.py`; enables queue-vs-steer split (spec §5, §9) |
| Keymap as data feeding both handlers and on-screen hints | `key_hint.rs`, `keymap.rs` | adopted | `ui/key_bindings_table.py`; hints can never drift from bindings |
| Debounced width reflow on resize (~75ms trailing rebuild) | `transcript_reflow.rs` | adopted | `ui/transcript_reflow.py`; drag-resize reflows once, not per-cell |
| Per-(block, width) render cache for the transcript | `history_cell/` layout caching | adopted | `ui/block_render_cache.py`; frozen blocks make the cache sound |
| Bounded span registry for transcript click targets | `chatwidget/` mouse handling | adopted | `ui/transcript_click_spans.py`; single-click affordances, drag stays with terminal (spec §3) |
| Footer that degrades tier-by-tier instead of wrapping | `bottom_pane/footer.rs` | adopted | `ui/footer.py` responsive tiers (spec §6) |
| Native scrollback via insert-history escapes | `insert_history.rs`, `custom_terminal.rs` | rejected | See ADR-0006 amendment: ED3 scrollback purges on resize, per-terminal replay caps, and reflow scheduling complexity outweigh terminal-native search/copy |
| OSC 8 hyperlinks in output | `terminal_hyperlinks.rs` | rejected | Uneven terminal support; conflicts with app-owned click spans and the evidence-reveal interaction; low value inside a full-screen app |
| @-mention file-search popup in the composer | `bottom_pane/file_search_popup.rs`, `mention_codec.rs`, `bottom_pane/mentions_v2/` | deferred | Apply as a second `CompletionProvider` beside the slash palette; needs a bounded async file-index |
| /resume session picker | `resume_picker.rs`, `session_resume.rs` | deferred | Apply via the generic `bottom_pane/list_selection_view.rs` pattern over the existing session store |
| /theme picker with live preview | `theme_picker.rs` | deferred | Tokens already themeable (`layered_repl_style.py` slate/graphite/carbon); needs live restyle + persistence |
| Story/snapshot tests of rendered frames | `snapshots/`, `test_backend.rs` | deferred | Golden-width tests cover layout today; frame snapshots would cover interaction sequences |
| Shimmer animation on the working line | `shimmer.rs`, `frames.rs` | deferred | Working-line glyph pulse (spec §3) is enough for now; shimmer needs per-cell gradient styling |
| Incremental markdown stream commit (only re-render the uncommitted tail) | `markdown_stream.rs`, `streaming/` | deferred | Block cache absorbs most cost; adopt if long streamed answers show redraw lag |
| Paste-burst detection (coalesce rapid key events into one paste) | `bottom_pane/paste_burst.rs` | deferred | Bracketed paste covers modern terminals; burst detection is the legacy fallback |

## Deferred backlog (how to apply)

- **@-mention popup** — register a trigger on `@` in
  `ui/repl.py::SlashCommandCompleter`-style completer or a sibling; back it
  with a bounded, sanitized file index; codex's `mention_codec.rs` shows how
  to round-trip mentions through message text.
- **/resume picker** — list sessions from the session store in a
  palette-style overlay (`ui/command_palette.py` is the local analogue of
  `list_selection_view.rs`); enter resumes, esc closes.
- **/theme live-preview** — cycle `TOKENS` themes in-place and re-style the
  running prompt_toolkit app; persist choice to settings.
- **Story snapshots** — capture rendered frames from the PTY harness
  (`tests/test_tui_pty.py`) into reviewable golden files per interaction
  story.
- **Shimmer** — animate a highlight window across the working line text;
  requires styled-fragment output from the status renderer.
- **Incremental stream commit** — split streamed answers into committed
  (cached) and tail (re-rendered) segments at newline boundaries.
- **Paste-burst** — time-bucket sub-threshold key events in
  `ui/layered_repl_input.py` and flush as one insert.

## Rejected: reasons kept for the record

- **Native scrollback** (the original TUI issue's invariant 4): codex spends
  `insert_history.rs`, `custom_terminal.rs`, and `transcript_reflow.rs`
  effort compensating for terminals purging scrollback on resize (ED3),
  per-terminal replay caps, and reordering hazards between inserted history
  and live UI. ADR-0006 chose a full-screen app with app-owned paging
  instead; the trade-offs are recorded in that ADR's Consequences.
- **OSC 8 hyperlinks**: rejected above; revisit only if evidence links need
  to survive outside the app (plain transcript handoff).
