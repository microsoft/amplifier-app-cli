# Foundation Collection

**Base layer for Amplifier - fundamental profiles and shared context**

---

## What This Provides

The `foundation` collection is the base layer bundled with Amplifier. It provides:

- **Core profiles**: `foundation`, `base`, `general`, `test`
- **Shared context**: Implementation and modular design philosophy documents
- **Common agent patterns**: Shared agent instruction components

Other collections build on foundation by depending on it in their `pyproject.toml`.

---

## Contents

### Profiles

| Profile | Purpose | When to Use |
|---------|---------|-------------|
| `foundation` | Minimal baseline | Starting point, learning |
| `base` | Standard features | Daily development work |
| `general` | General-purpose | Reliable, focused work |
| `test` | Testing-focused | CI/CD, test automation |

**Usage**:
```bash
# Use profiles (natural syntax)
amplifier profile use foundation:base
amplifier profile use foundation:foundation
amplifier profile use foundation:general

# Full path also works
amplifier profile use foundation:profiles/base.md
```

**In your profiles**:
```markdown
# Extend foundation profiles (both syntaxes work)
extends: foundation:base

# Or full path
extends: foundation:profiles/base.md
```

### Context

**Shared philosophy documents**:
- `IMPLEMENTATION_PHILOSOPHY.md` - Ruthless simplicity principles
- `MODULAR_DESIGN_PHILOSOPHY.md` - Bricks and studs approach

**Usage in agents and profiles**:
```markdown
@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
@foundation:context/MODULAR_DESIGN_PHILOSOPHY.md
```

---

## Dependencies

None - foundation is the base layer.

---

## Metadata

**Name**: foundation
**Version**: 1.0.0
**Author**: Amplifier Team
**Type**: Bundled (ships with amplifier-app-cli)
**Location**: `<package>/data/collections/foundation/`

---

## Usage in Other Collections

Collections depend on foundation for shared resources:

```toml
# In your collection's pyproject.toml
[tool.amplifier.collection]
requires = {foundation = "^1.0.0"}
```

Then reference foundation resources:

```markdown
# In your profiles
extends: foundation:profiles/base.md

context:
  - @foundation:context/IMPLEMENTATION_PHILOSOPHY.md
  - @your-collection:context/specialized.md

# In your agents
@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
@your-collection:context/agent-specific.md
```

---

## Related Collections

**developer-expertise**: Builds on foundation, adds development-focused profiles and specialized agents.

---

**Collection Version**: 1.0.0
**Last Updated**: 2025-10-26
