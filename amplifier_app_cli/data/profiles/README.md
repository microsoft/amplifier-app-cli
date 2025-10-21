# Bundled Amplifier Profiles

This directory contains bundled Amplifier profiles that ship with the `amplifier-app-cli` package. These provide pre-configured setups for common use cases.

## Profile System Overview

Amplifier uses **settings files** with clear scopes to manage configuration:

- **Local settings** (`.amplifier/settings.local.yaml`) - Your personal choices, gitignored
- **Project settings** (`.amplifier/settings.yaml`) - Project defaults, checked in
- **User settings** (`~/.amplifier/settings.yaml`) - User-global preferences

**Precedence order:**

1. CLI flag `--profile` (highest priority)
2. Local settings (`.amplifier/settings.local.yaml`)
3. Project settings (`.amplifier/settings.yaml`)
4. User settings (`~/.amplifier/settings.yaml`)
5. System defaults (fallback)

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

### foundation.md

Absolute minimum configuration:

- Basic orchestrator
- Simple context manager
- Anthropic provider only
- No tools, no hooks (pure foundation)

### base.md

Core functionality (extends foundation):

- Inherits orchestrator, context, and provider from foundation
- Adds filesystem and bash tools
- Adds essential hooks: redaction (priority 10), logging (priority 100)
- Context management (100K tokens, 80% compact threshold)
- Auto-compaction enabled

### dev.md

Development configuration (extends base):

- Streaming orchestrator for better feedback
- Inherits all tools and hooks from base
- Adds web and search tools
- Includes zen-architect agent for task delegation (via agents schema)
- Ideal for interactive development

### production.md

Production-optimized (extends base):

- Streaming orchestrator
- Persistent context for session continuity
- Enhanced context limits (150K tokens, 90% threshold)
- Inherits all tools and hooks from base
- Adds web tools for production features

### test.md

Testing configuration (extends base):

- Mock provider for deterministic testing
- Reduced token limits for faster testing
- Inherits all tools and hooks from base
- Adds task tool for sub-agent testing
- Configurable failure simulation

### full.md

Kitchen sink configuration (extends base):

- All available providers (Anthropic, OpenAI, Azure OpenAI, Ollama)
- All available tools (filesystem, bash, web, search, task)
- All available agents (loaded via unified agents schema)
- All available hooks (redaction, logging, approval, backup, cost-aware scheduler, heuristic scheduler)
- Maximum token capacity (200K)
- Persistent context
- Comprehensive feature testing

## Using Profiles

### Set Active Profile (Personal Choice)

```bash
# Set your personal profile choice
amplifier profile use dev

# All your runs use this profile
amplifier run "your prompt"

# Check what's active
amplifier profile current
```

**Note:** Your profile choice is saved to `.amplifier/settings.local.yaml` (gitignored).

### Set Project Default (Team Standard)

```bash
# Set project default for team
amplifier profile use base --project

# Commit to share with team
git add .amplifier/settings.yaml
git commit -m "Set base as project default"
```

**Note:** Project settings are saved to `.amplifier/settings.yaml` (committed).

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

Profiles are searched in order of precedence (lowest to highest):

1. **Bundled profiles** (lowest): Included with `amplifier-app-cli` package (`amplifier_app_cli/data/profiles/`)
2. **Project profiles** (middle): `.amplifier/profiles/` (committed to git)
3. **User profiles** (highest): `~/.amplifier/profiles/` (personal)
4. **Environment variables** (absolute highest): `AMPLIFIER_PROFILE_<NAME>=path`

## Creating Custom Profiles

### Extending Bundled Profiles

```markdown
---
profile:
  name: my-custom
  version: 1.0.0
  description: Custom profile based on dev
  extends: dev # Inherit from bundled dev profile

# Override or add specific settings
tools:
  - module: tool-custom

# Configure agents using unified schema
agents:
  dirs: ["./agents"] # Load from directory
  include: ["zen-architect"] # Only load specific ones
  inline: # Add custom inline agents
    my-agent:
      description: "My custom agent"
      providers:
        - module: provider-anthropic
          config:
            default_model: claude-sonnet-4-5
---
```

### Profile Overlays

Create a profile with the same name in a higher precedence location to override settings:

