---
profile:
  name: foundation
  version: 1.0.0
  description: Foundation configuration with only essential components

session:
  orchestrator: loop-basic
  context: context-simple

orchestrator:
  module: loop-basic
  source: git+https://github.com/microsoft/amplifier-module-loop-basic@main

context:
  module: context-simple
  source: git+https://github.com/microsoft/amplifier-module-context-simple@main

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-5
---

# Foundation Profile

The absolute minimum configuration required to run Amplifier. This profile serves as the base for all other profiles and contains only the essential components.

**When to use**: Never used directly - this is the foundation that all other profiles extend from.

**Extends**: None (this is the root profile)

**Key features**:
- Basic loop orchestrator for simple request-response flow
- Simple context manager with no persistence
- Anthropic provider with Claude Sonnet 4.5 as the default model
- No tools, hooks, or agents - pure foundation