# Bundled Amplifier Agents

This directory contains bundled Amplifier agents that ship with the `amplifier-app-cli` package. These provide specialized AI configurations for common development tasks.

## Agent System Overview

Agents in Amplifier are **partial mount plans** - configuration overlays that specialize sub-sessions for focused tasks. They work by:

1. Resolving agent file from search locations (first-match-wins)
2. Merging agent config with parent session config
3. Forking sub-session with specialized configuration
4. Executing task in focused environment

**Key principle**: Agents override only what's needed, inheriting everything else from parent.

## Agent Resolution

Agents are resolved using **first-match-wins** from multiple locations:

**Search order (highest to lowest priority)**:
1. Environment variable: `AMPLIFIER_AGENT_<NAME>=path`
2. User agents: `~/.amplifier/agents/`
3. Project agents: `.amplifier/agents/`
4. Bundled agents: `amplifier_app_cli/data/agents/` � This directory

Higher priority locations completely override lower ones (no merging across layers).

## Bundled Agents

### zen-architect.md
System design and architecture agent:
- **Model**: claude-sonnet-4-5
- **Tools**: filesystem, bash
- **Purpose**: Analyzes requirements, designs solutions, creates specifications
- **Modes**: ANALYZE (problem breakdown), ARCHITECT (system design), REVIEW (code quality)
- **Philosophy**: Ruthless simplicity, minimal abstractions, clear boundaries

### bug-hunter.md
Systematic debugging specialist:
- **Model**: claude-sonnet-4-5
- **Tools**: filesystem, bash
- **Purpose**: Hypothesis-driven debugging, systematic issue resolution
- **Approach**: Reproduce, isolate, fix, verify, prevent
- **Focus**: Finding root causes, not just symptoms

### researcher.md
Research and information synthesis:
- **Model**: claude-sonnet-4-5
- **Tools**: filesystem, web, search
- **Purpose**: Gather and synthesize information from multiple sources
- **Methodology**: Question formulation, source identification, extraction, synthesis
- **Output**: Clear summaries with source attribution

### modular-builder.md
Implementation specialist:
- **Model**: claude-sonnet-4-5
- **Tools**: filesystem, bash
- **Purpose**: Implements code from specifications
- **Process**: Understand specs, design structure, implement, test, document
- **Philosophy**: Follows zen-architect specifications exactly

## Using Bundled Agents

### In Profiles

Load via profile configuration:

```yaml
# Load all bundled agents
agents:
  dirs: ["./agents"]

# Load specific bundled agents
agents:
  include:
    - zen-architect
    - bug-hunter
```

Agents resolve from bundled location automatically.

### Override at Project Level

Customize bundled agent for project needs:

```bash
# Copy bundled agent
mkdir -p .amplifier/agents
amplifier agents show zen-architect > .amplifier/agents/zen-architect.md

# Edit for project-specific needs
# Commit to git
git add .amplifier/agents/zen-architect.md
git commit -m "Customize zen-architect for project"
```

### Override for Personal Use

Personal customization without affecting project:

```bash
# Copy to user location
mkdir -p ~/.amplifier/agents
amplifier agents show researcher > ~/.amplifier/agents/researcher.md

# Customize for your workflow
```

## Agent Characteristics

### What Makes Them Bundled

- **Shipped with package** - Always available after installation
- **Maintained by Amplifier** - Updated with package releases
- **Lowest priority** - Easily overridden at project or user level
- **Serve as templates** - Copy and customize for your needs

### What They Provide

- **Proven configurations** - Battle-tested tool and model combinations
- **Clear patterns** - Examples of effective agent design
- **Starting points** - Templates for creating custom agents
- **Defaults** - Sensible baseline configurations

## Customization Levels

### No Customization
Use bundled agents as-is via profile:

```yaml
agents:
  include: ["zen-architect", "bug-hunter"]
```

### Project-Level Customization
Override in `.amplifier/agents/` (shared via git):

```bash
cp bundled/zen-architect.md .amplifier/agents/zen-architect.md
# Edit for project needs
git add .amplifier/agents/
```

### User-Level Customization
Override in `~/.amplifier/agents/` (personal only):

```bash
cp bundled/researcher.md ~/.amplifier/agents/researcher.md
# Edit for personal preferences
```

### Temporary Testing
Override with environment variable:

```bash
export AMPLIFIER_AGENT_BUG_HUNTER=/tmp/test-hunter.md
# Test without affecting files
```

## CLI Commands

```bash
# List all agents (including bundled)
amplifier agents list

# Show bundled agent
amplifier agents show zen-architect

# Validate custom agent
amplifier agents validate my-agent.md
```

## Agent File Format

All bundled agents use standard format:

```yaml
---
meta:
  name: agent-name
  description: "What this agent does"

providers:
  - module: provider-anthropic
    config:
      model: claude-sonnet-4-5

tools:
  - module: tool-filesystem
  - module: tool-bash
---

[System instruction in Markdown]
```

**Notes**:
- Bundled agents use simple module refs without `source` fields (use default resolution)
- Custom agents can include `source` fields for custom module sources:
  ```yaml
  tools:
    - module: tool-custom
      source: git+https://github.com/you/custom-tool@main
      config:
        api_key: ${API_KEY}
  ```
- Full `ModuleConfig` structure supported (module, source, config)
- Bundled agents may include additional fields (hooks, session config, etc.) for specialized behavior

## Related Documentation

**For Users:**
- [AGENT_AUTHORING.md](../../../docs/AGENT_AUTHORING.md) - Creating custom agents
- [PROFILE_AUTHORING.md](../../../docs/PROFILE_AUTHORING.md) - Loading agents via profiles
- [CONFIGURATION_GUIDE.md](../../../docs/CONFIGURATION_GUIDE.md) - Configuration reference

**For Developers:**
- [AGENT_DELEGATION.md](../../../docs/AGENT_DELEGATION.md) - Technical agent system design
- [MODULE_DEVELOPMENT.md](../../../docs/MODULE_DEVELOPMENT.md) - Creating tool modules

## Summary

Bundled agents provide:
- **Proven configurations** for common tasks
- **Templates** for custom agent creation
- **Defaults** that work out of the box
- **Override targets** for customization

Start with bundled agents and override at project or user level as needed.
