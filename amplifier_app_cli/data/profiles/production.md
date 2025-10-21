---
profile:
  name: production
  version: 1.1.0
  description: Production configuration optimized for reliability
  extends: base

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true
      max_iterations: 100
  context:
    module: context-persistent
    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main
    config:
      max_tokens: 150000
      compact_threshold: 0.9
      auto_compact: true

ui:
  tool_output_lines: 2
  max_arg_length: 80
  show_elapsed_time: true
  use_tree_formatting: true
  render_markdown: true

tools:
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main

# Example: Selective agent loading for production
# Only researcher (no bug-hunter, modular-builder, zen-architect)
agents:
  dirs:
    - ./agents
  include:
    - researcher
---

# Production Profile

An optimized configuration for production use with enhanced reliability, persistence, and higher capacity limits.

**When to use**: For production deployments where persistence and reliability are critical.

**Extends**: base (inherits filesystem, bash, redaction, and logging)

**Key features**:

- Streaming orchestrator with extended thinking enabled
- Persistent context manager for maintaining state across sessions
- Increased token limit (150K) with higher compaction threshold (90%)
- Web tool for external data access
- Inherits security and observability hooks from base profile
- Optimized UI for production with minimal output truncation (2 lines) and clean formatting
