# Collections Overview

**Quick reference for AI agents on Amplifier's collection system**

---

## What Are Collections?

Collections are **shareable bundles** of related Amplifier resources:
- **Profiles** - Capability configurations
- **Agents** - Specialized AI personas
- **Context** - Shared knowledge documents
- **Scenario Tools** - Sophisticated CLI tools
- **Modules** - Provider/tool/hook modules

**Key principle**: Convention over configuration. Directory structure defines resources.

---

## Standard Structure

```
collection-name/
  pyproject.toml              # Metadata (required)
  profiles/                   # Profile definitions (.md)
  agents/                     # Agent definitions (.md)
  context/                    # Shared knowledge (.md)
  scenario-tools/             # CLI tools
  modules/                    # Amplifier modules
  README.md                   # Documentation
```

**Auto-discovery**: Amplifier discovers resources based on directory presence. No manifest file needed.

---

## @Mention Syntax

Reference collection resources:

```
@collection-name:path/to/resource
```

**Examples**:
```markdown
# In profiles
extends: foundation:profiles/base.md
context:
  - @foundation:context/IMPLEMENTATION_PHILOSOPHY.md
  - @memory-solution:context/patterns.md

# In agents
@foundation:context/shared/common-agent-base.md
@developer-expertise:context/agent-specific.md
```

**Shortcuts**:
```
@user:path          →  ~/.amplifier/path
@project:path       →  .amplifier/path
@path               →  Direct path
```

---

## Bundled Collections

**foundation**: Base profiles + shared context
- Profiles: foundation, base, production, test
- Context: Philosophy docs, common agent base
- No dependencies

**developer-expertise**: Development profiles + specialized agents
- Profiles: dev, full
- Agents: zen-architect, bug-hunter, modular-builder, researcher
- Depends on: foundation

---

## Search Path Precedence

Collections resolved in order (highest precedence first):
1. Project (`.amplifier/collections/`)
2. User (`~/.amplifier/collections/`)
3. Bundled (`<package>/data/collections/`)

---

## CLI Commands Quick Reference

```bash
# Install
amplifier collection add git+https://github.com/user/collection@v1.0.0

# List
amplifier collection list

# Details
amplifier collection show collection-name

# Remove
amplifier collection remove collection-name
```

---

## For Agents: When to Reference Collections

**Use collection references** when:
- Extending bundled profiles: `extends: foundation:profiles/base.md`
- Loading shared context: `@foundation:context/IMPLEMENTATION_PHILOSOPHY.md`
- Referencing common patterns: `@foundation:context/shared/common-agent-base.md`
- Using specialized collections: `@memory-solution:context/patterns.md`

**Use direct paths** when:
- Project-specific files: `@docs/project-specific.md`
- User files: `@user:config/custom.md`
- Local context: `@.amplifier/context/notes.md`

---

**See**: [COLLECTIONS_GUIDE.md](../../docs/COLLECTIONS_GUIDE.md) for comprehensive guide.
