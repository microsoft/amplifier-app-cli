# Interactive Mode Guide

Interactive chat mode with slash commands for controlling execution, saving work, and managing sessions.

## Overview

Interactive mode starts a chat session where you compose prompts, switch runtime modes (plan, brainstorm, debug, etc.), and manage session state via slash commands.

```bash
amplifier run --mode chat
# Or simply with no prompt:
amplifier run
```

Within an interactive session, you compose prompts and issue slash commands. Runtime mode switching is provided by the [`amplifier-bundle-modes`](https://github.com/microsoft/amplifier-bundle-modes) bundle (`/mode <name>`, `/modes`, `/mode off`); other commands are app-CLI built-ins documented below.

## Slash Commands

| Command | Purpose | Notes |
|---------|---------|-------|
| `/save [file]` | Save transcript | To session directory |
| `/clear` | Clear history | Keeps session active |
| `/status` | Show session info | Active mode, message count, tools count |
| `/tools` | List tools | Shows loaded capabilities |
| `/config` | Show configuration | Full mount plan |
| `/help` | List commands | Quick reference |
| `/stop` | Interrupt execution | Or use Ctrl+C |
| `/modes` | List available modes (from `amplifier-bundle-modes`) | |
| `/mode <name>` | Activate a runtime mode | Mode name or shortcut |
| `/mode off` | Deactivate the current mode | |

The first eight are app-CLI built-ins. The `/mode*` commands come from `amplifier-bundle-modes` when that bundle is composed into your active configuration (which is the case for every standard amplifier bundle).

## Runtime Modes

Runtime modes are runtime behavior overlays that modify how the assistant operates — restricting tools, contributing context, agents, or skills, and gating destructive actions. Plan mode is a common built-in:

```bash
> /mode plan
✓ Mode 'plan' activated

> Analyze auth system and suggest improvements
[AI analyzes without changes — writes blocked]

> /mode off
✓ Mode cleared

> Implement the improvements
[AI now makes changes]
```

See [`amplifier-bundle-modes`](https://github.com/microsoft/amplifier-bundle-modes) for the full list of built-in modes (`plan`, `careful`, `explore`, etc.) and how to author your own.

## Command Details

**/save — Persist Transcript**:
```bash
> /save auth_refactor.json
✓ Saved to ~/.amplifier/projects/<project>/sessions/<session-id>/auth_refactor.json
```

**Saves**: All messages, session config, timestamp. **Location**: Session directory `~/.amplifier/projects/<project>/sessions/<session-id>/`.

**/clear — Reset Context**:
Clears conversation history, session stays active. Use when switching topics or context grows too large.

**/status — Session Information**:
```bash
> /status
Mode: plan | Messages: 42 | Providers: anthropic | Tools: 8
```

**/tools — Capability Discovery**:
```bash
> /tools
filesystem - File operations
bash       - Shell commands
web        - Web search/fetch
task       - Agent delegation
```

## Usage Patterns

### Pattern 1: Safe Code Review

```bash
> /mode plan
✓ Mode 'plan' activated

> Analyze this codebase for security vulnerabilities
[AI provides analysis]

> Show me the top 3 most critical issues
[AI explains issues]

> /mode off
✓ Mode cleared

> Fix the SQL injection vulnerability in auth.py
[AI makes the fix]
```

### Pattern 2: Iterative Refactoring

```bash
> /mode plan
> Review the payment processing module for improvement opportunities
[AI provides recommendations]

> /save payment_analysis.json
✓ Transcript saved

> /mode off
> Implement recommendation #1: Extract payment validation
[AI makes changes]

> /status
Mode: off | Messages: 15 | ...

> /mode plan
> Review the changes we just made
[AI analyzes recent changes]
```

### Pattern 3: Multi-Session Work

**Session 1: Planning**
```bash
> /mode plan
> Create a plan for migrating to the new API
[AI creates detailed plan]

> /save api_migration_plan.json
✓ Transcript saved
> exit
```

**Session 2: Resume and Implement**
```bash
# Resume the most recent session
$ amplifier continue
Resuming session: a1b2c3d4
Messages: 5

> Implement step 1 of the migration plan
[AI implements with full context]

> /save api_migration_progress.json
```

**Alternative: Resume specific session**
```bash
$ amplifier session list
Recent Sessions:
  a1b2c3d4  5 messages
  e5f6g7h8  12 messages

$ amplifier session resume a1b2c3d4
# Or use: amplifier continue
```

### Pattern 4: Tool Discovery

```bash
> /tools
Available Tools:
  filesystem, bash, web, task

> /config
[Shows the full mount plan including providers, tools, agents]

> Use the web tool to fetch documentation from anthropic.com
[AI uses web tool]
```

## Tips & Best Practices

### When to Use Plan Mode

**Good candidates:**
- Large codebase reviews
- Architecture analysis
- Security audits
- Refactoring planning
- Exploring unfamiliar code

**Not needed for:**
- Small, focused changes
- Well-understood modifications
- Following existing patterns

### Managing Context Effectively

**Context grows with messages:**
- Every message adds to context
- Large context = slower responses + higher cost
- Use `/clear` when switching topics

**Save before clearing:**
```bash
> /save before_clear.json
> /clear
# Now start fresh
```

### Organizing Transcripts

**Naming convention suggestions:**
```bash
/save feature_name.json          # Feature work
/save bug_fix_issue_123.json     # Bug fixes
/save review_module_name.json    # Code reviews
/save planning_migration.json    # Planning sessions
```

Saved to session directory at `~/.amplifier/projects/<project>/sessions/<session-id>/`.

### Interactive Mode vs Single Mode

**Use interactive mode when:**
- Iterative development
- Need to adjust mid-task
- Want to review before proceeding
- Working on complex, multi-step tasks

**Use single mode when:**
- One-off commands
- Scripting / automation
- Simple, well-defined tasks

```bash
# Single shot:
amplifier run "Reply with just OK"

# Interactive:
amplifier run --mode chat
```

## Advanced: Bundle-Based Interactive Sessions

You can start interactive sessions with specific bundles:

```bash
# Use a specific bundle
amplifier run --bundle dev --mode chat
```

See:
- [Bundle Guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md) — bundle composition and structure
- [Modes Bundle](https://github.com/microsoft/amplifier-bundle-modes) — the runtime modes system

## Troubleshooting

### "Command not found"

Slash commands only work in interactive chat mode:

```bash
# ✗ Won't work
amplifier run "/mode plan analyze this code"

# ✓ Works
amplifier run --mode chat
> /mode plan
> analyze this code
```

### Transcript not saving

Check permissions on the session directory:

```bash
ls -ld ~/.amplifier/projects/<project>/sessions/<session-id>/
```

### Plan mode not blocking writes

Check `/tools` output to verify filesystem/bash tools are loaded. Confirm `amplifier-bundle-modes` is composed into your active bundle (it is, in every standard amplifier bundle). Verify with `/config`.

## Note: Legacy `/think` and `/do` Commands

Previous documentation referenced `/think` and `/do` as plan-mode toggles. These have been replaced by the unified runtime modes system (`/mode plan` and `/mode off`) provided by `amplifier-bundle-modes`. The legacy commands are no longer wired into the CLI.