- Bundled `dev.md` provides base configuration
- Project `.amplifier/profiles/dev.md` adds project-specific tools
- User `~/.amplifier/profiles/dev.md` adds personal preferences

All layers merge automatically, with user settings taking highest precedence.

## Settings Files

Amplifier uses YAML settings files with clear scope boundaries:

### `.amplifier/settings.local.yaml` (Local Overrides)

- Your personal settings (profile choice, module sources, config overrides)
- **Gitignored** - won't cause merge conflicts
- Set profile with: `amplifier profile use <name>`
- Edit directly for advanced overrides

**Example:**

```yaml
profile:
  active: dev

sources:
  tool-bash: file:///home/user/dev/tool-bash
```

### `.amplifier/settings.yaml` (Project Settings)

- Project-wide settings (default profile, pinned module versions, config standards)
- **Checked into git** - shared across project
- Set default with: `amplifier profile use <name> --project`
- Edit directly for project standards

**Example:**

```yaml
profile:
  default: dev

sources:
  tool-web: git+https://github.com/microsoft/amplifier-module-tool-web@v1.2.0

config:
  session:
    max_tokens: 150000
```

### `~/.amplifier/settings.yaml` (User Global)

- User-wide settings across all projects
- Personal preferences and module source overrides

**Git Strategy:**

```gitignore
# .gitignore
.amplifier/settings.local.yaml    # Local overrides (gitignored)

# But DO commit:
# .amplifier/settings.yaml         # Project settings
# .amplifier/profiles/              # Custom profiles
```

## Configuration Precedence

When using profiles, configuration is merged in this order (later overrides earlier):

1. Bundled defaults
2. Active profile (with inheritance + overlays)
3. User settings (`~/.amplifier/settings.yaml`)
4. Project settings (`.amplifier/settings.yaml`)
5. Local settings (`.amplifier/settings.local.yaml`)
6. `--config` file flag
7. CLI flags (`--profile`, `--provider`, `--model`, etc.)
8. Environment variables (`${VAR_NAME}` expansion)

## Agent Configuration

The unified `agents` schema supports flexible agent loading:

### Loading Patterns

```yaml
# 1. Load all agents from directory
agents:
  dirs: ["./agents"]

# 2. Load specific agents from directory
agents:
  dirs: ["./agents"]
  include: ["zen-architect", "bug-hunter"]

# 3. Define agents inline only
agents:
  inline:
    custom-agent:
      description: "Custom agent"
      providers:
        - module: provider-anthropic
      tools:
        - module: tool-filesystem

# 4. Combine directory and inline agents
agents:
  dirs: ["./agents"]
  include: ["zen-architect"]  # Filter directory agents
  inline:
    extra-agent:
      description: "Additional agent"
```

### Schema Fields

- **dirs** (optional): List of directories to scan for agent .md files
- **include** (optional): Filter to only load specific agent names from directories
- **inline** (optional): Define agents directly in the profile

When `dirs` is specified without `include`, all agents in those directories are loaded.
When both `dirs` and `inline` are used, agents from both sources are merged.

## Session Configuration

The `session` section configures the orchestrator (execution loop) and context manager (memory) for your sessions.

### Orchestrator Configuration

The orchestrator controls how the AI processes requests. Key configuration:

```yaml
session:
  orchestrator:
    module: loop-streaming
    config:
      extended_thinking: true   # Enable thinking blocks (Sonnet 4.5+)
      max_iterations: 50        # Maximum turns before stopping
```

**max_iterations** (required, default varies by profile)
- Maximum number of tool execution iterations per request
- Prevents infinite loops if AI gets stuck
- Profiles set different defaults based on use case:
  - **base/foundation**: 30 (conservative default)
  - **dev**: 50 (allows more exploration during development)
  - **production/full**: 100 (handle complex multi-step tasks)
  - **test**: 20 (faster test execution)

**When to adjust**:
- Increase if hitting "maximum iterations exceeded" errors
- Decrease for faster timeouts on runaway loops
- Complex multi-step tasks may need 100+

**Example - Custom profile with higher limit**:
```yaml
session:
  orchestrator:
    module: loop-streaming
    config:
      extended_thinking: true
      max_iterations: 150    # Allow very complex tasks
```

