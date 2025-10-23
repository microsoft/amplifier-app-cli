# Shared Instruction Library

This directory contains shared instruction files that can be referenced by agents and profiles using @mention syntax.

## Files

- **common-agent-base.md** - Core instructions shared across all agents

## Usage

Reference shared files with @mentions in your agent or profile markdown:

```markdown
# In agent definition
---
meta:
  name: my-agent
---

@shared/common-agent-base.md

Agent-specific instructions...
```

## Benefits

- **Single source of truth** - Update shared instructions in one place
- **Consistency** - All agents use same base instructions
- **Maintainability** - Changes propagate automatically
- **Explicit** - Clear what's being included

## Maintenance

When you update files in this directory, all agents and profiles that @mention them will receive the updates automatically when loaded.
