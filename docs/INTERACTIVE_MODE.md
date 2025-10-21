# Interactive Mode Guide

This guide covers the powerful interactive features available in Amplifier's chat mode.

## Overview

Interactive mode (`amplifier run --mode chat`) provides a conversational interface with the AI, enhanced by slash commands that control execution, save work, and manage sessions.

## Visual Features

The CLI provides enhanced visual formatting for a better development experience:

### Markdown Rendering

LLM responses are displayed with proper markdown formatting:
- **Bold** and *italic* text
- `Inline code` and code blocks with syntax highlighting
- Bulleted and numbered lists
- Headers and blockquotes

### Live Progress Feedback

During LLM operations, you see real-time feedback:
```
Processing... (3.2s ctrl+c to interrupt)
```

The timer updates continuously, showing you the system is working. Press `ctrl+c` to interrupt if needed.

### Tree-Style Output

Tool calls and outputs use visual hierarchy for clarity:
```
● read_file(path="config.yaml")
  ⎿  api_key: ${API_KEY}
     timeout: 30
     max_retries: 3

● bash(command="ls -la")
  ⎿  total 64
     drwxr-xr-x  12 user  staff  384
  ... (25 more lines)
```

### Configurable Truncation

Tool output is truncated by default (first 3 lines) to keep conversations readable. The "... (N more lines)" indicator shows how much was truncated.

To change truncation settings, configure in your profile:
```yaml
ui:
  tool_output_lines: 5    # Show first 5 lines
  tool_output_lines: -1   # Show all output (verbose)
```

See [Profile Documentation](../amplifier_app_cli/data/profiles/README.md#ui-configuration) for all UI options.

### Two Execution Modes

**Normal Mode** (default):
- AI can read and modify files
- Tools like `write`, `edit`, `bash` are enabled
- Best for active development

**Plan Mode** (`/think`):
- AI can only read files and plan
- Write operations are blocked
- Best for analysis and planning without changes

## Slash Commands Reference

### Planning Commands

#### `/think` - Enter Plan Mode
Enables read-only mode for thoughtful analysis without modifications.

**When to use:**
- Reviewing a complex codebase before changes
- Getting AI analysis of architecture
- Planning multi-step refactoring
- Exploring unfamiliar code safely

**Example:**
```bash
> /think
✓ Plan Mode enabled - all modifications disabled

> Analyze the authentication system and suggest improvements
[AI provides analysis without making changes]

> /do
✓ Plan Mode disabled - modifications enabled

> Implement the authentication improvements
[AI now makes the suggested changes]
```

#### `/do` - Exit Plan Mode
Returns to normal mode where AI can make modifications.

### Session Management Commands

#### `/save [filename]` - Save Transcript
Saves your conversation history to `.amplifier/transcripts/`.

**Arguments:**
- `filename` (optional): Custom filename. If omitted, uses timestamp.

**Example:**
```bash
> /save auth_refactor.json
✓ Transcript saved to .amplifier/transcripts/auth_refactor.json
```

**What's saved:**
- All messages (user and AI)
- Session configuration
- Timestamp

#### `/clear` - Clear Context
Clears conversation history to start fresh while keeping the session active.

**When to use:**
- Starting a completely different task
- Context getting too large
- Want to reset the AI's memory

**Note:** This does NOT end the session, just clears the history.

#### `/status` - Show Session Info
Displays current session information.

**Shows:**
- Plan mode status (ON/OFF)
- Number of messages in context
- Active providers
- Number of available tools

**Example:**
```bash
> /status
Session Status:
  Plan Mode: OFF
  Messages: 42
  Providers: anthropic
  Tools: 8
```

### Discovery Commands

#### `/tools` - List Available Tools
Shows all tools currently loaded in the session.

**Output includes:**
- Tool name
- Tool description
- Whether tool is available

**Example:**
```bash
> /tools
Available Tools:
  filesystem           - File operations (read, write, edit)
  bash                 - Execute shell commands
  web                  - Web search and fetch
  task                 - Delegate to sub-agents
```

#### `/config` - Show Configuration
Displays the complete session configuration including providers, modules, and settings.

**When to use:**
- Verifying which provider/model is active
- Checking context limits
- Debugging module loading issues

#### `/help` - Show Commands
Displays a quick reference of all available slash commands.

### Control Commands

#### `/stop` - Stop Execution
Interrupts the current AI operation.

**When to use:**
- AI is taking too long
- Realized the task needs different approach
- Emergency stop

**Note:** You can also use Ctrl+C for the same effect.

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

**Session 2: Implementation**
```bash
# Load the saved plan externally, then:
> Implement step 1 of the migration plan
[AI implements]

> /save api_migration_progress.json
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

# Use production profile (includes logging hooks)
amplifier run --profile production --mode chat
```

See [profiles/README.md](../profiles/README.md) for more on profiles.

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
