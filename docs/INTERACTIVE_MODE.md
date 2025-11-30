# Interactive Mode Guide

Interactive chat mode with slash commands for controlling execution, saving work, and managing sessions.

## Two Execution Modes

```
┌─────────────────────────────────────────────────────────┐
│ Normal Mode (default)                                    │
│ • AI reads and modifies files                           │
│ • All tools enabled (write, edit, bash)                 │
│ • Active development                                    │
├─────────────────────────────────────────────────────────┤
│ Plan Mode (/think)                                       │
│ • Read-only - write operations blocked                  │
│ • Analysis and planning without changes                 │
│ • Toggle: /think (enter) | /do (exit)                   │
└─────────────────────────────────────────────────────────┘
```

## Slash Commands

| Command | Purpose | Notes |
|---------|---------|-------|
| `/think` | Enter plan mode | Read-only, blocks writes |
| `/do` | Exit plan mode | Re-enables modifications |
| `/save [file]` | Save transcript | To `.amplifier/transcripts/` |
| `/clear` | Clear history | Keeps session active |
| `/status` | Show session info | Mode, messages, tools count |
| `/tools` | List tools | Shows loaded capabilities |
| `/config` | Show configuration | Full mount plan |
| `/help` | List commands | Quick reference |
| `/stop` | Interrupt execution | Or use Ctrl+C |

### Command Details

**/think - Plan Mode** (read-only analysis):
```bash
> /think
✓ Plan Mode enabled

> Analyze auth system and suggest improvements
[AI analyzes without changes]

> /do
✓ Plan Mode disabled

> Implement the improvements
[AI now makes changes]
```

**Use for**: Code review, architecture analysis, refactoring planning, security audits.

**/save - Persist Transcript**:
```bash
> /save auth_refactor.json
✓ Saved to .amplifier/transcripts/auth_refactor.json
```

**Saves**: All messages, session config, timestamp. **Location**: `.amplifier/transcripts/` (gitignored).

**/clear - Reset Context**:
Clears conversation history, session stays active. Use when switching topics or context grows too large.

**/status - Session Information**:
```bash
> /status
Plan Mode: OFF | Messages: 42 | Providers: anthropic | Tools: 8
```

**/tools - Capability Discovery**:
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
> /think
> Analyze this codebase for security vulnerabilities
[AI provides analysis]

> Show me the top 3 most critical issues
[AI explains issues]

> /do
> Fix the SQL injection vulnerability in auth.py
[AI makes the fix]
```

### Pattern 2: Iterative Refactoring

```bash
> /think
> Review the payment processing module for improvement opportunities
[AI provides recommendations]

> /save payment_analysis.json
✓ Transcript saved

> /do
> Implement recommendation #1: Extract payment validation
[AI makes changes]

> /status
Session Status:
  Plan Mode: OFF
  Messages: 15
  [...]

> /think
> Review the changes we just made
[AI analyzes recent changes]
```

### Pattern 3: Multi-Session Work

**Session 1: Planning**
```bash
> /think
> Create a plan for migrating to the new API
[AI creates detailed plan]

> /save api_migration_plan.json
✓ Transcript saved
> exit
```

**Session 2: Resume and Implement**
```bash
# Resume the planning session
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
  a1b2c3d4  2024-10-15 14:30  5 messages
  e5f6g7h8  2024-10-14 09:15  12 messages

$ amplifier session resume a1b2c3d4
# Or use: amplifier continue
```

### Pattern 4: Tool Discovery

```bash
> /tools
Available Tools:
  filesystem, bash, web, task

> /config
[Shows that web tool is using specific config]

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
/save feature_name_date.json          # Feature work
/save bug_fix_issue_123.json          # Bug fixes
/save review_module_name.json         # Code reviews
/save planning_migration.json         # Planning sessions
```

**Transcript location:**
- Saved to `.amplifier/transcripts/`
- Git-ignored by default (contains your conversations)
- Can be shared with team for collaboration

### Interactive Mode vs Single Mode

**Use interactive mode when:**
- Iterative development
- Need to adjust mid-task
- Want to review before proceeding
- Working on complex, multi-step tasks

**Use single mode when:**
- One-off commands
- Scripting/automation
- Simple, well-defined tasks

## Command Quick Reference

| Command | Purpose | Args |
|---------|---------|------|
| `/think` | Enter plan mode (read-only) | None |
| `/do` | Exit plan mode | None |
| `/save [file]` | Save transcript | Optional filename |
| `/clear` | Clear conversation history | None |
| `/status` | Show session info | None |
| `/tools` | List available tools | None |
| `/config` | Show configuration | None |
| `/help` | Show command list | None |
| `/stop` | Stop execution | None |

## Advanced: Profile-Based Interactive Sessions

You can start interactive sessions with specific profiles:

```bash
# Use development profile (includes more tools)
amplifier run --profile dev --mode chat

# Use general profile (includes logging hooks)
amplifier run --profile general --mode chat
```

**→ [Profile Authoring](https://github.com/microsoft/amplifier-profiles/blob/main/docs/PROFILE_AUTHORING.md)** for profile details.

## Troubleshooting

### "Command not found" Error

Slash commands only work in interactive chat mode:

```bash
# ✗ Won't work
amplifier run "/think analyze this code"

# ✓ Works
amplifier run --mode chat
> /think
> analyze this code
```

### Transcript Not Saving

Check permissions on `.amplifier/transcripts/`:

```bash
mkdir -p .amplifier/transcripts
chmod 755 .amplifier/transcripts
```

### Plan Mode Not Blocking Writes

This may happen if write tools aren't properly registered. Check `/tools` output to verify filesystem/bash tools are loaded.
