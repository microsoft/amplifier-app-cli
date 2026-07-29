# TUI v3 — Cohesive: presentation specification

Status: Approved design, source of truth for the interactive TUI's presentation.
Source: claude.ai/design project "Amplifier TUI design refinement",
file `Amplifier TUI v3 - Cohesive.dc.html` (project 0eef1524-817c-4122-bc86-5e58734a950e).
Scope: how the layered REPL *presents* — colors, glyphs, labels, layout, hints.
Mechanisms (trust postures, steering, evidence, ledger) are per ADR-0005/ADR-0006.

Any intentional change to this presentation must update this file and the golden
tests (`tests/test_transcript_golden_widths.py`, `tests/test_footer_golden_widths.py`)
in the same commit.

## 1. Theme tokens

Default theme is **slate**. `graphite` (warm) and `carbon` (cool, high contrast)
are alternates behind the same token names.

| Token      | slate     | graphite  | carbon    | Role |
|------------|-----------|-----------|-----------|------|
| `bg-term`  | `#232937` | `#211e1a` | `#14171d` | transcript background |
| `bg-chrome`| `#191d27` | `#181512` | `#0f1116` | footer / chrome background |
| `bg-tab`   | `#2b3243` | `#2c2722` | `#1f242e` | selection highlight |
| `fg`       | `#c9d1e0` | `#d6cfc4` | `#cdd6e4` | body text |
| `bright`   | `#eef2f8` | `#f2ede4` | `#f4f7fc` | emphasis text |
| `dim`      | `#6b7487` | `#8a8175` | `#65718a` | secondary text |
| `dimmer`   | `#4a5163` | `#575047` | `#3d4657` | tertiary / hints |
| `green`    | `#7ec699` | `#98c28b` | `#6fd39c` | success, prompt char, yield |
| `orange`   | `#e0a458` | `#dba15c` | `#e9b14f` | active, working, needs-you |
| `red`      | `#e06c75` | `#d97371` | `#ef6e7b` | blocked, deny |
| `blue`     | `#7aa2f7` | `#90a4d8` | `#6f9df2` | plan mode, info headers |
| `teal`     | `#6fc3c3` | `#80bcae` | `#57c8c8` | brainstorm, commands, steer, evidence |
| `rule`     | `#333b4d` | `#3a352e` | `#2a3140` | separators, turn rules |

## 2. Mode identity

Five modes; each has one accent color used in exactly three places
("tint = badge + footer + composer edge"):

| Mode        | Color   | Trust summary (footer)                          |
|-------------|---------|--------------------------------------------------|
| chat        | dim     | `ask all · auto read`                            |
| plan        | blue    | `read-only`                                      |
| brainstorm  | teal    | `no tools`                                       |
| build       | green   | `auto read,test · ask write,net,spend`           |
| auto        | orange  | `auto read,write · classifier-gated`             |

- User lines stamp the mode into scrollback: `❯ [mode] text` — green bold `❯ `,
  mode-colored `[mode] `, bright text. `mt` 10px-equivalent blank spacing before.
- Composer left edge: 2px accent in the mode color (`rule` color for chat).
- Footer shows `mode <id>` in the mode color.
- Shift-Tab cycles modes; `[mode]` label in the composer is the same cycle affordance.
- Ctrl-P independently cycles permission posture (chat → build → plan → auto →
  bypass → chat). Mode and permission are two orthogonal five-state cycles
  that share four names but diverge at the fifth (brainstorm vs bypass) --
  they have always been separate policy dimensions (ADR-0005) and now have
  separate controls to match.

## 3. Block grammar presentation

Calmer density: tool output and internals collapse to one dim line; telemetry
only ever appears as a suffix, never its own block.

| Block        | Presentation |
|--------------|--------------|
| Narration    | `● ` bright + body in `fg` |
| Tool (collapsed) | `  ● <summary> ` in `dim` + `· click or ctrl-o expand` in `dimmer`; expanded body indented 6 spaces in `dimmer`; expand/collapse toggles in place |
| Tool (expanded, long output) | head+tail elision: first 8 lines, then `… +K lines · full via ctrl-o again or transcript export` in `dim`, then last 4 lines (body lines stay `dimmer`) |
| Diff         | header `· <path> (+N −M)` — `fg` path (`→ <move_path>` in `fg` for renames), `+N` green, `−M` red, punctuation dim; hunk body `  <4-char right-aligned line number in dimmer> <sign><content>` — `+` lines green, `−` lines red, context in `fg`, `@@` headers and annotation lines dimmer |
| Command echo (while running) | `  └ ` dimmer + `$ <cmd>` dim; replaced by the collapsed tool line when the step completes |
| Plan header  | `· ` orange + title in `fg` + telemetry suffix `(Ns · ↓ x.xk tok)` in `dim` |
| Plan item    | pending `  □ ` dimmer + text dim; active `  ■ ` orange + text bright bold; done `  ✔ ` green + text dim |
| Blocked      | `  ⊘ blocked · <action> ` red + `· <reason> · finding safer path` dim |
| Recap        | `✳ ` dimmer + italic dim one-liner: `Goal: <goal>. Next: <next>.` |
| Answer       | body in `fg`, key phrases bright bold, identifiers teal; clickable → evidence reveal |
| Evidence     | header `· Evidence  1/2 · ←/→ select · enter expand · esc close` (teal dot, teal bold "Evidence", dimmer hints); rows `  ¹ "claim" → tool summary` (teal superscript, fg claim, dim arrow+tool) |
| Working line | animated glyph cycle `✳ ✦ ✧ ✦` orange (pulse) + `working · <N>s · ↓ <x.x>k tok · <n> agent(s) · ` dim + `esc to interrupt · type to steer` dimmer; removed when the turn ends |
| Subagent tree| `  ├─ ● name · activity · $cost` / `  └─ …` dimmer glyph, dim text; `✔` green when done |
| Steer queued | `  ↳ ` teal + `steer queued: "<text>" ` teal + `· applies at next step boundary` dimmer |
| Session header | version line bright bold; `Bundle: … | Provider: … · session <id>` dim |

