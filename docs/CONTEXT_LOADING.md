# Context Loading System

**@mention system**: Load files anywhere (bundles, runtime input, nested files). Content loads at message stack top, @mention stays as reference.

## Quick Start

### Basic Bundle with Inline Context

```markdown
# ~/.amplifier/bundles/my-bundle/bundle.md
---
bundle:
  name: my-bundle
---

You are a helpful Python development assistant.

Follow PEP 8 style guidelines and use type hints.
```

The markdown body becomes the system instruction.

### Bundle with @Mentioned Context

```markdown
# ~/.amplifier/bundles/dev-bundle/bundle.md
---
bundle:
  name: dev-bundle
---

You are an Amplifier development assistant.

Project context:
- @AGENTS.md
- @DISCOVERIES.md
- @ai_context/KERNEL_PHILOSOPHY.md

Work efficiently and follow project conventions.
```

The @mentioned files load automatically and are added as context.

## Bundles and Context

**Bundles** provide organized, shareable packages of context files. Most shared context is now organized in bundles rather than standalone files.

**Foundation bundle** provides:
- `@foundation:context/IMPLEMENTATION_PHILOSOPHY.md`
- `@foundation:context/MODULAR_DESIGN_PHILOSOPHY.md`
- `@foundation:context/shared/common-agent-base.md`

**Usage in bundles**:
```markdown
@foundation:context/shared/common-agent-base.md
@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
```

**→ [Bundle Guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md)** for complete bundle documentation.

## How @Mention Loading Works

```
User input contains "@FILE.md"
    ↓
App layer detects @mention → MentionLoader
    ↓
File loaded recursively (nested @mentions followed)
    ↓
Content deduplicated (same content = one copy, all paths credited)
    ↓
Wrapped in <context_file paths="...">content</context_file>
    ↓
Added via context.add_message() BEFORE user message
    ↓
Message stack: [Context files] [User message with @mention as reference]
    ↓
@mention preserved as semantic reference marker
```

**Key insight**: @mention appears in TWO places - content at stack top (full text), reference in original message (semantic marker).

## @Mention Syntax

### Basic @Mention

Reference a file by name:

```markdown
@AGENTS.md
@DISCOVERIES.md
```

Resolves relative to current working directory (where command runs)

### Path-Based @Mention

Reference a file with path:

```markdown
@ai_context/KERNEL_PHILOSOPHY.md
@custom/my-context.md
```

### Bundle Resource References

Reference files from bundles using `@bundle:path` syntax:

```markdown
@foundation:context/shared/common-agent-base.md
@developer-expertise:agents/zen-architect.md
@memory-solution:context/patterns.md
```

Searches bundle paths in precedence order (project → user → registered).

### User Home References

Reference files in user home directory using `@~/` prefix:

```markdown
@~/.amplifier/my-custom-context.md
@~/Documents/project-notes.md
```

Resolves ONLY to `Path.home() / {path}` with no fallback.

### Relative References

Relative to bundle file location:

```markdown
./context/project-specific.md
./guidelines/coding-standards.md
```

## @Mention Resolution

```
┌──────────────────────────────────────────────────────────┐
│ @mention Syntax Resolution Table                         │
├──────────────────────────────────────────────────────────┤
│ @bundle:path      → Bundle paths (project/user/registered)│
│ @user:path        → ~/.amplifier/{path}                  │
│ @project:path     → .amplifier/{path}                    │
│ @~/path           → User home: ~/{path}                  │
│ @path             → CWD-relative or search paths         │
│ ./path            → Relative to source file              │
└──────────────────────────────────────────────────────────┘
```

**Missing files**: Skipped gracefully (no error).
**Path traversal**: Blocked (`..` rejected in bundle refs).

## Recursive Loading

@mentions work in ANY loaded file:

```markdown
# AGENTS.md
Core philosophy:
- @ai_context/KERNEL_PHILOSOPHY.md
- @ai_context/IMPLEMENTATION_PHILOSOPHY.md

Project guidelines...
```

When AGENTS.md is loaded, its @mentions are followed recursively.

