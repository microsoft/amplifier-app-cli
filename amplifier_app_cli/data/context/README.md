# Bundled Context Files

This directory contains context files bundled with the Amplifier CLI that can be referenced in profiles and agents using @mention syntax.

## What Are Context Files?

Context files are markdown documents that provide additional context to AI sessions. Profiles and agents reference these files using @mentions to load relevant knowledge and instructions.

---

## Collections-Based Context

Most context files are now organized in **collections** rather than this flat directory. Collections provide better organization and shareability.

### Foundation Collection Context

Core philosophy and shared patterns:

```markdown
@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
@foundation:context/MODULAR_DESIGN_PHILOSOPHY.md
@foundation:context/shared/common-agent-base.md
```

### Developer Expertise Collection

Development-focused context (if developer-expertise collection is installed):

```markdown
@developer-expertise:context/...
```

---

## @Mention Syntax

Reference context files using @mention syntax:

### Collection References

```markdown
@collection-name:path/to/file.md
```

**Examples**:

```markdown
# Foundation collection context

@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
@foundation:context/shared/common-agent-base.md

# Other collection context

@memory-solution:context/patterns.md
```

### Shortcuts

```markdown
@user:path → ~/.amplifier/path
@project:path → .amplifier/path
@path → Direct path (CWD or relative)
```

### Examples in Profiles

```markdown
# profiles/custom.md

---

## name: custom

You are a specialized assistant.

Core context:

- @foundation:context/IMPLEMENTATION_PHILOSOPHY.md
- @user:context/my-standards.md
- @project:context/project-specific.md

Work according to these principles.
```

---

## Resolution Order

When resolving @mentions, Amplifier searches in precedence order:

**For collection references** (`@collection:path`):

1. Project collections (`.amplifier/collections/`)
2. User collections (`~/.amplifier/collections/`)
3. Bundled collections (`<package>/data/collections/`)

**For direct paths** (`@path`):

1. Relative to profile file (if `./`)
2. Project context (`.amplifier/context/`)
3. User context (`~/.amplifier/context/`)
4. Current working directory

---

## How Context Loading Works

When a profile or agent includes @mentions:

1. **Parse @mentions** from markdown body
2. **Resolve each mention**:
   - Collection references → search collection paths
   - Direct paths → search context paths
3. **Load files recursively** (following @mentions in loaded files)
4. **Deduplicate content** by hash (same content = one copy)
5. **Create context messages**
6. **Inject into session** before conversation

---

## Creating Custom Context

### Project Context

Create `.amplifier/context/` in your project:

```bash
mkdir -p .amplifier/context
cat > .amplifier/context/project-standards.md << 'EOF'
# Project Standards

Our project uses these patterns:
- ...
EOF
```

Reference with `@project:context/project-standards.md` or `@project-standards.md`

### User Context

Create `~/.amplifier/context/` for personal context:

```bash
mkdir -p ~/.amplifier/context
cat > ~/.amplifier/context/my-preferences.md << 'EOF'
# My Preferences

I prefer:
- ...
EOF
```

Reference with `@user:context/my-preferences.md` or `@user:my-preferences.md`

---
