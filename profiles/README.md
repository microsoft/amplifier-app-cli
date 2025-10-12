# Amplifier Profiles

This directory contains official Amplifier profiles that provide pre-configured setups for common use cases.

## Profile System Overview

Amplifier uses a **two-tier profile system** to separate personal developer preferences from team defaults:

- **Local profile** (`.amplifier/profile`) - Your personal choice, gitignored
- **Project default** (`.amplifier/default-profile`) - Team's recommended profile, checked in

**Precedence order:**
1. CLI flag `--profile` (highest priority)
2. Local profile (your choice)
3. Project default (team's choice)
4. Hardcoded defaults (fallback)

This design prevents git merge conflicts while allowing projects to specify sensible defaults.

## Profile Architecture

The profiles follow a hierarchical inheritance structure:

```
foundation (absolute minimum - orchestrator, context, provider)
    ↓
base (adds essential tools and hooks)
    ↓
dev/production/test (environment-specific configurations)
    ↓
full (kitchen sink - all available modules)
```

## Available Profiles

### foundation.toml
Absolute minimum configuration:
- Basic orchestrator
- Simple context manager
- Anthropic provider only
- No tools, no hooks (pure foundation)

### base.toml
Core functionality (extends foundation):
- Inherits orchestrator, context, and provider from foundation
- Adds filesystem and bash tools
- Adds essential hooks: redaction (priority 10), logging (priority 100)
- Context management (100K tokens, 80% compact threshold)
- Auto-compaction enabled

### dev.toml
Development configuration (extends base):
- Streaming orchestrator for better feedback
- Inherits all tools and hooks from base
- Adds web and search tools
- Adds agent-architect for task delegation
- Ideal for interactive development

### production.toml
Production-optimized (extends base):
- Streaming orchestrator
- Persistent context for session continuity
- Enhanced context limits (150K tokens, 90% threshold)
- Inherits all tools and hooks from base
- Adds web tools for production features

### test.toml
Testing configuration (extends base):
- Mock provider for deterministic testing
- Reduced token limits for faster testing
- Inherits all tools and hooks from base
- Adds task tool for sub-agent testing
- Configurable failure simulation

### full.toml
Kitchen sink configuration (extends base):
- All available providers (Anthropic, OpenAI, Azure OpenAI, Ollama)
- All available tools (filesystem, bash, web, search, task)
- All available agents (agent-architect)
- All available hooks (redaction, logging, approval, backup, cost-aware scheduler, heuristic scheduler)
- Maximum token capacity (200K)
- Persistent context
- Comprehensive feature testing

## Using Profiles

### Set Local Profile (Personal Choice)
```bash
# Set your personal profile choice
amplifier profile apply dev

# All your runs use this profile
amplifier run "your prompt"

# Clear local choice (falls back to project default if set)
amplifier profile reset
```

**Note:** Your local profile choice is gitignored and won't affect other developers.

### Set Project Default (Team Standard)
```bash
# Show current project default
amplifier profile default

# Set project default (requires commit)
amplifier profile default --set base

# Clear project default
amplifier profile default --clear
```

**Note:** Remember to commit `.amplifier/default-profile` after setting it.

### Use Profile for Single Run
```bash
# Override active profile for one session
amplifier run --profile production "your prompt"
```

### List Available Profiles
```bash
amplifier profile list
```

The active profile is marked with a star (★) and highlighted in green.

### Check Active Profile
```bash
# Show which profile is currently active and its source
amplifier profile current
```

This shows whether the profile comes from local choice or project default.

### Show Profile Details
```bash
amplifier profile show dev
```

## Profile Locations

Profiles are searched in order of precedence:

1. **User profiles** (highest): `~/.amplifier/profiles/`
2. **Team profiles** (middle): `.amplifier/profiles/`
3. **Official profiles** (lowest): `/usr/share/amplifier/profiles/` or bundled with CLI

## Creating Custom Profiles

### Extending Official Profiles
```toml
[profile]
name = "my-custom"
version = "1.0.0"
description = "Custom profile based on dev"
extends = "dev"  # Inherit from dev profile

# Override or add specific settings
[[tools]]
module = "tool-custom"
```

### Profile Overlays
Create a profile with the same name in a higher precedence location to override settings:

- Official `dev.toml` provides base configuration
- Team `.amplifier/profiles/dev.toml` adds team-specific tools
- User `~/.amplifier/profiles/dev.toml` adds personal preferences

All three merge automatically, with user settings taking precedence.

## Profile State Files

Amplifier uses two state files to track which profiles are active:

### `.amplifier/profile` (Local Choice)
- Contains your personal profile choice
- Simple text file with profile name
- **Gitignored** - won't cause merge conflicts
- Set with: `amplifier profile apply <name>`
- Clear with: `amplifier profile reset`

### `.amplifier/default-profile` (Project Default)
- Contains the team's recommended profile
- Simple text file with profile name
- **Checked into git** - shared across team
- Set with: `amplifier profile default --set <name>`
- Clear with: `amplifier profile default --clear`

**File Naming Convention:**
Following Unix conventions (like git's `HEAD` file), profile state files use no extension. Config files that require parsing (like `*.toml`) keep their extensions for format clarity.

**Git Strategy:**
```gitignore
# .gitignore
.amplifier/profile          # Local choice (gitignored)

# But DO commit:
# .amplifier/default-profile (project default)
# .amplifier/config.toml     (project config)
# .amplifier/profiles/       (custom profiles)
```

## Configuration Precedence

When using profiles, configuration is merged in this order (later overrides earlier):

1. Default configuration
2. Active profile (with inheritance + overlays)
3. User config (`~/.amplifier/config.toml`)
4. Project config (`.amplifier/config.toml`)
5. `--config` file flag
6. CLI flags (`--provider`, `--model`, etc.)
7. Environment variables (`${VAR_NAME}` expansion)

## Environment Variable Expansion

Profiles support environment variable expansion:

```toml
[[providers]]
module = "provider-anthropic"
config = { api_key = "${ANTHROPIC_API_KEY}" }
```

The `${VAR_NAME}` syntax is expanded when the profile is loaded.
