# Amplifier Discoveries

This file documents non-obvious problems, solutions, and patterns discovered during Amplifier development. It's designed to be loaded via @mention from profile markdown bodies to provide lessons learned.

## Purpose

Prevent repeated mistakes and share non-obvious insights. This file is regularly reviewed and updated, with outdated entries removed or evolved.

## Current Discoveries

### Discovery Template

When adding new discoveries, use this format:

```markdown
## [Issue Name] (Date Added)

### Issue
[What problem was encountered]

### Root Cause
[Why it happened]

### Solution
[How it was solved]

### Prevention
[How to avoid in future]

### Key Learnings
[Non-obvious insights]
```

### Example: Request Envelope V1 Models

**Issue**: Providers were using `dict[str, Any]` for messages, causing type safety issues and data loss.

**Root Cause**: Specs and JSON schema existed but no Python Pydantic models in amplifier-core.

**Solution**: Created complete Pydantic models (`message_models.py`) implementing REQUEST_ENVELOPE_V1 spec. Updated all providers to use shared models.

**Prevention**: When creating specs, implement corresponding Pydantic models immediately.

**Key Learnings**:
- Specs without implementations drift
- Type safety prevents entire classes of bugs
- Shared models ensure consistency across providers

## How to Use This File

**Loading in Profiles**:

```markdown
# profiles/dev.md
---
profile:
  name: dev
---

Development assistant for Amplifier.

Context:
- @AGENTS.md
- @DISCOVERIES.md

[Additional instructions...]
```

**Maintenance**:
- Add new discoveries as they occur
- Update existing entries if solutions evolve
- Remove outdated discoveries
- Keep entries concise and actionable

## Related

For general project context, see @AGENTS.md
For philosophy and design principles, see @ai_context/KERNEL_PHILOSOPHY.md
