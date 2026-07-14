# ADR-0006: Full-Screen Pinned Interactive Shell

Status: Accepted

## Context

The original TUI issue requires all output to use native terminal scrollback and
forbids alternate-screen applications. Later product feedback repeatedly
requires the interactive CLI to take over the terminal, keep the composer and
footer pinned at the bottom, and provide an app-owned continuous chat viewport.

Those requirements cannot both hold in one terminal process. Native scrollback
cannot keep application chrome pinned while the user navigates older output.

## Decision

The later full-screen product direction supersedes interaction invariant 4 in
the original TUI issue for interactive sessions.

Interactive Amplifier sessions use a full-screen prompt-toolkit application
with:

- a continuous transcript viewport above the composer;
- a multi-line composer and stable footer pinned at the bottom;
- complete in-session transcript retention with bounded viewport paging;
- explicit PageUp, PageDown, and mouse-wheel history navigation;
- terminal restoration and a plain transcript handoff when the app exits.

Non-interactive commands and redirected output continue to use normal terminal
output without an alternate screen.

## Consequences

- The interactive shell matches the later Codex/Claude-style UX direction.
- Transcript storage and viewport rendering are separate so long sessions do
  not rebuild the complete prompt-toolkit document on every streamed chunk.
- PTY acceptance tests must cover pinned chrome, resize, tail following, paused
  history, old-page reachability, approvals, and editable input while running.
- The implementation must not be described as literally compliant with the
  original native-scrollback invariant; this ADR is the intentional exception.

## Non-Goals

This decision does not change batch output, JSON output, or shell command
behavior. It also does not permit transcript truncation merely to bound the
visible prompt-toolkit viewport.
