# Model Class Routing

Model class routing lets you specify *what kind* of model you need (`reasoning`, `fast`, etc.)
instead of naming specific providers and models. The system resolves classes to the best
available concrete model at runtime.

## How It Works

1. An agent or recipe step declares `class: reasoning` in its `provider_preferences`
2. The system queries all configured providers for models with matching capabilities
3. The routing strategy (cost, quality, balanced) picks the best candidate
4. Explicit `provider/model` entries after the class entry serve as fallbacks

## Configuration via settings.yaml

Routing is configured in the `routing:` section of `settings.yaml`. The configuration
follows progressive disclosure — start simple, add complexity only when needed.

### Level 0: Zero Configuration (Default)

No `routing:` section needed. The system uses the `balanced` strategy and resolves
model classes from whatever providers are configured.

```yaml
# ~/.amplifier/settings.yaml
providers:
  - module: provider-anthropic
  - module: provider-openai
# No routing section — defaults apply
```

### Level 1: Strategy Only

Choose how models are selected when multiple candidates match a class:

```yaml
# ~/.amplifier/settings.yaml
routing:
  strategy: cost      # Prefer cheapest matching model
```

| Strategy | Behavior |
|----------|----------|
| `cost` | Prefer cheapest matching model (lowest cost tier) |
| `quality` | Prefer highest-capability matching model |
| `balanced` | Balance cost and capability (default) |

### Level 2: Strategy + Cost Ceiling

Add a global cost ceiling to prevent expensive models from being selected:

```yaml
routing:
  strategy: balanced
  max_tier: tier-2     # Never select models above tier-2
```

Cost tiers range from `tier-1` (cheapest) to `tier-5` (most expensive). Each provider
module reports a `cost_tier` in its model metadata.

### Level 3: Per-Class Provider Restrictions

Restrict which providers can serve specific model classes:

```yaml
routing:
  strategy: quality
  classes:
    reasoning:
      providers: [anthropic]     # Only Anthropic for reasoning tasks
    fast:
      providers: [openai]        # Only OpenAI for fast tasks
```

### Level 4: Full Control

Combine strategy, global ceiling, and per-class overrides:

```yaml
routing:
  strategy: balanced
  max_tier: tier-3               # Global ceiling
  classes:
    reasoning:
      max_tier: tier-4           # Override: allow expensive reasoning models
      providers: [anthropic, openai]
    fast:
      max_tier: tier-1           # Override: keep fast tasks cheap
```

## Model Classes

| Class | Matches Models With | Typical Agents |
|-------|---------------------|----------------|
| `reasoning` | `reasoning` or `thinking` capability | zen-architect, security-guardian, bug-hunter |
| `fast` | `fast` capability | file-ops, shell-exec, post-task-cleanup |
| `vision` | `vision` capability | Image analysis agents |
| `research` | `deep_research` capability | Research agents |

## Using Classes in Agent Files

Agent `.md` files declare class preferences in their YAML frontmatter:

```yaml
# In agents/zen-architect.md
provider_preferences:
  - class: reasoning          # Resolved at runtime
  - provider: anthropic       # Fallback if class resolution fails
    model: claude-opus-*
  - provider: openai
    model: gpt-5.[0-9]
```

The `class:` entry is tried first. If no model matches (e.g., no providers have reasoning
models configured), the system falls through to the explicit provider/model entries.

## Using Classes in Recipes

Recipe steps use the same `provider_preferences` syntax:

```yaml
steps:
  - id: "design"
    agent: "foundation:zen-architect"
    provider_preferences:
      - class: reasoning
    prompt: "Design the architecture..."

  - id: "classify"
    agent: "foundation:explorer"
    provider_preferences:
      - class: fast
    prompt: "Classify this file..."
```

## Using Classes in the Delegate Tool

When spawning agents programmatically via the delegate tool:

```json
{
  "agent": "foundation:zen-architect",
  "instruction": "Design the caching layer",
  "provider_preferences": [
    {"class": "reasoning"},
    {"provider": "anthropic", "model": "claude-sonnet-*"}
  ]
}
```

## Resolution Flow

```
class: reasoning
    │
    ├─ Query all providers for models with "reasoning" or "thinking" capability
    ├─ Filter by max_tier (global and per-class)
    ├─ Filter by allowed providers (if per-class restriction set)
    ├─ Sort by strategy (cost → cheapest first, quality → best first)
    └─ Return best match, or fall through to next preference entry
```

## Inheritance

The routing configuration is registered as the `session.routing` capability and
automatically inherited by child sessions. This means delegated agents use the
same routing strategy as their parent — no per-agent configuration needed.

## Examples

**Cost-conscious team** — minimize spend, allow expensive models only for reasoning:
```yaml
routing:
  strategy: cost
  max_tier: tier-2
  classes:
    reasoning:
      max_tier: tier-4
```

**Quality-first team** — always use the best available models:
```yaml
routing:
  strategy: quality
```

**Single-provider team** — route everything through one provider:
```yaml
routing:
  strategy: balanced
  classes:
    reasoning:
      providers: [anthropic]
    fast:
      providers: [anthropic]
```