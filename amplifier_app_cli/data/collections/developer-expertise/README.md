# Developer Expertise Collection

**Development-focused profiles and specialized agents for software engineering**

---

## What This Provides

The `developer-expertise` collection provides specialized tools for software development:

- **Development profiles**: `dev`, `full`
- **Specialized agents**: Architecture, debugging, building, research
- **Builds on**: `foundation` collection

This collection is for developers building software with Amplifier. It includes agents that understand code architecture, can hunt bugs systematically, build modular components, and research technical topics.

---

## Contents

### Profiles

| Profile | Purpose | Includes |
|---------|---------|----------|
| `dev` | Full development environment | All agents, all tools, development-optimized config |
| `full` | Maximum capabilities | Everything enabled, for complex tasks |

**Usage**:
```bash
# Use profiles (natural syntax)
amplifier profile use developer-expertise:dev
amplifier profile use developer-expertise:full

# Full path also works
amplifier profile use developer-expertise:profiles/dev.md
```

### Agents

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| `zen-architect` | Architecture design and review | Planning features, reviewing designs |
| `bug-hunter` | Systematic debugging | Tracking down bugs, fixing issues |
| `modular-builder` | Modular component building | Implementing from specifications |
| `researcher` | Technical research | Understanding libraries, patterns, codebases |

**Usage**:
```bash
# Load profile that includes agents
amplifier profile use developer-expertise:dev

# Start session - agents available for delegation
amplifier run "design an auth system"

# The session includes:
# - zen-architect for architecture and design
# - bug-hunter for debugging
# - modular-builder for implementation
# - researcher for technical research

# Agents are used via delegation within the session
# See AGENT_DELEGATION.md for details
```

---

## Dependencies

Requires: `foundation ^1.0.0`

The developer-expertise collection extends foundation with development-specific resources:

```markdown
# dev.md profile extends base (natural syntax)
extends: foundation:base

# Or full path
extends: foundation:profiles/base.md

# Agents reference foundation context
@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
@foundation:context/MODULAR_DESIGN_PHILOSOPHY.md
```

---

## Agent Descriptions

### zen-architect

**Expertise**: Architecture design, code review, modular design

**Capabilities**:
- Design system architecture
- Review code for complexity and philosophy compliance
- Create module specifications
- Analyze trade-offs

**Philosophy**: Ruthless simplicity, analysis-first development

**Example**:
```bash
# Load dev profile (includes zen-architect)
amplifier profile use developer-expertise:dev
amplifier run "Design a caching layer for the API"
```

### bug-hunter

**Expertise**: Systematic debugging, hypothesis-driven investigation

**Capabilities**:
- Track down bugs methodically
- Generate and test hypotheses
- Identify root causes
- Propose fixes

**Philosophy**: Systematic over random, evidence-driven

**Example**:
```bash
# Load dev profile (includes bug-hunter)
amplifier profile use developer-expertise:dev
amplifier run "The login times out intermittently"
```

### modular-builder

**Expertise**: Building self-contained modules from specifications

**Capabilities**:
- Implement from specifications
- Create regeneratable modules
- Follow bricks-and-studs philosophy
- Write comprehensive tests

**Philosophy**: Specifications first, implementation second

**Example**:
```bash
# Load dev profile (includes modular-builder)
amplifier profile use developer-expertise:dev
amplifier run "Build the auth module from the spec"
```

### researcher

**Expertise**: Technical research, library understanding, pattern discovery

**Capabilities**:
- Research technical topics
- Understand library usage
- Find patterns in codebases
- Synthesize documentation

**Philosophy**: Understand before using, breadth before depth

**Example**:
```bash
# Load dev profile (includes researcher)
amplifier profile use developer-expertise:dev
amplifier run "How does FastAPI handle WebSocket connections?"
```

---

## Metadata

**Name**: developer-expertise
**Version**: 1.0.0
**Author**: Amplifier Team
**Type**: Bundled (ships with amplifier-app-cli)
**Location**: `<package>/data/collections/developer-expertise/`
**Depends on**: `foundation ^1.0.0`

---

## Related Collections

**foundation**: Base collection with core profiles and shared context. Required dependency.

---

**Collection Version**: 1.0.0
**Last Updated**: 2025-10-26
