---
last_updated: 2025-10-31
status: stable
audience: developer
layer: App-Layer Implementation
---

# Agent Delegation - amplifier-app-cli Implementation

This document describes how **amplifier-app-cli** implements agent delegation using the amplifier-profiles library.

**For agent concepts and authoring**: See [amplifier-profiles](https://github.com/microsoft/amplifier-profiles)

**For kernel mechanism**: See amplifier-core SESSION_FORK_SPECIFICATION.md

---

## Overview

amplifier-app-cli implements agent delegation by:

1. Using `AgentResolver` and `AgentLoader` from amplifier-profiles
2. Resolving agent files from CLI-specific search paths
3. Supporting environment variable overrides for testing
4. Compiling agent configs via `compile_profile_to_mount_plan`
5. Using amplifier-core's `session.fork()` for sub-session creation

---

## CLI-Specific Search Paths

Agent files are resolved using **first-match-wins** strategy (highest priority first):

### 1. Environment Variables (Highest Priority)

```bash
# Format: AMPLIFIER_AGENT_<NAME> (uppercase, dashes → underscores)
export AMPLIFIER_AGENT_ZEN_ARCHITECT=~/test-zen-architect.md
export AMPLIFIER_AGENT_BUG_HUNTER=/tmp/debug-hunter.md

# Use in session
amplifier run "design auth system"  # Uses ~/test-zen-architect.md
```

**Use case**: Testing agent changes before committing

### 2. User Directory

```
~/.amplifier/agents/
├── zen-architect.md       # Personal zen-architect override
└── custom-analyzer.md     # Personal agent
```

**Use case**: Personal agent customizations

### 3. Project Directory

```
.amplifier/agents/
├── project-reviewer.md    # Project-specific agent
└── zen-architect.md       # Project zen-architect override
```

**Use case**: Project-specific agents, committed to version control

### 4. Bundled Agents (Lowest Priority)

```
amplifier_app_cli/data/agents/
├── zen-architect.md
├── bug-hunter.md
├── modular-builder.md
└── researcher.md
```

**Use case**: Default agents shipped with CLI

---

## Implementation Details

### Agent Resolution

```python
from amplifier_profiles import AgentResolver
from pathlib import Path
import os

# Build search paths (CLI-specific)
search_paths = [
    Path(__file__).parent / "data" / "agents",       # Bundled (lowest)
    Path(".amplifier/agents"),                        # Project
    Path.home() / ".amplifier" / "agents",           # User (highest)
]

# Create resolver
resolver = AgentResolver(search_paths=search_paths)

# Check environment variable override first (CLI-specific)
agent_name = "zen-architect"
env_var = f"AMPLIFIER_AGENT_{agent_name.upper().replace('-', '_')}"
agent_path = os.environ.get(env_var)

if not agent_path:
    # Fall back to resolver
    agent_path = resolver.resolve(agent_name)

# Load agent
from amplifier_profiles import AgentLoader
loader = AgentLoader(resolver=resolver)
agent = loader.load_agent(agent_name)
```

### Session Forking

Uses amplifier-core's `session.fork()` with agent config overlay:

```python
# In parent session
parent_session = AmplifierSession(config=parent_mount_plan)

# Load agent config
agent = agent_loader.load_agent("zen-architect")
agent_mount_plan_fragment = agent.to_mount_plan_fragment()

# Fork session with agent overlay
sub_session = await parent_session.fork(
    config_overlay=agent_mount_plan_fragment,
    task_description="Design authentication system"
)

# Execute in sub-session
result = await sub_session.execute("Design the auth system")
```

---

## CLI Commands

### Agent Listing

```bash
# List all agents (includes collection agents)
amplifier agent list

# Example output:
# zen-architect                           bundled
# bug-hunter                              bundled
# design-intelligence:art-director        user-collection
# project-reviewer                        project
```

### Agent Inspection

```bash
# Show agent configuration
amplifier agent show zen-architect

# Output: Agent config in YAML format
```

### Using Agents in Sessions

Agents are loaded automatically when profiles specify them:

```yaml
# In profile.md
agents:
  dirs: ["./agents"]
  include: ["zen-architect", "bug-hunter"]
```

Then use via task tool delegation within sessions (see amplifier-profiles docs for details).

---

## Environment Variable Override Examples

### Testing Agent Changes

```bash
# Test modified agent without committing
export AMPLIFIER_AGENT_ZEN_ARCHITECT=~/work/test-zen.md
amplifier run "design system"  # Uses test version

# Unset to use normal resolution
unset AMPLIFIER_AGENT_ZEN_ARCHITECT
```

### Temporary Agent Injection

```bash
# Use one-off agent for specific task
export AMPLIFIER_AGENT_SPECIAL_ANALYZER=/tmp/special.md
amplifier run "analyze codebase"
unset AMPLIFIER_AGENT_SPECIAL_ANALYZER
```

---

## Integration with amplifier-profiles Library

amplifier-app-cli uses amplifier-profiles library for ALL agent functionality:

**What CLI provides** (policy):
- CLI-specific search paths (bundled, user, project directories)
- Environment variable override mechanism
- CLI commands for listing/showing agents
- Integration with profile system

**What library provides** (mechanism):
- Agent file format parsing (markdown + YAML frontmatter)
- AgentResolver (path-based resolution)
- AgentLoader (loading and parsing agent files)
- Agent schemas (Agent, AgentMeta, SystemConfig)
- First-match-wins resolution logic

**Boundary**: CLI calls library APIs, library doesn't know about CLI.

---

## Related Documentation

**Agent Concepts**:
- [amplifier-profiles](https://github.com/microsoft/amplifier-profiles) - Agent system design and API
- [Agent Authoring Guide](https://github.com/microsoft/amplifier-profiles/blob/main/docs/AGENT_AUTHORING.md) - How to create agents

**Kernel Mechanism**:
- amplifier-core SESSION_FORK_SPECIFICATION.md - Session forking contract

**CLI Integration**:
- [Profile Management](../README.md#profile-management) - How profiles load agents

---

**Document Version**: 1.0
**Last Updated**: 2025-10-31
