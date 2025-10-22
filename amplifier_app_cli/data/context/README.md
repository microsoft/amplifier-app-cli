# Bundled Context Library

This directory contains context files that are bundled with the Amplifier CLI application and can be referenced from profiles using @mentions.

## What Are Context Files?

Context files are markdown documents that can be loaded into sessions to provide additional context beyond the system instruction. Profiles can reference these files using @mention syntax in their markdown bodies.

## Files in This Directory

### AGENTS.md

Shared project guidelines and best practices for AI assistants working with Amplifier. Contains build commands, code style, design philosophy, and common patterns.

**Usage**: Reference with `@AGENTS.md` in profile markdown body

### DISCOVERIES.md

Non-obvious problems and solutions discovered during development. Documents patterns, gotchas, and lessons learned.

**Usage**: Reference with `@DISCOVERIES.md` in profile markdown body

## How Context Loading Works

When a profile uses @mentions in its markdown body, the context loader:

1. Parses @mentions from the profile markdown
2. Resolves each mention to a file (bundled, project, or user)
3. Loads the file content recursively (following @mentions in loaded files)
4. Deduplicates content by hash (same content from multiple paths = one copy)
5. Creates messages from the loaded context
6. Injects into the session

## Search Path Resolution

@mentions are resolved using this search order (first match wins):

1. Relative to profile file (if starts with `./`)
2. Bundled context (`amplifier_app_cli/data/context/`)
3. Project context (`.amplifier/context/`)
4. User context (`~/.amplifier/context/`)

## Example Usage

```markdown
# profiles/dev.md
---
profile:
  name: dev
---

You are a development assistant for Amplifier.

Core context:
- @AGENTS.md
- @DISCOVERIES.md
- @ai_context/IMPLEMENTATION_PHILOSOPHY.md

Work efficiently and follow project conventions.
```

When this profile loads, the context loader:
- Loads AGENTS.md (from this bundled directory)
- Loads DISCOVERIES.md (from this bundled directory)
- Loads IMPLEMENTATION_PHILOSOPHY.md (from bundled ai_context/)
- Combines into context messages
- Injects before conversation messages

## Creating Custom Context Files

### Project Context

Create `.amplifier/context/` in your project:

```bash
mkdir -p .amplifier/context
echo "Project-specific guidelines..." > .amplifier/context/project-standards.md
```

Reference with `@project-standards.md` from profiles

### User Context

Create `~/.amplifier/context/` for personal context:

```bash
mkdir -p ~/.amplifier/context
echo "My personal preferences..." > ~/.amplifier/context/my-standards.md
```

Reference with `@my-standards.md` from profiles

## Bundled Context Files

These context files ship with Amplifier and can be referenced by any profile without additional setup.

See individual files for their specific content and purpose.