**Cycle detection**: Prevents infinite loops if files reference each other.

## Content Deduplication

Same content from multiple paths is loaded once:

```markdown
# Bundle A mentions @AGENTS.md
# Bundle B (extending A) also mentions @AGENTS.md
# Context file mentions @AGENTS.md again
```

Result: AGENTS.md content loaded once, all three paths credited:

```
<context_file paths="AGENTS.md (from bundle), AGENTS.md (from parent), AGENTS.md (from context-file.md)">
[Content here - only once]
</context_file>
```

## Sharing Instructions with @Mentions

Create reusable instruction files to avoid copy-pasting shared content:

### Pattern: Shared Instruction Files

```markdown
# Step 1: Create shared file
# File: shared/common-base.md
---
Core instructions for all bundles...
Standard practices...
Quality guidelines...
---

# Step 2: Reference in bundles
# File: specialized/bundle.md
---
bundle:
  name: specialized
  extends: foundation  # YAML config inheritance
---

@foundation:context/shared/common-base.md

Additionally, you specialize in database architecture.

Context:
- @database/best-practices.md
```

### Benefits

- **Single source of truth** - Update shared instructions in one place
- **Consistency** - All bundles use same base instructions
- **Explicit** - Clear what's being included
- **Flexible** - Can compose multiple shared files

### Note on Bundle Inheritance

The `extends:` field in YAML frontmatter inherits configuration (modules, settings) but NOT markdown body. Use @mentions to share markdown content across bundles.

## Provider-Specific Handling

### Anthropic (Messages API)

**System instruction** → `system` parameter (string)
**Context files** → `user` messages with XML wrapper (at top of messages array)

```python
# What Amplifier sends to Anthropic
{
    "system": "You are an Amplifier assistant...",  # Pure system instruction
    "messages": [
        # Context files first (as user with XML)
        {
            "role": "user",
            "content": "<context_file paths=\"AGENTS.md\">\n[AGENTS.md content]\n</context_file>"
        },
        {
            "role": "user",
            "content": "<context_file paths=\"DISCOVERIES.md\">\n[DISCOVERIES.md content]\n</context_file>"
        },
        # Then conversation
        {"role": "user", "content": "User's actual question"},
        {"role": "assistant", "content": "Response"}
    ]
}
```

### OpenAI (Responses API)

**System instruction** → `instructions` parameter (string)
**Context files** → Prepended to `input` with XML wrapper

```python
# What Amplifier sends to OpenAI
{
    "instructions": "You are an Amplifier assistant...",  # Pure system instruction
    "input": """
<context_file paths="AGENTS.md">
[AGENTS.md content]
</context_file>

<context_file paths="DISCOVERIES.md">
[DISCOVERIES.md content]
</context_file>

User: User's actual question
"""
}
```

### XML Wrapper Format

Context files are wrapped in XML tags for clarity:

```xml
<context_file paths="path1, path2, path3">
File content here...
</context_file>
```

This clearly indicates to the model:
- These are loaded files (not user conversation)
- Source paths for reference
- Semantic boundary

## Creating Context Files

### Bundled Context

Amplifier ships with bundled context files in `amplifier_app_cli/data/context/`:
- `AGENTS.md` - Project guidelines
- `DISCOVERIES.md` - Lessons learned

Reference with `@AGENTS.md` from any bundle.

### Project Context

Create `.amplifier/context/` in your project:

```bash
mkdir -p .amplifier/context
```

```markdown
# .amplifier/context/project-standards.md
# Project-Specific Standards

Code style:
- Line length: 100 characters
- Use async/await for all I/O
- Error handling: fail fast

Testing:
- pytest with fixtures
- 90% coverage minimum
```

Reference with `@project-standards.md` from bundles in `.amplifier/bundles/`.

### User Context

Create `~/.amplifier/context/` for personal context:

```bash
mkdir -p ~/.amplifier/context
```

```markdown
# ~/.amplifier/context/my-preferences.md
# My Preferences

- Prefer explicit over implicit
- Always add type hints
- Use descriptive variable names
```

Reference with `@my-preferences.md` from any bundle.