### Context Configuration

Context managers control conversation memory and token limits:

```yaml
session:
  context:
    module: context-simple
    config:
      max_tokens: 100000
      compact_threshold: 0.8
      auto_compact: true
```

See base profile for full context configuration examples.

## UI Configuration

The `ui` section in profiles controls the visual display and formatting of CLI output. All UI settings are optional with sensible defaults.

### Available Fields

```yaml
ui:
  # Tool output truncation (-1 for all, N for first N lines)
  tool_output_lines: 3

  # Maximum length for tool arguments in display
  max_arg_length: 100

  # Show live elapsed time during LLM operations
  show_elapsed_time: true

  # Use tree-style formatting (● and ⎿ characters)
  use_tree_formatting: true

  # Render markdown in LLM responses (bold, italic, code blocks)
  render_markdown: true

  # Show thinking stream (for streaming orchestrators)
  show_thinking_stream: true

  # Lines of tool I/O to show in streaming UI
  show_tool_lines: 5
```

### Field Details

**tool_output_lines** (default: 3)
- Controls how many lines of tool output to display
- Set to `-1` to show all output (verbose mode)
- Set to `1-5` for cleaner conversations
- Example: Tool returns 50 lines, only first 3 shown with "... (47 more lines)"

**max_arg_length** (default: 100)
- Maximum character length for tool arguments in display
- Long values are truncated with "..."
- Keeps command displays clean and readable

**show_elapsed_time** (default: true)
- Shows live timer during LLM operations: "Processing... (2.3s)"
- Updates in real-time so user knows system is working
- Includes ctrl+c interrupt guidance

**use_tree_formatting** (default: true)
- Applies visual hierarchy with bullet characters:
  - `●` for messages and tool calls
  - `⎿` for first line of tool output
  - Indentation for subsequent lines
- Creates professional, scannable output

**render_markdown** (default: true)
- Renders markdown formatting in LLM responses
- Bold, italic, code blocks, lists display correctly
- Dramatically improves readability of responses

**show_thinking_stream** (default: true, streaming only)
- Shows thinking blocks as they stream from model
- Only applies to streaming orchestrators
- Provides transparency into model reasoning

**show_tool_lines** (default: 5, streaming only)
- Controls tool output lines in streaming UI hook
- Different from `tool_output_lines` (applies to final display)
- Only used with streaming-ui hook

### Profile-Specific Configurations

Different profiles optimize UI for their use cases:

**base.md** (standard defaults):
```yaml
ui:
  tool_output_lines: 3
  max_arg_length: 100
  show_elapsed_time: true
  use_tree_formatting: true
  render_markdown: true
```

**dev.md** (development-friendly):
```yaml
ui:
  show_thinking_stream: true  # See model reasoning
  show_tool_lines: 5          # More tool output during streaming
  tool_output_lines: 3
  max_arg_length: 100
  show_elapsed_time: true
  use_tree_formatting: true
  render_markdown: true
```

**production.md** (minimal output):
```yaml
ui:
  tool_output_lines: 2        # Minimal truncation
  max_arg_length: 80          # Shorter args
  show_elapsed_time: true
  use_tree_formatting: true
  render_markdown: true
```

**test.md** (verbose for debugging):
```yaml
ui:
  tool_output_lines: -1       # Show ALL output
  max_arg_length: 150         # Longer args for debugging
  show_elapsed_time: true
  use_tree_formatting: true
  render_markdown: true
```

### Customizing UI in Your Profile

To customize UI settings, create a profile overlay or extend an existing profile:

```yaml
---
profile:
  name: my-profile
  extends: base

ui:
  tool_output_lines: 5        # Show more tool output
  render_markdown: false      # Disable markdown (prefer plain text)
---
```

Or override in settings files:

```yaml
# .amplifier/settings.yaml
config:
  ui:
    tool_output_lines: -1     # Always show full output
```

## Environment Variable Expansion

Profiles support environment variable expansion:

```yaml
providers:
  - module: provider-anthropic
    config:
      api_key: ${ANTHROPIC_API_KEY}
```

The `${VAR_NAME}` syntax is expanded when the profile is loaded.