Click affordances in the transcript are single-click (no-drag) actions, and each
has a keyboard equivalent: collapsed tool line → click or `ctrl-o` toggles
expansion; answer → click or `ctrl-e` reveals evidence; turn rule → click or
`ctrl-r` opens the rewind picker. Click-and-drag is never captured — text
selection stays with the terminal.

## 4. Turn rules (terminator + checkpoint)

Every completed turn ends with a horizontal rule: a 1px line in `rule` color with
a right-aligned label. The rule IS the rewind checkpoint (single-click, no drag,
or ctrl-r).

- Label format: `<secs>s · <tok>k tok, <cache>% cached · $<cost> · <yield>`
  - Yield examples: `answer` · `3 files · +142/−38 · tests ✔` · `interrupted` · `plan ready`
- Label color: `dim` when the turn shipped (files/diff/tests), `dimmer` when answer-only.
- Footer shows ` ▲` in green after the cost when the last turn shipped.

## 5. Bottom stack (top to bottom)

Order of surfaces below the transcript: notice (floating, right-aligned, dim,
~4s auto-dismiss) → palette → agent lanes → rewind bar → queued-message bar →
approval bar → composer → footer. Only relevant surfaces are visible.

The bottom stack is visually separated from the transcript by a full-width
horizontal rule row (`─` in the `rule` color) — the terminal rendition of the
mockup's `border-top`. The composer and footer sit on `bg-chrome`; on truecolor
terminals (`COLORTERM=truecolor`) the app must request 24-bit color so the
`bg-term`/`bg-chrome` distinction survives (256-color quantization collapses it).

### Composer
- `[mode]` clickable mode-colored label, green bold `❯ `, then input.
- Placeholder: `Message Amplifier…  ( / commands · shift+tab mode · ctrl-p perms · enter send · type mid-turn to steer )`
- Hidden while an approval is pending.

### Approval bar (replaces composer)
- `Approval required ·` orange bold, then the prompt in `fg`, then options inline:
  `[y] Allow once`, `[a] Allow always`, `[d] Deny`.
- The bracketed shortcut prefix renders `dimmer` when unselected; the selected
  option renders it inside the `bg-tab` highlight. In the narrow ratio fallback
  the shortcut prefixes are dropped (the bare selected label shows; `ctrl-a`
  remains the escape hatch to the full detail).
- Selected option: `› ` prefix, bright on `bg-tab`, bold. Deny in red when unselected.
- Keys: arrows/tab cycle, enter confirm, esc = deny; `y`/`a`/`d` decide
  directly; `ctrl-a` prints an `Approval request` full-detail transcript block
  while the bar stays active.

### Palette
- Opens when input starts with `/`. Rows: command in teal (fixed min width),
  description (`fg` for the selected row, `dim` otherwise), tag (`built-in` /
  `skill` / `mcp`) in dimmer small caps.
- When the filter is exactly `/`, group headers appear in phase order:
  Setup · During · Parallel · Ship · Between · Repair (uppercase, dimmer).
- Enter runs the selected row; esc closes.

### Agent lanes (ctrl-t)
- Header: `Agent lanes` bright bold + `· ↑↓ select · enter focus · esc close` dimmer.
- Lane row (aligned columns): `  <glyph> <name> · <activity> · <elapsed> · $<cost>`
  — glyph `◐` running (teal), `■` working (fg), `✔` done (dim/green).
- Enter/click focuses the subagent's own transcript; banner:
  `focused: <name> · subagent of <parent-id> · own context window · results report back to parent · esc back`.

### Rewind bar (ctrl-r or click a turn rule)
- `rewind › <id> · $<cost> · <label>` in orange, `‹ ›` to move between
  checkpoints, `enter fork` (bright on bg-tab), `esc close` (dimmer).

### Queue vs steer (mid-turn input)
- Enter mid-turn = steer this turn (steer line in transcript, teal).
- Shift+Enter (kitty/CSI-u terminals; alt+enter everywhere, see §9) = queue a
  full next-turn message; bar: `▹ queued next: "<text>" · runs when this turn ends`
  orange with a `dimmer` suffix ` · alt+up edit`; footer badge ` · q1` orange;
  auto-runs at turn end. `alt+up` recalls the newest queued message (text and
  attachments) back into the composer for editing.