## Context in Bundles

### Simple Bundle (Inline)

All context inline in bundle markdown:

```markdown
# simple/bundle.md
---
bundle:
  name: simple
---

You are a helpful assistant.

Be concise and clear.
```

Distribution: Single directory, easy to share.

### Bundle with References

References shared context:

```markdown
# dev/bundle.md
---
bundle:
  name: dev
  extends: foundation  # YAML config inheritance
---

@foundation:context/shared/common-base.md

Development-specific context:
- @AGENTS.md
- @DISCOVERIES.md
- @ai_context/IMPLEMENTATION_PHILOSOPHY.md

Use extended thinking for complex tasks. Delegate to specialized agents for focused work.
```

Distribution: Via git (`.amplifier/` directory) or with bundled context.

## Best Practices

**Keep system instruction focused**:
- Clear role and capabilities
- Brief and actionable
- Use @mentions for lengthy context

**Use @mentions for reusable context**:
- Project guidelines → @AGENTS.md
- Lessons learned → @DISCOVERIES.md
- Philosophy docs → @ai_context/*.md

**Organize by purpose**:
- System instruction: Who you are, what you do
- Context files: How to do it, what to know

**Deduplication is automatic**:
- Reference the same file multiple times safely
- Content loaded once, paths credited

**Missing files are OK**:
- No error if @mentioned file not found
- Author responsible for testing
- Flexible, use-at-your-own-risk

## Troubleshooting

### Context Not Loading

**Check**:
1. Bundle markdown body exists (not empty/whitespace)
2. @mentions use correct syntax (`@FILENAME.md`)
3. Referenced files exist in search paths

**Debug**:
```bash
# Show bundle with resolved content
amplifier bundle show my-bundle

# Check context files exist
ls ~/.amplifier/context/
ls .amplifier/context/
```

### Circular References

If files reference each other in a loop, cycle detection prevents infinite recursion.

**Example**:
```markdown
# A.md mentions @B.md
# B.md mentions @A.md
```

**Handled**: Each file loaded once, cycle broken.

### Content Not Appearing

**Check message logs**: Context files appear as user messages with XML wrappers at the top of the conversation.

**Verify**: Check session logs at `~/.amplifier/projects/<project>/sessions/<session-id>/events.jsonl` to see actual messages sent to provider.

## Examples

### Research Assistant Bundle

```markdown
---
bundle:
  name: researcher
---

You are a research specialist.

Context:
- @research/methodology.md
- @research/citation-guidelines.md

Gather information systematically and cite sources.
```

### Team Standard Bundle

```markdown
---
bundle:
  name: team-standard
  extends: foundation  # YAML config inheritance
---

@foundation:context/shared/common-base.md

Team-specific context:
- @project:context/team-conventions.md
- @project:context/architecture-decisions.md

Follow team standards strictly. All code changes must align with documented architecture decisions.
```

## Related Documentation

- **→ [Bundle Guide](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md)** - Creating bundles with @mentions

## Using @Mentions at Runtime

@mentions aren't just for bundles - use them in chat anytime:

### Runtime @Mention Example

```
User: "Explain @docs/KERNEL.md"
  ↓
Message Stack:
  [1] <context_file paths="docs/KERNEL.md">[full content]</context_file>
  [2] "Explain @docs/KERNEL.md"  ← @mention as reference
  ↓
Model: "The @docs/KERNEL.md describes..." (can reference by name)
```

**Why this works**: Content loaded at top, @mention preserved as semantic marker for referencing.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Kernel (amplifier-core)                                 │
│ • utils/mentions.py - Text parsing only (no file I/O)  │
│ • context.add_message() - API for adding messages      │
├─────────────────────────────────────────────────────────┤
│ App Layer (amplifier-app-cli)                           │
│ • lib/mention_loading/ - File loading, deduplication   │
│ • Calls add_message() to inject loaded context         │
│ • Policy: When to process @mentions (init + runtime)   │
└─────────────────────────────────────────────────────────┘
```

**Orchestrators unchanged**: Don't know about @mentions (app-layer feature).
