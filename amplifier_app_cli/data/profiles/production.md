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
  context:
    module: context-persistent
    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main
    config:
      max_tokens: 150000
      compact_threshold: 0.9
      auto_compact: true

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

{{parent_instruction}}

Production context:
- @DISCOVERIES.md

You are operating in production mode. Prioritize reliability and careful operation. Use extended thinking for critical decisions.