## 6. Footer (single row, bg-chrome)

Left side, dim, with `·` separators in dimmer:
`mode <id>` (mode color) `· <trust summary> · <bundle> · <session-short-id> · $<cost>`
`▲` (green, last turn shipped) ` · q<n>` (orange, queued) and, when decisions
are deferred: `<n> decision(s) waiting · ctrl-y` in orange, clickable.

Right side, dimmer, context-sensitive hints:

| State          | Hint |
|----------------|------|
| idle           | `/ commands · shift+tab mode · ctrl-t tasks · ctrl-p perms` |
| running        | `esc interrupt · enter steer · shift+enter queue` |
| approval open  | `arrows select · enter confirm · esc deny` |
| palette open   | `↑↓ select · enter run · esc close` |
| lane focused   | `esc back to parent · transcript is the subagent's own` |

Idle hints prioritize the two ADR-0005 controls (mode, permission) alongside
the always-available `/ commands` and `ctrl-t tasks` hints -- the footer
never shows more than four hints (see Responsive behavior below). Tasks
keeps narrow-width priority over permission (it was already protected at
tight widths); permission posture is additive at the widest slot and is
the first to yield when space is tight. `ctrl-p` remains discoverable via
the `?` shortcut-help overlay when it doesn't fit.

The running hint advertises `shift+enter`, which queues natively on terminals
speaking the kitty keyboard protocol or xterm modifyOtherKeys (§9); alt+enter
is the fallback on legacy terminals. The label is probe-dependent: when the
startup capability probe finds no kitty keyboard protocol support, the hint
reads `alt+enter queue` instead (the labels come from the keymap table via
`hint_label` with capability overrides).

### Responsive behavior

The footer never wraps: both zones degrade to fit the terminal width. Hints
degrade first — the right zone steps down through levels (three hints → three
compact hints → two → one → none) and, within each hint level, the left state
zone tries its tiers richest-first. State tiers, widest first:

| Tier | ≥100 cols only | Left-zone state |
|------|----------------|-----------------|
| full         |     | `mode <id> · <full trust dial> · <bundle ≤24> · <sess> · $<cost> ▲` |
| wide-compact | yes | `mode <id> · <abbrev dial: r,t,w,n,$> · <bundle ≤14> · …` |
| wide-tight   | yes | `mode <id> · <glyph dial: a:… ?:…> · <bundle ≤14> · …` |
| compact      |     | `<id> · <abbrev dial> · <bundle ≤14> · …` |
| tight        |     | `<id> · <glyph dial> · <bundle ≤10> · $<cost>▲` (cost space dropped) |

At 100 columns and wider the `mode <id>` prefix is preserved by abbreviating
the trust dial (the wide-compact/wide-tight tiers) before the prefix is ever
dropped; below 100 columns the prefix gives way to the trust dial and cost
(compact/tight tiers only). When even the tight tier cannot fit, an essential
state — posture, cost, needs-you — is fitted field by field. During an
approval, state may shrink to essentials before decision hints are sacrificed.

## 7. Terminal title

`amplifier-app-cli — Amplifier — <activity> — <bundle> — <session-short-id>`
with a spinner glyph (`✳ ✦ ✧ ✦`) prefix while running; `ready` when idle.

## 8. Needs-you queue

Denials escalate to a batch queue, never a halt (ADR-0005 deny-and-continue).
`ctrl-y` or clicking the footer badge prints the queue: header
`· Needs you  <n> deferred decision(s)` orange; each row is actionable inline
with a bracketed suggested action in green on `bg-tab`.

## 9. Keybindings (presentation-relevant)

| Key        | Action |
|------------|--------|
| shift+tab  | cycle mode (chat → build → plan → auto → brainstorm → chat) |
| ctrl-p     | cycle permission posture (chat → build → plan → auto → bypass → chat), independent of mode |
| ctrl-t     | toggle agent lanes |
| ctrl-l     | print session ledger to scrollback |
| ctrl-y     | show needs-you queue |
| ctrl-r     | open rewind picker |
| esc        | close topmost overlay; if none and running, interrupt |
| enter      | approval confirm / palette run / submit / steer |
| shift+enter| queue full next-turn message mid-turn (alt+enter on legacy terminals) |
| alt+enter  | queue full next-turn message mid-turn (works everywhere) |
| ctrl-g     | edit the composer draft in `$VISUAL`/`$EDITOR`, round-trip back |
| alt+up     | recall the newest queued message into the composer for editing |
| y / a / d  | approval decide: allow once / allow always / deny |
| ctrl-a     | approval full detail — prints an `Approval request` transcript block while the bar stays active |

The app requests progressive keyboard enhancement (kitty keyboard protocol +
xterm modifyOtherKeys), so shift+enter queues natively on kitty, WezTerm, foot,
ghostty, iTerm2 3.5+, and recent xterm. Legacy terminal input cannot
distinguish shift+enter from enter, so on terminals without either protocol
alt+enter is the fallback; it works everywhere.
