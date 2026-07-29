# Interactive TUI architecture

How the full-screen interactive shell is put together: the `runtime/` vs `ui/`
split, the input → command → turn → approval → render flow, and the
storage-vs-viewport separation for the transcript.

Governing decisions:

- [ADR-0005 — Interaction Modes and Trust Postures](../decisions/ADR-0005-interaction-modes-and-trust-postures.md)
  (modes, approvals, deny-and-continue, steering, evidence, ledger).
- [ADR-0006 — Full-Screen Pinned Interactive Shell](../decisions/ADR-0006-full-screen-pinned-interactive-shell.md)
  (why a layered prompt_toolkit application replaced the line-based REPL).

Presentation (colors, glyphs, labels, layout, hints) is specified by
[tui-v3-cohesive.md](tui-v3-cohesive.md); theme tokens live in
`amplifier_app_cli/ui/layered_repl_style.py`. The old monolithic `main.py` is
mapped to these modules in
[MIGRATION-main-decomposition.md](../MIGRATION-main-decomposition.md).

## The `runtime/` vs `ui/` split

- **`amplifier_app_cli/runtime/`** owns session *lifecycle and mechanism*:
  assembling a session, routing submissions, executing turns, interrupt
  handling, persistence, transcript repair, resume switching. It makes no
  rendering decisions; everything it needs from the presentation layer is
  injected through typed request/dependency dataclasses (patchable seams
  pinned by `tests/test_main_entrypoint_boundary.py` and
  `tests/test_runtime_config_boundaries.py`).
- **`amplifier_app_cli/ui/`** owns *presentation and interaction*: the layered
  prompt_toolkit application and its surfaces (composer, footer, approval bar,
  palette, agent lanes, notices), typed transcript blocks rendered with Rich,
  slash-command processing, and mode/trust display.

```mermaid
flowchart TD
    subgraph entry [Entry]
        MAIN["main.py<br/>click group + thin compat adapters"]
    end
    subgraph runtime [runtime/ — lifecycle & mechanism]
        LOOP["interactive_resume_loop.py<br/>in-process resume switching"]
        HOST["interactive_host.py<br/>assemble one interactive session"]
        RES["interactive_resources.py<br/>session, store, command processor"]
        ROUTER["interactive_input.py<br/>InteractiveInputRouter"]
        TURN["interactive_turn.py<br/>InteractiveTurnRunner"]
        EXEC["turn_execution.py + execution_interrupt.py"]
        PERSIST["session_persistence.py + transcript_repair.py"]
        RUNNER["interactive_repl_runner.py<br/>REPL lifecycle owner"]
    end
    subgraph ui [ui/ — presentation & interaction]
        REPL["layered_repl*.py<br/>full-screen prompt_toolkit app"]
        CMD["command_processor.py<br/>+ command_*.py mixins"]
        BLOCKS["transcript_blocks.py<br/>typed blocks (Rich)"]
        FOOTER["footer.py<br/>two-zone footer"]
        VIEW["layered_transcript.py + terminal_transcript.py<br/>viewport + storage"]
    end
    MAIN --> LOOP --> HOST
    HOST --> RES
    HOST --> ROUTER
    HOST --> TURN --> EXEC
    HOST --> PERSIST
    HOST --> RUNNER --> REPL
    ROUTER --> CMD
    REPL --> BLOCKS
    REPL --> FOOTER
    REPL --> VIEW
```

Single-shot (`amplifier run "prompt"`) bypasses the TUI entirely:
`main.py execute_single` → `runtime/single_execution.py`.

## Input → command → turn → approval → render

One composer submission flows through a single dispatch path
(`runtime/interactive_input.py InteractiveInputRouter`):

```mermaid
sequenceDiagram
    participant User
    participant App as ui/layered_repl*.py<br/>(composer, key bindings)
    participant Router as runtime/interactive_input.py
    participant Cmd as ui/command_processor.py
    participant Turn as runtime/interactive_turn.py
    participant Approve as ui approval surface<br/>(layered_repl_approval.py)
    participant View as transcript viewport

    User->>App: type + enter
    App->>Router: submission (text / attachments)
    alt starts with "/"
        Router->>Cmd: process_input → handle_command
        Cmd-->>View: command output (blocks / notices)
    else prompt
        Router->>Turn: run turn (mentions expanded,<br/>mode + trust applied)
        Turn->>Turn: await_turn_or_interrupt<br/>(esc → ExecutionInterruptController)
        Turn->>Approve: tool needs approval<br/>(approval bar replaces composer)
        Approve-->>Turn: allow once / always / deny
        Turn-->>View: streamed events → typed blocks<br/>(narration, tool, plan, answer, terminator)
        Turn-->>App: turn outcome (ledger, footer state)
    end
```

Key properties:

- **Mid-turn input** is routed, not blocked: enter steers the running turn,
  queued messages run at turn end (spec section 5, ADR-0005 steering).
- **Approvals** suspend the composer, not the event loop; denial follows
  deny-and-continue (ADR-0005) and can defer to the needs-you queue.
- **Interrupts** (esc) go through `ExecutionInterruptController` so the
  session cancels cooperatively and the turn terminator still renders.
- **Rendering** is always typed: runtime code emits blocks/events; only
  `ui/transcript_blocks.py TranscriptRenderer` decides what they look like
  (goldens: `tests/test_transcript_golden_widths.py`).

## Transcript: storage vs viewport

The transcript is stored and displayed by different objects with different
lifetimes:

```mermaid
flowchart LR
    RICH["Rich Console output<br/>(TranscriptRenderer, tool output,<br/>stdout offload)"]
    STORE["ui/terminal_transcript.py<br/>TerminalTranscript<br/><i>storage</i>: parses terminal writes into<br/>styled lines; bounded (max_lines);<br/>drops control bytes, keeps SGR styles"]
    VIEWPORT["ui/layered_transcript.py<br/>LayeredTranscriptView<br/><i>viewport</i>: windowed buffer (512 lines),<br/>scrolling, mouse selection, copy"]
    PERSIST2["runtime/session_persistence.py +<br/>session_store.py<br/><i>durable</i>: message transcript on disk,<br/>repaired on resume"]

    RICH --> STORE --> VIEWPORT
    RICH -. "session messages,<br/>not pixels" .-> PERSIST2
```

- **Storage** (`TerminalTranscript`) captures everything written to the
  terminal — including ANSI-styled output from Rich — as compact immutable
  lines, so scrollback survives resize and re-render without re-executing
  anything.
- **Viewport** (`LayeredTranscriptView`) is a prompt_toolkit `BufferControl`
  window over that storage: it materializes only the visible window
  (~512 lines), and owns scrolling, selection, and copy behavior.
- **Durable transcript** is separate again: `SessionStore` persists the
  *conversation* (messages, metadata), not the rendered pixels;
  `runtime/transcript_repair.py` reconciles it on resume.

This separation is why the TUI can re-theme, resize, and window scrollback
cheaply, and why golden tests hash the *renderer output* rather than the
screen: presentation is a pure function of typed blocks plus theme tokens.

## Testing map

| Concern | Suite |
|---|---|
| Typed block rendering (exact) | `tests/test_transcript_golden_widths.py` |
| Footer rendering (exact) | `tests/test_footer_golden_widths.py` |
| Storage parser (ANSI, bounds) | `tests/test_terminal_transcript.py` |
| Layered REPL surfaces / layout | `tests/test_layered_repl*.py` |
| Input routing / turns / interrupts | `tests/test_interactive_*.py`, `tests/test_turn_execution.py` |
| Architectural boundaries | `tests/test_private_api_boundaries.py` and the `*_boundary*.py` suites |
| Real PTY behavior | `tests/test_tui_pty.py` (`uv run pytest -m integration`) |

Golden regeneration: `uv run python tests/regen_goldens.py --write`
(see `AGENTS.md`).
